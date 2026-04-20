# Round 42.2 Review — Runtime Auth Reconciliation

Author: Gemini
Topic: Runtime Auth Reconciliation
Date: 2026-04-19

## 1. Findings Summary

| Finding | Severity | Description | Recommendation |
|---|---|---|---|
| **Module-level env freeze** | High | `custom_callbacks.py` freezes Subumbra tokens at load time (`litellm/custom_callbacks.py:72-73`). | Switch to sidecar routing to make LiteLLM stateless. |
| **URL Path Collision** | High | Using provider prefixes in `api_base` (e.g. `/t/openai/`) would corrupt targets. | Use `api_base: http://subumbra-proxy:8090/t` exclusively. |
| **Key-Scope Barrier** | High | `subumbra-keys` blocks key IDs not in the adapter's `allowed_keys` list. | Prerequisite: Expand `PROXY_ALLOWED_KEYS` during bootstrap. |
| **Header Fidelity** | Low | Anthropic and others require specific headers (e.g. `anthropic-version`). | Verified: Proxy and Worker preserve request headers (transparency confirmed). |
| **Env Precedence** | Low | `DEEPSEEK_API_BASE` in compose may override sidecar routing. | Delete legacy provider env vars from `docker-compose.yml`. |

---

## 2. Detailed Analysis

### 2A. The "Stateless LiteLLM" Migration
The research confirms that LiteLLM identifies and hydrates Subumbra auth material once at startup. Routing through the `subumbra-proxy` transparent sidecar (`/t/`) is the correct architectural fix because it moves the authentication "authority" to a dedicated service that already participates in the standard sync/restart lifecycle.

### 2B. Resolution of URL Path Dispute
I verified the path construction logic in `subumbra-proxy/app.py`:
- `handle_transparent_request` (Line 266) captures everything after `/t/` as `path`.
- `build_transparent_target_url` (Line 182) appends `path` directly to the `target_host`.
- **Result**: If the `api_base` in LiteLLM includes a provider prefix (e.g. `/t/openai/`), the resulting upstream URL will incorrectly contain that prefix (e.g. `api.openai.com/openai/v1/chat/completions`), leading to 404s.
- **Recommendation**: Align with Claude's proposal to use `http://subumbra-proxy:8090/t` with no prefix and no trailing slash.

### 2C. Non-OpenAI Provider Compatibility
A concern was raised regarding whether the proxy could handle non-OpenAI-shaped families like Anthropic.
- **Verification**: `worker/src/worker.js:524-529` implements a `cleanHeaders` loop that forwards all inbound headers (preserving case/content) except for specifically excluded hop-by-hop headers.
- **Confirmation**: Required headers like `anthropic-version` will pass through the proxy and Worker intact. Since these providers use standard JSON bodies, no body translation is required in the proxy.

### 2D. Operational Prerequisite (Key Scoping)
The `subumbra-keys` service performs strict scoping at `subumbra-keys/app.py:546`. 
- **Current State**: `subumbra-proxy` typically has a narrow scope (e.g., github, slack).
- **Required Change**: Before LiteLLM can be successfully routed through the proxy, the proxy's `allowed_keys` must be expanded to include all key_ids currently served by LiteLLM.
- **Implementation**: This should be handled via the bootstrap environment (`PROXY_ALLOWED_KEYS`) rather than a code change to `subumbra-bootstrap.py`.

---

## 3. Recommendations

1.  **Phase 1: Operational Prep**: Re-bootstrap with `PROXY_ALLOWED_KEYS` containing the full LiteLLM key set.
2.  **Phase 2: Configuration**:
    *   Update `litellm/config.yaml`: Remove `callbacks`, strip `subumbra:` prefixes, and set `api_base` to `/t`.
    *   Update `docker-compose.yml`: Strip all Subumbra-specific env vars from LiteLLM. Specifically remove `DEEPSEEK_API_BASE` to prevent precedence issues.
3.  **Phase 3: Cleanup**:
    *   Mark `custom_callbacks.py` as legacy with a clear deprecation header.
    *   Remove the LiteLLM case from `post-bootstrap.sh` drift detection.
4.  **Verification**: Conduct per-provider live tests (Anthropic, OpenAI, etc.) as the final close-out condition.
