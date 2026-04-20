# Round 42.2 Dispute Review — Runtime Auth Reconciliation

Author: Gemini
Topic: Dispute Review for Council Round 42.2
Date: 2026-04-19

## Dispute 1 — Base64 Encoding in Registry Parsing

### Claims
The approved plan ([runtime-auth-reconciliation.md:71](file:///home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation.md#L71)) includes a `base64.b64decode()` step for the `SUBUMBRA_ADAPTER_REGISTRY` variable. Codex disputes this, claiming the registry is stored as plain JSON.

### Evidence
- `bootstrap/subumbra-bootstrap.py:1720`: `f"SUBUMBRA_ADAPTER_REGISTRY={json.dumps(adapter_registry, separators=(',', ':'))}"`
- **Result**: The bootstrap script writes the registry as a plain JSON string directly to the `.env` file. No Base64 encoding is performed.

### Position
**SUSTAINED**. Codex is correct. The verification command in the approved plan is technically broken and will fail with a `binascii.Error: Incorrect padding` when run against a standard Subumbra `.env` file.

**Proposed Fix**:
Update the V3 verification script in the approved plan to parse the JSON content directly without decoding:
```python
# Updated parsing line
data = json.loads(reg)
```

---

## Dispute 2 — Gemini-specific `api_base` Exception

### Claims
The approved plan includes a specific `api_base: http://subumbra-proxy:8090/t/v1beta/openai` for Gemini. Codex disputes this as inconsistent with the syntheses, which generally endorse a universal `/t` contract.

### Evidence
- **Technical**: Google's Gemini (OpenAI mode) provides its service at `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`. LiteLLM appends `/chat/completions`. A universal `/t` prefix would result in a `404` at `api.googleapis.com/chat/completions`.
- **Synthesis**: My own synthesis ([gemini-synthesis.md:10-13](file:///home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-synthesis.md#L10-L13)) explicitly specifies "no provider prefix" without acknowledging the Gemini carve-out.

### Position
**SUSTAINED**. While the Gemini exception is technically **required** for connectivity, Codex is correct that it was not explicitly synthesized as a consensus item. Implementing a carve-out that contradicts the stated primary contract of the syntheses creates ambiguity for other implementers.

**Proposed Fix**:
Consensus is now reached on the technical necessity of the Gemini exception. All three synthesis documents should be updated to explicitly list the `/t/v1beta/openai` carve-out as a settled technical requirement, or the approved plan should be revised to move Gemini back to the "Future Investigation" or "Deferred" section to maintain a clean universal contract for this round.

---

## Conclusion
Both disputes are sustained by direct code evidence. The approved plan is currently **not consistent** with the syntheses and contains a non-functional verification command.

**Next Step**: Skip further synthesis. Proceed directly to **Approved Plan v2** with the following corrections:
1. Fix the V3 script to remove `base64.b64decode`.
2. Explicitly label the Gemini `api_base` as a "Technically Required Exception" in the roadmap/mechanics section.
