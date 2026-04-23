# Subumbra — Key Lifecycle Management
## Future Functionality Proposal
*Prepared by Claude Design · April 2026*
*For council review — proposed Round 45 (Key Lifecycle Controls)*

---

## Overview

This proposal covers four new capabilities:

1. **Delete** — permanently remove a key record
2. **Pause / Block** — temporarily suspend a key without deleting it
3. **Rate limiting** — cap requests per key per time window
4. **Time-window restrictions** — allow a key only during defined hours/days

Plus honest answers to three hard questions:
- What happens when a key is added "off hours" and time restrictions are active?
- How do we enforce all of this without a background process running 24/7?
- How do local enforcement, Cloudflare enforcement, and audit logging compose?

---

## The Core Insight: Where Enforcement Actually Lives

Before designing any of these features, the architecture must be understood clearly.
There are **three enforcement points** in the Subumbra stack, and they are not equivalent:

```
Browser / App
    ↓
subumbra-proxy (adapter)
    ↓  POST /proxy
Cloudflare Worker + Durable Object
    ↓  decrypted key injected here
API Provider
```

```
subumbra-keys  ←  serves encrypted records to adapters
    ↓
keys.json + audit.db  ← state lives here
```

The Cloudflare Worker is the only point that ever touches the plaintext key.
`subumbra-keys` never decrypts. `subumbra-proxy` never decrypts.

This means:
- **Local enforcement** (subumbra-keys refusing to serve a record) = the adapter
  never gets the ciphertext = the Worker never gets called = hard block, no network egress
- **Worker enforcement** (Worker refusing to proxy even with valid ciphertext) =
  belt-and-suspenders, defence-in-depth, catches compromised adapters
- **Both together** = an attacker needs to compromise both layers simultaneously

For pause/block and time restrictions, **both layers should enforce independently**.
They are not redundant — they serve different threat models.

---

## Feature 1: Delete

### What it means

Remove the key record from `keys.json` permanently. The encrypted blob is gone.
The plaintext key is irrecoverable from Subumbra (it was never stored).
The API provider key itself is unaffected — deletion from Subumbra does not
revoke the key at the provider. That is an operator responsibility.

### Complexity: Low

This is the simplest operation. `keys.json` is a dict — delete the entry, write atomically.

### Implementation

**`subumbra-keys/app.py`** — new endpoint:
```
DELETE /keys/<key_id>   requires can_write_keys scope
```
- Remove entry from `keys.json` via `_write_keys_atomic()`
- Record audit event: `verdict=allow, reason_code=key_deleted`
- Return `200 OK` or `404` if not found

**UI modal** — confirmation dialog:
```
⚠ Delete "anthropic_prod"?

This removes the encrypted record from Subumbra.
The API key at Anthropic is NOT revoked — do that separately.
This cannot be undone.

[ Cancel ]  [ Delete Key ]
```

**Terminal** — new bootstrap flag:
```
docker compose --profile bootstrap run --rm -it bootstrap --delete <key_id>
```

### Conflicts and risks

| Risk | Mitigation |
|---|---|
| Active requests in flight when key is deleted | Worker serves from Durable Object cache (~100ms window); deletion is near-instant in practice |
| Operator forgets to revoke at provider | UI warning + audit log entry with reminder copy |
| Accidental delete | Confirmation dialog (UI) + `--confirm` flag required (terminal) |
| keys.json write race with subumbra-keys serving | Atomic `os.replace()` — same pattern as bootstrap; reader gets old or new, never corrupt |

---

## Feature 2: Pause / Block

### What it means

Suspend a key so requests are denied without deleting the record.
The key can be unpaused and resume serving immediately.
Useful for: suspected compromise, vendor incident, planned maintenance, off-hours policy.

### The state question: where does "paused" live?

Three options, each with tradeoffs:

**Option A: In `keys.json` as a field**
```json
{
  "anthropic_prod": {
    "enc_version": 2,
    "paused": true,
    "paused_at": "2026-04-22T18:00:00Z",
    "paused_by": "admin",
    ...
  }
}
```
- Durable across restarts ✓
- `subumbra-keys` checks the field before serving ✓
- Requires atomic write to pause/unpause ✓
- Worker cannot see this field (it only sees what the adapter sends) ✗
  → Worker enforcement requires a separate signal

**Option B: Cloudflare KV entry per key**
```
KV key: "paused:anthropic_prod" → "1"
```
- Worker can check this before decrypting ✓
- Survives Worker cold starts ✓
- Requires KV write API call from UI/terminal (needs CF credentials at runtime) ✗
- KV is eventually consistent (up to 60s propagation) — not instant ✗

**Option C: Both (recommended)**

`keys.json` is the authoritative source. `subumbra-keys` refuses to serve a paused key immediately. A KV signal is pushed to Cloudflare as belt-and-suspenders — eventual consistency is acceptable because `subumbra-keys` already blocks fast.

```
Pause request → write paused:true to keys.json (instant, local)
              → push KV entry to CF (async, best-effort, eventual)
              → audit log both events
```

If the CF push fails, the local block is still in force. The CF push failure is logged and surfaced in the dashboard.

### No background process needed

The key insight: **enforcement is pull-based, not push-based**.

`subumbra-keys` checks `paused` at request time — when an adapter calls `GET /keys/<key_id>`, the handler reads the field from the in-memory loaded keys dict. No daemon, no cron, no watchdog. The check costs one dict lookup.

The CF KV push is a one-time HTTP call at the moment of pause, not a recurring job.

### Implementation

**`subumbra-keys/app.py`** — check in `get_key()`:
```python
entry = keys[key_id]
if entry.get("paused"):
    _record_audit(..., verdict="deny", reason_code="key_paused")
    return _err("key paused", 403)
```

**New endpoints:**
```
POST /keys/<key_id>/pause    → set paused:true, paused_at, paused_by
POST /keys/<key_id>/unpause  → set paused:false, clear paused_at/paused_by
```

**`ui/app.py`** — proxy endpoints + optional CF KV push via the existing `CF_WORKER_URL` credential path.

**UI** — pause toggle on each key card. Paused keys show a visual state (red border, `⏸ Paused` badge). The audit log shows `key_paused` reason codes.

**Terminal:**
```
docker compose --profile bootstrap run --rm bootstrap --pause <key_id>
docker compose --profile bootstrap run --rm bootstrap --unpause <key_id>
```

---

## Feature 3: Rate Limiting

### Complexity: Medium

Rate limiting is stateful — it requires tracking request counts over a rolling time window. There are two honest approaches:

### Option A: Local rate limiting in `subumbra-keys` (recommended for POC)

`subumbra-keys` already maintains `_request_counts` in memory. Extend this with a sliding window counter per key:

```python
# In-memory sliding window — reset on container restart
_rate_windows: dict[str, deque] = defaultdict(deque)   # key_id → deque of timestamps
_rate_limits: dict[str, dict] = {}   # key_id → {max_requests, window_seconds}
```

At request time:
```python
limit = _rate_limits.get(key_id)
if limit:
    now = time.time()
    window = _rate_windows[key_id]
    # Prune entries outside the window
    while window and window[0] < now - limit["window_seconds"]:
        window.popleft()
    if len(window) >= limit["max_requests"]:
        _record_audit(..., verdict="deny", reason_code="rate_limit_exceeded")
        return _err("rate limit exceeded", 429)
    window.append(now)
```

Rate limit config lives in `keys.json`:
```json
{
  "anthropic_prod": {
    "rate_limit": {
      "max_requests": 100,
      "window_seconds": 3600
    }
  }
}
```

**Caveat:** resets on container restart. For a single-operator POC this is acceptable.
For multi-instance or persistence, move to Redis or SQLite (see Option B).

### Option B: SQLite rate limiting (durable, no extra services)

`subumbra-keys` already has `audit.db`. Add a `rate_window_events` table:
```sql
CREATE TABLE rate_window_events (
    key_id TEXT NOT NULL,
    ts     INTEGER NOT NULL
);
CREATE INDEX idx_rate ON rate_window_events(key_id, ts);
```

Query at request time:
```sql
SELECT COUNT(*) FROM rate_window_events
WHERE key_id = ? AND ts > ?
```

Prune on each write (keep only last N hours). This survives restarts and is auditable.

### Worker-side rate limiting

The CF Worker can enforce rate limits using Cloudflare's built-in rate limiting rules (CF plan dependent) or by tracking counts in a Durable Object. This is more complex but provides protection even if `subumbra-keys` is bypassed. For POC, local enforcement is sufficient.

### No background process needed

Rate limiting is enforced at request time — zero background work. The sliding window prunes itself passively on each new request. The SQLite approach adds a DELETE on each write (same pattern as the existing nonce prune).

---

## Feature 4: Time-Window Restrictions

### What it means

A key is only valid during defined hours/days. Outside the window, requests are denied.

Examples:
- `allowed_hours: 09:00–17:00 UTC` (business hours only)
- `allowed_days: [Mon, Tue, Wed, Thu, Fri]` (weekdays only)
- `allowed_after: 2026-05-01T00:00:00Z` (future activation)
- `allowed_until: 2026-12-31T23:59:59Z` (expiry)

### Config in `keys.json`

```json
{
  "anthropic_prod": {
    "time_restrictions": {
      "allowed_days":  ["Mon","Tue","Wed","Thu","Fri"],
      "allowed_hours": {"start": "09:00", "end": "17:00", "tz": "UTC"},
      "allowed_after": "2026-05-01T00:00:00Z",
      "allowed_until": "2026-12-31T23:59:59Z"
    }
  }
}
```

### Enforcement — pull-based at request time

```python
def _check_time_restrictions(entry: dict, now: datetime) -> str | None:
    """Returns a reason_code string if denied, None if allowed."""
    r = entry.get("time_restrictions")
    if not r:
        return None

    if r.get("allowed_after"):
        if now < datetime.fromisoformat(r["allowed_after"]):
            return "not_yet_active"

    if r.get("allowed_until"):
        if now > datetime.fromisoformat(r["allowed_until"]):
            return "key_expired"

    if r.get("allowed_days"):
        day_name = now.strftime("%a")   # "Mon", "Tue", etc.
        if day_name not in r["allowed_days"]:
            return "outside_allowed_days"

    if r.get("allowed_hours"):
        h = r["allowed_hours"]
        start = datetime.strptime(h["start"], "%H:%M").time()
        end   = datetime.strptime(h["end"],   "%H:%M").time()
        current_time = now.time()
        if not (start <= current_time <= end):
            return "outside_allowed_hours"

    return None
```

Called in `get_key()` before serving:
```python
denial_reason = _check_time_restrictions(entry, datetime.now(timezone.utc))
if denial_reason:
    _record_audit(..., verdict="deny", reason_code=denial_reason)
    return _err("key access restricted", 403)
```

**No background process needed.** `subumbra-keys` checks the current time on every request. The restriction is enforced at the moment of the fetch — no daemon required.

---

## The Off-Hours Add Problem

This is the most interesting design question in the proposal.

### Scenario

An admin adds a key at 11pm with a `allowed_hours: 09:00–17:00` restriction.
The key is written to `keys.json` at 11pm. The restriction says it cannot be used until 9am.

**What should happen?**

Three possible behaviours:

**A. Allow the add, enforce at request time** *(recommended)*
The key record is written immediately. The time restriction is checked when an adapter tries to fetch it. At 11pm, `get_key()` returns `403 outside_allowed_hours`. At 9am it returns `200`. No conflict — the restriction is honoured correctly.

**B. Warn the operator at add time**
The UI detects that the key's time restriction would currently deny access and shows:
```
⚠ Note: this key's time restriction means it will not be usable
until Monday 09:00 UTC (in 10 hours). It will be saved now and
activate automatically.
```
This is a UX nicety, not a security requirement. Cheap to add.

**C. Block the add entirely if outside the allowed window**
Unnecessarily restrictive. Admins set up keys in advance — forcing them to wait until business hours to configure a business-hours key is hostile UX. Option A + B is correct.

### What if the restriction itself is added after the key?

Same answer — the check happens at request time against the current state of `keys.json`. Updating a time restriction takes effect immediately on next request. No restart, no flush, no cache to invalidate. `_load_keys()` reads from disk on every request (the current implementation) or from an in-memory cache that's invalidated on write.

---

## Logging & Audit — What to Capture

The existing audit schema captures `verdict` and `reason_code`. The new reason codes slot in cleanly:

| reason_code | Meaning |
|---|---|
| `key_paused` | Key is administratively suspended |
| `key_deleted` | Key was deleted (for the deletion audit event) |
| `rate_limit_exceeded` | Request count exceeded window limit |
| `outside_allowed_hours` | Request outside permitted hours |
| `outside_allowed_days` | Request on a non-permitted day |
| `not_yet_active` | Request before `allowed_after` |
| `key_expired` | Request after `allowed_until` |

These all flow through the existing `_record_audit()` function — no schema changes needed.

### What should the UI surface?

**Per-key violation count** — dashboard key card shows a `⚠ 12 violations` badge if any denied requests exist in the audit log for that key in the last 24h.

**Violations panel** — a new collapsible section in the dashboard below the request log, filtered to `verdict=deny` rows. Sorted by most recent. Operators see at a glance what is being blocked and why.

**Alert on repeated violations** — if the same `remote` IP hits a paused or time-restricted key more than N times in a window, surface a `⚠ Possible probe from 192.168.x.x` banner. This is cheap: count `verdict=deny` by remote in the audit query.

---

## Cloudflare Enforcement — What's Actually Needed

For pause/block and time restrictions to be enforced at the Worker level (defence-in-depth), the Worker needs to be able to check key state before decrypting.

### Option A: KV entry per key (simple, eventual)

```
KV key: "key_state:anthropic_prod"
KV value: JSON { "paused": true, "allowed_hours": {...} }
```

Worker checks this KV entry before calling the Durable Object:
```javascript
const state = await env.PROVIDER_REGISTRY_KV.get(`key_state:${key_id}`, "json");
if (state?.paused) return new Response("Key paused", { status: 403 });
// time checks...
```

**Propagation:** KV is eventually consistent (~60s). This is acceptable because `subumbra-keys` already blocks instantly — the KV check is a secondary layer for the case where a compromised adapter tries to call the Worker directly with a captured ciphertext blob.

Push a KV update whenever key state changes (pause, unpause, restriction update). This can be done from `ui/app.py` via the CF API, same as `--push-registry` does today.

### Option B: Embed state in the ciphertext request (zero new infra)

`subumbra-proxy` already sends a canonical `POST /proxy` body. Add a `key_state_token` field — a short-lived HMAC-signed token generated by `subumbra-keys` when serving the encrypted record, attesting that the key was valid at fetch time.

```
subumbra-keys generates: HMAC(key_id + ":" + unix_timestamp, SUBUMBRA_HMAC_KEY)
subumbra-proxy includes: key_state_token in /proxy body
Worker verifies: token is fresh (within 30s) and signature is valid
```

If the key is paused, `subumbra-keys` refuses to serve it → no token is generated → the Worker never gets called. The HMAC key is already pushed to CF Secrets. No new CF infrastructure needed.

This is more elegant than KV for the pause/block case but doesn't cover time-restriction enforcement at the Worker level (since the token is generated at adapter-fetch time, not at Worker-decode time). For time restrictions, KV is still needed if Worker-level enforcement is desired.

**Recommendation for POC:** implement local enforcement only (subumbra-keys checks) with audit logging. Add Worker-level KV enforcement as a hardening step when the codebase reaches MVP. Document this as a known limitation in `PROJECT_STATUS.md`.

---

## Background Processes — The Full Answer

The question was: *how do we enforce all of this without a 24/7 background process?*

The complete answer:

| Feature | Enforcement mechanism | Background work needed? |
|---|---|---|
| Delete | Atomic write; missing key = 404 on next request | None |
| Pause/Block | `paused` field checked at request time | None (KV push is one-shot on state change) |
| Rate limiting | Sliding window checked at request time; prune on write | None |
| Time restrictions | Current time checked at request time | None |
| Session TTL sweep | Tiny background thread (already exists in proposal) | Yes — but it's ~10 lines, sleeps 30s, no I/O |
| SSE watcher | Background thread polling subumbra-keys every 3s | Yes — already proposed in Round 44 |

**Every enforcement decision is made at request time by the service that receives the request.** There are no daemons watching clocks, no cron jobs, no scheduled tasks. The system is correct at the moment of each request and requires zero work between requests.

The only background threads are the session TTL sweep (already proposed) and the SSE status watcher (already proposed). Both are lightweight, sleep-based, and already planned.

---

## What Could Go Wrong — Honest Risk List

Things not covered by the above that the council should consider:

**1. Clock skew between containers**
Time restriction enforcement uses `datetime.now(timezone.utc)` inside `subumbra-keys`. If the container's clock drifts from the operator's expectation (e.g. NTP not running in the Docker host), keys may be allowed or denied at unexpected times. Mitigation: enforce UTC only, document this clearly, consider surfacing the container's current time in the dashboard.

**2. Cached ciphertext replay**
`subumbra-proxy` fetches the ciphertext from `subumbra-keys` on each request (it does not cache). But a compromised adapter could cache a ciphertext blob it fetched before the key was paused and replay it directly to the Worker. Mitigation: the `key_state_token` HMAC approach (Option B above) closes this — the token is short-lived and cannot be reused.

**3. Audit log as the only violation record**
If `audit.db` is lost (volume deleted, corruption), violation history is gone. For the POC this is accepted. For MVP: periodic export or remote syslog.

**4. UI-triggered writes during high load**
If 10 operators simultaneously hit "Pause" on the same key, `_write_keys_atomic()` with `os.replace()` is safe (atomic) but the last writer wins. For POC (small team) this is fine. For multi-admin environments: add a write lock or optimistic concurrency (etag on keys.json).

**5. Rate limit state lost on restart**
In-memory sliding window resets on container restart. A bad actor who triggers a restart (OOM, deployment) can reset their rate limit counter. Mitigation: use SQLite option (Option B in Feature 3) to persist window state across restarts.

**6. Time restriction bypass via adapter clock**
The time restriction check runs in `subumbra-keys` using the server's clock. An adapter cannot influence this. But if the Docker host clock is wrong (see point 1), the check is wrong. UTC + NTP is the defence.

**7. No notification on violation**
A key being probed outside hours silently logs to `audit.db`. No alert is sent. For MVP: add a webhook or email notification trigger on repeated violations from the same remote.

---

## Proposed `keys.json` Schema Extension

All new fields are optional and backward-compatible. Existing records without them behave exactly as today.

```json
{
  "anthropic_prod": {
    "key_id":       "anthropic_prod",
    "enc_version":  2,
    "pub_key_fp":   "sha256:...",
    "wrapped_dek":  "...",
    "ciphertext":   "...",
    "provider":     "anthropic",
    "target_host":  "api.anthropic.com",
    "created_at":   "2026-04-01T09:00:00Z",
    "label":        "anthropic_prod",

    "paused":       false,
    "paused_at":    null,
    "paused_by":    null,

    "rate_limit": {
      "max_requests":  100,
      "window_seconds": 3600
    },

    "time_restrictions": {
      "allowed_days":  ["Mon","Tue","Wed","Thu","Fri"],
      "allowed_hours": { "start": "09:00", "end": "17:00", "tz": "UTC" },
      "allowed_after": null,
      "allowed_until": null
    }
  }
}
```

---

## Proposed Work Order (future round)

```
Phase 1 — Local enforcement (subumbra-keys only)
  1a. keys.json schema extension (paused, rate_limit, time_restrictions)
  1b. subumbra-keys: DELETE /keys/<key_id>
  1c. subumbra-keys: POST /keys/<key_id>/pause + unpause
  1d. subumbra-keys: time_restrictions check in get_key()
  1e. subumbra-keys: rate limiting (in-memory Option A first, SQLite Option B later)
  1f. ui/app.py: proxy endpoints for all of the above
  1g. dashboard.js + HTML: delete confirm modal, pause toggle, restrictions editor
  1h. bootstrap: --delete, --pause, --unpause terminal flags

Phase 2 — Worker-level enforcement (belt-and-suspenders)
  2a. key_state_token HMAC in subumbra-keys response + verification in Worker
  2b. KV key state push on pause/unpause (optional, eventually consistent)

Phase 3 — Alerting (MVP hardening)
  3a. Violation count badge on key cards in dashboard
  3b. Violations panel (filtered audit log)
  3c. Webhook/notification on repeated violations
```

Phase 1 can be a single council round. Phase 2 and 3 are separate rounds.

---

*End of proposal*
