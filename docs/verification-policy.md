# Verification Policy

This is the council-facing reference for how the existing harness is used during
implementation and verification rounds.

## Default Fresh-State Sequence

Use this sequence before claiming a full host-facing E2E result:

1. Preferred for fresh-state or certification-style proof capture:
   `./scripts/council/clean-run.sh --round <round-dir-name> --agent <llm>`
2. Direct fallback for follow-up reruns, diagnostics, or when clean-run v1 is
   impractical:
   - `./scripts/council/reset.sh`
   - `./scripts/council/reset.sh --build <services>` when image-built service
     source changed
   - `AGENT=<llm> ./scripts/council/verify.sh <round-dir-name>`
3. Run any additional manual host-facing checks that the approved plan requires.

Direct `reset.sh` may be skipped only if the running state is already known-good
and the verifier documents that reason explicitly in the verification report.

## `.env.bootstrap` And `.env.bootstrap_bak`

- If `.env.bootstrap` exists, `reset.sh` uses it as the expected fresh-state input.
- If `.env.bootstrap` is missing but `.env.bootstrap_bak` exists, `reset.sh`
  restores `.env.bootstrap` from the backup and prints a notice.
- If both are missing, fresh-state reset fails and bootstrap must be re-created
  outside the harness flow.
- `clean-run.sh` copies the host repo's `.env.bootstrap` or `.env.bootstrap_bak`
  into its temporary workspace before bootstrap runs.
- If an approved plan requires editing `.env.bootstrap` before bootstrap, make
  that edit in the host repo before starting `clean-run.sh`.

## `reset.sh` Versus `reset.sh --build`

- Use plain `reset.sh` for recreate-only cases, including token, auth, or
  bootstrap-affecting changes and cases where verifier state is uncertain.
- Use `reset.sh --build <services>` when image-built service source changed.
- For the current rebuild distinction, use the help text in `scripts/council/reset.sh`.

## Evidence Taxonomy

- `PROOF`
  Run-tagged proof artifacts created by `verify.sh`. This is official PASS evidence.
  For direct runs, cite `council/{round}/runs/{run-id}/`. For clean-run exports,
  cite `council/clean-run-harness/runs/{clean-run-id}/proof/{verify-run-id}/`.
- `DIAG`
  Diagnostic artifacts such as logs. Useful for investigation, not PASS evidence.
- Manual / report narrative
  Context, interpretation, or round-specific host-facing checks that the approved
  plan explicitly requires in addition to harness proof.

`preflight.sh` output is readiness only. Logs, manual `curl`, and `docker exec`
are diagnostic-only unless the approved plan explicitly requires an additional
host-facing manual check.

## `clean-run.sh` v1

`./scripts/council/clean-run.sh` is the preferred fresh-state wrapper for
certification-style proof capture. It executes the normal bootstrap and council
harness sequence inside a temporary workspace, using:

- `COMPOSE_PROJECT_NAME=subumbra-clean-run`
- `CF_WORKER_NAME=subumbra-clean-run`

V1 does not support parallel execution with the normal local stack. If the
named Subumbra containers are already running, it aborts instead of trying to
coexist with them.

Artifacts for each run are written under:

- `council/clean-run-harness/runs/<clean-run-id>/`
- exported proof artifacts live under
  `council/clean-run-harness/runs/<clean-run-id>/proof/<verify-run-id>/`

The fixed clean-run Cloudflare worker may persist after the run and may require
manual deletion.

## Artifact Citation Example

Prefer citing artifact paths instead of pasting long command output. Example:

`See council/round-29-adapter-identity/runs/codex-20260407T120000/summary.txt`

## What Stays Out Of Product Approved Plans

Product approved plans should define:

- what must be proven
- the proof checks and success conditions
- any round-specific prerequisites

Product approved plans should not restate:

- the full harness sequence policy
- the global PASS-versus-diagnostic taxonomy
- the general script role definitions
