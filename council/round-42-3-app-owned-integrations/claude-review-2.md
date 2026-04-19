# Round 42.3 Secondary Review — App-Owned Integrations

Author: Claude
Date: 2026-04-19
Round: `round-42-3-app-owned-integrations`

Reviews read: claude-review.md, codex-review.md, gemini-review.md

---

## Purpose of This Review

This secondary pass resolves the key technical dispute between Codex and Gemini (Worker body
inspection reliability), adds two new findings surfaced by reading `post-bootstrap.sh` and
`ui/app.py` directly, and confirms or refutes the factual claims across all three first-round
reviews.

---

## Findings Table

| # | Source | Severity | Finding | Verdict |
|---|---|---|---|---|
| S1 | New | **Critical** | `post-bootstrap.sh:43` makes `SUBUMBRA_TOKEN_LITELLM` a blocking required value — if Change R2 removes the litellm adapter from bootstrap without updating `post-bootstrap.sh`, every new install will fail at `post-bootstrap.sh` | New finding — missing from all three reviews |
| S2 | Codex F1 | Confirmed | Operator truth cleanup scope is wider than `docs/subumbra-install.md` alone — `README.md`, `docs/subumbra-testing.md`, `post-bootstrap.sh`, and bootstrap summary text also require changes | Both Claude and Codex identified this; now precisely scoped |
| S3 | Dispute | Resolved | Codex F3 vs Gemini: is `{"error":"unauthorized"}` body inspection reliable? — **Gemini is correct for current providers; Codex is correct about long-term brittleness** — but both apply because Change D and Change E are complementary, not alternatives | See §2 |
| S4 | Gemini | Confirmed | `ui/app.py:154` probes `CF_WORKER_URL/health` (no auth) instead of the sidecar `/health` — UI will not surface `worker_auth` state unless updated | `ui/app.py:102-115`, `ui/app.py:154` |
| S5 | New | Minor | `bootstrap/subumbra-bootstrap.py:1828,1834` — bootstrap success summary still instructs "Update litellm/config.yaml" and uses `docker exec -i litellm` for health check example | `bootstrap/subumbra-bootstrap.py:1828-1837` |
| S6 | All | Confirmed | Proposals are correct that bundled LiteLLM must be fully removed, not renamed/profile-gated | `docker-compose.yml:73-106,81-82` |
| S7 | Claude F8 | Confirmed | Sidecar healthcheck interval is 10s — synchronous outbound Worker probe per Docker poll is a real risk | `docker-compose.yml:185-189` |

---

## Detailed Analysis

### S1 — `post-bootstrap.sh` blocking dependency on `SUBUMBRA_TOKEN_LITELLM` (CRITICAL)

None of the three first-round reviews identified this specific breakage. `post-bootstrap.sh:43`:

```bash
if [[ -z "$SUBUMBRA_ADAPTER_REGISTRY" || -z "$SUBUMBRA_TOKEN_LITELLM" || -z "$SUBUMBRA_TOKEN_PROXY" || \
      -z "$SUBUMBRA_TOKEN_UI" || -z "$SUBUMBRA_TOKEN_PROBE" || -z "$SUBUMBRA_HMAC_KEY" || -z "$CF_WORKER_URL" ]]; then
    echo "ERROR: runtime.env is missing one or more required values." >&2
    exit 1
fi
```

`SUBUMBRA_TOKEN_LITELLM` is in the required-values guard. This value comes from
`bootstrap/subumbra-bootstrap.py:1745` writing `SUBUMBRA_TOKEN_LITELLM=...` to `runtime.env`.

If the council approves removing the `litellm` adapter from `ADAPTER_SCOPE_VARS`
(`bootstrap/subumbra-bootstrap.py:105`) and the bootstrap wizard (`line 1074`), bootstrap
will no longer write `SUBUMBRA_TOKEN_LITELLM` to `runtime.env`, and `post-bootstrap.sh`
will exit with `ERROR: runtime.env is missing one or more required values.`

**The chain:** `bootstrap/subumbra-bootstrap.py:105,1074,1741,1745` →
`runtime.env:SUBUMBRA_TOKEN_LITELLM` → `post-bootstrap.sh:29,43,68`.

There is also a fallback path at `post-bootstrap.sh:30-31`:
```bash
SUBUMBRA_TOKEN_LITELLM=$(_get SUBUMBRA_TOKEN_LITELLM)
if [[ -z "$SUBUMBRA_TOKEN_LITELLM" ]]; then
    SUBUMBRA_TOKEN_LITELLM=$(_get FORGE_TOKEN_LITELLM)
fi
```
Even the `FORGE_TOKEN_LITELLM` fallback will be empty if neither name is written to
`runtime.env`. The check at line 43 is then fatal.

**Required fix:** The approved plan must explicitly include `post-bootstrap.sh` in Change R2
(bootstrap litellm adapter fate). The fix is one of:
- Remove `SUBUMBRA_TOKEN_LITELLM` from the required-values guard at line 43 and the
  `.env` write at line 68
- Or retain the adapter but make it optional/unnamed

This is not recoverable silently — operators will see a hard failure on `./post-bootstrap.sh`
and will not understand why without reading this document.

---

### S2 — Confirmed and scoped: operator truth cleanup is wider than stated in proposals

Codex F1 identified this at a high level. I now confirm precise targets after reading source:

**`post-bootstrap.sh`** (beyond S1):
- Line 20: `SUBUMBRA_TOKEN_LITELLM=x FORGE_TOKEN_LITELLM=x` in the env list passed to
  `docker compose run` to seed `runtime.env` reads
- Line 68: `update_env "SUBUMBRA_TOKEN_LITELLM" "$SUBUMBRA_TOKEN_LITELLM"` — writes to `.env`
- Line 74: `update_env "LITELLM_ALLOWED_KEYS" "$LITELLM_ALLOWED_KEYS"` — writes to `.env`
- Line 80: `SUBUMBRA_TOKEN_LITELLM` in the post-write verification loop

**`bootstrap/subumbra-bootstrap.py` summary text:**
- Line 1828: "Update litellm/config.yaml with the correct subumbra:key_id values — see
  copy/paste hints below." — references obsolete callback contract
- Line 1834: `docker exec -i litellm python - <<'PY'` — health check uses litellm container
- Line 1840: `{chr(10).join(litellm_alignment_lines)}` — prints LiteLLM alignment block

The `_build_litellm_alignment_lines` function at line 647-679 already branches on whether
the `litellm` scope is populated (line 654: `if litellm_key_ids:`). If the litellm adapter
is removed, this function must also be removed or generalized.

**`README.md`** (exact references):
- Line 311: subumbra-keys health check uses `docker exec litellm`
- Line 346: "Make sure the `subumbra:<key_id>` values in `litellm/config.yaml`" — references
  the callback-era `subumbra:` prefix contract, now obsolete

**`.env.example`**:
- Line 40: `FORGE_TOKEN_LITELLM=` (pre-rebrand name in generated section)
- Line 46: `LITELLM_ALLOWED_KEYS=` (generated section)
Both are orphaned by removing the litellm adapter.

---

### S3 — Resolving the Codex vs Gemini dispute on body inspection reliability

**Codex F3 claim:** Body inspection for `{"error":"unauthorized"}` is "too brittle" because
the Worker forwards provider responses unchanged, so the proxy cannot reliably know if a 401
came from the Worker auth gate.

**Gemini claim:** The inspection is reliable because the Worker's auth logic is the only path
returning that specific string.

**Evidence from source:**

`worker.js:281-285` defines `jsonError`:
```javascript
function jsonError(message, status) {
  return new Response(JSON.stringify({ error: message }), { ... });
}
```

`worker.js:454`: `return jsonError("unauthorized", 401)` → `{"error":"unauthorized"}`

`worker.js:389-392` — provider response forwarding:
```javascript
return new Response(upstreamResponse.body, {
  status: upstreamResponse.status,
  headers: responseHeaders,
});
```

The Worker forwards provider responses unchanged. If a provider returns 401, the provider's
own body comes through.

**Are any provider 401 bodies `{"error":"unauthorized"}`?**

Real provider 401 bodies use nested structures:
- Anthropic: `{"type":"error","error":{"type":"authentication_error","message":"..."}}`
- OpenAI: `{"error":{"message":"Incorrect API key...","type":"invalid_request_error"}}`
- Groq: `{"error":{"message":"Invalid API Key...","type":"invalid_request_error"}}`

None match the flat `{"error":"unauthorized"}` pattern. Gemini's claim holds for all
current providers in the Subumbra registry.

**Resolution:**

Both Codex and Gemini are partially right:
- Gemini is correct that body inspection is **reliable today** — no current provider uses
  `{"error":"unauthorized"}` as a 401 body
- Codex is correct that it is **brittle as the sole long-term mechanism** — a future provider
  or a future Worker change could collide with this pattern

But this is not a dispute requiring one to win. Change D (body inspection) and Change E
(`/auth-ping` probe) address different moments:
- **Change E** is proactive: detects stale token before any live request fails
- **Change D** is reactive: classifies a failure when it happens on a live request

Together they are sound. The approved plan should frame them as complementary, with Change E
as the primary stale-token detection mechanism and Change D as per-request classification.

The approved plan should also include: "if `{"error":"unauthorized"}` is detected on a
Worker response, emit `reason_code: worker_auth_failure`; if the Worker format changes in
the future, this classification will degrade gracefully (the reason_code will not appear
rather than misclassifying a provider error)."

---

### S4 — UI probes Worker directly; will miss `worker_auth` state unless updated

`ui/app.py:29`: `WORKER_URL = os.environ.get("CF_WORKER_URL", "")` — the UI has the raw
Worker URL but no Subumbra token.

`ui/app.py:60-62`: `_worker_http` has no `X-Subumbra-Token` header.

`ui/app.py:107`: `_worker_http.get(f"{WORKER_URL}{path}")` — probes the Worker with no auth.

`ui/app.py:154`: `worker_data, worker_err = _worker_get("/health")` — calls unauthenticated
`GET /health`, which the Worker accepts without auth (`worker.js:412`). This returns
`{"status":"ok"}` regardless of whether the sidecar's token is valid.

**Gemini's recommendation is correct:** If the sidecar `/health` returns
`worker_auth: "stale"`, but the UI shows `worker_reachable: true` (from its own unauthenticated
probe), the operator gets a contradictory picture. The UI should fetch the sidecar `/health`
endpoint instead of probing the Worker directly, giving it the same aggregated signal.

**Implementation note:** The UI is on the `internal` network (`docker-compose.yml:126`). The
sidecar is also on `internal` (`docker-compose.yml:170`). The sidecar is reachable from the
UI container as `http://subumbra-proxy:8090` on the internal network — no new network
permissions needed.

The UI would change from:
```python
worker_data, worker_err = _worker_get("/health")   # direct CF Worker
```
to:
```python
proxy_health, proxy_err = _proxy_get("/health")    # sidecar, which probes Worker
```
This is a small change to `ui/app.py` and should be added to the approved plan's optional
extension for Change E.

---

### S5 — Bootstrap summary text needs updating alongside bootstrap wizard changes

`bootstrap/subumbra-bootstrap.py:1828`:
```
2. ⚠  Update litellm/config.yaml with the correct subumbra:key_id values
```
This references the callback-era `subumbra:<key_id>` prefix that was removed in R42.2.
`litellm/config.yaml` now uses plain `key_id` values. This is both wrong about the contract
and references a file that is now example-only.

`bootstrap/subumbra-bootstrap.py:1834`:
```
Check subumbra-keys health: docker exec -i litellm python - <<'PY'
```
This will break after Change A removes the litellm container.

These lines are part of the bootstrap success summary printed to the operator after
every bootstrap run. They are not a doc file — they are Python code that must be updated
alongside the wizard prompt change. The approved plan should include these as explicit
change targets within the bootstrap cleanup.

---

### S6 — Full removal confirmed correct (not rename/profile-gate)

Codex F3 alternative path via response headers would work, but adds Worker scope. The
approved path (full removal + `/auth-ping`) is narrower. Codex correctly rejects Gemini's
profile-rename in favor of Claude's full removal. Confirmed with source: the `profiles:
- litellm` gate at `docker-compose.yml:81-82` doesn't prevent deliberate activation; it
only prevents accidental startup. Full removal is the correct direction.

---

### S7 — Docker healthcheck probe rate confirmed

`docker-compose.yml:185-189`:
```yaml
healthcheck:
  test: [ "CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')" ]
  interval: 10s
  timeout: 5s
  retries: 3
```

At 10s intervals, a synchronous `/auth-ping` outbound probe per Docker health call = 6
Worker requests/minute = 360/hour = 8,640/day from health polling alone, before any verify
harness or actual traffic. The CF Worker free tier is 100k requests/day. This is not an
emergency but is worth bounding in the plan. A 60-second cached result is the clean fix.

---

## Consolidated Recommendations

All three first-round reviews agree on the core direction. The secondary review adds or
refines:

| # | Priority | Recommendation |
|---|---|---|
| SR1 | Blocking | `post-bootstrap.sh` must be updated alongside bootstrap.py in Change R2 — specifically: remove `SUBUMBRA_TOKEN_LITELLM` from the required-values guard (line 43), the `.env` write (line 68), and the post-write verification loop (line 80) |
| SR2 | Required | Add `post-bootstrap.sh:20,68,74,80` and `bootstrap/subumbra-bootstrap.py:1828-1840` to the explicit change list for Change B/R2 |
| SR3 | Required | Frame Change D and Change E as complementary: E is proactive detection; D is per-request classification. Neither replaces the other. Approved plan should say: "body inspection degrades gracefully if the Worker format changes" |
| SR4 | Recommended | Add `ui/app.py` UI probe update as the optional extension of Change E — change `_worker_get("/health")` to probe the sidecar `/health` instead of the Worker directly |
| SR5 | Required | Add bootstrap summary text (`bootstrap/subumbra-bootstrap.py:1828-1840`) as explicit change target when updating the bootstrap wizard |
| SR6 | Required | Change E implementation must specify probe caching (recommended: 60s TTL) or a sub-second probe timeout to prevent Docker health instability |

---

## Summary Assessment

The three first-round reviews agree on all strategic questions. The blocking gap missed by all
three is **S1** — `post-bootstrap.sh:43` treats `SUBUMBRA_TOKEN_LITELLM` as a required value
and will fail fatally if the litellm adapter is removed from bootstrap without a coordinated
`post-bootstrap.sh` change.

The Codex/Gemini dispute on body inspection (S3) resolves to "both are right in different
time horizons" — the approved plan should use Change D and Change E together, not as
alternatives.

The approved plan should explicitly enumerate these files as change targets:
- `docker-compose.yml` (Change A)
- `docs/subumbra-install.md`, `README.md`, `docs/subumbra-testing.md`, `.env.example` (Change B)
- `docs/standalone-litellm.md` new file (Change C)
- `subumbra-proxy/app.py` — body inspection + `/health` probe (Changes D, E)
- `worker/src/worker.js` — `GET /auth-ping` endpoint (Change E)
- `bootstrap/subumbra-bootstrap.py` — wizard, summary, adapter scope (Change R2)
- `post-bootstrap.sh` — remove LITELLM required-values dependency (Change R2)
- `ui/app.py` — update Worker probe to use sidecar (optional extension of Change E)
