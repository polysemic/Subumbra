# Codex Verification — Round 41.6: App Validation Cleanup

## Verdict

PASS.

The approved `41.6` scope is implemented and the proof path now closes the
three carried-forward blockers from `41.5`:

1. the bootstrap overlay is self-contained in the active round
2. the `r41-3` hook is stabilized with retry and retains attempt logs
3. a live manual import-wizard proof exists and includes a successful proxy call

## What I Verified

### Spec match

- Added active-round overlay file exactly as approved:
  [council/round-41-6-app-validation-cleanup/bootstrap-overlay.env](/home/eric/git/LiteLLM-KeyVault/council/round-41-6-app-validation-cleanup/bootstrap-overlay.env)
- Added round-local retry hook:
  [council/round-41-6-app-validation-cleanup/verify-round.sh](/home/eric/git/LiteLLM-KeyVault/council/round-41-6-app-validation-cleanup/verify-round.sh)
- `r41-1` and `r41-2` remain functionally inherited from
  [council/closed/round-41-5-app-validation/verify-round.sh](/home/eric/git/LiteLLM-KeyVault/council/closed/round-41-5-app-validation/verify-round.sh)
- `r41-3` now:
  - retries up to 5 times
  - sleeps 15s between failed attempts
  - appends attempt lines into the artifact
  - writes response excerpt only on success
  Evidence:
  [council/round-41-6-app-validation-cleanup/verify-round.sh](/home/eric/git/LiteLLM-KeyVault/council/round-41-6-app-validation-cleanup/verify-round.sh)

### Official proof capture

VPS clean-run PASS:
- clean-run wrapper:
  [clean-run-20260416T214038](/home/eric/git/LiteLLM-KeyVault/council/round-41-6-app-validation-cleanup/runs/clean-run-20260416T214038)
- official verify run:
  [codex-20260416T214125](/home/eric/git/LiteLLM-KeyVault/council/round-41-6-app-validation-cleanup/runs/codex-20260416T214125)

Key proof files:
- [result.json](/home/eric/git/LiteLLM-KeyVault/council/round-41-6-app-validation-cleanup/runs/clean-run-20260416T214038/result.json)
  - `overall: PASS`
  - `verify_run_id: codex-20260416T214125`
- [summary.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-6-app-validation-cleanup/runs/codex-20260416T214125/summary.txt)
  - `Round hook status: PASS`
  - `overall: PASS`
- [r41-3-transparent-proxy-direct.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-6-app-validation-cleanup/runs/codex-20260416T214125/r41-3-transparent-proxy-direct.txt)
  - attempt log preserved
  - first attempt succeeded with `http_status: 200`
  - response excerpt is non-empty

### Manual Phase 2 proof

Live VPS transcript:
- [manual-migration-proof.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-6-app-validation-cleanup/runs/phase2-import-proof/manual-migration-proof.txt)

The transcript includes the required items:
- exact bootstrap command with `/opt/litellm:/host_litellm:ro`
- detected provider keys from `/host_litellm/.env`
- operator-assigned/default key_ids
- bootstrap completion
- post-bootstrap + `docker compose up -d --force-recreate`
- successful transparent-proxy curl with non-empty response body

## VPS Workflow Used

I used the documented fallback staging path because the VPS repo at `/opt/subumbra`
was still on:
- branch: `round-41-real-app-validation`
- SHA: `98f4206`

and did not yet contain the active `41.6` round files.

Staging steps used:
- `ssh subumbra mkdir -p /opt/subumbra/council/round-41-6-app-validation-cleanup`
- `scp council/round-41-6-app-validation-cleanup/bootstrap-overlay.env council/round-41-6-app-validation-cleanup/verify-round.sh subumbra:/opt/subumbra/council/round-41-6-app-validation-cleanup/`

Proof run used:
- `ssh subumbra "cd /opt/subumbra && ./scripts/council/clean-run.sh --round round-41-6-app-validation-cleanup --agent codex --build bootstrap subumbra-ui --bootstrap-overlay council/round-41-6-app-validation-cleanup/bootstrap-overlay.env"`

Artifact fetch-back used:
- `scp -r subumbra:/opt/subumbra/council/round-41-6-app-validation-cleanup/runs/clean-run-20260416T214038 council/round-41-6-app-validation-cleanup/runs/`
- `scp -r subumbra:/opt/subumbra/council/round-41-6-app-validation-cleanup/runs/codex-20260416T214125 council/round-41-6-app-validation-cleanup/runs/`
- `scp subumbra:/opt/subumbra/council/round-41-6-app-validation-cleanup/runs/phase2-import-proof/manual-migration-proof.txt council/round-41-6-app-validation-cleanup/runs/phase2-import-proof/`

## Notes

- No product code was changed in this round.
- I did not include the local `scripts/council/clean-run.sh` `/tmp` → `temp/`
  workspace change in this pass; it remains out of scope for `41.6`.
- The remote manual proof needed one operator-style setup step not called out in
  the approved plan text: `/opt/subumbra/.env` did not exist, so the transcript
  includes creating it from `.env.example` before `./post-bootstrap.sh`. That did
  not change the approved scope; it was just required to execute the documented
  wizard-path post-bootstrap flow on that host.
