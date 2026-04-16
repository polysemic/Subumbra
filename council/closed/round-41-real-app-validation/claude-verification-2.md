# Claude Verification 2 — Round 41: Real App Validation

## Environment

- Branch: `round-41-real-app-validation`
- Commit tested: `98f4206` (verification fixes on top of `e10d28e` implementation)
- VPS path: `/opt/subumbra`
- VPS checkout: clean at `98f4206` (stashed a local `post-bootstrap.sh` drift, fast-forward pull)
- Verification date: 2026-04-16

## Scope

This pass had two goals:

1. Read the updated workflow docs and assess the new Lane A/B/C structure
2. Reproduce Codex's failures, identify root causes, fix them, and rerun to PASS

---

## Workflow Docs Assessment

Docs reviewed: [docs/subumbra-developer.md](docs/subumbra-developer.md),
[docs/subumbra-testing.md](docs/subumbra-testing.md).

**What's working well:**

- Lane A/B/C distinction is clear and practically useful. The "don't pay the
  full clean-run cost for every small edit" guidance is the right default.
- Round-local hook (`verify-round.sh`) is a clean pattern — keeps round-specific
  checks out of the shared harness.
- Artifact fetch-back guidance in §2 is now explicit enough to follow without
  guessing where proof lives.
- `--bootstrap-overlay` mechanism is exactly right for round-specific bootstrap
  config that can't live in `.env.bootstrap_bak`.

**Gaps found and fixed (see §Fixes below):**

- Local `clean-run.sh` precondition (stack must be down) wasn't stated near the
  command. Added a callout box.
- `--build` flag requirement for stale images wasn't documented. Added.
- Failing verify still produces a run folder — wasn't documented. Added.

---

## Codex Failure Reproduction

### Failure 1 — r41-3 transparent proxy: 502 / 403

**Root cause:** `.env.bootstrap_bak` has `PROXY_ALLOWED_KEYS=` (empty, matching
`.env.bootstrap.example` default). In automation bootstrap mode, empty
`PROXY_ALLOWED_KEYS` gives the proxy zero key scope. `subumbra-keys` returns
`403 key_scope_denied` on any key fetch. The transparent proxy returns `502`.

**Fix:** Created `council/round-41-real-app-validation/bootstrap-overlay.env`
with `PROXY_ALLOWED_KEYS=openai_prod`. Passed with `--bootstrap-overlay` flag.

**Confirmed:** r41-3 artifact now shows HTTP 200 with a real OpenAI response.
See: [runs/claude-20260416T183754/r41-3-transparent-proxy-direct.txt](council/round-41-real-app-validation/runs/claude-20260416T183754/r41-3-transparent-proxy-direct.txt)

### Failure 2 — P9.5 UI status: forge_healthy vs subumbra_keys_healthy

**Root cause:** The VPS `subumbra-ui` image was built before Round 41.4
(full rebrand), which renamed `forge_healthy` → `subumbra_keys_healthy` in
`ui/app.py`. `verify.sh` correctly checks for `subumbra_keys_healthy`. The
stale image returned `forge_healthy`. Clean-run does not rebuild images unless
`--build` is passed.

**Fix:** Added `--build subumbra-ui` (and `--build bootstrap` for the same
reason) to the clean-run command. The workspace's current `ui/app.py` was built
into a fresh image. `subumbra_keys_healthy` is now present in the response.

**Confirmed:** P9.5 PASS in summary. UI response contains `subumbra_keys_healthy`.
See: [runs/claude-20260416T183754/p9-5-ui-status.txt](council/round-41-real-app-validation/runs/claude-20260416T183754/p9-5-ui-status.txt)

### Failure 3 — verify_run_id: null in result.json

**Root cause:** `export_round_runs_if_present` (called by the cleanup trap) copied
run folders but did not set `verify_run_id`. Only `copy_proof_artifacts` (called
on the happy path) set it. When verify failed and execution went to the trap,
`result.json` was written with `"verify_run_id": null` even though the run folder
existed.

**Fix:** Extended `export_round_runs_if_present` in `scripts/council/clean-run.sh`
to resolve `verify_run_id` from the workspace run folders after copying, same
logic as `copy_proof_artifacts`. Overall stays FAIL (correct); the run ID is now
captured.

**Confirmed:** `result.json` in this passing run now shows correct `verify_run_id`:
```json
{
  "clean_run_id": "clean-run-20260416T183708",
  "overall": "PASS",
  "verify_run_id": "claude-20260416T183754",
  "failed_step": null
}
```
See: [runs/clean-run-20260416T183708/result.json](council/round-41-real-app-validation/runs/clean-run-20260416T183708/result.json)

---

## Clean-Run Command Used

```bash
./scripts/council/clean-run.sh \
  --round round-41-real-app-validation \
  --agent claude \
  --build bootstrap subumbra-ui \
  --bootstrap-overlay council/round-41-real-app-validation/bootstrap-overlay.env
```

Note for future verifiers: `--build bootstrap subumbra-ui` is required until
these images are rebuilt from the updated source. Once the VPS images are current
(i.e., after a full rebuild from main), `--build` can be omitted.

---

## Proof Artifacts

| File | Status |
|------|--------|
| [runs/claude-20260416T183754/summary.txt](council/round-41-real-app-validation/runs/claude-20260416T183754/summary.txt) | overall: PASS |
| [runs/claude-20260416T183754/r41-1-subumbra-net-membership.txt](council/round-41-real-app-validation/runs/claude-20260416T183754/r41-1-subumbra-net-membership.txt) | subumbra-proxy on net, subumbra-keys absent |
| [runs/claude-20260416T183754/r41-2-bundled-litellm-absent.txt](council/round-41-real-app-validation/runs/claude-20260416T183754/r41-2-bundled-litellm-absent.txt) | litellm not running |
| [runs/claude-20260416T183754/r41-3-transparent-proxy-direct.txt](council/round-41-real-app-validation/runs/claude-20260416T183754/r41-3-transparent-proxy-direct.txt) | HTTP 200, real OpenAI response |
| [runs/claude-20260416T183754/p9-5-ui-status.txt](council/round-41-real-app-validation/runs/claude-20260416T183754/p9-5-ui-status.txt) | HTTP 200, subumbra_keys_healthy present |
| [runs/claude-20260416T183754/p9-6-worker-invalid-token.txt](council/round-41-real-app-validation/runs/claude-20260416T183754/p9-6-worker-invalid-token.txt) | Worker rejects bad token |
| [runs/clean-run-20260416T183708/result.json](council/round-41-real-app-validation/runs/clean-run-20260416T183708/result.json) | overall: PASS, verify_run_id: set |

---

## Summary

| Check | Status | Notes |
|-------|--------|-------|
| P9.5 UI status | **PASS** | subumbra_keys_healthy present (image rebuilt) |
| P9.6 Worker invalid token | **PASS** | |
| r41-1 subumbra-net membership | **PASS** | proxy present, keys absent |
| r41-2 bundled litellm absent | **PASS** | profile gate working |
| r41-3 transparent proxy direct | **PASS** | HTTP 200, real API response |
| result.json verify_run_id | **PASS** | no longer null on clean-run fail |
| Workflow docs | **Updated** | precondition, --build, failing verify notes |

**Overall: PASS**

---

## Cleanup Items

Added to `council/cleanup.md`:
- `PROJECT_STATUS.md` and `CLAUDE.md` documentation updates (truth alignment)
  deferred from Round 41 scope per Dispute 1 resolution

---

## Notes

- The VPS stash (`post-bootstrap.sh` local drift) was not applied. The stashed
  version appeared to be an older more-verbose variant predating the branch's
  version. The branch version is authoritative.
- `FORGE_TOKEN_LITELLM` in previous bootstrap logs was from the old cached
  bootstrap image. Rebuilding with `--build bootstrap` confirms new code logs
  `SUBUMBRA_TOKEN_LITELLM`. No functional impact since `post-bootstrap.sh` has
  a fallback for both names.
- The `subumbra-net` Docker network already existed on the VPS from the testbed
  setup (`docker network create subumbra-net` in `docs/testbed-install.md`).
