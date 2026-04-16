# Claude Verification 2 — Round 41.5: Closure Audit

## Environment

- Branch: `round-41-real-app-validation`
- Commit audited: `a9b01bf`
- Codex artifacts referenced: `codex-20260416T192605` (FAIL), `codexr2-20260416T192730` (PASS)
- Note: Codex 41.5 run artifacts were not fetched to local repo; findings are taken from
  `codex-verification.md` as-reported, cross-checked against committed code.
- Audit date: 2026-04-16

---

## 1. Closure Verdict

**Round 41 is NOT closure-grade in its current state.**

Two specific blockers prevent an honest close:

1. **r41-3 is flaky** — Cloudflare Secrets propagation lag causes intermittent 401 on
   fresh-bootstrap runs. Same committed state, same command produced both FAIL and PASS
   for Codex in back-to-back runs. This is not a one-time environment issue; the timing
   window is structural.

2. **The proof path is not self-contained** — The clean-run requires a `bootstrap-overlay.env`
   that lives in `council/closed/` (a gitignored path). Codex could not find it on the
   VPS after a clean pull and had to `scp` it manually. A round whose full verification
   command requires manual file transfers is not reproducibly closed.

These are both narrow and fixable. Neither requires design changes or new product scope.
They are harness/proof hardening items.

---

## 2. What Is Actually Fixed

These items are verified in committed code at `a9b01bf`. Code citations are commit-stable.

### Phase 0 — docker-compose.yml (4 changes)

All four approved changes are present and correct:
- LiteLLM profile-gate: [docker-compose.yml:80-81](docker-compose.yml#L80)
- `subumbra-net` external network declaration: [docker-compose.yml:13-18](docker-compose.yml#L13)
- `subumbra-proxy` on `subumbra-net`: [docker-compose.yml:180](docker-compose.yml#L180)
- `restart: unless-stopped` on all long-running services: lines 44, 82, 126, 176

Verified by code inspection and confirmed by r41-1 (proxy on net, keys absent) and r41-2
(bundled litellm absent) PASS in both Codex reruns.

### Phase 1 — Bootstrap implementation

All approved items are present at the committed code:
- `IMPORT_PROVIDER_WHITELIST` (21 entries): [bootstrap/subumbra-bootstrap.py:200-225](bootstrap/subumbra-bootstrap.py#L200)
- `IMPORT_EXCLUSION_LIST` (10 entries): [bootstrap/subumbra-bootstrap.py:229-240](bootstrap/subumbra-bootstrap.py#L229)
- `_parse_env_file`: [bootstrap/subumbra-bootstrap.py:370-411](bootstrap/subumbra-bootstrap.py#L370)
- `_run_import_screen`: [bootstrap/subumbra-bootstrap.py:414-504](bootstrap/subumbra-bootstrap.py#L414)
- Wizard integration at Screen 2 start: [bootstrap/subumbra-bootstrap.py:935-947](bootstrap/subumbra-bootstrap.py#L935)

Codex confirmed `python3 -m py_compile bootstrap/subumbra-bootstrap.py` passes.

### Bug Fix — P9.5 UI field mismatch

`ui/app.py` returns `subumbra_keys_healthy`. `verify.sh` checks for `subumbra_keys_healthy`.
Confirmed PASS by Codex's independent rerun (both runs 1 and 2). Fixed in committed code.

### Bug Fix — verify_run_id null on failure path

`export_round_runs_if_present` in [scripts/council/clean-run.sh:283-289](scripts/council/clean-run.sh#L283)
now resolves `verify_run_id` from workspace run folders after copying.
Confirmed working by Codex rerun 1's `result.json` which correctly recorded
`"verify_run_id": "codex-20260416T192605"` even though the overall run was FAIL.

### Workflow documentation

Three precondition callout boxes added to [docs/subumbra-developer.md](docs/subumbra-developer.md)
Lane B/C section (stack-down requirement, `--build` requirement, failing verify still
produces artifacts). Present in committed code.

---

## 3. What Is Not Closure-Grade Yet

### Blocker A — r41-3 is flaky: CF Secrets propagation lag

**Evidence:** Codex ran the same clean-run command on the same VPS at `98f4206` twice.
Run 1 (`codex-20260416T192605`): r41-3 returned `401 {"error":"unauthorized"}`. Run 2
(`codexr2-20260416T192730`): r41-3 returned `200` with a real OpenAI response.

**Root cause:** The Worker authenticates inbound requests at
[worker/src/worker.js:439-444](worker/src/worker.js#L439). It validates
`X-Subumbra-Token` against `SUBUMBRA_ADAPTER_TOKENS` from CF Secrets. When clean-run
bootstraps a fresh workspace, it:
1. pushes new `SUBUMBRA_ADAPTER_TOKENS` to CF Secrets via wrangler
2. starts containers with the new `SUBUMBRA_TOKEN_PROXY`
3. immediately runs verify.sh

If the CF Worker isolate is still cached with the old `SUBUMBRA_ADAPTER_TOKENS` value
when step 3 runs, the new proxy token is not yet in the valid set → 401. The isolate
cache eventually expires and picks up the new secret, which is why rerun 2 (run after
rerun 1 had already forced the isolate to refresh) passed.

This is a timing window, not a product bug. But a timing window that produces
alternating PASS/FAIL on the same state is not closure-grade proof. A retry loop in
`verify-round.sh` would make this deterministic.

**This is not a new bug introduced by Round 41** — this timing behavior exists for any
clean-run that calls a live Cloudflare Worker endpoint immediately after a fresh
bootstrap. Round 41 exposed it because r41-3 is the first round hook to make a
live CF Worker call.

### Blocker B — Bootstrap overlay not self-contained on clean pull

**Evidence:** Codex ran:
```bash
./scripts/council/clean-run.sh \
  --bootstrap-overlay council/closed/round-41-real-app-validation/bootstrap-overlay.env \
  ...
```
and got "bootstrap overlay file not found." The file was absent from `/opt/subumbra`
after a clean pull. Codex had to `scp` it manually.

**Root cause (two layers):**

1. `council/` is in `.gitignore`. Files force-added via `git add -f` ARE tracked and
   DO update on `git pull`. However, the overlay was committed in the original round at
   `council/round-41-real-app-validation/bootstrap-overlay.env`, then **moved** to
   `council/closed/round-41-real-app-validation/bootstrap-overlay.env` by `git mv` in
   commit `e21b88d`. If the VPS was not pulled to `e21b88d` before running the 41.5
   clean-run, the file would not be at the closed path. This is a documentation/guidance
   gap: verifiers were not told to pull to the latest commit before running.

2. Even if correctly pulled, the clean-run command references an archived (`council/closed/`)
   path. Verification documentation that requires pointing at archived state is fragile.
   The overlay should live in the active round folder that the verifier is working from.

**Consequence:** The proof path for r41-3 requires:
- a `.env.bootstrap_bak` with `OPENAI_KEY_ID=openai_prod` (operator-provided, correct),
  AND
- `PROXY_ALLOWED_KEYS=openai_prod` (provided by overlay)

Both are needed. The overlay is the part that currently requires manual intervention.

### Non-blocker (but worth noting) — Phase 2 verification gap

Phase 2 (LiteLLM/OpenWebUI/N8N cutover) and Phase 3 manual proof artifacts (screenshots,
curl proofs, absence proofs) were not re-verified in the 41.5 pass. My first verification
(`claude-verification.md`) said so explicitly.

This is a real gap but its severity is lower than blockers A and B:
- The Phase 1 code (the import wizard) is in committed code and code-audited
- Phase 2 steps are documented operator actions, not code changes
- Phase 3 manual proofs require the real testbed running LiteLLM/OpenWebUI/N8N

A narrow proof — one live import wizard run producing at least one key, plus one
successful API call through that key — would close this gap without re-running the
full testbed.

### Non-blocker — IMPORT_PROVIDER_WHITELIST comment inaccuracy

[bootstrap/subumbra-bootstrap.py:216](bootstrap/subumbra-bootstrap.py#L216) reads
"7 providers have mismatched names" but the alias block contains 8 entries (including
`SENDGRID_API_KEY → sendgrid` alongside canonical `SENDGRID_KEY`). The whitelist itself
is functionally correct; only the comment count is off. Cleanup scope only.

---

## 4. What Stays In 41.5

- All verified Phase 0 and Phase 1 implementation (no changes needed, confirmed fixed)
- P9.5 UI fix (confirmed in committed code and by independent rerun)
- verify_run_id fix (confirmed in committed code and by independent rerun)
- Workflow doc updates (committed)
- This closure audit and Codex/Gemini concurrent audits
- Diagnosis of both blockers

---

## 5. What Must Move To 41.6

Narrow scope. No product redesign. No new architecture. Just two harness/proof fixes
and one minimal proof artifact.

### 5a — Fix r41-3 flakiness (required for honest close)

**Change:** Add a retry loop with inter-attempt sleeps to the transparent proxy check
in [council/round-41-5-app-validation/verify-round.sh](council/round-41-5-app-validation/verify-round.sh)
(or the 41.6 equivalent). The Worker 401 on fresh bootstrap is a propagation window,
not a permanent failure. Three to five attempts with 15-second sleeps between them is
enough to outlast any observed CF Secrets propagation lag.

Example shape:
```bash
for attempt in 1 2 3 4 5; do
    # run curl, capture http_status
    if [[ "${proxy_status:-}" == "200" ]]; then break; fi
    [[ "$attempt" -lt 5 ]] && sleep 15
done
```

This makes r41-3 deterministic without changing what is being proved.

### 5b — Make bootstrap overlay self-contained (required for honest close)

**Change:** Move `bootstrap-overlay.env` from `council/closed/round-41-real-app-validation/`
to the active round folder (`council/round-41-5-app-validation/` or a designated
`council/round-41-6-/` folder for 41.6). Update the clean-run command in verifier docs
to reference the active path.

The `--bootstrap-overlay` mechanism itself is correct and stays as-is. This is purely a
path hygiene fix so that "pull branch and run" works without manual file transfers.

### 5c — Phase 2 minimum proof (required for honest close)

**Narrow requirement:** One proof run of the bootstrap import wizard importing at least
one real provider key from a mounted `.env` file, followed by one successful API call
through the imported key. Must be committed as a proof artifact in the 41.6 round folder.

This is not a re-run of the full LiteLLM/OpenWebUI/N8N testbed. It is the minimum that
proves the Phase 1 code works end-to-end under real conditions, not just in a code
review.

---

## Round 41.6 Scope Summary

| Item | Type | Priority |
|------|------|----------|
| Retry loop in r41-3 verify hook | Harness fix | Required |
| Move bootstrap-overlay.env to active round path | Proof hygiene | Required |
| Phase 2 minimum import+call proof artifact | Proof completion | Required |
| Fix IMPORT_PROVIDER_WHITELIST comment (8 not 7) | Cleanup | Optional |

These four items represent the minimum honest close for Round 41. None of them change
the product architecture. All of them are verifiable by a single clean-run after the
fix.
