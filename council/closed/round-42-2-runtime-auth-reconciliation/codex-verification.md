# Round 42.2 Verification — Codex

Date: 2026-04-19
Round: `round-42-2-runtime-auth-reconciliation`
Plan: [runtime-auth-reconciliation-v2.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation-v2.md)
Branch under test: `round-42-2-runtime-auth-reconciliation`
Local commit: `a879eee`
VPS path under test: `/opt/subumbra`
VPS commit: `a879eee`
VPS worktree note: untracked `temp/` present

## Result

**FAIL**

The source-file implementation matches the approved v2 plan, but the round does
not yet produce a closure-grade live proof on the current VPS automation inputs.
The blocking issue is that the bootstrap/env fallback path still seeds
`PROXY_ALLOWED_KEYS` with the old GitHub/Slack/SendGrid-only scope, so the
required V3 prerequisite fails before V2 LiteLLM provider proof can honestly
pass.

## Findings Table

| Finding | Severity | Evidence |
|---|---|---|
| Source-file implementation matches the approved v2 spec across the listed files | Pass | [litellm/config.yaml](/home/eric/git/Subumbra/litellm/config.yaml#L1-L143), [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L90-L106), [litellm/custom_callbacks.py](/home/eric/git/Subumbra/litellm/custom_callbacks.py#L1-L14), [post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L89-L106), [subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L647-L681), [subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1063-L1067), [README.md](/home/eric/git/Subumbra/README.md#L221-L227), [README.md](/home/eric/git/Subumbra/README.md#L394-L419), [subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L152-L175) |
| V3 prerequisite fails on the actual VPS bootstrap inputs because `subumbra-proxy` scope remains limited to `github_prod,slack_prod,sendgrid_prod` | Fail | [runtime-auth-reconciliation-v2.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation-v2.md#L55-L85), [runtime-auth-reconciliation-v2.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation-v2.md#L663-L681), [subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L892-L899), [subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1717-L1724) |
| Live sidecar probe for `openai_prod` still fails at record fetch, which is consistent with missing proxy scope rather than provider/runtime transport failure | Fail | [subumbra-keys/app.py](/home/eric/git/Subumbra/subumbra-keys/app.py#L546-L561), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L298-L305) |

## Detailed Analysis

### 1. File-level implementation matches the approved plan

The branch contains the exact structural changes the v2 plan called for:

- `litellm/config.yaml` uses `api_base: http://subumbra-proxy:8090/t`, plain
  `api_key: <key_id>`, no `callbacks:` stanza, and the Gemini model is
  commented out as deferred.
  [litellm/config.yaml](/home/eric/git/Subumbra/litellm/config.yaml#L1-L143)
- `docker-compose.yml` removes LiteLLM-side Subumbra auth material and adds
  `subumbra-proxy: service_healthy` to `litellm.depends_on`.
  [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L98-L106)
- `custom_callbacks.py` is retained with a legacy header only.
  [litellm/custom_callbacks.py](/home/eric/git/Subumbra/litellm/custom_callbacks.py#L1-L14)
- `post-bootstrap.sh` no longer checks `litellm` for stale
  `SUBUMBRA_ACCESS_TOKEN`.
  [post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L89-L106)
- `bootstrap/subumbra-bootstrap.py` now teaches the proxy-routing model in both
  `_build_litellm_alignment_lines()` and the Step 3 wizard text.
  [subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L647-L681)
  [subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1063-L1067)
- `README.md` and `docs/subumbra-install.md` now describe the proxy-routing
  contract instead of the callback-era `subumbra:` prefix pattern.
  [README.md](/home/eric/git/Subumbra/README.md#L221-L227)
  [README.md](/home/eric/git/Subumbra/README.md#L394-L419)
  [subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L152-L175)

So this is not a “files didn’t change” failure.

### 2. The approved V3 prerequisite fails on the actual VPS bootstrap inputs

The approved plan requires the verifier to confirm that `PROXY_ALLOWED_KEYS`
covers the active LiteLLM model key_ids before running the V2 live test.
[runtime-auth-reconciliation-v2.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation-v2.md#L55-L85)
[runtime-auth-reconciliation-v2.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation-v2.md#L663-L681)

Bootstrap automation still loads adapter scopes directly from env input:
[subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L892-L899)

And bootstrap writes those scopes back into runtime `.env` exactly as generated:
[subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1717-L1724)

Important command output from the VPS:

```text
.env.bootstrap
LITELLM_ALLOWED_KEYS=anthropic_prod,openai_prod,groq_prod,deepseek_prod,cerebras_prod,gemini_prod,mistral_prod,openrouter_prod,together_prod,xai_prod
PROXY_ALLOWED_KEYS=github_prod,slack_prod,sendgrid_prod

.env.bootstrap_bak
LITELLM_ALLOWED_KEYS=anthropic_prod,openai_prod,groq_prod,deepseek_prod,cerebras_prod,gemini_prod,mistral_prod,openrouter_prod,together_prod,xai_prod
PROXY_ALLOWED_KEYS=github_prod,slack_prod,sendgrid_prod

.env
LITELLM_ALLOWED_KEYS=anthropic_prod,openai_prod,groq_prod,deepseek_prod,cerebras_prod,gemini_prod,mistral_prod,openrouter_prod,together_prod,xai_prod
PROXY_ALLOWED_KEYS=github_prod,slack_prod,sendgrid_prod
```

And the approved V3 scope check on the VPS produced:

```text
subumbra-proxy allowed_keys: ['github_prod', 'slack_prod', 'sendgrid_prod']
```

That fails the plan’s own prerequisite because the active LiteLLM config now
references provider key_ids such as `anthropic_prod`, `openai_prod`,
`groq_prod`, `deepseek_prod`, `mistral_prod`, and others.
[litellm/config.yaml](/home/eric/git/Subumbra/litellm/config.yaml#L20-L128)

### 3. Live sidecar proof still fails at record fetch for LiteLLM provider keys

I refreshed the VPS stack with the approved fallback path:

```text
./scripts/council/reset.sh --build bootstrap
```

Important output:

```text
NOTICE: restored .env.bootstrap from .env.bootstrap_bak
...
Successfully tagged subumbra-bootstrap:latest
...
No token drift detected.
```

That rebuilt the changed bootstrap image and recreated the running services, but
it did not alter the stale proxy scope because the current bootstrap inputs still
encode the old values.

I then ran a direct sidecar probe on the VPS with the current
`SUBUMBRA_TOKEN_PROXY` and an allowed-by-config-but-not-by-proxy key id:

```text
502
---BODY---
{"detail":"subumbra record fetch failed: status 403"}
```

That behavior matches the current enforcement path:

- `subumbra-keys` denies record fetches for adapters whose `allowed_keys` do not
  include the requested `key_id`.
  [subumbra-keys/app.py](/home/eric/git/Subumbra/subumbra-keys/app.py#L546-L561)
- `subumbra-proxy` surfaces record-fetch failures as `502` with the underlying
  status embedded in the detail.
  [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L298-L305)

So the live blocker is still scope, not provider auth, not sidecar routing, and
not the old callback/HMAC drift failure.

### 4. Clean-run and official proof capture notes

The preferred clean-run path was attempted first on the VPS:

```text
[clean-run 18:08:24] ERROR: normal stack already running
```

That made the clean-run lane impractical in this verification attempt, so I used
the policy-approved fallback:

```text
./scripts/council/reset.sh --build bootstrap
AGENT=codex ./scripts/council/verify.sh round-42-2-runtime-auth-reconciliation
```

Official run artifacts were copied back locally:

- [clean-run-20260419T180824](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/runs/clean-run-20260419T180824)
- [codex-20260419T180954](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/runs/codex-20260419T180954)

The fallback `verify.sh` artifact is valid as official proof capture, but for
this round it only exercised the shared baseline checks and reported:

```text
P9.5 UI status: PASS
P9.6 Worker invalid token: PASS
overall: PASS
```

That does not override the round-specific manual V1/V3/V2 evidence above. The
approved plan explicitly requires the V3 scope check and V2 live proof for this
round.
[runtime-auth-reconciliation-v2.md](/home/eric/git/Subumbra/council/approved/runtime-auth-reconciliation-v2.md#L630-L681)

## Recommendations

1. Treat this as a real verification failure, not a transient VPS glitch.
2. Decide whether Round 42.2 must also update the automation/bootstrap input
   truth for `PROXY_ALLOWED_KEYS`, because the current approved implementation
   leaves the standard automation path pointed at the old proxy scope.
3. After that is resolved, rerun V3 and then rerun the live provider proof path
   required by the approved plan.
