# Claude Verification — Round 42.3: App-Owned Integrations

**Verdict: PASS**

run_id: claude-20260419T234649  
timestamp: 2026-04-19T23:46:49+0000  
artifacts: `council/round-42-3-app-owned-integrations/runs/claude-20260419T234649/`

---

## Proof Summary

| Check | Description | Result |
|-------|-------------|--------|
| r42-3-1 | Bundled LiteLLM absent from core compose stack | PASS |
| r42-3-2 | Bootstrap/post-bootstrap no LITELLM token sync | PASS |
| r42-3-3 | Proxy `/health` exposes `worker_auth` state | PASS |
| r42-3-4 | UI `/api/status` reads proxy-owned `worker_auth` | PASS |
| r42-3-5 | Standalone LiteLLM at `/opt/litellm` uses sidecar contract | PASS |
| r42-3-6 | All operator doc truth checks pass | PASS |
| **overall** | | **PASS** |

---

## Static Source Verification

### Change A — Remove bundled LiteLLM from docker-compose.yml

`docker compose config --services` returns exactly three services:
```
subumbra-keys
subumbra-proxy
subumbra-ui
```
`litellm` is absent. The `litellm/` source directory is preserved as a legacy
reference per the approved plan ("No removal of LiteLLM as an example
integration entirely").

### Change B — Bootstrap and post-bootstrap cleanup

`bootstrap/subumbra-bootstrap.py`:
- `ADAPTER_SCOPE_VARS` has no `litellm` key.
- `BUILTIN_TOKEN_SUFFIXES = {"PROXY", "UI", "PROBE"}` — `LITELLM` absent.

`post-bootstrap.sh`:
- No reads of `SUBUMBRA_TOKEN_LITELLM` or `LITELLM_ALLOWED_KEYS`.
- Required-values guard at line 38 does not include `LITELLM`.
- No write or verify of LiteLLM token values.
- The r42-3-2 proof confirms: "No bundled LiteLLM token references found in
  bootstrap/subumbra-bootstrap.py or post-bootstrap.sh".

`.env.example`:
- `LITELLM_MASTER_KEY`, `FORGE_TOKEN_LITELLM`, `LITELLM_ALLOWED_KEYS` removed.
- Comment added: "App-owned integrations such as standalone LiteLLM keep their
  own .env files outside this repo."

### Change C — Worker `/auth-ping` endpoint

`worker/src/worker.js`:
- `GET /auth-ping` dispatches to `handleAuthPing()`.
- `handleAuthPing()` calls `authorizeRequest()` (same shared helper used by
  `/proxy`) and returns `200` or `401 {"error":"unauthorized"}`.
- No KV access, no subumbra-keys calls, no audit entries in this path.

### Change D — Proxy request-time stale-token classification

`subumbra-proxy/app.py`:
- For Worker responses ≥400: calls `aread()` to buffer the full body before
  classification (correct; cannot both stream and inspect).
- When `status == 401` and body == `b'{"error":"unauthorized"}'`: returns
  `JSONResponse(status_code=401, content={"error": "worker auth failure",
  "reason_code": "worker_auth_failure"})`.
- All other error responses pass through unchanged.

### Change E — Proxy health Worker-auth probe with TTL cache

`subumbra-proxy/app.py`:
- `get_worker_auth_status()` calls `CF_WORKER_URL/auth-ping` with
  `WORKER_AUTH_TIMEOUT_SECONDS = 2.0`.
- A positive result is cached for `WORKER_AUTH_OK_TTL_SECONDS = 60` seconds
  via `_worker_auth_ok_until` monotonic guard, preventing Docker healthcheck
  saturation.
- Tri-state: `"ok"` (200), `"stale"` (401 + exact unauthorized body), or
  `"unreachable"` (network error or unexpected status).
- `/health` returns `{"status": "ok", "worker_auth": <tri-state>}`.

Live proof:
```
{"status":"ok","worker_auth":"ok"}
```

### Change F — UI reads proxy-owned worker_auth signal

`ui/app.py`:
- `_proxy_get()` at line 102 probes `SUBUMBRA_PROXY_URL/health`.
- `api_status()` at line 151 reads `worker_auth` from the proxy health
  response.
- No direct Worker calls in the UI status path.

Live proof from `/api/status`:
```json
"worker_auth": "ok",
"worker_reachable": true
```

### Change G — Operator doc truth alignment

r42-3-6 confirms all doc checks pass:

| File | Required strings | Forbidden strings |
|------|-----------------|-------------------|
| `README.md` | app-owned installs, subumbra-proxy, docs/standalone-litellm.md | — |
| `docs/subumbra-install.md` | /opt/litellm, "LiteLLM is no longer part of the core stack", api_key: \<key_id\> | — |
| `docs/subumbra-testing.md` | /opt/litellm, worker_auth, "Standalone LiteLLM lives outside /opt/subumbra" | — |
| `docs/adapter-contract.md` | shared subumbra-proxy identity, App-Owned Integrations | "Adapter #1" |
| `docs/standalone-litellm.md` | http://subumbra-proxy:8090/t, shared subumbra-proxy identity, do not use `subumbra:<key_id>` | — |

All required strings present. `Adapter #1` forbidden string absent from
`docs/adapter-contract.md`.

---

## Standalone LiteLLM Live Proof

The `/opt/litellm` install on the VPS demonstrates the full app-owned contract:
- 16 model entries, all using `api_base: http://subumbra-proxy:8090/t`
- No legacy `subumbra:<key_id>` auth references
- Live `claude-sonnet-4` completion via the sidecar path:
  ```
  POST /t → subumbra-proxy → CF Worker → Anthropic
  response: {"content":"test only.",...}
  http_status: 200
  x-litellm-model-api-base: http://subumbra-proxy:8090/t
  ```

---

## Deviations from Approved Plan

None. All seven changes (A–G) are implemented as specified. The approved plan
exclusions are also respected:
- No per-app Worker identity model introduced.
- No custom-app bootstrap wizard.
- `subumbra-proxy` service name unchanged.

---

## Codex Cross-Check

Codex run `codex-20260419T233303` (independent, same stack):
- All 6 round checks: PASS
- overall: PASS
- Both runs agree on all proof values.
