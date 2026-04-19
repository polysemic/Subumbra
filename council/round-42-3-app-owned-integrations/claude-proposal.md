# Round 42.3 Proposal — App-Owned Integrations

Author: Claude
Date: 2026-04-19

---

## 1. Evidence

### 1.1 Bundled LiteLLM is already profile-gated

`docker-compose.yml:81`: `profiles: - litellm` — the bundled LiteLLM service is NOT started
by `docker compose up -d` unless the operator explicitly passes `--profile litellm`. It is
already a non-default service. The stack's core services are `subumbra-keys`, `subumbra-proxy`,
`subumbra-ui` — LiteLLM is optional.

### 1.2 Round 42.2 already defined the sidecar contract

`litellm/config.yaml` (post-42.2): every active model uses:
```yaml
api_base: http://subumbra-proxy:8090/t
api_key: <key_id>   # plain key_id, no subumbra: prefix
```
No `callbacks:` stanza. No `custom_callbacks.py`. The bundled LiteLLM service itself now uses
this contract (`litellm/config.yaml`). This IS the app-owned contract — it is already
implemented in the bundled path.

### 1.3 Round 41.7 approved plan is superseded by 42.2

`council/approved/standalone-litellm-runtime-fix.md:28-34,143-169` — Round 41.7's fix mounts
`custom_callbacks.py`, uses `api_key: "subumbra:<key_id>"` format, and requires the app to
carry `SUBUMBRA_ACCESS_TOKEN`, `SUBUMBRA_HMAC_KEY`, `SUBUMBRA_KEYS_URL`, and `CF_WORKER_URL`.

Round 42.2 removed the callback path from the bundled LiteLLM entirely. The Round 41.7 plan
describes a contract that is now legacy. Round 41.7 was never verified (`council/COUNCIL.md:188`
shows it "Open" — implemented scope is missing). Round 42.3 should supersede 41.7 with the
correct sidecar-based standalone contract.

### 1.4 subumbra-proxy is already the app-facing API

`docker-compose.yml:163-188` — `subumbra-proxy` is:
- On `subumbra-net` (external: `docker-compose.yml:17-19`) — joinable by external compose stacks
- Published on `127.0.0.1:8090` — reachable from host processes
- Already has `/t/{path}` transparent route (R42.2) and `/v1/request` explicit route (R25)
- Health-checked (`docker-compose.yml:186-188`)

An app-owned LiteLLM on `subumbra-net` can reach `http://subumbra-proxy:8090/t` today with
no changes to the Subumbra stack.

### 1.5 Install docs describe bundled LiteLLM as the default flow

`docs/subumbra-install.md:154-199` — step 7 (key_id alignment), step 8 (start the stack), and
step 9 (verify locally) all describe LiteLLM as part of the default `docker compose up -d` run.
`docs/subumbra-install.md:191`: "Expected services: `subumbra-keys` (healthy), `subumbra-proxy`
(healthy), `subumbra-ui`, `litellm`." But `litellm` is profile-gated and not present in a bare
`docker compose up -d` as of 42.2. The install doc has drifted.

### 1.6 CF Worker 401 is undifferentiated from provider 401

`council/closed/round-42-2-runtime-auth-reconciliation/claude-verification.md` (P9.1 artifact):
the 401 returned to LiteLLM says `AnthropicException - {"error":"unauthorized"}`. LiteLLM wraps
the upstream response; the caller cannot tell whether `401` originated from:
- Subumbra-proxy → CF Worker auth failure (stale CF_ACCESS credentials or Worker identity)
- CF Worker → Anthropic auth failure (bad or expired provider key)

`subumbra-proxy` health endpoint (`docker-compose.yml:186`) checks only local process readiness;
it does not probe the Worker edge.

### 1.7 Stale-caller sources

Three distinct auth states can drift independently:
1. **`SUBUMBRA_TOKEN_PROXY`** — HMAC-signed bearer token subumbra-proxy uses to call subumbra-keys.
   Changes on re-bootstrap. If subumbra-proxy container is not recreated, the old token remains
   in container env.
2. **`CF_ACCESS_CLIENT_ID`/`CF_ACCESS_CLIENT_SECRET`** — CF Access service token. Managed by
   Cloudflare; can expire or be rotated in the CF dashboard with no local change required.
3. **`CF_WORKER_URL`** — Worker URL. Changes only on Worker redeployment with a new script name.

Only source (1) is currently detectable via `post-bootstrap.sh`'s drift check. Sources (2) and
(3) are invisible from the Subumbra side until a live request fails.

---

## 2. Current vs Desired

### Current state

| Layer | Behavior |
|---|---|
| Bundled LiteLLM | Profile-gated (`--profile litellm`); uses sidecar contract (42.2); not in default stack |
| Standalone LiteLLM | Round 41.7 plan: callback-based (`subumbra:key_id`), approved but never verified, contract already obsolete |
| Install docs | Describe LiteLLM as a default stack service; step 9 health check references `docker exec litellm` |
| App-owned contract | Implicit: `api_base` + plain `key_id` works today but is undocumented as the official standalone path |
| Worker 401 | Indistinguishable from provider 401 at the caller side |
| Stale-caller detection | Token drift detectable via drift check (source 1 only); CF Access drift (source 2) silent until failure |

### Desired state

| Layer | Desired |
|---|---|
| Bundled LiteLLM | Explicitly demoted to an optional convenience service; not the validation path or install-doc primary example |
| Standalone LiteLLM | Documented as the primary example integration using sidecar contract (no callback, no subumbra-specific env vars) |
| Install docs | Describe core stack without LiteLLM; standalone integration as a separate section |
| App-owned contract | Documented normatively: `api_base`, `api_key`, network requirements, key_id scope alignment |
| Worker 401 | Subumbra-proxy returns a distinguishable error when the Worker edge returns 401/403 vs when the upstream provider does |
| Stale-caller detection | One diagnostic action that surfaces whether the proxy-to-Worker path is live without requiring Cloudflare dashboard spelunking |

---

## 3. Proposal

### Change A — Supersede Round 41.7 with the sidecar-contract standalone path

**Scope:** operator documentation and `docs/standalone-litellm.md` (create new doc).

Round 41.7 is approved but never verified and its contract is already obsolete. Close 41.7 by
documenting the correct standalone LiteLLM path based on the 42.2 sidecar contract.

The normative standalone LiteLLM setup is:

```yaml
# /opt/litellm/docker-compose.yml
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-latest@sha256:7c311546...   # same pin as bundled
    networks:
      - subumbra-net      # external: true — join subumbra's shared network
networks:
  subumbra-net:
    external: true
    name: subumbra-net
```

```yaml
# /opt/litellm/config.yaml (per-model)
litellm_params:
  api_base: http://subumbra-proxy:8090/t
  api_key: <key_id>       # plain, no subumbra: prefix; must match bootstrap key_id
```

No `custom_callbacks.py`. No `SUBUMBRA_ACCESS_TOKEN`. No `SUBUMBRA_HMAC_KEY`. No `CF_WORKER_URL`.
The app carries nothing security-relevant; subumbra-proxy owns all auth.

**Network alternative (host binding):** For apps that cannot join `subumbra-net`, use
`api_base: http://127.0.0.1:8090/t` (subumbra-proxy is bound to `127.0.0.1:8090` per
`docker-compose.yml:174`). Works from any process on the VPS host.

**Key scope requirement:** key_ids used in `api_key` must be in the `PROXY_ALLOWED_KEYS`
(i.e., in the `subumbra-proxy` adapter's `allowed_keys` registry entry). Mismatch returns
`403 key_scope_denied` from subumbra-proxy. This is operator-configured at bootstrap.

### Change B — Update install docs to match profile-gated reality

**Scope:** `docs/subumbra-install.md` steps 7–9.

`docs/subumbra-install.md:191` lists `litellm` as an expected service in `docker compose ps`.
This is wrong after 42.2. The core services are `subumbra-keys`, `subumbra-proxy`, `subumbra-ui`.
LiteLLM is opt-in.

- Remove LiteLLM from the "expected services" list in step 8.
- Remove the `docker exec litellm python3 -c ...` subumbra-keys health check from step 9
  (this requires the litellm container to be running; use direct host test instead).
- Add a "Standalone App Integration" section (or link to `docs/standalone-litellm.md`)
  describing the `api_base` + plain `key_id` pattern for external apps.

**Do not** remove the bundled LiteLLM profile. Leave `--profile litellm` available for
operators who want it. Just stop describing it as the default.

### Change C — Subumbra-proxy Worker-edge 401 signal

**Scope:** `subumbra-proxy/app.py` — the proxy-to-Worker error path.

When subumbra-proxy forwards a request to the CF Worker and receives `401` or `403` from the
Worker edge (not from the upstream provider), the response returned to the caller should
distinguish this from a provider auth failure.

**Minimum required signal:** a specific `reason_code` or error message that identifies
Worker-edge auth failures as distinct from upstream provider failures.

**Implementation constraint:** no CF Access credentials, tokens, or Worker response bodies
logged. The signal is the HTTP status + a reason code that the operator can act on.

Example distinguishable states:
- `worker_auth_failure` — CF Worker returned 401/403 before any provider call
- `provider_auth_failure` — CF Worker reached the provider; provider returned 401
- `worker_unreachable` — CF Worker returned connection error or timeout

This classification already exists implicitly in the proxy's response handling; the change is
to surface it explicitly so `curl http://127.0.0.1:8090/health` or a failed request returns
a diagnosable reason_code rather than a generic 500 or passthrough 401.

**No new logging fields.** The reason_code appears in the HTTP response body only, not in
persisted logs.

### Change D — Subumbra-proxy `/health` Worker reachability probe

**Scope:** `subumbra-proxy/app.py` `/health` endpoint.

`docker-compose.yml:186-188` shows the current healthcheck is local-only (checks that the
process is up). The `/health` endpoint should expose, as an additional field, whether the
CF Worker `/health` is reachable from the proxy's current credentials.

**Field:** `worker_reachable: true/false` — already implemented in the UI status call
(`docs/subumbra-install.md:221`: `curl -sS "$CF_WORKER_URL/health"`). The probe exists;
it just needs to be surfaced in the sidecar health response so operators and the verify
harness can check it from one endpoint.

**Constraint:** worker auth failure must not expose the CF Access token or any credential
in the health response. The field is binary: reachable with current auth = true/false.

---

## 4. Failure Modes

| Failure | Signal after this round |
|---|---|
| Standalone LiteLLM uses wrong `api_key` format (old `subumbra:` prefix) | subumbra-proxy returns `{"detail":"invalid key_id"}` (already exists per R42.2) |
| key_id not in PROXY_ALLOWED_KEYS scope | subumbra-proxy returns 403 `key_scope_denied` (already exists) |
| App not on subumbra-net and not using 127.0.0.1:8090 | Connection refused — operator sees network error immediately |
| CF Access credentials expired or rotated | subumbra-proxy `/health` shows `worker_reachable: false`; live request returns `worker_auth_failure` reason_code |
| SUBUMBRA_TOKEN_PROXY stale after re-bootstrap | Existing `post-bootstrap.sh` drift detection covers this (source 1) |
| Operator copies bundled LiteLLM config but includes Gemini model | Gemini: `/v1/chat/completions` path known to fail via subumbra-proxy (R42.2 known exclusion); returns 404 from provider — not a new failure mode |

---

## 5. Exclusions

| Item | Reason |
|---|---|
| Gemini routing fix | Separate path issue (`/v1beta/openai/` vs `/v1/`); deferred from R42.2; not in scope here |
| OpenWebUI, N8N adapter specifics | Future adapter rounds; sidecar contract generalizes to them but their specific configs are out of scope |
| `custom_callbacks.py` redesign or removal | Legacy code; leave in place for any operator running old callback path; no changes needed |
| CF Access token rotation automation | Requires CF API scope changes; not appropriate for this round |
| `post-bootstrap.sh` standalone path awareness | Post-bootstrap must remain independent of external app paths (R41.7 exclusion still valid) |
| Broad observability (new audit fields, Cloudflare log forwarding) | Out of scope per kickoff.md and security invariants |
| Redesigning HMAC, token architecture, or key cryptography | Explicitly out of scope |
| P9.1/P9.2 harness architectural redesign | R42.2 cleanup item; separate harness maintenance round |
| Round 41.7 verification | 41.7 is superseded by Change A; close it as superseded, not as verified |

---

## 6. Open Questions

**Q1: Should bundled LiteLLM keep `--profile litellm` or be removed from docker-compose.yml?**
The profile-gated service is harmless and provides convenience for operators who want it as a
quick validation tool. Removing it from compose would be irreversible and offers no security
benefit. Recommendation: keep it, but explicitly label it as "convenience only, not the
reference path" in docs.

**Q2: Should `docs/standalone-litellm.md` be the canonical integration guide for all app
types (LiteLLM, OpenWebUI, N8N), or just LiteLLM for this round?**
Round 42.3 should use standalone LiteLLM as the concrete first example. OpenWebUI/N8N integration
patterns follow the same sidecar contract but have different config surfaces. Those belong in
per-app docs created in later rounds, not here.

**Q3: Does Change C (Worker-edge 401 signal) require a subumbra-proxy version bump or is it
backward-compatible?**
Changing the error body of a 4xx/5xx response is backward-compatible for clients that don't
parse the body (which most LiteLLM integrations don't — they re-raise the upstream status).
No protocol or API contract change required.

**Q4: Is there a verification proof target that exercises Change C/D honestly?**
The verify.sh harness could be extended with a `P9.7` check that probes
`http://127.0.0.1:8090/health` and asserts `worker_reachable: true`. This is a single HTTP
call and requires no secret-bearing logic. It should be a round-hook check, not a global probe.

**Q5: Should Round 41.7 be formally closed as "superseded" or "withdrawn"?**
Superseded is accurate: the 41.7 plan is technically correct for its era but the contract it
implements was retired by R42.2. Closing it as superseded preserves the audit trail without
implying a verification failure.
