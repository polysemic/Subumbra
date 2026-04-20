# Round 42.2 Remediation — Codex

Date: 2026-04-19
Round: `round-42-2-runtime-auth-reconciliation`
Plan: [runtime-auth-reconciliation-v2.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation-v2.md)
Branch: `round-42-2-runtime-auth-reconciliation`
Commit under remediation: `a879eee`

## FAIL Items From Verification Reports

| FAIL item | Source | Classification | One-line reason |
|---|---|---|---|
| V3 prerequisite failed because `subumbra-proxy` scope remained limited to `github_prod,slack_prod,sendgrid_prod` | [codex-verification.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-verification.md#L17-L24) | IN-SCOPE FIX | The approved plan explicitly requires expanding `PROXY_ALLOWED_KEYS` before live proof if V3 does not include all active LiteLLM key_ids. |
| `P9.1` / `P9.2` still fail after the scope fix because `verify.sh` sends `api_key: "subumbra:<key_id>"` and expects `litellm` adapter audit events | Clean-run rerun artifacts: [summary.txt](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/runs/codex-20260419T190555/summary.txt), [p9-1-litellm-allowed.txt](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/runs/codex-20260419T190555/p9-1-litellm-allowed.txt) | OUT-OF-SCOPE / DESIGN DECISION | Fixing this would require changing the global verification harness or adding new round-specific proof logic not specified by the approved plan. |

## Exact Changes Made For IN-SCOPE FIX Items

### 1. Expand proxy scope in VPS bootstrap inputs

I updated the operator bootstrap input files on the VPS so the proxy scope
matches the active LiteLLM model key_ids required by the approved plan’s V3
prerequisite.

Changed files on VPS:

- `/opt/subumbra/.env.bootstrap`
- `/opt/subumbra/.env.bootstrap_bak`

Exact change:

- `PROXY_ALLOWED_KEYS` changed from:
  - `github_prod,slack_prod,sendgrid_prod`
- to:
  - `github_prod,slack_prod,sendgrid_prod,anthropic_prod,openai_prod,groq_prod,deepseek_prod,cerebras_prod,mistral_prod,openrouter_prod,together_prod,xai_prod`

Reference after the change:

```text
PROXY_ALLOWED_KEYS=github_prod,slack_prod,sendgrid_prod,anthropic_prod,openai_prod,groq_prod,deepseek_prod,cerebras_prod,mistral_prod,openrouter_prod,together_prod,xai_prod
```

I then re-ran fresh proof from the VPS using the approved preferred path:

```bash
./scripts/council/clean-run.sh --round round-42-2-runtime-auth-reconciliation --agent codex
```

This cleared the original scope blocker:

- `P9.3 sidecar allowed key: PASS`
- `P9.4 sidecar disallowed key: PASS`

See:
- [summary.txt](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/runs/codex-20260419T190555/summary.txt)
- [p9-3-sidecar-allowed.txt](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/runs/codex-20260419T190555/p9-3-sidecar-allowed.txt)

## New Verification Run Result

**FAIL**

Fresh-state proof after the in-scope fix:

- Clean-run wrapper: [clean-run-20260419T190511](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/runs/clean-run-20260419T190511)
- Official verify run: [codex-20260419T190555](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/runs/codex-20260419T190555)

What changed:

- The original approved-plan blocker was fixed.
- The sidecar path now proves successfully under the updated proxy scope.

What still fails:

- `P9.1 LiteLLM allowed key: FAIL`
- `P9.2 LiteLLM disallowed key: FAIL`

Why those now fail:

- `verify.sh` still builds the LiteLLM proof payload using the old callback-era
  contract:
  [verify.sh](/home/eric/git/Subumbra/scripts/council/verify.sh#L651-L672)
- It sends:
  - `api_key: "subumbra:<key_id>"`
- But Round 42.2 explicitly changed the contract to:
  - `api_key: <key_id>` with no `subumbra:` prefix
  [runtime-auth-reconciliation-v2.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation-v2.md#L96-L101)
- The same harness logic still expects `litellm` adapter audit events rather
  than `subumbra-proxy`-owned fetch behavior:
  [verify.sh](/home/eric/git/Subumbra/scripts/council/verify.sh#L735-L743)

The proof artifact shows the exact mismatch:

- [p9-1-litellm-allowed.txt](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/runs/codex-20260419T190555/p9-1-litellm-allowed.txt)

Important excerpt:

```text
http_status: 400
{"error":{"message":"... {\"detail\":\"invalid key_id\"} ..."}}
```

That is consistent with the harness sending the now-invalid `subumbra:`-prefixed
payload, not with the approved implementation failing its new contract.

## OUT-OF-SCOPE Items Deferred To Disputes

1. `scripts/council/verify.sh` still encodes the old LiteLLM callback-era proof
   contract for `P9.1` and `P9.2`.
   - This is outside the approved Round 42.2 implementation scope.
   - Fixing it would require either:
     - changing the global harness, or
     - adding a new round-local verification path
   - Neither is described in the approved v2 plan.

2. The same harness also retains older Round 34 assumptions about
   `subumbra:`-prefixed `api_key` values in static proof logic.
   [verify.sh](/home/eric/git/Subumbra/scripts/council/verify.sh#L1394-L1401)
   - This did not block the in-scope remediation itself, but it confirms that
     the remaining failure is verifier-side contract drift, not an unresolved
     approved-plan implementation miss.
