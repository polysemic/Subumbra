# Round 42.2 — Gemini Proposal: Runtime Auth Reconciliation

## Status
Verifier: Gemini
Round: 42.2
Topic: Runtime Auth Reconciliation

## 1. Evidence

### 1A. LiteLLM Stateful Vulnerability
LiteLLM currently reads Subumbra authentication material into module-level constants at load time. These values are frozen until the container is recreated.

*   `litellm/custom_callbacks.py:71-76`:
    ```python
    SUBUMBRA_KEYS_URL       = os.environ.get("SUBUMBRA_KEYS_URL", ...).rstrip("/")
    SUBUMBRA_ACCESS_TOKEN   = os.environ.get("SUBUMBRA_ACCESS_TOKEN",   "")
    SUBUMBRA_HMAC_KEY       = os.environ.get("SUBUMBRA_HMAC_KEY",       "").encode()
    CF_WORKER_URL           = os.environ.get("CF_WORKER_URL",         "").rstrip("/")
    ```
*   `post-bootstrap.sh:92`: Explicitly monitors `litellm` for token drift because it is known to hold stale state.

### 1B. Proxy Capability (Transparent Sidecar)
`subumbra-proxy` already implements the exact same logic required by the LiteLLM callback (record fetch + signatures + worker proxying) via its `/t/{path}` route.

*   `subumbra-proxy/app.py:273`: `@app.api_route("/t/{path:path}")` captures outbound paths.
*   `subumbra-proxy/app.py:135-151`: `extract_transparent_key_id` supports `Authorization: Bearer <key_id>`, which matches LiteLLM's standard header output when `api_base` is redirected.
*   `subumbra-proxy/app.py:316`: Correctly routes to `proxy_via_worker`, utilizing the Proxy's own (local) auth state.

## 2. Current vs Desired

| Feature | Current | Desired |
|---|---|---|
| **LiteLLM Role** | Heavy Stateful Adapter (holds Subumbra tokens) | Stateless Upstream Request Builder |
| **Auth Distribution** | Split across Proxy, LiteLLM, UI, and Probe | Consolidated into Proxy (transparent layer) |
| **Rotation Sensitivity** | High (LiteLLM requires restart on every bootstrap) | Zero (LiteLLM is agnostic to Subumbra rotation) |
| **Security Exposure** | Subumbra auth material exists in LiteLLM memory | Subumbra auth material isolated to Proxy sidecar |

## 3. Proposal

**Route LiteLLM through the `subumbra-proxy` transparent sidecar.**

This eliminates the need for LiteLLM to hold any Subumbra authentication material. Rotation state is reconciled at the Proxy layer alone.

### 3A. Consolidate Scope in Bootstrap
Modify `bootstrap/subumbra-bootstrap.py` to ensure `subumbra-proxy` is authorized for all key IDs required by LiteLLM.
*   Update `allowed_keys_by_adapter['subumbra-proxy']` to include the `litellm` key pool.
*   Alternatively, define a `SYSTEM_LLM_KEY_POOL` shared by both.

### 3B. LiteLLM Config Update
Update `litellm/config.yaml` to utilize the side-car:
*   Set `api_base: http://subumbra-proxy:8090/t/<provider_prefix>/` for all models.
*   Strip `subumbra:` prefix from `api_key` (e.g., `openai_prod` instead of `subumbra:openai_prod`).
*   Remove `callbacks: custom_callbacks.proxy_handler_instance`.

### 3C. Docker Clean-up
Update `docker-compose.yml`:
*   Remove `SUBUMBRA_ACCESS_TOKEN`, `SUBUMBRA_HMAC_KEY`, `SUBUMBRA_KEYS_URL`, and `CF_WORKER_URL` from the `litellm` environment block.
*   LiteLLM only requires its own app secret (e.g., `LITELLM_MASTER_KEY`).

### 3D. Drift Check Clean-up
Update `post-bootstrap.sh`:
*   Remove `litellm` from the `Check for token drift` loop. LiteLLM is now "stateless" regarding Subumbra-core tokens.

## 4. Failure Modes

| Failure Mode | Impact | Mitigation |
|---|---|---|
| **Proxy Latency** | Slight increase (~5-10ms) per request. | Minimal compared to Worker/Provider roundtrip. Networks are internal/local. |
| **Proxy Outage** | LiteLLM fails to reach providers. | `subumbra-proxy` is a thin sidecar with lower crash risk than LiteLLM. |
| **Path Mismatch** | `api_base` incorrectly omits provider prefix (e.g. `/v1`). | Explicitly document path requirements in `operator-guide.md` and `config.yaml`. |

## 5. Exclusions
*   No changes to the Cloudflare Worker decryption logic.
*   No changes to `subumbra-keys` encryption/storage.
*   `subumbra-ui` and `subumbra-probe` remain as standalone adapters for now to avoid circular dependencies.

## 6. Open Questions
*   **Gemini/Vertex AI Support**: Does LiteLLM properly support `api_base` for Gemini when using the OpenAI-compatible route? (Research suggests yes, confirmed by current `config.yaml` line 81).
*   **Non-Standard Ports**: Should we move the Proxy to a more standard transparent proxy port (e.g. 8080 or 443 internal) to make `api_base` shorter? (Deferred; 8090 is fine for now).
