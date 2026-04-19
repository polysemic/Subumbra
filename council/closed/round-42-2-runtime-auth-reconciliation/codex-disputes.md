# Round 42.2 Disputes — Runtime Auth Reconciliation

Date: 2026-04-19
Author: Codex
Topic: Approved-plan consistency check for `council/approved/runtime-auth-reconciliation.md`

## Dispute 1 — The approved plan hardcodes a registry parsing method that is not supported by the syntheses or the source of truth

### What the disagreement is

The approved plan's V3 prerequisite command base64-decodes
`SUBUMBRA_ADAPTER_REGISTRY` before parsing JSON:

- [runtime-auth-reconciliation.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation.md#L62-L73)

But the actual bootstrap writer stores `SUBUMBRA_ADAPTER_REGISTRY` as plain JSON
in `.env`, not base64:

- [subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1717-L1724)

So the approved plan is not implementable as written for this prerequisite.

### What the three positions are

- Claude synthesis: `PROXY_ALLOWED_KEYS` verification is a required prerequisite,
  but does not endorse any base64 decoding step.
  [claude-synthesis.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-synthesis.md#L36-L44)
- Codex synthesis: verify `PROXY_ALLOWED_KEYS` before live verification, but does
  not endorse any base64 decoding step.
  [codex-synthesis.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-synthesis.md#L215-L215)
- Gemini synthesis: confirm `subumbra-proxy` has full key scope in the running
  registry, but does not endorse any base64 decoding step.
  [gemini-synthesis.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-synthesis.md#L48-L49)

### What evidence would resolve it

One of these must be made true:

1. The approved plan changes its prerequisite command to parse plain JSON from
   `SUBUMBRA_ADAPTER_REGISTRY`, matching the bootstrap output; or
2. Source evidence shows bootstrap actually writes a base64-encoded registry,
   which current code evidence does not support.

### Suggested next step

Code read only: update the approved plan so the V3 prerequisite command matches
the bootstrap writer exactly. No new implementation investigation is needed.

---

## Dispute 2 — The approved plan includes a Gemini-specific `api_base` exception that is not reflected in all three syntheses

### What the disagreement is

The approved plan instructs Gemini to use:

- `api_base: http://subumbra-proxy:8090/t/v1beta/openai`
  [runtime-auth-reconciliation.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation.md#L92-L92)
  [runtime-auth-reconciliation.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation.md#L196-L200)

But the syntheses are not aligned on that exception:

- Claude synthesis explicitly allows the Gemini-specific exception.
  [claude-synthesis.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-synthesis.md#L119-L128)
- Codex synthesis states the settled sidecar base is `/t` and does not adopt a
  special Gemini exception in the consensus section.
  [codex-synthesis.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-synthesis.md#L28-L35)
  [codex-synthesis.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-synthesis.md#L214-L223)
- Gemini synthesis states the `api_base` must be `http://subumbra-proxy:8090/t`
  with no provider prefix, and does not approve a Gemini-specific exception.
  [gemini-synthesis.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-synthesis.md#L10-L13)

So the approved plan goes beyond at least two synthesis documents while claiming
to be based on three-synthesis consensus.

### What the three positions are

- Claude: Gemini needs a specific `/t/v1beta/openai` exception.
- Codex: the consensus line is `/t` as the sidecar base; Gemini exception was
  not carried into the synthesized consensus.
- Gemini: `/t` no provider prefix is the approved contract.

### What evidence would resolve it

One of these needs to happen:

1. Human/council decision: explicitly narrow Round 42.2 to exclude Gemini from
   the approved plan and keep the universal `/t` contract only; or
2. New council consensus doc update: all three syntheses explicitly accept the
   Gemini-specific exception as a deliberate carve-out; or
3. Direct verification evidence proves the Gemini exception is required and the
   syntheses are updated to reflect that exact exception.

### Suggested next step

Human decision or synthesis-alignment update needed. The cleanest path is
either:

- remove Gemini from the approved plan for this round, or
- revise the syntheses / approved plan together so the Gemini carve-out is
  explicitly unanimous instead of implied.
