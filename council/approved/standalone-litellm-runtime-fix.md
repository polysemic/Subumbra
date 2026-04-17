# Approved Plan — Round 41.7: Standalone LiteLLM Runtime Fix

Date: 2026-04-16

## Consensus Basis

All three syntheses (claude, codex, gemini) agree without reservation on:
- Both root causes and their evidence
- The network topology fix (`name: subumbra_internal`)
- Rejection of Gemini's `subumbra-keys → subumbra-net` attachment
- Deferral of `_wire_transport_once()` expansion
- Rejection of `post-bootstrap.sh` token-sync modification
- Token identity requirement (`SUBUMBRA_TOKEN_LITELLM`, not `SUBUMBRA_TOKEN_PROXY`)
- Image pin as compatibility-risk reduction
- Claude's proposal as the implementation base

---

## Root Causes Being Fixed

**401 Incorrect API key provided: subumbra:...**
The standalone LiteLLM template in `docs/testbed-install.md:168-171` does not mount
`custom_callbacks.py`. LiteLLM starts without the Subumbra callback module on its Python path.
`litellm_settings.callbacks: custom_callbacks.proxy_handler_instance` silently fails to wire.
`async_pre_call_deployment_hook` never runs. The raw `subumbra:<key_id>` string reaches the upstream
provider as the bearer token.
Evidence: `docs/testbed-install.md:168-171`, `litellm/config.yaml:115-118`,
`litellm/custom_callbacks.py:364-381,435-445`.

**500 subumbra-keys service is unreachable**
`subumbra-keys` is attached only to the `internal` network (`docker-compose.yml:39-47`), which has
`internal: true` (`docker-compose.yml:7-10`). A standalone LiteLLM on `subumbra-net` only has no
route to `subumbra-keys`. The fetch in `custom_callbacks.py:383-415` fails at the network level.
Evidence: `docker-compose.yml:7-10,39-47,83-85,101-103`,
`litellm/custom_callbacks.py:71,383-415`.

---

## Change 1 — Product: Give the `internal` Network a Stable Name

**File:** `docker-compose.yml`

**Location:** lines 7-10, the `internal:` block under `networks:`

**Before:**
```yaml
networks:
  internal:
    driver: bridge
    internal: true # Docker enforces: no outbound routing
```

**After:**
```yaml
networks:
  internal:
    name: subumbra_internal
    driver: bridge
    internal: true # Docker enforces: no outbound routing
```

**Why this is safe:**
- `name: subumbra_internal` gives the Docker bridge network a stable, predictable name in Docker's
  namespace instead of the default compose-project-prefixed name
- `internal: true` remains unchanged — Docker continues to enforce no outbound routing for this
  network at the iptables level
- All existing services that join `internal` by compose name (`subumbra-keys`, `litellm`, etc.)
  continue to work without any other changes
- `subumbra-keys` does NOT join any additional network; its `networks: [internal]` declaration
  is untouched

**What this enables:**
An external project (standalone `/opt/litellm/`) can now reference this network by its stable
name via `external: true` in its own compose file, giving its LiteLLM container a route to
`subumbra-keys` via Docker DNS (`http://subumbra-keys:9090`).

---

## Change 2 — Operator: Standalone `/opt/litellm/docker-compose.yml`

**File:** `/opt/litellm/docker-compose.yml` (operator's standalone LiteLLM compose file)

Replace the existing content with:

```yaml
services:
  litellm:
    # Pinned to match the bundled Subumbra LiteLLM service — required for
    # custom_callbacks.py transport compatibility (litellm.module_level_aclient.client)
    image: ghcr.io/berriai/litellm:main-latest@sha256:7c311546c25e7bb6e8cafede9fcd3d0d622ac636b5c9418befaa32e85dfb0186
    container_name: litellm
    restart: unless-stopped
    ports:
      - "127.0.0.1:4000:4000"
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./custom_callbacks.py:/app/custom_callbacks.py:ro
    env_file:
      - .env
    command: ["--config", "/app/config.yaml", "--port", "4000", "--num_workers", "1"]
    networks:
      - subumbra-net
      - subumbra_internal

networks:
  subumbra-net:
    external: true
    name: subumbra-net
  subumbra_internal:
    external: true
    name: subumbra_internal
```

**Notes:**
- The `litellm-db` service and its `depends_on:` are intentionally omitted. Subumbra's
  `config.yaml` uses `no_database: true`. If the operator has an existing DB they want to retain,
  they may keep it, but it is not required.
- `subumbra_internal` must be joined as `external: true` with `name: subumbra_internal` — both
  fields are required. The `name:` field ensures Docker looks up the network by the stable name
  set in Change 1, not a compose-project-prefixed variant.
- The `custom_callbacks.py` source file must be present at `/opt/litellm/custom_callbacks.py`
  before starting. Copy it from the Subumbra repo:
  ```bash
  cp /opt/subumbra/litellm/custom_callbacks.py /opt/litellm/custom_callbacks.py
  ```

---

## Change 3 — Operator: `/opt/litellm/.env` Token Alignment

**File:** `/opt/litellm/.env`

Add or update the following variables. Source values from `/opt/subumbra/.env`:

```bash
# === Subumbra adapter credentials ===
# CRITICAL: Use SUBUMBRA_TOKEN_LITELLM, NOT SUBUMBRA_TOKEN_PROXY.
# Using the proxy token causes adapter_unknown → 401 from subumbra-keys.
SUBUMBRA_ACCESS_TOKEN=<value of SUBUMBRA_TOKEN_LITELLM from /opt/subumbra/.env>
SUBUMBRA_HMAC_KEY=<value of SUBUMBRA_HMAC_KEY from /opt/subumbra/.env>
SUBUMBRA_KEYS_URL=http://subumbra-keys:9090
CF_WORKER_URL=<value of CF_WORKER_URL from /opt/subumbra/.env>

# Optional: only required if your Cloudflare Worker is behind CF Access
CF_ACCESS_CLIENT_ID=<value of CF_ACCESS_CLIENT_ID from /opt/subumbra/.env, or leave blank>
CF_ACCESS_CLIENT_SECRET=<value of CF_ACCESS_CLIENT_SECRET from /opt/subumbra/.env, or leave blank>
```

**Failure mode if misconfigured:**
- Wrong token (`SUBUMBRA_TOKEN_PROXY` instead of `SUBUMBRA_TOKEN_LITELLM`):
  `subumbra-keys/app.py:323-340` returns `adapter_unknown` → callback raises with 401
- Correct token but key not in `allowed_keys` for the `subumbra_litellm` adapter record:
  `subumbra-keys/app.py:546` returns 403 `key_scope_denied`
- Missing or wrong `SUBUMBRA_HMAC_KEY`:
  HMAC signature validation fails → subumbra-keys rejects the request before token check
- Missing `CF_WORKER_URL`:
  `custom_callbacks.py:99-100` logs a warning at startup; requests fail when transport tries to
  POST to an empty URL

**Token copy commands (run on the VPS):**
```bash
source /opt/subumbra/.env
echo "SUBUMBRA_ACCESS_TOKEN=${SUBUMBRA_TOKEN_LITELLM}" >> /opt/litellm/.env
echo "SUBUMBRA_HMAC_KEY=${SUBUMBRA_HMAC_KEY}"          >> /opt/litellm/.env
echo "SUBUMBRA_KEYS_URL=http://subumbra-keys:9090"      >> /opt/litellm/.env
echo "CF_WORKER_URL=${CF_WORKER_URL}"                   >> /opt/litellm/.env
# Add CF Access only if your worker is behind CF Access:
# echo "CF_ACCESS_CLIENT_ID=${CF_ACCESS_CLIENT_ID}"     >> /opt/litellm/.env
# echo "CF_ACCESS_CLIENT_SECRET=${CF_ACCESS_CLIENT_SECRET}" >> /opt/litellm/.env
```

---

## Change 4 — Operator: `/opt/litellm/config.yaml` Subumbra Alignment

**File:** `/opt/litellm/config.yaml`

The config must contain both a `model_list` with `subumbra:` entries and the `callbacks:` stanza.
Replace the existing config with Subumbra's own config (then prune models the operator doesn't need):

```bash
cp /opt/subumbra/litellm/config.yaml /opt/litellm/config.yaml
```

If the operator needs to retain custom models or settings, the minimum required additions to any
existing config are:

1. Each model that should use Subumbra must use `api_key: "subumbra:<key_id>"`:
   ```yaml
   model_list:
     - model_name: gpt-4o-mini
       litellm_params:
         model: openai/gpt-4o-mini
         api_key: "subumbra:openai_prod"  # key_id must match an entry in subumbra-keys
   ```

2. The `litellm_settings.callbacks:` stanza must be present:
   ```yaml
   litellm_settings:
     callbacks: custom_callbacks.proxy_handler_instance
   ```
   Without this stanza, the custom_callbacks module is not wired even if the file is mounted
   and importable.

3. The `no_database: true` setting avoids a DB dependency:
   ```yaml
   general_settings:
     no_database: true
   ```

**Key ID alignment check:**
Each `subumbra:<key_id>` in config.yaml must exactly match a key ID in the bootstrap-generated
`subumbra-keys/keys.json`. Mismatch symptom: `subumbra-keys` returns HTTP 403 with
`reason_code=key_scope_denied`, and LiteLLM shows the model in `unhealthy_endpoints`.

---

## Verification Steps

Run these after applying all four changes and restarting the Subumbra product stack and the
standalone LiteLLM:

### V1 — Product stack restart (after Change 1)
```bash
cd /opt/subumbra
docker compose down
docker compose up -d
docker compose ps  # subumbra-keys and litellm should be healthy
```

Verify the `internal` network now has the stable name:
```bash
docker network inspect subumbra_internal --format '{{.Name}} internal={{index .Options "com.docker.network.bridge.enable_icc"}}'
# Should print: subumbra_internal internal=...
# Also check:
docker network inspect subumbra_internal --format '{{.Internal}}'
# Must print: true
```

### V2 — Standalone LiteLLM start with callback loaded (Changes 2, 3, 4)
```bash
cd /opt/litellm
docker compose up -d
docker compose logs litellm | head -50
```

Expected in startup logs:
- No `ModuleNotFoundError: No module named 'custom_callbacks'`
- No `subumbra-callback: SUBUMBRA_ACCESS_TOKEN not set` warning
- No `subumbra-callback: SUBUMBRA_HMAC_KEY not set` warning
- No `subumbra-callback: CF_WORKER_URL not set` warning
- LiteLLM startup banner showing models loaded

Failure signals:
- `ModuleNotFoundError` → `custom_callbacks.py` is not mounted correctly (re-check Change 2)
- Missing-var warnings → `.env` token values not copied (re-check Change 3)

### V3 — Network path verification
```bash
# From the standalone LiteLLM container, confirm subumbra-keys is reachable:
docker exec litellm python3 -c "
import urllib.request, os
url = os.environ.get('SUBUMBRA_KEYS_URL', 'http://subumbra-keys:9090')
try:
    resp = urllib.request.urlopen(url + '/health', timeout=3)
    print('subumbra-keys reachable:', resp.status)
except Exception as e:
    print('subumbra-keys UNREACHABLE:', e)
"
# Expected: subumbra-keys reachable: 200
```

### V4 — End-to-end Subumbra-backed request
```bash
export LITELLM_MASTER_KEY="$(grep ^LITELLM_MASTER_KEY /opt/litellm/.env | cut -d= -f2)"
curl -s --compressed \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"say: test only."}],"max_tokens":10}' \
  http://127.0.0.1:4000/v1/chat/completions
```

Expected: HTTP 200 with a JSON response body containing `"content": "test only."` (or similar).
The request must flow through: standalone LiteLLM → callback intercept → subumbra-keys record
fetch → CF Worker `/proxy` POST → OpenAI → streamed response back.

**Artifact requirement:** Save the startup log excerpt (V2) and the curl response (V4) to
`council/round-41-7-standalone-litellm-runtime-fix/runs/standalone-proof/`:
- `startup-logs.txt` — `docker compose logs litellm` output from initial start
- `curl-response.txt` — full curl output including HTTP status

---

## Explicit Exclusions

These must NOT be changed in this round:

| Item | Reason |
|------|--------|
| `subumbra-keys` network membership | Must remain `internal` only; adding `subumbra-net` grants internet access via bridge gateway |
| `litellm/custom_callbacks.py` — `_wire_transport_once()` | No runtime evidence that a fully-loaded callback on a properly wired standalone deployment fails for openai/together/cerebras |
| `post-bootstrap.sh` | Must not know about operator standalone paths; token propagation stays manual |
| `docs/testbed-install.md` | Broad template polish is Round 42 scope |
| OpenWebUI / N8N operatorization | Round 42 scope |
| Callback rename (`custom_callbacks.py` → `subumbra_adapter.py`) | Round 42 scope |
| Preflight automation or config validation scripts | Round 42 scope |

---

## Logging and Error Handling

**No new code changes required to `custom_callbacks.py`.**

The existing callback already emits the necessary operator-visible signals:

| Signal | Location | When |
|--------|----------|------|
| `subumbra-callback: SUBUMBRA_ACCESS_TOKEN not set` | `custom_callbacks.py:95-96` | Module import with missing env var |
| `subumbra-callback: SUBUMBRA_HMAC_KEY not set` | `custom_callbacks.py:97-98` | Module import with missing env var |
| `subumbra-callback: CF_WORKER_URL not set` | `custom_callbacks.py:99-100` | Module import with missing env var |
| `subumbra-keys returned <status>` | `custom_callbacks.py:392-399` | Auth/token failure |
| `subumbra-keys service is unreachable` | `custom_callbacks.py:410-415` | Network path failure |

What must never be logged (unchanged): raw bearer tokens, decrypted keys, full request headers,
full provider payloads, `SUBUMBRA_HMAC_KEY` value.

---

## Known Limitations Carried Forward

1. **`_wire_transport_once()` coverage for openai/together/cerebras is unverified post-fix.**
   The module-level client patch at `custom_callbacks.py:255` should handle these paths, but this
   has not been proved with a loaded callback on a fixed standalone deployment. Round 42 should
   include a runtime test for all providers listed in `litellm/config.yaml`.

2. **Token rotation is not automated for the standalone path.**
   If `SUBUMBRA_TOKEN_LITELLM` is rotated (new bootstrap run), the operator must manually re-copy
   the new value to `/opt/litellm/.env` and restart the standalone service. There is currently no
   drift-detection for the standalone path (the bundled path has `post-bootstrap.sh:90-107`).
   Round 42 scope.

3. **`custom_callbacks.py` version alignment is manual.**
   If `litellm/custom_callbacks.py` is updated in the Subumbra repo, the operator must re-copy it
   to `/opt/litellm/custom_callbacks.py`. No mechanism currently enforces this.
   Round 42 scope.

4. **LiteLLM image pin may become stale.**
   The digest `sha256:7c311546c25e7bb6e8cafede9fcd3d0d622ac636b5c9418befaa32e85dfb0186`
   (LiteLLM 1.82.6) is the current bundled pin. When the bundled service is updated in
   `docker-compose.yml:78`, the standalone pin must be updated to match.
