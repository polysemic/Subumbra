# Council Memory

*Shared workflow memory for fresh council sessions. This file captures how the
three-amigos process works in practice, beyond the raw prompt templates.*

This is not a replacement for `council/COUNCIL.md` or `council/COUNCIL_PROMPT_v2.md`.
It exists to preserve the working habits that long-running chats tend to learn
and fresh sessions tend to miss.

---

## 1. What The Council Is Optimizing For

- independent evidence gathering first
- cross-checking before implementation
- narrow, implementable approved plans
- explicit deferrals instead of silent scope creep
- truthful separation between what is proven, what is inferred, and what is
  still a design choice

---

## 2. Practical Round Discipline

- Early rounds are often muddy. That is normal.
- Proposal phase should clarify the real disagreement before anyone starts
  acting like details are settled.
- Reviews should be evidence-heavy and cite exact file/line references.
- Approved plans should be specific enough that any of the three models could
  implement them without guessing.
- App-validation rounds that produce `docs/apps/*/` deliverables should stage
  round-local docs under `council/{round}/docs/`; those staged docs are the
  operator baseline under test, `CORRECTIONS.md` records in-round fixes, and
  promotion into tracked `docs/apps/` happens only after verification confirms
  the staged docs match the proven flow.
- Round-local `council/{round}/verify-round.sh` hooks stay local-only under the
  ignored `council/` tree; when VPS proof needs them, copy them into the VPS
  checkout or staging path and record that transfer in the report.
- **R61 hook:** the round-local hook asserts no `bootstrap-checkpoint.json` in
  `subumbra-keys`, scans `/app/data` for a fixed canary substring (must be
  absent), curls proxy `/health`, and statically checks `run_rotate_wizard` for
  checkpoint symbols — it does not run bootstrap or compose bring-up.
  **Close-out (2026-05-12):** Scenario B must capture **stdout only** from the
  canary `docker compose exec` (do not merge Compose stderr into
  `canary-grep.txt` — avoids false FAIL on `SUBUMBRA_TOKEN_PROBE` warnings).
  Official `fresh-install` PASS: `codex-vps-20260512T174950Z`.
- Treat `/opt/subumbra` as the canonical operator checkout during VPS work.
  Bundle/staging fallback is exception-only and must use a one-off `~/`
  sibling path without deleting, replacing, emptying, or repurposing
  `/opt/subumbra`.
- `existing-stack` verification must not shut down the live `/opt/subumbra`
  Docker stack. `fresh-install` teardown must stay scoped to the isolated proof
  workspace and its scoped Compose project only.
- When proof wrappers run over `ssh ... bash -s`, any non-interactive
  `docker compose run` step should redirect stdin away from the SSH script
  stream unless interactive input is intentionally required.
- **R51 `verify-round.sh` / S1:** A green S1 line requires a template-backed key
  in the manifest under test plus `VERIFY_TEMPLATE_KEY_ID`,
  `VERIFY_ADAPTER_TOKEN`, and `VERIFY_KEYS_JSON` (per the round hook). Fresh-install
  proofs that only use inline `policy` keys may classify S1 as harness/environmental
  while still closing the round on PASS for structural scenarios.
- Verification should be treated like a normal operator path first: if the
  documented install/bootstrap/update flow breaks, report that product-facing
  failure and stop instead of inventing expert-only workarounds.
- If a proof failure is clearly harness-only, stop once the pattern is clear
  and classify it as a harness issue instead of burning repeated retries on the
  same tooling gap.
- **R58 `verify-round.sh` V2:** For UI `/api/status`, expect **401** when the
  `subumbra-ui` container has non-empty `UI_USERNAME` (Basic Auth enabled) and
  **200** when Basic Auth is not configured — matches product and avoids false
  FAIL on open local dashboards.
- **R60 — harness probes:** (1) Round-local `verify-round.sh` hooks must not run
  `curl` inside `subumbra-ui`; read secrets with `docker compose exec -T` and
  call **host** `curl`, then `unset` credential shell variables. (2) When
  **`SUBUMBRA_UI_CONTAINER`** is set (isolated fresh-install / `vps-proof-run`
  temp workspace), **do not** probe the dashboard via host-published
  `127.0.0.1:6563`; `preflight.sh` uses Docker health for the prefixed UI
  container, and   **`verify.sh` P9.5** records **SKIP** with reason
  `isolated-mode-no-host-port`. For **nested** self-tests (e.g. `verify-round.sh`
  invoking `verify.sh` for the same round), set **`VERIFY_SKIP_ROUND_HOOK=1`**
  so round hooks are not re-entered.
- **R63 `verify-round.sh`:** Proves `/stats` and `/keys` stability (two rapid fetches; `jq` compact `per_key` match), proxy `/health`, optional bad-token 401 on `/stats`, and writes artifacts under `${VERIFY_ARTIFACT_DIR}/` (`r63-stats-double.json`, `r63-keys-double.json`, `r63-proxy-health.json`, `r63-verify-exit.txt`). Uses `docker compose exec -T subumbra-ui` + in-container `urllib` to `http://subumbra-keys:9090` with `SUBUMBRA_TOKEN_UI` / `VERIFY_UI_TOKEN` from the host environment (R60 hygiene: no curl inside UI for secrets). Approved plan: `council/approved/r63-observability-consistency.md`.
- **R63 close-out (2026-05-12):** Round **CLOSED**; archive `council/closed/r63-observability-consistency/`. Official VPS `existing-stack` PASS runs: `claude-vps-20260512T233235Z`, `gemini-vps-20260512T234111Z` (implementation `0d403ef`). **`codex-verification.md` not filed** — process gap only; Claude + Gemini both PASS. Claude noted **HARNESS_ISSUE:** first proof without `--build` can hit stale images; use `--build subumbra-keys` (and affected services) for code-change rounds.
- **R64 `verify-round.sh`:** Proves `GET /audit` (base + `key_id` + `verdict=deny` + invalid `verdict` → 400), host `GET /api/status` (`worker_reachable` + `worker_auth`; conditional `stale` → `worker_error` contains `stale`), proxy `/health` includes `worker_auth`, and **`subumbra-keys` logs** (tail) contain **no** `Control server error` after the R64 image (`--no-control-socket`). Uses **`SUBUMBRA_ACCESS_TOKEN` inside `subumbra-ui`** for keys requests (matches dashboard; avoids stale host `.env` drift). Approved plan: `council/approved/r64-launch-polish.md`.

---

## 3. When To Use Follow-Up Round Tools

### Proposal-2

Use when initial proposals diverge but the disagreement is still mostly about
scope or framing.

### Review-2 / Evidence-Based Review

Use when the discussion needs stronger direct evidence before synthesis can be
useful.

### Secondary Synthesis

Use when the disagreement is already narrow and the goal is to resolve only the
remaining blocking issues.

### Investigation

Use when the blocking issue is genuinely unclear in the code/runtime and needs
deeper proof instead of more opinion.

---

## 4. Repeated Failure Modes In Council Work

- treating shorthand status text as if it were a final approved spec
- allowing late-raised side issues to derail a round that already has a narrow
  core objective
- mixing roadmap decisions with implementation decisions
- broadening a round to solve future architecture questions prematurely
- confusing “helpful future direction” with “in-scope for this round”

---

## 5. Review Tone And Scope Habits

- prefer pattern-level conclusions over vague architectural taste
- keep operator/logging additions minimal and security-conscious
- say “evidence missing” instead of filling in gaps with confident guesses
- if a design decision belongs in a future round, say so explicitly
- if something does not materially block approval, say so explicitly

---

## 6. Fresh Session Anchors

For a new council session, the shortest reliable reset path is usually:

1. `council/COUNCIL.md`
2. `council/COUNCIL_PROMPT_v2.md`
3. `PROJECT_STATUS.md`
4. `CLAUDE.md`
5. this file
6. `docs/project-memory.md`
7. the active round folder

If a round references a roadmap or approved plan, read that before trusting
summary wording elsewhere.

---

## 7. Memory Update Rule

At closeout, ask:

- Did this round change something a fresh session would likely misread?
- Did it change the workflow, deployment reality, or project invariants?

If yes:

- update `docs/project-memory.md` and/or `docs/council-memory.md`

If no:

- leave them unchanged

These files should stay lean. If they become a second archive, they lose their
value.
