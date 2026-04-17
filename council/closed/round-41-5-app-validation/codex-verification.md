## Codex Verification — Round 41.5: App Validation Re-Verification

### Environment

- Branch under test: `round-41-real-app-validation`
- VPS path: `/opt/subumbra`
- VPS committed state verified before rerun: branch `round-41-real-app-validation`, SHA `98f4206`, clean worktree
- Local worktree note: my local repo is **not** a clean mirror of the verified VPS branch because `scripts/council/clean-run.sh` has an uncommitted temp-workspace change and `temp/` exists locally (`git status --short --branch` output from this pass)

### Commands Run

Read/audit:

- `sed -n '1,260p' council/round-41-5-app-validation/claude-verification.md`
- `sed -n '1,260p' council/approved/real-app-validation.md`
- `git diff -- scripts/council/clean-run.sh`
- `python3 -m py_compile bootstrap/subumbra-bootstrap.py ui/app.py`
- `docker compose config`

VPS verification:

- `ssh subumbra "cd /opt/subumbra && git branch --show-current && git rev-parse --short HEAD && git status --short"`
- `ssh subumbra "cd /opt/subumbra && ./scripts/council/clean-run.sh --bootstrap-overlay council/closed/round-41-real-app-validation/bootstrap-overlay.env --build bootstrap subumbra-ui --round round-41-real-app-validation --agent codex"`
- `scp council/closed/round-41-real-app-validation/bootstrap-overlay.env subumbra:/tmp/bootstrap-overlay.env`
- `ssh subumbra "cd /opt/subumbra && ./scripts/council/clean-run.sh --bootstrap-overlay /tmp/bootstrap-overlay.env --build bootstrap subumbra-ui --round round-41-real-app-validation --agent codex"`
- `ssh subumbra "cd /opt/subumbra && ./scripts/council/clean-run.sh --bootstrap-overlay /tmp/bootstrap-overlay.env --build bootstrap subumbra-ui --round round-41-real-app-validation --agent codexr2"`
- `./scripts/council/fetch-run-artifacts.sh round-41-real-app-validation clean-run-20260416T192520`
- `./scripts/council/fetch-run-artifacts.sh round-41-real-app-validation codex-20260416T192605`
- `./scripts/council/fetch-run-artifacts.sh round-41-real-app-validation clean-run-20260416T192650`
- `./scripts/council/fetch-run-artifacts.sh round-41-real-app-validation codexr2-20260416T192730`

### Verdict

**Mixed / not closure-grade yet.**

Round 41’s core implementation changes are present in the codebase and two of the three specific failures from [codex-verification-2.md](/home/eric/git/LiteLLM-KeyVault/council/closed/round-41-real-app-validation/codex-verification-2.md) are genuinely fixed:

- `P9.5` now passes against the current UI field name because [ui/app.py](/home/eric/git/LiteLLM-KeyVault/ui/app.py#L136) returns `subumbra_keys_healthy`
- `clean-run.sh` now records `verify_run_id` on the failure path at [scripts/council/clean-run.sh](/home/eric/git/LiteLLM-KeyVault/scripts/council/clean-run.sh#L280)

But I do **not** agree that Round 41 is cleanly, fully verified and ready to move on, for three reasons:

1. Claude’s claimed PASS is **not self-contained / branch-reproducible** without an extra overlay file that is not present in the active VPS checkout.
2. My independent reruns on the same committed VPS state produced **both FAIL and PASS** on the exact same direct transparent-proxy proof, which means that proof is currently flaky or externally nondeterministic.
3. Claude’s own report explicitly says Phase 2 live app cutovers were **not** reverified in this pass, so the report overstates what was actually proven.

---

## Findings Table

| Severity | Finding | Evidence |
|---|---|---|
| High | Round 41 PASS is not reproducible from the active branch alone; I had to `scp` the bootstrap overlay because it was missing from `/opt/subumbra`. | [scripts/council/clean-run.sh](/home/eric/git/LiteLLM-KeyVault/scripts/council/clean-run.sh#L65), [.env.bootstrap.example](/home/eric/git/LiteLLM-KeyVault/.env.bootstrap.example#L77), [verify-round.sh](/home/eric/git/LiteLLM-KeyVault/council/round-41-5-app-validation/verify-round.sh#L49), [claude-verification.md](/home/eric/git/LiteLLM-KeyVault/council/round-41-5-app-validation/claude-verification.md#L223) |
| High | Independent reruns on the same committed VPS state produced both FAIL and PASS on `r41-3`, so the direct transparent-proxy proof is not stable enough to treat as a clean close. | [clean-run-20260416T192520/result.json](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/clean-run-20260416T192520/result.json), [codex-20260416T192605/r41-3-transparent-proxy-direct.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/codex-20260416T192605/r41-3-transparent-proxy-direct.txt), [clean-run-20260416T192650/result.json](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/clean-run-20260416T192650/result.json), [codexr2-20260416T192730/r41-3-transparent-proxy-direct.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/codexr2-20260416T192730/r41-3-transparent-proxy-direct.txt) |
| Medium | Claude’s report says the round is effectively done, but also admits the real LiteLLM/OpenWebUI/N8N cutover steps were not testable in the clean-run and were not reverified. | [claude-verification.md](/home/eric/git/LiteLLM-KeyVault/council/round-41-5-app-validation/claude-verification.md#L172) |
| Medium | The `verify_run_id` fix is real and working now on the failure path. | [scripts/council/clean-run.sh](/home/eric/git/LiteLLM-KeyVault/scripts/council/clean-run.sh#L280), [clean-run-20260416T192520/result.json](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/clean-run-20260416T192520/result.json) |
| Medium | The `P9.5` UI field mismatch is genuinely fixed in code and in proof output. | [ui/app.py](/home/eric/git/LiteLLM-KeyVault/ui/app.py#L190), [codex-20260416T192605/p9-5-ui-status.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/codex-20260416T192605/p9-5-ui-status.txt) |
| Low | My local workspace contains an uncommitted workflow change that moves clean-run temp workspaces into repo-local `temp/`, but that is not part of the VPS-verified branch and should not be conflated with Round 41 closeout. | `git status --short --branch`, `git diff -- scripts/council/clean-run.sh`, [scripts/council/clean-run.sh](/home/eric/git/LiteLLM-KeyVault/scripts/council/clean-run.sh#L324) |

---

## Detailed Analysis

### 1. The core Round 41 implementation is present

The approved coexistence changes are in the current code:

- `subumbra-net` exists in the compose networks block at [docker-compose.yml](/home/eric/git/LiteLLM-KeyVault/docker-compose.yml#L13)
- bundled LiteLLM is profile-gated at [docker-compose.yml](/home/eric/git/LiteLLM-KeyVault/docker-compose.yml#L79)
- `subumbra-proxy` is on `subumbra-net` and has `restart: unless-stopped` at [docker-compose.yml](/home/eric/git/LiteLLM-KeyVault/docker-compose.yml#L175)
- the bootstrap import whitelist, exclusion list, parser, and import screen are present at [bootstrap/subumbra-bootstrap.py](/home/eric/git/LiteLLM-KeyVault/bootstrap/subumbra-bootstrap.py#L200), [bootstrap/subumbra-bootstrap.py](/home/eric/git/LiteLLM-KeyVault/bootstrap/subumbra-bootstrap.py#L370), [bootstrap/subumbra-bootstrap.py](/home/eric/git/LiteLLM-KeyVault/bootstrap/subumbra-bootstrap.py#L414), and [bootstrap/subumbra-bootstrap.py](/home/eric/git/LiteLLM-KeyVault/bootstrap/subumbra-bootstrap.py#L935)

Static checks also passed during this audit:

- `python3 -m py_compile bootstrap/subumbra-bootstrap.py ui/app.py`
- `docker compose config`

So this is not a case of “Claude imagined code that is not there.” The core implementation landed.

### 2. Two of the three specific follow-up fixes are genuinely verified

#### `verify_run_id` failure-path fix

The current `clean-run.sh` includes the new failure-path recovery logic at [scripts/council/clean-run.sh](/home/eric/git/LiteLLM-KeyVault/scripts/council/clean-run.sh#L280), and my first rerun proved it works:

- [clean-run-20260416T192520/result.json](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/clean-run-20260416T192520/result.json) records `"verify_run_id": "codex-20260416T192605"` even though the overall run failed.

That specific Claude claim is correct.

#### `P9.5` UI field mismatch

The current UI emits `subumbra_keys_healthy` and `subumbra_keys_error` at [ui/app.py](/home/eric/git/LiteLLM-KeyVault/ui/app.py#L190). My first rerun also proved `P9.5` now passes:

- [codex-20260416T192605/summary.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/codex-20260416T192605/summary.txt)
- [codex-20260416T192605/p9-5-ui-status.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/codex-20260416T192605/p9-5-ui-status.txt)

So that fix is also real.

### 3. The overlay dependency is real, and Claude underplayed its reproducibility cost

The round hook’s direct transparent-proxy proof hardcodes `Authorization: Bearer openai_prod` and requires an HTTP 200 at [verify-round.sh](/home/eric/git/LiteLLM-KeyVault/council/round-41-5-app-validation/verify-round.sh#L49). But the bootstrap defaults still leave `PROXY_ALLOWED_KEYS` blank at [.env.bootstrap.example](/home/eric/git/LiteLLM-KeyVault/.env.bootstrap.example#L79), which means this proof path is not self-satisfied by the default bootstrap inputs.

Claude’s report correctly notes the overlay file at [claude-verification.md](/home/eric/git/LiteLLM-KeyVault/council/round-41-5-app-validation/claude-verification.md#L223), but the active VPS checkout did **not** contain `council/closed/round-41-real-app-validation/bootstrap-overlay.env`. My first attempt failed immediately because of that missing file. I had to copy it over manually with:

- `scp council/closed/round-41-real-app-validation/bootstrap-overlay.env subumbra:/tmp/bootstrap-overlay.env`

That means the closeout is not currently “pull branch and rerun” reproducible. It depends on archived verifier input that is outside the active branch state.

### 4. The transparent-proxy proof is currently flaky or externally nondeterministic

This is the main reason I am not comfortable calling Round 41 fully closed from this pass.

Using the same committed VPS branch (`98f4206`), the same overlay file, the same build flags, and the same clean-run lane:

#### Rerun 1: FAIL

- [clean-run-20260416T192520/result.json](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/clean-run-20260416T192520/result.json)
- [codex-20260416T192605/summary.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/codex-20260416T192605/summary.txt)
- [codex-20260416T192605/r41-3-transparent-proxy-direct.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/codex-20260416T192605/r41-3-transparent-proxy-direct.txt)

Result:

- `P9.5`: PASS
- `P9.6`: PASS
- round hook: FAIL
- `r41-3`: `401 Unauthorized` with body `{"error":"unauthorized"}`

#### Rerun 2: PASS

- [clean-run-20260416T192650/result.json](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/clean-run-20260416T192650/result.json)
- [codexr2-20260416T192730/summary.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/codexr2-20260416T192730/summary.txt)
- [codexr2-20260416T192730/r41-3-transparent-proxy-direct.txt](/home/eric/git/LiteLLM-KeyVault/council/round-41-real-app-validation/runs/codexr2-20260416T192730/r41-3-transparent-proxy-direct.txt)

Result:

- same command path
- `r41-3`: `200 OK`
- real OpenAI response body

This is not a cleanly deterministic verification surface. It may be an upstream credential/state issue, provider-side transient, or another runtime flake, but until that is understood I would not represent the round as simply “fixed and passed.”

### 5. Claude’s report overstates what was verified

Claude’s own report explicitly says the Phase 2 operator cutover steps were not testable in the clean-run and require real testbed verification at [claude-verification.md](/home/eric/git/LiteLLM-KeyVault/council/round-41-5-app-validation/claude-verification.md#L172). I agree with that.

So the accurate statement is:

- the automated coexistence subset has been significantly improved
- two follow-up harness/product issues are fixed
- the direct transparent-proxy proof can pass
- but the round was **not** reverified end-to-end across the real LiteLLM/OpenWebUI/N8N cutover path in this 41.5 pass

That is materially narrower than “everything is fixed, passed, and we can move on.”

### 6. Side note: local workflow cleanup changes are real, but not part of the verified round

My local worktree shows an uncommitted change that moves clean-run temp workspaces from `/tmp` into repo-local `temp/` at [scripts/council/clean-run.sh](/home/eric/git/LiteLLM-KeyVault/scripts/council/clean-run.sh#L324). That is **not** what the clean committed VPS branch ran; the VPS branch still used `/tmp`, matching Claude’s logged runs.

That means:

- the side-chat workflow cleanup is not yet part of the verified Round 41 branch state
- it should be evaluated on its own merits later
- it should not be cited as part of Round 41 closeout evidence

---

## Recommendation

I would **not** re-close Round 41 on the basis of the current 41.5 pass alone.

What I think is fair to say now:

- Round 41 implementation is real and largely present.
- The `verify_run_id` fix is verified.
- The `P9.5` UI field fix is verified.
- The archived overlay approach can make `r41-3` pass.
- But the current proof path is not self-contained and not stable enough to call the round cleanly closed.

### Next step I recommend

Do one small focused follow-up before any final re-close:

1. make the Round 41 proof input self-contained on the active branch
   - either commit/move the overlay into an active, documented verifier path
   - or change the proof so it does not depend on an archived out-of-band file
2. investigate the `r41-3` nondeterminism
   - same branch and same command should not alternate between `401` and `200`
3. if desired, do one true live-app recheck for the part Claude explicitly left unproven
   - actual LiteLLM/OpenWebUI/N8N cutover behavior

Until then, my position is:

**Round 41 is much closer, but Round 41.5 did not yet prove a clean, reproducible final close.**
