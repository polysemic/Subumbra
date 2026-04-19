# Round 42.3 Proposal — App-Owned Integrations

Author: Claude
Date: 2026-04-19
Revision: 2 (updated after operator feedback — LiteLLM full removal + Worker auth observability)

---

## 1. Evidence

### 1.1 Bundled LiteLLM is profile-gated but was used as the real test target

`docker-compose.yml:81`: `profiles: - litellm` — LiteLLM is not started by default. However,
the bundled service was the one used for all verification testing because the standalone
`/opt/litellm` install was blocked by it running in the Subumbra compose stack. The profile
gate prevented an accidental start but not a deliberate one. Keeping the service in
`docker-compose.yml` makes it too easy for future rounds to re-use it as the "easier" test
path, re-centering LiteLLM in Subumbra's validation surface.

### 1.2 Round 42.2 sidecar contract is the app-owned contract

`litellm/config.yaml` (post-42.2): every active model uses:
```yaml
api_base: http://subumbra-proxy:8090/t
api_key: <key_id>   # plain key_id, no subumbra: prefix
```
No `callbacks:` stanza. No `custom_callbacks.py`. No Subumbra-specific env vars in the app.
This contract works for ANY OpenAI-compatible app that can reach `subumbra-proxy:8090`.
LiteLLM is just the first concrete example, not a special case.

### 1.3 Round 41.7 approved plan uses the obsolete callback contract

`council/approved/standalone-litellm-runtime-fix.md:28-34,143-169` — 41.7 mounts
`custom_callbacks.py`, uses `api_key: "subumbra:<key_id>"`, and requires `SUBUMBRA_ACCESS_TOKEN`,
`SUBUMBRA_HMAC_KEY`, `SUBUMBRA_KEYS_URL`, `CF_WORKER_URL` in the app environment. Round 42.2
removed this path from the bundled LiteLLM. Round 41.7 was never verified or implemented
(`council/COUNCIL.md:188` — status "Open"). It describes a contract that no longer exists in
the product. It must be superseded, not implemented.

### 1.4 subumbra-proxy is already the correct app-facing API

`docker-compose.yml:163-188`:
- Bound to `127.0.0.1:8090` on the host — reachable from any VPS process
- On `subumbra-net` (`docker-compose.yml:172`) — joinable by external compose stacks
- Already provides `/t/{path}` transparent route (R42.2)
- Already health-checked (`docker-compose.yml:186-188`)

An external app using `api_base: http://subumbra-proxy:8090/t` requires zero Subumbra-specific
code or credentials in the app.

### 1.5 Install docs describe LiteLLM as a default stack component (drift)

`docs/subumbra-install.md:191`: "Expected services: `subumbra-keys` (healthy), `subumbra-proxy`
(healthy), `subumbra-ui`, `litellm`." After Round 42.2 profile-gating, `litellm` does not
appear in a bare `docker compose up -d`. The step 9 health check at
`docs/subumbra-install.md:209` uses `docker exec litellm` — this requires the LiteLLM container,
which is now opt-in. The install guide has drifted from the actual stack.

### 1.6 CF Worker log confirms the exact Worker-auth failure mode

Operator-provided CF Worker log from the Round 42.2 verification window (2026-04-19 13:30 PDT):

```
GET  /health → 200 OK         (Worker up, no auth required)
POST /proxy  → 401            (x-subumbra-token present but rejected)
  warn: "subumbra: unauthorized request from 62.146.169.39"
POST /proxy  → 401            (same, second attempt)
```

`worker.js:449-455` — the exact code path triggered:
```javascript
const incomingToken = request.headers.get("X-Subumbra-Token") ?? "";
const tokenOk = await tokenSetContains(incomingToken, validTokens);
if (!tokenOk) {
    console.warn("subumbra: unauthorized request from", request.headers.get("CF-Connecting-IP"));
    return jsonError("unauthorized", 401);
}
```

The Worker received the token, checked it against `SUBUMBRA_ADAPTER_TOKENS`, found no match,
and returned `{"error":"unauthorized"}` with 401. `/health` returning 200 in the same window
confirms the Worker was live and configured — only the token was wrong.

**What the verify harness sees:** a 401 from the sidecar. Indistinguishable from:
- Provider returning 401 for a bad API key
- CF Worker returning 401 because HMAC or fingerprint validation failed downstream

**What the CF logs show:** `"subumbra: unauthorized request"` — clear, actionable, but only
visible to operators with CF dashboard access. The verify harness never captures this.

### 1.7 Three distinct stale-caller sources, only one currently detectable

1. **`SUBUMBRA_TOKEN_PROXY`** — changes on re-bootstrap. `post-bootstrap.sh` drift check
   covers this (`post-bootstrap.sh:90-107`). The stale token reaches the Worker as
   `X-Subumbra-Token`, which the Worker rejects.

2. **CF Access credentials (`CF_ACCESS_CLIENT_ID`/`CF_ACCESS_CLIENT_SECRET`)** — managed by
   Cloudflare. Can expire or be rotated in the CF dashboard with no local change. This causes
   the request to be blocked *before* reaching the Worker's own auth logic (at the CF Access
   layer). Invisible from the Subumbra side until a request fails.

3. **`SUBUMBRA_ADAPTER_TOKENS` in CF Secrets** — set by bootstrap. If re-bootstrap regenerates
   these and redeploys the Worker with new token values, but `subumbra-proxy` is not recreated,
   the old token in the sidecar's env is stale. This is what produced the CF log above.

Source (3) is the confirmed cause of the Round 42.2 verification 401s. Source (1) is covered.
Sources (2) and (3) are silent until a live request fails.

---

## 2. Current vs Desired

### Current state

| Layer | Behavior |
|---|---|
| Bundled LiteLLM in compose | Profile-gated; was still used as verification target |
| `litellm/` directory | Present with `config.yaml`, `custom_callbacks.py` (legacy), `custom_callbacks.py` (legacy marker) |
| Standalone LiteLLM contract | Round 41.7 approved but obsolete (callback-based) |
| Install docs | Describe LiteLLM as expected default service |
| App-owned contract | Works via sidecar (42.2) but undocumented as the normative path |
| Worker 401 | Indistinguishable from provider 401 on the Subumbra side; visible only in CF dashboard |
| Stale token detection | Source (1) covered; sources (2) and (3) invisible until failure |

### Desired state

| Layer | Desired |
|---|---|
| Bundled LiteLLM in compose | **Removed from `docker-compose.yml`** — not profile-gated, gone |
| `litellm/` directory | Retained as reference/example (config, legacy callback) — no service |
| Standalone LiteLLM contract | Documented using sidecar contract (no callback, no app-side auth material) |
| Install docs | Describe core stack only (`subumbra-keys`, `subumbra-proxy`, `subumbra-ui`) |
| App-owned contract | Normative documented path: `api_base` + plain `key_id` + network requirements |
| Worker 401 | subumbra-proxy returns a reason_code distinguishing Worker-auth 401 from provider 401 |
| Stale token detection | Operator-accessible check that confirms proxy-to-Worker auth is live without CF dashboard |

---

## 3. Proposal

### Change A — Remove bundled LiteLLM service from `docker-compose.yml`

**Scope:** `docker-compose.yml` — delete the `litellm:` service block entirely (lines 73-106).

The service block, its profile gate, its volume mounts, its `depends_on`, and its port
declaration are all removed. The `litellm/` directory (`config.yaml`, `custom_callbacks.py`)
stays as a reference for operators who want to stand up their own LiteLLM — it becomes example
material, not a running service.

This removes the escape hatch that allowed testing against the bundled service instead of the
real standalone target. It forces future verification against an app that actually lives outside
the Subumbra compose stack.

**What changes:** `docker-compose.yml` loses the `litellm:` service. No other files change.

**What stays:** `litellm/config.yaml` (example config showing sidecar contract),
`litellm/custom_callbacks.py` (legacy reference, marked as such from R42.2).

**Impact on install docs:** step 8 "Expected services" must be updated (Change B).

### Change B — Update install docs to match the real stack

**Scope:** `docs/subumbra-install.md` steps 7–9.

- Step 8 (`docs/subumbra-install.md:191`): Remove `litellm` from "Expected services". Core
  stack is `subumbra-keys`, `subumbra-proxy`, `subumbra-ui`.
- Step 9 (`docs/subumbra-install.md:209`): Replace `docker exec litellm python3 -c ...`
  (requires litellm container) with a direct host-side check using the `127.0.0.1:9090`
  path if needed, or remove that check entirely since subumbra-keys health is now served
  via subumbra-ui's `/api/status`.
- Add a brief "Standalone App Integration" section pointing to `docs/standalone-litellm.md`
  (created in Change C) as the reference example for connecting external apps.

### Change C — Create `docs/standalone-litellm.md` (supersedes Round 41.7)

**Scope:** new file `docs/standalone-litellm.md` + close Round 41.7 as superseded.

The normative standalone integration contract:

```yaml
# /opt/litellm/docker-compose.yml
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-latest@sha256:7c311546...
    networks:
      - subumbra-net      # joins subumbra's shared network
networks:
  subumbra-net:
    external: true
    name: subumbra-net
```

```yaml
# /opt/litellm/config.yaml (per-model entry)
litellm_params:
  api_base: http://subumbra-proxy:8090/t
  api_key: <key_id>       # plain key_id; must match bootstrap key_id in PROXY_ALLOWED_KEYS
```

**No** `custom_callbacks.py`. **No** `SUBUMBRA_ACCESS_TOKEN`. **No** `SUBUMBRA_HMAC_KEY`.
**No** `CF_WORKER_URL`. The app is auth-free; subumbra-proxy owns all authentication.

**Host-binding alternative** for apps that cannot join `subumbra-net`:
```yaml
api_base: http://127.0.0.1:8090/t
```
Works from any process bound to the VPS (subumbra-proxy is on `127.0.0.1:8090` per
`docker-compose.yml:174`).

**Key scope requirement:** key_ids used in `api_key` must appear in the `subumbra-proxy`
adapter's `allowed_keys` list. Mismatch produces `403 key_scope_denied`. Bootstrap step 3
prompts for this; the bootstrap summary prints the scoped list.

Round 41.7 (`council/approved/standalone-litellm-runtime-fix.md`) is superseded by this
document. Close 41.7 as superseded in `council/COUNCIL.md`.

### Change D — subumbra-proxy: distinguish Worker auth 401 from provider 401

**Scope:** `subumbra-proxy/app.py` — Worker error response handling.

**Root cause (from CF logs):** When the Worker's `tokenSetContains` check fails
(`worker.js:450-455`), it returns:
```json
{"error": "unauthorized"}
```
with HTTP 401. This is the same status code providers return for bad API keys. The sidecar
currently passes this 401 back upstream without any differentiation.

**Minimum change:** When subumbra-proxy receives a 4xx from the Worker (`CF_WORKER_URL/proxy`
or `/t/` path), inspect the response body:
- If body contains `{"error": "unauthorized"}` → this is a Worker-auth rejection. The sidecar
  returns `reason_code: worker_auth_failure` to the caller (in addition to the status code).
- All other 4xx/5xx → pass through or classify as `provider_error` / `worker_error`.

This is body inspection only. No credentials are logged. No token values are exposed. The
response to the caller gains a `reason_code` field that operators can read from LiteLLM error
logs or `curl` output.

**Explicitly NOT in scope:** scraping CF Worker logs, calling CF Logs API, exposing CF Access
credentials in any response or log.

### Change E — subumbra-proxy `/health` endpoint: Worker auth probe

**Scope:** `subumbra-proxy/app.py` `/health` endpoint.

**Observation from CF logs:** When Worker token is stale, `GET /health` returns 200 (no auth)
but `POST /proxy` returns 401. This combination is diagnosable: Worker is alive, token is wrong.
Neither the verify harness nor an operator running `curl 127.0.0.1:8090/health` currently
surfaces this.

**Change:** Extend the `/health` response with two fields:
- `worker_reachable: true/false` — can the sidecar reach `CF_WORKER_URL/health` (GET, no auth)?
- `worker_auth_ok: true/false` — does the sidecar's current token pass a lightweight auth check?

**Worker auth check implementation:** Rather than a full `/proxy` POST, this can use a
dedicated `GET /auth-ping` endpoint on the Worker (requires a one-line Worker change) that
requires `X-Subumbra-Token` and returns 200 or 401. Alternatively, if we do not want to add a
Worker endpoint this round, the sidecar can attempt a `POST /proxy` with a minimal payload and
classify the result.

If a Worker-side endpoint is out of scope for this round, a fallback is acceptable: the sidecar
health response reports `worker_reachable: true/false` only (based on `/health` GET), and
`worker_auth_ok` is deferred to the Worker endpoint round. `worker_reachable: false` is already
actionable; `worker_auth_ok: false` is the new signal that covers the stale-token case.

**Constraint:** No token values, CF Access credentials, or sensitive material in the health
response. The fields are boolean only.

### Change F — verify.sh: add Worker auth health probe as a round hook

**Scope:** `scripts/council/verify.sh` round hook for `round-42-3`.

A round-hook check that calls `http://127.0.0.1:8090/health` and asserts:
- `worker_reachable: true` (Worker is reachable from sidecar)
- `worker_auth_ok: true` (if the field exists after Change E)

This surfaces the exact failure that caused silent 401s during Round 42.2 verification, without
requiring any CF API credentials or dashboard access. A single `curl` + JSON parse in the hook.

---

## 4. Failure Modes

| Failure | Signal after this round |
|---|---|
| Standalone app uses wrong `api_key` format (old `subumbra:` prefix) | subumbra-proxy: `{"detail":"invalid key_id"}` (already exists, R42.2) |
| key_id not in PROXY_ALLOWED_KEYS scope | subumbra-proxy: 403 `key_scope_denied` (already exists) |
| App not on `subumbra-net` or `127.0.0.1:8090` not used | Connection refused — immediate network error |
| SUBUMBRA_TOKEN_PROXY stale after re-bootstrap (source 1) | `post-bootstrap.sh` drift detection already covers this |
| `SUBUMBRA_ADAPTER_TOKENS` stale after Worker redeploy (source 3) | `worker_auth_ok: false` in sidecar `/health` + `reason_code: worker_auth_failure` on live request |
| CF Access token expired (source 2) | `worker_reachable: false` in sidecar `/health` (Access blocks before Worker) |
| Operator accidentally starts bundled LiteLLM (profile-gated) | After Change A: service removed; no such accident possible |
| Operator's standalone LiteLLM has Gemini model | Returns 404 from Anthropic/Google — known exclusion from R42.2; documented in exclusions |

---

## 5. Exclusions

| Item | Reason |
|---|---|
| Gemini routing fix | Separate path issue; deferred from R42.2 |
| OpenWebUI, N8N adapter specifics | Future adapter rounds; same sidecar contract applies but their config surfaces differ |
| `custom_callbacks.py` code changes | Legacy reference; leave as-is; no runtime use after bundled LiteLLM removal |
| CF Access token rotation automation | Requires CF API scope beyond current bootstrap permissions |
| CF Logs API integration in verify harness | Requires CF API token with logs:read — appropriate for a future harness round |
| `post-bootstrap.sh` standalone app awareness | Must remain independent of external app paths |
| Broad observability expansion | Out of scope per kickoff.md and project security invariants |
| Token or cryptographic architecture redesign | Out of scope |
| P9.1/P9.2 harness architectural redesign | Open harness maintenance item from R42.2 cleanup |
| `litellm/` directory removal | Keep as reference/example material even after compose service removal |

---

## 6. Open Questions

**Q1: Should the Worker receive a new `GET /auth-ping` endpoint for Change E?**
Adding a lightweight auth-check endpoint to `worker.js` is a small change (one route, returns
200 or 401 based on `X-Subumbra-Token`). It enables `worker_auth_ok` in the sidecar health
response without a full proxy round-trip. The alternative (POST `/proxy` with minimal payload)
is heavier and may produce confusing audit entries. Recommendation: add `GET /auth-ping` in
this round; it is one-function scope.

**Q2: After removing the bundled LiteLLM service, should `litellm/config.yaml` stay as-is or
be annotated as "example only"?**
Adding a header comment noting it's a reference example (not a running service config) costs
nothing and prevents future confusion. Recommendation: add a one-line comment at the top;
no other changes.

**Q3: Should Round 41.7 be closed as "superseded" or should a follow-up verification be run
to officially close it?**
41.7 was approved but never implemented or verified. The contract it describes is now obsolete.
Treating it as "superseded" in COUNCIL.md is accurate and sufficient. No verification run needed
for a plan that describes a removed feature.

**Q4: Is the proof target for this round a standalone LiteLLM, or can it be any app using the
sidecar contract?**
Standalone LiteLLM (`/opt/litellm/`) is the most immediate concrete example and requires the
least new infrastructure. The proof should exercise: (a) standalone app starts with no Subumbra
env vars, (b) request flows through sidecar to CF Worker to provider, (c) `/health` on sidecar
shows `worker_reachable: true` and `worker_auth_ok: true`. This is a V-series static check +
a single live-request proof — does not require a full P9-series harness run.

**Q5: The CF Worker logs show `"x-subumbra-token"` in the stripped-headers list
(`worker.js:116`). After the request, is the token value ever accessible to the sidecar
through any response path?**
No. The Worker strips `x-subumbra-token` from forwarded requests (`worker.js:116`) and does not
include it in error responses. The only way to know "was the token correct" from outside the
Worker is the response status code and body. Change D addresses this by interpreting
`{"error":"unauthorized"}` as a Worker-auth signal; no token value is ever exposed.
