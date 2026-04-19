# Round 42.3 Review — App-Owned Integrations

Author: Claude
Date: 2026-04-19
Round: `round-42-3-app-owned-integrations`

Proposals reviewed:
- `council/round-42-3-app-owned-integrations/claude-proposal.md` (v1)
- `council/round-42-3-app-owned-integrations/claude-proposal-2.md` (v2/alignment)
- `council/round-42-3-app-owned-integrations/gemini-proposal.md`
- `council/round-42-3-app-owned-integrations/codex-proposal-2.md`

---

## Findings Summary

| # | Severity | Finding | Proposal | Files |
|---|---|---|---|---|
| F1 | Confirmed | Change A evidence is accurate — service block lines match | All | `docker-compose.yml:73-106` |
| F2 | Confirmed | Change B evidence is accurate — install doc LiteLLM drift confirmed | All | `docs/subumbra-install.md:191,209-219` |
| F3 | Gap | Change B scope understates drift: `.env.example`, `README.md`, `docs/subumbra-testing.md` also reference LiteLLM/LITELLM_MASTER_KEY but are not listed as Change B targets | All | `.env.example:6,20,40,46`, `README.md:248-339`, `docs/subumbra-testing.md:16,32-65` |
| F4 | Gap | Bootstrap `litellm` adapter scope is not addressed: wizard still prompts for LiteLLM scope (line 1074), bootstrap still generates `SUBUMBRA_TOKEN_LITELLM` (line 1745) and `LITELLM_ALLOWED_KEYS` (line 1741) — these become orphaned after Change A | All | `bootstrap/subumbra-bootstrap.py:1074,1741,1745` |
| F5 | Confirmed | Change D gap confirmed — `proxy_via_worker` passes Worker response through as stream without body inspection | All | `subumbra-proxy/app.py:229,242-247` |
| F6 | Risk | Change D implementation constraint: Worker error responses are currently returned via `worker_resp.aiter_raw()` streaming path; body inspection requires buffering 4xx responses before streaming, which is not the current pattern | Claude/Codex | `subumbra-proxy/app.py:227,242-247` |
| F7 | Confirmed | Change E gap confirmed — `/health` returns bare `{"status": "ok"}` with no Worker probe | All | `subumbra-proxy/app.py:250-252` |
| F8 | Risk | Change E health probe blocks sidecar health on outbound network call — Docker's healthcheck polls every 10s; a CF network blip would cause sidecar to report unhealthy and potentially trigger restarts | All | `docker-compose.yml:185-189` |
| F9 | Confirmed | Worker has no `/auth-ping` endpoint — only `GET /health` and `POST /proxy` exist | All | `worker/src/worker.js:408-427` |
| F10 | Confirmed | `/auth-ping` safety: token is 32-byte hex (256-bit entropy); responding 200/401 to a presented token does not introduce viable brute-force attack surface | All | `bootstrap/subumbra-bootstrap.py:1664` |
| F11 | Minor | `.env.example:40` has `FORGE_TOKEN_LITELLM=` (pre-rebrand name) while bootstrap generates `SUBUMBRA_TOKEN_LITELLM` — naming inconsistency pre-dates this round but should be cleaned | — | `.env.example:40` |
| F12 | Minor | Bootstrap wizard step 3 intro text already updated per R42.2 (line 1063-1064 describes proxy as default since R42.2) but the prompt at line 1074 still names "LiteLLM" as the first scope; combined with Change A removal, this misleads operators about a service that no longer exists | — | `bootstrap/subumbra-bootstrap.py:1060-1078` |

---

## Detailed Analysis

### F1–F2: Evidence claims verified

**Change A** (`docker-compose.yml:73-106`) is exactly the block described in all proposals.
The `profiles: - litellm` gate is at line 82. The service has four other components that
must also be removed: port binding (line 88), volumes including `providers.json` (lines 90-92),
`LITELLM_MASTER_KEY` env (line 99), and `depends_on: subumbra-keys + subumbra-proxy` (lines
102-106). The proposal correctly scopes this as a full block deletion.

**Change B** `docs/subumbra-install.md:191` reads:
```
Expected services: `subumbra-keys` (healthy), `subumbra-proxy` (healthy),
`subumbra-ui`, `litellm`.
```
`docs/subumbra-install.md:209-219` uses `docker exec litellm` for the subumbra-keys
health check, `LITELLM_MASTER_KEY` for LiteLLM health check, and
`curl http://127.0.0.1:4000/health` — all of which require the LiteLLM container. These
must change; the proposals correctly identify this.

---

### F3: `.env.example`, `README.md`, `docs/subumbra-testing.md` drift (GAP)

None of the proposals include these files in Change B scope. After Change A removes the
bundled LiteLLM service, the following references become confusing or actively misleading:

**`.env.example`:**
- Line 6: "Edit LITELLM_MASTER_KEY" in the usage comment
- Line 20: `LITELLM_MASTER_KEY=change-me-use-openssl-rand-hex-32` (operator sets this pre-bootstrap)
- Line 40: `FORGE_TOKEN_LITELLM=` (generated section — post-bootstrap value)
- Line 46: `LITELLM_ALLOWED_KEYS=` (generated section — post-bootstrap value)

If the bundled LiteLLM service is removed but `LITELLM_MASTER_KEY` remains in `.env.example`,
new operators will set it and wonder what it's for. If `LITELLM_ALLOWED_KEYS` and
`FORGE_TOKEN_LITELLM` remain in the generated section, they'll appear in every bootstrap
output and confuse operators about what services hold them.

**`docs/subumbra-testing.md:16`:**
```bash
export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' .env)"
```
Line 32-33 uses `docker exec litellm` as the only way to health-check subumbra-keys (internal
network restriction). Line 41-65 describes LiteLLM as the functional test surface. These
become broken instructions after Change A.

**`README.md:248-339`:** Multiple sections reference `LITELLM_MASTER_KEY` and LiteLLM
health/functional test commands as the primary quickstart path.

**Recommendation:** Change B must explicitly list `.env.example`, `README.md`, and
`docs/subumbra-testing.md` alongside `docs/subumbra-install.md`. The subumbra-keys internal
health check (currently `docker exec litellm`) needs a replacement path — the UI's
`/api/status` endpoint or direct `docker exec subumbra-keys` is the alternative.

---

### F4: Bootstrap `litellm` adapter scope — orphaned configuration (GAP)

All proposals note the bootstrap wizard is still LiteLLM-shaped, but none specify what
happens to the `litellm` adapter entries in bootstrap after Change A.

**Current state:**

`bootstrap/subumbra-bootstrap.py:104-108`:
```python
ADAPTER_SCOPE_VARS: dict[str, str] = {
    "litellm": "LITELLM_ALLOWED_KEYS",
    "subumbra-proxy": "PROXY_ALLOWED_KEYS",
    ...
}
```

`bootstrap/subumbra-bootstrap.py:1074`: Interactive wizard prompts for "LiteLLM" key scope.

`bootstrap/subumbra-bootstrap.py:1741,1745`: Bootstrap outputs:
```
LITELLM_ALLOWED_KEYS=...
SUBUMBRA_TOKEN_LITELLM=...
```

These are written to `.env` by `post-bootstrap.sh`. After Change A removes the bundled
LiteLLM service, `SUBUMBRA_TOKEN_LITELLM` is generated but never consumed by any service in
the stack. The `LITELLM_ALLOWED_KEYS` variable is similarly orphaned.

**The identity concern (from Codex §1.3):** The wizard at line 1063-1064 already describes
`subumbra-proxy` as the default since R42.2. But line 1074 still names "LiteLLM" as the
first adapter scope and prompts operators to define its allowed keys. An operator who has just
removed the bundled LiteLLM service will be confused by this prompt.

**The correct 42.3 answer:** The approved plan should explicitly state whether to:
(a) remove the `litellm` adapter from `ADAPTER_SCOPE_VARS` and the interactive wizard, or
(b) retain it as a disabled/optional legacy entry with a note that it applies only to
operators who still run a standalone LiteLLM via the old callback path (not the sidecar path)

Given that the callback path is now legacy and 41.7 is being superseded, option (a) is cleaner.
None of the proposals call this out as an explicit change target.

---

### F5: Change D confirmed at `subumbra-proxy/app.py:229,242-247`

Current code at `subumbra-proxy/app.py:229`:
```python
if worker_resp.status_code >= 400:
    LOG.warning("worker failure key_id=%s status=%s", key_id, worker_resp.status_code)
```
The warning is emitted, but lines 242-247 stream the response through unchanged:
```python
return StreamingResponse(
    worker_resp.aiter_raw(),
    status_code=worker_resp.status_code,
    ...
)
```

The Worker's `{"error":"unauthorized"}` body at `worker.js:454` is currently passed through
opaquely. Change D's body inspection is the correct fix.

---

### F6: Change D implementation constraint — streaming vs buffering (RISK)

The current `proxy_via_worker` path sends the request and immediately starts streaming the
response via `worker_resp.aiter_raw()`. This works correctly for large streaming completions
but creates a constraint for Change D:

**To inspect the body for `{"error":"unauthorized"}`, the implementation must:**
1. Check `worker_resp.status_code >= 400` — already done
2. Buffer the response body — requires `await worker_resp.aread()` instead of `aiter_raw()`
3. Parse JSON and check for the `"unauthorized"` error value
4. Return the enriched error response

**The constraint:** `aread()` and `aiter_raw()` are mutually exclusive on the same
`httpx.AsyncClient` streaming response. The fix must branch at status code: for 4xx/5xx,
buffer and inspect; for 2xx/3xx, keep streaming. This is a real but simple implementation
change — approximately 10-15 lines to add around `subumbra-proxy/app.py:229`.

The proposals describe the desired behavior correctly but do not mention this buffering
requirement. The approved plan should name it explicitly so the implementer doesn't reach for
`aiter_raw()` for error responses.

**No security concern:** Buffering error responses (typically `{"error":"..."}` — small JSON)
does not expose sensitive material. The Worker never includes token values in error responses
(`worker.js:454`: `return jsonError("unauthorized", 401)` — no credential material).

---

### F7–F8: Change E — confirmed gap, but probe design needs a constraint (RISK)

**F7 confirmed:** `subumbra-proxy/app.py:250-252`:
```python
@app.get("/health")
async def health():
    return {"status": "ok"}
```
No Worker contact whatsoever.

**F8 — probing risk:**

Docker's healthcheck at `docker-compose.yml:185-189` polls `GET /health` every 10 seconds.
If Change E makes `/health` synchronously probe `GET /auth-ping` on the CF Worker, every
Docker health poll triggers an outbound network call to `workers.dev`. This creates two risks:

1. **CF Worker probe rate:** 6 probes/minute × 24 hours = 8,640 outbound `GET /auth-ping`
   requests per day just from Docker health polling. The CF Worker free tier is 100k
   requests/day — this consumes ~8.6% of the daily budget for health traffic alone.

2. **Sidecar health instability:** If the CF Worker is temporarily unreachable (network blip,
   CF incident, DNS delay), the synchronous probe will block the `/health` response. Docker
   waits `timeout: 5s` before marking unhealthy. After `retries: 3` failed health checks,
   Docker will restart the sidecar. A sidecar restart during normal operation is a more
   disruptive failure mode than the stale-token problem it was meant to surface.

**Resolution:** The approved plan should specify one of:
- **Cached probe (preferred):** Probe the Worker at most once per N seconds (e.g., 60s);
  `/health` returns the cached result. Most callers see a fresh enough result; Docker health
  never blocks on an outbound call.
- **Very short probe timeout (acceptable):** 1-second timeout on the `/auth-ping` call;
  timeout becomes `worker_auth: "unreachable"`. Docker health never waits more than 1s.

Without this constraint, the implementation may choose synchronous/unbounded probing, which
is a correctness risk.

---

### F9–F10: Worker `/auth-ping` — confirmed gap, security is acceptable

**F9 confirmed:** `worker.js:408-427` — the Worker's `fetch()` handler has exactly two
endpoints: `GET /health` (line 412) and `POST /proxy` (line 421). A third branch for
`GET /auth-ping` must be added.

**F10 — brute force not viable:** The `parseAdapterTokens` function at `worker.js:442`
validates tokens from `SUBUMBRA_ADAPTER_TOKENS`. Bootstrap generates tokens as
`secrets.token_hex(32)` at `bootstrap/subumbra-bootstrap.py:1664` — 64 hex chars, 256 bits
of entropy. Responding 200/401 to a presented token does not help an attacker narrow down
the value at any practical rate. The security concern is not meaningful.

**Implementation note:** `/auth-ping` should require `GET` method (not accept POST), perform
only the `tokenSetContains` check (`worker.js:450`), and return immediately without touching
KV, keys service, or any crypto operations. This keeps it as cheap as possible.

---

### F11–F12: Minor consistency issues

**F11:** `.env.example:40` has `FORGE_TOKEN_LITELLM=` (a pre-rebrand name from before the
`FORGE_TOKEN_*` → `SUBUMBRA_TOKEN_*` rename). Bootstrap outputs `SUBUMBRA_TOKEN_LITELLM`
(`subumbra-bootstrap.py:1745`). These should agree; this appears to be a pre-existing
naming artifact unrelated to 42.3 but should be cleaned in the same pass as `.env.example`.

**F12:** `bootstrap/subumbra-bootstrap.py:1063-1064` already says "Leave empty if LiteLLM
routes through subumbra-proxy (the default since Round 42.2)." This is good. But line 1074
still labels the first prompt as "LiteLLM", creating a mixed message alongside the
"LiteLLM is removed" direction of Change A. If the adapter entry is retained for backwards
compatibility, the prompt label should change.

---

## Error Handling and Logging Assessment

Round 42.3 introduces the following new failure modes that need operator-visible signals:

| Failure | Current signal | Proposed signal | Assessment |
|---|---|---|---|
| Worker token stale (`SUBUMBRA_ADAPTER_TOKENS` drift) | None from Subumbra side; CF dashboard only | `worker_auth: "stale"` in sidecar `/health` | Correct and sufficient |
| Worker unreachable (CF incident, DNS) | 502 on next request | `worker_auth: "unreachable"` in sidecar `/health` | Correct and sufficient |
| Worker auth 401 on live request | 401 to caller, no reason code | `reason_code: worker_auth_failure` in response | Correct; implementor must buffer 4xx responses |
| Standalone app uses wrong api_key format | `{"detail":"invalid key_id"}` from sidecar | Unchanged (already exists from R42.2) | No change needed |

**No secret-bearing or overly verbose logging concerns in the proposals.** The Gemini and
Claude proposals explicitly state: no token values, no CF Access credentials, boolean/enum
fields only in health responses. The Worker's existing pattern of logging only the source IP
on auth failure (`worker.js:453`) is preserved.

The `reason_code: worker_auth_failure` field on the sidecar response does not expose any
credential material — it tells the caller that the proxy-to-Worker auth failed, which is
information they already have from the 401 status code.

---

## Recommendations

### R1 — Expand Change B to cover all LiteLLM references

Change B must include `.env.example`, `README.md`, and `docs/subumbra-testing.md` alongside
`docs/subumbra-install.md`. The `docker exec litellm` subumbra-keys health check needs a
replacement (suggest `docker exec subumbra-keys python3 -c "..."` or use the UI `/api/status`
endpoint which is already accessible).

### R2 — Explicitly decide the bootstrap `litellm` adapter fate

The approved plan must state one of:
(a) Remove `litellm` from `ADAPTER_SCOPE_VARS`, the interactive wizard, and the
    `post-bootstrap.sh` output — this is the clean path given Change A removes the service
(b) Retain as deprecated/optional legacy entry with a label change

This affects `bootstrap/subumbra-bootstrap.py:105,1074,1741,1745`, `.env.example:40,46`,
and the `LITELLM_ALLOWED_KEYS` variable in `post-bootstrap.sh`.

### R3 — Change D must specify 4xx-branch buffering

The approved plan for Change D should say: "For responses with `status_code >= 400`, read the
full response body via `aread()` before inspecting and returning. For all other status codes,
continue using `aiter_raw()` streaming." This is a one-sentence implementation clarification
that prevents the implementer from leaving the streaming path in place for errors.

### R4 — Change E must specify probe frequency constraint

The approved plan for Change E must specify either:
- Cached probe result with TTL (recommended: 60s), or
- Maximum probe timeout (1-2s) so Docker health never blocks

Without this, the synchronous probe pattern risks sidecar instability during CF network blips
and generates unnecessary CF Worker request volume.

### R5 — Add `GET /auth-ping` spec to the Worker change

The approved plan should specify the Worker change precisely: `GET /auth-ping` requires
`X-Subumbra-Token` header, calls `tokenSetContains()` only, returns 200 or 401, does not
touch KV/secrets/crypto. This makes it unambiguous for implementation.

---

## Overall Assessment

The proposal set (Claude v2, Codex, Gemini) agrees on all core directions: full LiteLLM
removal, sidecar contract as the normative app path, and Worker-auth visibility via the sidecar
health endpoint. The evidence claims are all verifiable and accurate against the current source.

The most important gap is **F3/R1** (`.env.example`, `README.md`, `docs/subumbra-testing.md`
not in scope) and **F4/R2** (bootstrap LiteLLM adapter orphaned by Change A). These are
documentation cleanup items that must be part of the implementation to avoid leaving the
operator truth in a contradictory state after Change A.

The most important implementation risk is **F8/R4** (synchronous `/health` probe blocking
Docker health checks). This needs one sentence in the approved plan to prevent a correctness
problem in the Change E implementation.
