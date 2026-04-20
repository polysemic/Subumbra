# Round 42.2 Synthesis — Runtime Auth Reconciliation

Date: 2026-04-19
Author: Codex
Round: round-42-2-runtime-auth-reconciliation

## Why Synthesis Is Needed

Synthesis is necessary here because the three-amigo work has already narrowed
the round to a mostly-shared technical direction, but the remaining differences
still matter at approval time:

- some items are now settled by direct evidence
- some are implementation-shape choices
- some are product/UX decisions that should not be smuggled in as “technical
  blockers”
- and an approved plan was written early, before that split was fully cleaned up

So the purpose of this synthesis is to turn the council’s evidence into one
truthful consensus line before the approved-plan consistency/dispute pass.

## 1. Consensus Items

All three converge on these points:

1. The callback-era LiteLLM auth model is the recurring source of runtime auth
   drift and should no longer be the preferred integration pattern.
   Evidence: [litellm/custom_callbacks.py](/home/eric/git/Subumbra/litellm/custom_callbacks.py#L71-L76), [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L98-L109), [claude-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-review.md#L15-L24), [gemini-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-review.md#L10-L14), [codex-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-review.md#L19-L25)

2. The right direction is to route LiteLLM through `subumbra-proxy` using the
   transparent sidecar path, with `subumbra-proxy` owning record fetch, HMAC,
   and Worker forwarding.
   Evidence: [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L135-L179), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L266-L315), [claude-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-review.md#L117-L143), [gemini-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-review.md#L19-L21), [codex-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-review.md#L19-L25)

3. Gemini’s provider-prefixed `api_base` format is wrong for the current `/t/{path}`
   route. The sidecar base must be `/t`, not `/t/<provider_prefix>/`.
   Evidence: [gemini-proposal.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-proposal.md#L49-L52), [gemini-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-proposal-2.md#L31-L34), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L182-L190), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L266-L315), [claude-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-proposal-2.md#L82-L106)

4. `subumbra-proxy` must be scoped to the key IDs LiteLLM will use, or the
   migrated flow will fail at `subumbra-keys` with `403 key_scope_denied`.
   Evidence: [subumbra-keys/app.py](/home/eric/git/Subumbra/subumbra-keys/app.py#L546-L561), [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L574-L625), [claude-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-review.md#L31-L68), [gemini-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-review.md#L28-L31), [codex-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-review.md#L23-L31)

5. `DEEPSEEK_API_BASE` should be removed from the LiteLLM environment block.
   The evidence now shows it does not override a model-level `api_base`, but it
   remains misleading legacy config once the sidecar becomes the authority.
   Evidence: [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L98-L110), `/app/litellm/llms/deepseek/chat/transformation.py:97-125` as captured in [codex-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-investigation.md#L109-L149), [claude-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-investigation.md#L92-L120), [gemini-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-investigation.md#L19-L28)

6. `custom_callbacks.py` should become legacy-labeled, not deleted, and the
   LiteLLM drift check in `post-bootstrap.sh` should be removed once LiteLLM no
   longer carries Subumbra auth material.
   Evidence: [post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L89-L107), [claude-proposal.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-proposal.md#L223-L237), [gemini-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-review.md#L35-L39), [codex-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-proposal-2.md#L149-L170)

7. Minimal logging only: no new secret-bearing diagnostics. The only new round-specific
   visibility worth adding is clearer operator distinction for proxy-side `403 key_scope_denied`
   versus generic record-fetch failure.
   Evidence: [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L98-L101), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L298-L305), [codex-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-review.md#L128-L131)

## 2. Disagreements

### Disagreement A — Does Anthropic remain in Round 42.2 scope?

Claude’s claim:
- Anthropic can stay in scope if `api_base` is set to `http://subumbra-proxy:8090/t`.
- In investigation, Claude concludes LiteLLM’s Anthropic provider honors
  `api_base`, appends `/v1/messages`, and therefore works through the transparent sidecar.
  [claude-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-investigation.md#L26-L87)

Gemini’s claim:
- Anthropic is compatible because proxy/Worker preserve headers and body shape,
  so no translation layer is needed.
  [gemini-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-review.md#L23-L27), [gemini-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-investigation.md#L9-L17)

My earlier claim:
- Anthropic should not be treated as automatically in scope without direct proof
  that LiteLLM honors `api_base` for the `anthropic/...` provider path.
  [codex-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-proposal-2.md#L77-L100)

My position now:
- Anthropic is **technically in scope**.

Reasoning:
- direct LiteLLM package evidence shows Anthropic’s code prefers a passed
  `api_base` over env/defaults and carries it into the actual request path.
  `/app/litellm/llms/anthropic/common_utils.py:546-555` and
  `/app/litellm/llms/anthropic/chat/handler.py:193-230, 466-470`, as recorded in
  [codex-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-investigation.md#L37-L95)
- the sidecar rebuilds the upstream URL correctly from `target_host` plus
  `/v1/messages`.
  [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L182-L190), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L266-L315)

So this disagreement is now resolved by evidence, not by preference.

### Disagreement B — Must bootstrap code automatically merge LiteLLM scope into proxy scope?

Gemini’s proposal:
- modify bootstrap so the proxy automatically inherits LiteLLM’s key pool.
  [gemini-proposal.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-proposal.md#L45-L47), [gemini-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-proposal-2.md#L35-L37)

Claude’s claim:
- do not change bootstrap logic; per-adapter scope is a security property.
- instead, make `PROXY_ALLOWED_KEYS` an operational prerequisite and fix prompts/docs.
  [claude-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-proposal-2.md#L61-L80), [claude-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-investigation.md#L166-L177)

My claim:
- bootstrap/doc truth updates are required for correctness, but auto-merge is
  not technically necessary.
  [codex-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-review.md#L23-L31), [codex-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-investigation.md#L227-L268)

My position:
- bootstrap auto-merge is **not a technical requirement** for 42.2.
- prompt/doc truth updates **are** a technical requirement for correctness.

Reasoning:
- current bootstrap can already generate a correct registry if the operator
  scopes the same key IDs to `subumbra-proxy`.
  [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L560-L625), [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L632-L644)
- what is broken is that the official prompts/docs still teach the wrong scope
  model for a post-42.2 LiteLLM deployment.
  [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L648-L664), [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1045-L1056), [README.md](/home/eric/git/Subumbra/README.md#L221-L226)

So this is no longer a technical blocker; it is a product choice.

### Disagreement C — Is bootstrap/install doc work in scope, or just optional polish?

Claude’s claim:
- it is required in-scope work, because otherwise fresh installs can still be
  configured into `403 key_scope_denied`.
  [claude-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-investigation.md#L127-L251)

Gemini’s claim:
- key-scope expansion is treated more as operational prep than as a required doc/prompt rewrite.
  [gemini-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-review.md#L28-L31), [gemini-review-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-review-2.md#L47-L49)

My claim:
- doc/prompt updates are required for correctness if LiteLLM is moved behind the proxy.
  [codex-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-review.md#L23-L31), [codex-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-investigation.md#L152-L225)

My position:
- Claude and I are right here: this is **required round scope**, not optional polish.

Reasoning:
- the wizard, alignment hints, README adapter scopes, README LiteLLM model section,
  and `docs/subumbra-install.md` still teach callback-era behavior.
  [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L648-L664), [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1045-L1056), [README.md](/home/eric/git/Subumbra/README.md#L221-L226), [README.md](/home/eric/git/Subumbra/README.md#L395-L426), [docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L152-L170)
- `subumbra-keys` enforces per-adapter scope, so bad prompt guidance is not
  harmless; it can directly produce a broken fresh install.
  [subumbra-keys/app.py](/home/eric/git/Subumbra/subumbra-keys/app.py#L546-L561)

### Disagreement D — What doc target actually exists?

Claude/Gemini claim:
- `docs/standalone-litellm.md` does not exist now, so it should not be named as
  the update target.
  [claude-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-proposal-2.md#L108-L118), [gemini-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-proposal-2.md#L25-L27)

My earlier claim:
- I treated `docs/standalone-litellm.md` as existing based on earlier context.
  [codex-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-proposal-2.md#L102-L109)

My position:
- the file is absent in this checkout, and the real operator docs that must be
  updated are `README.md` and `docs/subumbra-install.md`.

Reasoning:
- `ls docs` confirms the file is absent.
  [codex-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-review.md#L46-L64)
- the callback-era instructions that actually matter are currently in
  `README.md` and `docs/subumbra-install.md`.
  [README.md](/home/eric/git/Subumbra/README.md#L395-L426), [docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L152-L170)

So this disagreement is resolved; the approved plan should target the docs that
exist now.

## 3. What The Other Two Missed

What Claude missed:
- his early framing treated bootstrap scope/doc updates mostly as supporting
  changes until the investigation pass tightened them; my review pushed the
  fresh-install failure mode earlier and more explicitly.
  [codex-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-review.md#L23-L31)

What Gemini missed:
- Gemini correctly identified the key-scope barrier and path collision, but
  underweighted the importance of updating the official bootstrap/install truth.
  That is not just operator preference.
  [gemini-review.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-review.md#L28-L39)

What I missed earlier:
- I was too cautious about Anthropic scope before we had direct LiteLLM package
  evidence. The investigation closed that gap.
  [codex-investigation.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-investigation.md#L37-L95)

## 4. Phased Plan

### What to do now

1. Approve the decoupling mechanism:
   - `litellm/config.yaml`: plain `api_key: <key_id>`, `api_base: http://subumbra-proxy:8090/t`
   - remove callback stanza
   - keep `custom_callbacks.py` as legacy-labeled only

2. Remove callback-era LiteLLM env ownership:
   - strip `SUBUMBRA_ACCESS_TOKEN`, `SUBUMBRA_HMAC_KEY`, `SUBUMBRA_KEYS_URL`,
     `CF_WORKER_URL`, CF Access vars, and `DEEPSEEK_API_BASE` from the LiteLLM
     environment block

3. Make correctness updates to bootstrap/operator truth:
   - bootstrap Step 3 wording
   - `_build_litellm_alignment_lines()`
   - README adapter-scope section
   - README LiteLLM model section
   - `docs/subumbra-install.md` Section 7

4. Keep the explicit prerequisite check:
   - verify `PROXY_ALLOWED_KEYS` includes the LiteLLM key IDs before live verification

5. Verification should prove at least:
   - Anthropic
   - OpenAI
   - Groq
   - DeepSeek
   - Mistral
   through the migrated LiteLLM path

### What needs more investigation or a future round

1. Automatic bootstrap scope merging (`litellm` → `subumbra-proxy`)
   - not technically required for 42.2
   - could be a later UX simplification round

2. App env/config import and “swap & shred”
   - useful future usability work
   - not part of 42.2’s technical core

3. Containerizing `post-bootstrap.sh`
   - future deployment/process redesign
   - not needed to close 42.2

## 5. Consensus Status

There **is** clear enough consensus for a path forward now.

The settled technical core is:
- decouple LiteLLM from Subumbra auth state
- use `subumbra-proxy` as the authority surface
- use `/t` as the sidecar base
- keep Anthropic in scope
- remove `DEEPSEEK_API_BASE`
- require bootstrap/operator truth updates for correctness

The only remaining non-consensus item is whether bootstrap should automatically
merge proxy scope with LiteLLM scope. That is no longer a technical blocker; it
is a product/UX choice and can be deferred without weakening the round.
