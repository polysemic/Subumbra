# Round 42.2 Proposal-2 — Runtime Auth Reconciliation (Alignment Pass)

Date: 2026-04-19
Author: Claude
Based on: claude-proposal.md, codex-proposal.md, codex-proposal-2.md, gemini-proposal.md

---

## 1. Positions I Accept

### From Gemini: key-scope authorization is a real blocker (partially)

Gemini's 3A correctly identified that routing LiteLLM through `subumbra-proxy`
requires the proxy to be authorized to access the same key_ids that LiteLLM
currently accesses.

This is confirmed by evidence:

- `subumbra-keys/app.py:546`: hard-blocks any request for a key_id outside the
  adapter's `allowed_keys` list with `403 key_scope_denied`
- `bootstrap/subumbra-bootstrap.py:104-108`: each adapter has a separate scope
  variable (`LITELLM_ALLOWED_KEYS`, `PROXY_ALLOWED_KEYS`) that becomes
  `allowed_keys` in the built `SUBUMBRA_ADAPTER_REGISTRY`
- `bootstrap/subumbra-bootstrap.py:574-595`: `_build_adapter_registry` constructs
  separate registry entries for `litellm` and `subumbra-proxy` with their own
  `allowed_keys` lists

If the current deployment's `PROXY_ALLOWED_KEYS` does not include
`anthropic_prod`, `openai_prod`, etc., the sidecar will get `403` on every
record fetch for those key_ids. This must be verified and resolved before the
round can be closed.

### From Gemini: strategic direction — stateless LiteLLM is the right goal

The Current vs Desired table (Gemini Section 2) is the clearest statement of
why this matters beyond just fixing drift: rotation sensitivity drops to zero,
auth exposure shrinks to the proxy layer alone. I accept this framing fully.

### From Codex-2: scope guardrail — verify each provider before claiming migration complete

Codex-2 correctly flags that the sidecar contract was approved for
OpenAI-compatible flows and does not automatically cover every provider family.
The Anthropic provider in LiteLLM may not honor `api_base` the same way
OpenAI-compat providers do. The round should require live verification for each
provider, not assume all pass because the mechanism is correct in theory.

I accept this as a verification requirement, not a reason to narrow the
implementation scope. The change should target all currently working providers,
but the round is not closed until each passes live.

### From Codex-2: reject reconciliation tooling as the primary output

I accept Codex-2's withdrawal of the standalone sync script and HMAC drift
extension. Those approaches preserve the broken trust shape. The right output is
eliminating LiteLLM's auth ownership, not making that ownership more ergonomic.

---

## 2. Positions I Reject

### Reject from Gemini 3A: modifying `bootstrap/subumbra-bootstrap.py` code

Gemini proposes modifying the bootstrap script to consolidate LiteLLM key scope
into `subumbra-proxy`'s adapter entry. I reject this as a code change in this
round.

Why:

- The `allowed_keys` split is intentional — per-adapter scope is a security
  property, not a bug. Removing the distinction in code makes ALL adapters
  share key pools by default, which is a broader change than this round warrants.
- The correct fix is operational: the next bootstrap run should be executed with
  `PROXY_ALLOWED_KEYS` set to include the key_ids that were formerly in
  `LITELLM_ALLOWED_KEYS`. This is a bootstrap-input change, not a bootstrap-code
  change.
- If an operator re-bootstraps after this round using the interactive wizard,
  they will be prompted for `PROXY_ALLOWED_KEYS` — the correct answer is all
  key_ids currently served through LiteLLM.

The round should document this prerequisite, not change the bootstrap code.

### Reject from Gemini 3B: `api_base` format with provider prefix

Gemini proposes:
```yaml
api_base: http://subumbra-proxy:8090/t/<provider_prefix>/
```

This is incorrect. The sidecar route at `subumbra-proxy/app.py:266` is:
```python
@app.api_route("/t/{path:path}", methods=TRANSPARENT_METHODS)
```

The `{path:path}` wildcard captures everything after `/t/`. If `api_base`
includes a provider prefix (e.g., `/t/openai`), the captured path would be
`openai/v1/chat/completions` and `build_transparent_target_url()` at `app.py:182`
would produce `api.openai.com/openai/v1/chat/completions` — a broken URL.

The correct `api_base` is:
```yaml
api_base: http://subumbra-proxy:8090/t
```

LiteLLM appends its own provider path (e.g., `/v1/chat/completions`,
`/v1/messages`). The sidecar captures that suffix intact and appends it to
`record["target_host"]`. No provider prefix belongs in `api_base`.

### Reject from Codex-2: the claim that `docs/standalone-litellm.md` exists

Codex-2 asserts the standalone LiteLLM doc exists and should be updated.

`docs/standalone-litellm*` — no files found on current filesystem.

This file does not exist. Codex-1 referenced it (with specific line numbers) but
that proposal was dated 2026-04-18 and may have been written when the file
existed, or may have hallucinated specific line references. Regardless, the file
is absent now. There is no doc to update, and no action needed for standalone
docs in this round.

---

## 3. Path That Resolves the Disagreement

The three proposals now converge on direction. The only substantive divergences
are the key-scope prerequisite and the `api_base` format. Both are resolved
above. The merged path is:

### 3A. Prerequisite: verify `PROXY_ALLOWED_KEYS` scope before implementation

Before the config changes take effect, confirm that the running deployment's
`SUBUMBRA_ADAPTER_REGISTRY` grants `subumbra-proxy` access to all key_ids
currently served through LiteLLM.

Check by inspecting the registry:
```bash
grep SUBUMBRA_ADAPTER_REGISTRY .env | python3 -c \
  "import sys,json; r=json.loads(sys.stdin.read().split('=',1)[1]); \
   [print(k, r[k]['allowed_keys']) for k in r]"
```

If `subumbra-proxy`'s `allowed_keys` is missing any key_ids in LiteLLM's list:
- Re-bootstrap with `PROXY_ALLOWED_KEYS` expanded to include them, OR
- For a running system: update `.env.bootstrap` and re-run bootstrap before
  the round's live verification

This is a prerequisite step, not a code change. Document it in the approved
plan's verification steps.

### 3B. `litellm/config.yaml` — transparent sidecar migration

For each model using `api_key: "subumbra:<key_id>"`, replace with:
```yaml
api_base: http://subumbra-proxy:8090/t
api_key: <key_id>
```

Remove the `callbacks: custom_callbacks.proxy_handler_instance` stanza from
`litellm_settings`.

If the Anthropic provider does not honor `api_base` in `litellm_params`, declare
Anthropic models using `openai/<model-name>` to force the OpenAI-compat code
path. The sidecar derives the real upstream from `record["target_host"]`
regardless of the LiteLLM provider prefix.

**Target providers:** Anthropic, OpenAI, Groq, DeepSeek, Mistral (all confirmed
working in prior rounds). Gemini stays excluded.

### 3C. `docker-compose.yml` — strip LiteLLM environment

Remove from the `litellm` service `environment:` block:
```
SUBUMBRA_ACCESS_TOKEN
SUBUMBRA_HMAC_KEY
SUBUMBRA_KEYS_URL
CF_WORKER_URL
CF_ACCESS_CLIENT_ID
CF_ACCESS_CLIENT_SECRET
```

Verify whether `DEEPSEEK_API_BASE: https://api.deepseek.com/v1`
(`docker-compose.yml:100`) conflicts with the per-model `api_base` override for
DeepSeek. If LiteLLM resolves the env var before the config value, remove it.

Keep: `LITELLM_MASTER_KEY`.

### 3D. `post-bootstrap.sh` — remove dead drift check case

Remove `litellm` from the drift check loop at `post-bootstrap.sh:92`. After
this round LiteLLM will not have `SUBUMBRA_ACCESS_TOKEN` set — the check is a
dead no-op. Do not replace it.

### 3E. `custom_callbacks.py` — legacy header

Add a header comment marking the file as the legacy integration path,
superseded by the transparent sidecar in Round 42.2. Do not delete in this
round.

### 3F. Verification requirement (incorporating Codex-2's scope guardrail)

V1 — Static check: no `callbacks:` stanza, no `subumbra:` prefix in any
`api_key`, no `SUBUMBRA_ACCESS_TOKEN` or `SUBUMBRA_HMAC_KEY` in the LiteLLM
compose environment block.

V2 — Live end-to-end, per provider: one successful completion request through
LiteLLM for each of Anthropic, OpenAI, Groq/Llama, DeepSeek, Mistral. Each
must return HTTP 200 from LiteLLM with no `key_scope_denied` or auth errors in
`subumbra-proxy` logs. The round is not closed until all five pass.

V3 — Key-scope verification: confirm `subumbra-proxy`'s `allowed_keys` in the
running `SUBUMBRA_ADAPTER_REGISTRY` includes all five key_ids before V2.

---

## 4. Revised Round Shape Summary

| Item | Decision | Basis |
|---|---|---|
| Core direction: transparent sidecar | ACCEPTED — unanimous | Claude, Gemini, Codex-2 all agree |
| `api_base` format: `/t` no prefix | ACCEPTED — Claude | Gemini's provider-prefix format would corrupt upstream URLs |
| Key-scope prerequisite check | ADDED | Gemini 3A confirmed by `subumbra-keys/app.py:546` and bootstrap code |
| Bootstrap code change for scope | REJECTED | Operational input change is sufficient; per-adapter scope is a security property |
| Provider-family scope guardrail | ACCEPTED — Codex-2 | Live V2 required per provider; Anthropic needs `api_base` honor verification |
| Reconciliation tooling | REJECTED | Codex-2 withdrew; preserves wrong trust shape |
| `docs/standalone-litellm.md` update | N/A — file does not exist | Confirmed absent on current filesystem |
| Remove LiteLLM drift check case | ACCEPTED — Claude | Dead no-op after this round |
| Legacy header on `custom_callbacks.py` | ACCEPTED — Claude + Codex-2 | Marks transition, no breakage |
