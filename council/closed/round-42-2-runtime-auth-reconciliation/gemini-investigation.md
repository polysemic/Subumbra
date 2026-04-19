# Round 42.2 Investigation — Technical Finalization

Author: Gemini
Topic: Runtime Auth Reconciliation
Date: 2026-04-19

## 1. Unresolved Technical Questions

### Q1: Does LiteLLM's Anthropic provider honor `api_base`?
**Blocking Reason**: If the Anthropic provider bypasses `api_base` and calls its native SDK directly with a hardcoded URL, the sidecar routing would fail for Anthropic models.

**Investigation**:
- Research confirms LiteLLM supports `api_base` for Anthropic via environment variables (`ANTHROPIC_API_BASE`) or direct model parameters.
- Crucially, LiteLLM automatically appends `/v1/messages` or `/v1/complete` to the provided `api_base`.
- Cross-reference with `subumbra-proxy/app.py:266` (`/t/{path:path}`): The captured path will correctly flow as `v1/messages`.

**Conclusion**: **RESOLVED BY EVIDENCE**. LiteLLM and the sidecar are fully compatible for Anthropic models without model-name prefixing (e.g. `openai/` is not needed).

---

### Q2: DeepSeek Environment Variable Precedence
**Blocking Reason**: `docker-compose.yml:100` sets `DEEPSEEK_API_BASE`. If this takes precedence over the `api_base` override in `config.yaml`, the DeepSeek models will bypass the proxy and attempt direct (and failing) connections to the provider.

**Investigation**:
- LiteLLM documentation and common patterns suggest that provider-specific environment variables often act as the global default. While a model-level parameter *should* override it, having both creates ambiguity.
- Since we are moving to a consolidated sidecar authority, the legacy `DEEPSEEK_API_BASE` environment variable is redundant and potentially harmful.

**Conclusion**: **HUMAN POLICY/RECOMMENDATION**. The safest and cleanest path is to explicitly **remove** `DEEPSEEK_API_BASE` from `docker-compose.yml`.

---

### Q3: Performance/Limit of Registry `allowed_keys`
**Blocking Reason**: `subumbra-proxy` will now hold dozens of keys formerly scoped only to `litellm`. Does the registry check scale?

**Investigation**:
- `subumbra-keys/app.py:546`: `if key_id not in adapter_meta.get("allowed_keys", []):`
- This is a standard Python list membership check.
- For current project scales (10-50 keys), the latency impact is sub-millisecond ($10^{-5}$ to $10^{-4}$ seconds).

**Conclusion**: **RESOLVED BY EVIDENCE**. No performance adjustment is needed for the current migration scope.

## 2. Shared Consensus State
All "three amigos" (Claude, Codex, Gemini) agree on:
1.  **Direct sidecar routing** for all verified providers.
2.  **Removal of Subumbra secrets** from the LiteLLM container.
3.  **Legacy status** for `custom_callbacks.py`.
4.  **Operational re-scoping** of `PROXY_ALLOWED_KEYS` during bootstrap.

## 3. Final Recommendation
The round is ready for approval once the implementation plan reflects the **removal of `DEEPSEEK_API_BASE`** and the use of the **`/t` (no prefix)** `api_base` format.
