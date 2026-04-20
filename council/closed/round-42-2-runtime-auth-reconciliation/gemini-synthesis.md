# Round 42.2 Synthesis — Runtime Auth Reconciliation

Author: Gemini
Topic: Runtime Auth Consolidation
Date: 2026-04-19

## 1. Consensus Items
All three reviewers (Claude, Codex, Gemini) have reached a final consensus on the following core technical points:

1.  **Architecture**: The callback-era stateful auth model in LiteLLM is the primary cause of token drift and must be replaced by the **stateless sidecar routing model**. 
2.  **Authority Surface**: `subumbra-proxy` will serve as the sole gateway for LiteLLM traffic, owning record fetch, HMAC signing, and Worker forwarding.
3.  **URL Contract**: The LiteLLM `api_base` must be set to `http://subumbra-proxy:8090/t` (with no provider prefix). The sidecar natively appends the inbound path (e.g. `/v1/messages`) to the upstream host.
4.  **Legacy Cleanup**: 
    *   Strip all Subumbra-specific environment variables (`TOKEN`, `HMAC`, etc.) from the LiteLLM container.
    *   Remove `DEEPSEEK_API_BASE` from `docker-compose.yml` to prevent legacy path overrides.
    *   Remove the LiteLLM drift check loop from `post-bootstrap.sh`.
    *   Mark `custom_callbacks.py` as **legacy compatibility** but do not delete it in this round.

## 2. Disagreements & Resolutions

### A. Phase 1 Scope: Is Anthropic Compatible?
- **Previous positions**: Codex expressed caution regarding Anthropic's unique body/header needs. Gemini/Claude argued for full migration.
- **Resolution**: **RESOLVED BY EVIDENCE**. Investigations (`gemini-investigation.md`, `codex-investigation.md`) confirm that LiteLLM honors `api_base` for Anthropic and correctly appends the suffix. `subumbra-proxy` preserves mandatory headers (e.g., `anthropic-version`). Anthropic remains **IN SCOPE**.

### B. "Doc Truth" vs. Optional Polish
- **Previous positions**: Gemini initially underweighted documentation updates. Claude and Codex argued they are required for correctness.
- **Resolution**: **ACCEPTED**. Because `subumbra-keys` performs strict scoping, incorrect prompts or out-of-date alignment hints in the bootstrap wizard will directly lead to broken fresh installs (`403 key_scope_denied`).
- **Action**: Updating Step 3 prompts, alignment hints in `subumbra-bootstrap.py`, and the `README.md` is **REQUIRED ROUND SCOPE**.

### C. Bootstrap Auto-merge UX
- **Previous positions**: Gemini proposed modifying the bootstrap code to automatically merge `litellm` and `proxy` scopes. Claude/Codex argued this is a security property change.
- **Resolution**: **DEFERRED**. Auto-merging is not a technical requirement for functionality. Expanding `PROXY_ALLOWED_KEYS` is currently an **operational prerequisite**. This UX simplification should be a future round.

### D. Documentation Targets
- **Previous positions**: Disputes existed over `docs/standalone-litellm.md`.
- **Resolution**: **RESOLVED**. The file does not exist on disk. The correct targets for "operator truth" are `README.md` and `docs/subumbra-install.md`.

## 3. What We Missed
- **Dependency Chain**: We must ensure `subumbra-proxy` is healthy before LiteLLM starts, now that LiteLLM is a downstream consumer. `docker-compose.yml` should be updated with a `service_healthy` check for the proxy (from Claude-review).

## 4. Phased Plan

### Phase 1: Implementation (Now)
1.  **Configure Sidecar**: Update `litellm/config.yaml` with `/t` no-prefix routing and strip existing callbacks.
2.  **Prune Secrets**: Remove all Subumbra secrets and legacy DeepSeek env vars from the LiteLLM container.
3.  **Update Operator Truth**:
    *   Modify `subumbra-bootstrap.py` Step 3 prompts and `_build_litellm_alignment_lines`.
    *   Update `README.md` adapter scope and model configuration sections.
    *   Update `docs/subumbra-install.md` Section 7.
4.  **Maintenance**: Label `custom_callbacks.py` as legacy; remove LiteLLM case from `post-bootstrap.sh`.

### Phase 2: Verification (Now)
1.  **Prerequisite**: Confirm `subumbra-proxy` has full key scope in the running registry.
2.  **Live Cross-Provider Test**: Prove Anthropic, OpenAI, Groq, DeepSeek, and Mistral connectivity through the sidecar.

### Phase 3: Future Rounds
1.  Automatic "Swap & Shred" for app-specific `.env` files.
2.  Full containerization of the post-bootstrap drift probe.

---

## 5. Consensus Status: APPROVED
There is a clear, evidence-backed line of consensus. All three LLMs have signed off on the architecture and implementation mechanism. The round is ready to move to the **Approved Plan** stage.
