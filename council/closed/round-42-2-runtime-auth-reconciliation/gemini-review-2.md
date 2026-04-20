# Round 42.2 Secondary Review — Evidence-Based Alignment

Author: Gemini
Topic: Runtime Auth Consolidation & Multi-App Friction
Date: 2026-04-19

## 1. Findings Table

| Finding | Evidence (File:Line) | Impact | Recommendation |
|---|---|---|---|
| **Authority Drift** | `litellm/custom_callbacks.py:72` | `SUBUMBRA_ACCESS_TOKEN` freeze at import is the root cause of rotation breaks. | **PROCEED**: Switch LiteLLM to stateless sidecar routing. |
| **Universal Transparency** | `subumbra-proxy/app.py:91` | Sidecar uses `{path:path}` wildcard and `TRANSPARENT_METHODS`. | High fidelity: Already supports any HTTP endpoint (Chat, Embeddings, etc.). |
| **Shared Key Authority** | `subumbra-keys/app.py:348` | `get_record()` works on `key_id`, independent of the calling adapter. | High fidelity: One key encrypted once serves multiple apps (LiteLLM, n8n, etc.). |
| **Friction (Import)** | `bootstrap/...:414` | Manual interactive import is a known operator friction point. | **FOLLOW-UP**: Align with user's "Swap & Shred" vision in a future round. |

---

## 2. Analysis of User Goals (Alignment Pass)

Based on `council/round-42-2-runtime-auth-reconciliation/eric-questions.md`, I have analyzed the current and proposed implementation against the user's long-term vision.

### A. The "Universal Proxy" Goal (User Q1 & Q5)
The user correctly identifies that Subumbra handles more than just LiteLLM.
- **Evidence**: `subumbra-proxy/app.py:266` is path-agnostic. It appends the inbound `{path}` to the target host.
- **Confirmation**: This supports OpenAI-style `/v1/chat/completions` as well as any other endpoint type (Anthropic messages, custom provider paths). The sidecar is already the "Universal Proxy" the user envisions.

### B. "Swap & Shred" for Existing .env (User Q3)
The user proposes reading an existing `.env`, replacing secret keys with Subumbra tokens, and then shredding.
- **Evidence**: `bootstrap/subumbra-bootstrap.py:491-496` already handles the **Shred** part.
- **Insight**: It currently does not **Replace** in-place (it just imports).
- **Feasibility**: High. We can extend Step 2 of the bootstrap to write a "cleansed" version of the imported file back to the original location (e.g. `LITELLM_API_KEY=subumbra:openai_prod`). This resolves the "manual additions" friction mentioned by the user.

### C. Key Reuse Fidelity (User Q4)
- **Evidence**: `subumbra-keys/app.py:348` and `subumbra-bootstrap.py:1456`.
- **Logic**: API keys are encrypted once per `key_id`. If n8n, LiteLLM, and Open WebUI all use `key_id: "openai_prod"`, they all refer to the **same** encrypted blob and same Worker decryption path.
- **Security**: This is efficient and safe, as rotation updates the single central `keys.json` record, immediately reconciling for all consumers.

### D. Containerized Post-Bootstrap (User Q2)
- **Problem**: `post-bootstrap.sh` currently uses `docker compose` which requires host-level control.
- **Solution**: For a future round, we could containerize the **Drift PROBE** (the `curl` check), while keeping the **Recreate ACTIONS** at the operator/ansible layer. The current Proposal-2 removes LiteLLM's drift sensitivity entirely, making this easier to achieve.

---

## 3. Final Recommendations

1.  **Proceed with Sidecar Model**: The evidence confirms that routing LiteLLM through the proxy is the cleanest way to support "Universal Proxying" across multiple apps.
2.  **Reject In-Code Key Scoping (Operational Preference)**: Keep the per-adapter scope in `bootstrap`. If a user wants shared keys, they should explicitly set `PROXY_ALLOWED_KEYS` to include the `LITELLM` key set. This preserves the security contract of the registry.
3.  **Draft "Swap & Shred" Vision**: While Proposal-2 solves LiteLLM's drift, a dedicated follow-up round should be planned to implement the user's "Automatic In-Place .env Cleansing" to finalize the removal of manual setup friction.
4.  **Remove Provider-specific Env Vars**: As noted in Review-1, `DEEPSEEK_API_BASE` and others in `docker-compose.yml` should be removed to ensure the sidecar `api_base` override in `config.yaml` is the sole authority.
