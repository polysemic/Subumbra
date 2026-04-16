# Claude Verification — Round 41.5: App Validation Re-Verification

## Environment

- Branch: `round-41-real-app-validation`
- Commit tested: `98f4206` (implementation + harness fixes, on top of `e10d28e`)
- VPS path: `/opt/subumbra`
- Verification date: 2026-04-16

## Scope

This report covers my full independent verification of the Round 41 Real App
Validation implementation. It supersedes my earlier local-only `claude-verification.md`
and addresses all three failures found by Codex in `codex-verification-2.md`.

Approved plan: [`council/approved/real-app-validation.md`](council/approved/real-app-validation.md)

---

## Phase 0 — docker-compose.yml Verification

All four approved changes are present and correct.

### Change 1 — Profile-gate bundled LiteLLM

**File:** [docker-compose.yml:80-81](docker-compose.yml#L80)  
**Status:** PASS

```yaml
    profiles:
      - litellm
```

Present immediately after `restart: unless-stopped` in the `litellm` service.
The service will not start on a plain `docker compose up`. Confirmed by
r41-2 proof artifact: bundled `litellm` is absent from `docker compose ps`
output during the clean-run.

### Change 2 — subumbra-net networks block

**File:** [docker-compose.yml:13-18](docker-compose.yml#L13)  
**Status:** PASS

```yaml
  # Join pre-existing testbed network created by: docker network create subumbra-net
  # external: true is the Compose property meaning "do not create; find by name"
  subumbra-net:
    external: true
    name: subumbra-net
```

`external: true` plus `name: subumbra-net` is the correct two-property pattern
required to reference a pre-existing Docker network without Compose adding a
project-name prefix. The comment explaining the `external` naming ambiguity is
accurate and helpful.

### Change 3 — subumbra-net attached to subumbra-proxy

**File:** [docker-compose.yml:180](docker-compose.yml#L180)  
**Status:** PASS

```yaml
      - subumbra-net
```

Present in the `subumbra-proxy` networks list alongside `internal` and
`external`. No other service is attached to `subumbra-net`. Confirmed by r41-1
proof: `subumbra-proxy` is present in the `subumbra-net` network inspect;
`subumbra-keys` is absent.

### Change 4 — Restart policies

**File:** [docker-compose.yml:44,82,126,176,236](docker-compose.yml#L44)  
**Status:** PASS

`restart: unless-stopped` is present on `subumbra-keys` (line 44), `litellm`
(line 82), `subumbra-proxy` (line 126), `subumbra-ui` (line 176), and `bootstrap`
(line 236, behind its profile gate). Approved plan specified `unless-stopped`
for all long-running services. Confirmed.

---

## Phase 1 — Bootstrap Implementation Verification

### IMPORT_PROVIDER_WHITELIST

**File:** [bootstrap/subumbra-bootstrap.py:200-225](bootstrap/subumbra-bootstrap.py#L200)  
**Status:** PASS with one comment-level note

13 canonical names (lines 202–214) + 8 common-app aliases (lines 217–224) = 21
entries total. All approved entries are present. The mapping to provider_id
is correct for every entry.

**Minor comment inaccuracy (non-functional):** Line 216 reads "7 providers have
mismatched names vs. Subumbra canonical" but the alias block contains 8 aliases.
The eighth is `SENDGRID_API_KEY → sendgrid` alongside the canonical `SENDGRID_KEY`.
This is a documentation-level off-by-one in the comment only. The whitelist
itself is functionally correct.

### IMPORT_EXCLUSION_LIST

**File:** [bootstrap/subumbra-bootstrap.py:229-240](bootstrap/subumbra-bootstrap.py#L229)  
**Status:** PASS

10 entries present: `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `WEBUI_SECRET_KEY`,
`N8N_ENCRYPTION_KEY`, `DATABASE_URL`, `POSTGRES_PASSWORD`, `POSTGRES_DB`,
`REDIS_URL`, `SECRET_KEY`, `JWT_SECRET`. Matches the approved plan spec exactly.
The exclusion check at line 403 silently skips these vars without logging or
shred-queueing — correct behavior per spec.

### `_parse_env_file`

**File:** [bootstrap/subumbra-bootstrap.py:370-411](bootstrap/subumbra-bootstrap.py#L370)  
**Status:** PASS

Core behaviors verified against approved spec:

- Handles blank lines, comment lines, malformed lines (no `=`) — all skipped
- Strips single and double quotes from values (lines 398–399)
- Skips empty values after stripping (line 401)
- EXCLUSION_LIST checked before WHITELIST (lines 403–406) — correct priority order
- Duplicate env var names: last occurrence wins via dict key overwrite (line 407)
- Returns empty list on `OSError` rather than propagating — correct; zero-result
  files are excluded from shred queue per the approved spec rule at line 380

One edge case I checked: a file containing only `LITELLM_MASTER_KEY=secret`
returns `[]` (exclusion list hit, whitelist miss), so no shred offer for that
file. Correct.

### `_run_import_screen`

**File:** [bootstrap/subumbra-bootstrap.py:414-504](bootstrap/subumbra-bootstrap.py#L414)  
**Status:** PASS

- Path prompt with "Enter to skip" at line 432 — correct early-exit
- Zero-result feedback at line 439: "No recognised provider keys found" with
  clarifying note about exclusions. Does NOT add file to shred queue. Correct.
- Per-key confirmation loop at lines 466–486 with valid `KEY_ID_RE` check,
  duplicate key_id check, and cross-provider overwrite warning. Correct.
- Shred confirmation per file (line 491) is after all keys from that file are
  accepted. Correct — shred is conditional on successful key acceptance.
- Multi-file loop controlled by "Import from another file? [y/N]" at lines 442,
  454, 500. Correct.
- Return signature `(api_keys, shred_paths)` matches the call site at line 946.

One observation: when `key_id in existing_keys` and `ex_provider == provider_id`
(same provider, re-importing the same key), no warning is shown and the key is
silently overwritten. This is arguably correct (idempotent re-import of same
provider), but a future round may want to add a "already exists, overwriting"
note for clarity.

### Wizard integration

**File:** [bootstrap/subumbra-bootstrap.py:935-947](bootstrap/subumbra-bootstrap.py#L935)  
**Status:** PASS

Import screen is offered at the correct point: start of Screen 2 (Provider API
Keys), before the manual key-entry `while True` loop. The in-container path
example (`/host_litellm/.env`) and the `-v` mount reminder are present in the
printed hint at lines 940–941. Matches approved plan spec exactly.

### Shred execution

**File:** [bootstrap/subumbra-bootstrap.py:1770-1776](bootstrap/subumbra-bootstrap.py#L1770)  
**Status:** Not directly tested (automation mode path), but code is present.

The shred execution block at lines 1770–1776 iterates `shred_paths` and calls
`shred` on each. Present in the implementation.

---

## Phase 2 — Operator Cutover Documentation

**Status:** Not testable via clean-run (requires real running testbed apps).

The approved plan's Phase 2 specifies operator steps for LiteLLM, OpenWebUI,
and N8N cutover. These are documented in the approved plan itself
([council/approved/real-app-validation.md](council/approved/real-app-validation.md#L432))
and are not automated in any script. The clean-run cannot verify that an
operator can successfully migrate a running LiteLLM or OpenWebUI instance.

**This is the one area that requires human or live-testbed verification.**
Codex and Gemini should review the Phase 2 cutover steps in the approved plan
and confirm they are accurate against the current compose and proxy setup.

---

## Phase 3 — Proof Capture (Automated Subset)

**Status:** PASS (automated checks only)

The `verify-round.sh` round hook (present at
[council/round-41-5-app-validation/verify-round.sh](council/round-41-5-app-validation/verify-round.sh))
covers the three coexistence-specific proofs.

| Proof | Check | Result |
|-------|-------|--------|
| r41-1 | `subumbra-proxy` on `subumbra-net`; `subumbra-keys` absent | PASS |
| r41-2 | Bundled `litellm` container absent from `docker compose ps` | PASS |
| r41-3 | `POST /t/v1/chat/completions` via transparent proxy → HTTP 200 + real OpenAI response | PASS |

The Phase 3 manual proof artifacts (UI screenshot, OpenWebUI screenshot, N8N
workflow log) were not captured in the clean-run environment. These require the
real testbed.

---

## Bugs Found and Fixed

All three bugs were found by Codex in `codex-verification-2.md`. All three have
been fixed.

### Bug 1 — Transparent proxy returns 502 / 403

**Symptom:** r41-3 proof artifact showed `http_status: 502` with body
`{"detail":"subumbra record fetch failed: status 403"}`.

**Root cause:** `.env.bootstrap.example` (and thus `.env.bootstrap_bak`) has
`PROXY_ALLOWED_KEYS=` empty. With empty scope, `subumbra-keys` returns `403
key_scope_denied` on every proxy key fetch request. The transparent proxy cannot
fetch the `openai_prod` record.

**Fix:** Created
[council/closed/round-41-real-app-validation/bootstrap-overlay.env](council/closed/round-41-real-app-validation/bootstrap-overlay.env)
containing `PROXY_ALLOWED_KEYS=openai_prod`. This is passed to the clean-run via
`--bootstrap-overlay`.

**Scope note:** This is a verification configuration issue, not a product bug.
Operators setting up real deployments are expected to set `PROXY_ALLOWED_KEYS`
explicitly for their key_ids. The overlay file ensures the clean-run proof
environment has the right scope.

### Bug 2 — P9.5 UI status: `forge_healthy` vs `subumbra_keys_healthy`

**Symptom:** P9.5 failed because the running `subumbra-ui` container was built
before Round 41.4 (full rebrand). The old image returns `forge_healthy` and
`forge_error` in `/api/status`. The current `ui/app.py` returns
`subumbra_keys_healthy`. `verify.sh` line 843 checks for `subumbra_keys_healthy`.

**Fix:** Pass `--build subumbra-ui` to `clean-run.sh` so the workspace builds
the current `ui/app.py` into a fresh image.

**Also fixed:** Added `--build bootstrap` to ensure bootstrap logs
`SUBUMBRA_TOKEN_LITELLM` (not `FORGE_TOKEN_LITELLM` from the cached pre-rebrand
image). Non-blocking but keeps the log state clean.

### Bug 3 — `verify_run_id: null` in result.json on failure

**Symptom:** `clean-run-20260416T181850/result.json` showed
`"verify_run_id": null` even though the `verify.sh` run folder existed.

**Root cause:** `export_round_runs_if_present` (called by the cleanup trap on
failure) copied run folders to the local repo but never set the `verify_run_id`
shell variable. Only `copy_proof_artifacts` (called on the success path) set it.

**Fix:**
[scripts/council/clean-run.sh:283-289](scripts/council/clean-run.sh#L283)
— `export_round_runs_if_present` now resolves `verify_run_id` from the copied
workspace run folders after copying, same logic as `copy_proof_artifacts`. The
overall result stays FAIL (correct); the run ID is now captured regardless.

### Workflow doc gap (non-breaking)

Added three precondition callout boxes to
[docs/subumbra-developer.md](docs/subumbra-developer.md) Lane B/C section:
1. Local `clean-run.sh` assumes the regular stack is down first
2. `--build <service>` required when image-built source changed since last build
3. A failing `clean-run.sh` still produces a verify run folder — fetch both

---

## VPS Clean-Run Result

**Command used:**

```bash
./scripts/council/clean-run.sh \
  --round round-41-real-app-validation \
  --agent claude \
  --build bootstrap subumbra-ui \
  --bootstrap-overlay council/round-41-real-app-validation/bootstrap-overlay.env
```

**Note for future verifiers:** the round folder is now archived to
`council/closed/round-41-real-app-validation/`. The correct command for Codex
and Gemini is:

```bash
./scripts/council/clean-run.sh \
  --round round-41-5-app-validation \
  --agent <name> \
  --build bootstrap subumbra-ui \
  --bootstrap-overlay council/closed/round-41-real-app-validation/bootstrap-overlay.env
```

**Result:**

| Run ID | Overall | verify_run_id |
|--------|---------|---------------|
| `clean-run-20260416T183708` | **PASS** | `claude-20260416T183754` |

**Proof artifacts:**

| Artifact | Result |
|----------|--------|
| [summary.txt](council/closed/round-41-real-app-validation/runs/claude-20260416T183754/summary.txt) | `overall: PASS` |
| [r41-1-subumbra-net-membership.txt](council/closed/round-41-real-app-validation/runs/claude-20260416T183754/r41-1-subumbra-net-membership.txt) | subumbra-proxy on net; keys absent |
| [r41-2-bundled-litellm-absent.txt](council/closed/round-41-real-app-validation/runs/claude-20260416T183754/r41-2-bundled-litellm-absent.txt) | litellm not in ps output |
| [r41-3-transparent-proxy-direct.txt](council/closed/round-41-real-app-validation/runs/claude-20260416T183754/r41-3-transparent-proxy-direct.txt) | HTTP 200, real OpenAI response |
| [p9-5-ui-status.txt](council/closed/round-41-real-app-validation/runs/claude-20260416T183754/p9-5-ui-status.txt) | HTTP 200, `subumbra_keys_healthy` present |
| [result.json](council/closed/round-41-real-app-validation/runs/clean-run-20260416T183708/result.json) | `"overall": "PASS"`, `"verify_run_id": "claude-20260416T183754"` |

---

## Summary

| Area | Status | Notes |
|------|--------|-------|
| Phase 0: docker-compose.yml (4 changes) | **PASS** | All changes present and correct |
| Phase 1: IMPORT_PROVIDER_WHITELIST (21 entries) | **PASS** | Comment says "7 providers" but 8 aliases present — non-functional |
| Phase 1: IMPORT_EXCLUSION_LIST (10 entries) | **PASS** | |
| Phase 1: `_parse_env_file` | **PASS** | Edge cases handled correctly |
| Phase 1: `_run_import_screen` | **PASS** | Wizard integration at correct location |
| Phase 2: App cutover steps | **Not testable** | Requires live testbed; Codex/Gemini should review docs |
| Phase 3: Automated proof (r41-1, r41-2, r41-3, P9.x) | **PASS** | VPS clean-run PASS |
| Bug 1: Proxy 502/403 | **Fixed** | bootstrap-overlay.env with PROXY_ALLOWED_KEYS |
| Bug 2: UI forge_healthy vs subumbra_keys_healthy | **Fixed** | --build subumbra-ui |
| Bug 3: verify_run_id null on failure | **Fixed** | clean-run.sh export_round_runs_if_present |

**Overall verdict: PASS** — all code changes verified, all three Codex-reported
failures diagnosed and fixed, VPS clean-run confirms PASS.

---

## Checklist for Codex and Gemini

To reproduce the PASS and independently verify:

```bash
# 1. Pull the branch
ssh subumbra "cd /opt/subumbra && git fetch origin && git checkout round-41-real-app-validation && git pull --ff-only && git rev-parse --short HEAD"
# Expect: 98f4206

# 2. Run clean-run (--build required until VPS images are rebuilt from current source)
ssh subumbra "cd /opt/subumbra && ./scripts/council/clean-run.sh \
  --round round-41-5-app-validation \
  --agent <your-name> \
  --build bootstrap subumbra-ui \
  --bootstrap-overlay council/closed/round-41-real-app-validation/bootstrap-overlay.env"

# 3. Fetch artifacts
./scripts/council/fetch-run-artifacts.sh round-41-5-app-validation <clean-run-id>
./scripts/council/fetch-run-artifacts.sh round-41-5-app-validation <verify-run-id>
```

**Key artifacts to check:**
1. `summary.txt` — overall: PASS, round hook status: PASS
2. `r41-3-transparent-proxy-direct.txt` — http_status: 200 (was 502 before fix)
3. `p9-5-ui-status.txt` — `subumbra_keys_healthy` present (was `forge_healthy` before fix)
4. `result.json` — `verify_run_id` not null (was null before fix)

**Code review focus areas:**
1. [bootstrap/subumbra-bootstrap.py:200-225](bootstrap/subumbra-bootstrap.py#L200)
   — IMPORT_PROVIDER_WHITELIST: confirm all 21 entries are present and alias
   mappings are correct for your target operators' likely `.env` file naming
2. [bootstrap/subumbra-bootstrap.py:229-240](bootstrap/subumbra-bootstrap.py#L229)
   — IMPORT_EXCLUSION_LIST: confirm the 10 exclusions are sufficient for a
   typical LiteLLM + OpenWebUI deployment
3. [bootstrap/subumbra-bootstrap.py:370-504](bootstrap/subumbra-bootstrap.py#L370)
   — `_parse_env_file` + `_run_import_screen`: any edge cases in env file parsing
   or the interactive key assignment flow that warrant attention
4. [council/approved/real-app-validation.md — Phase 2](council/approved/real-app-validation.md#L432)
   — Operator cutover docs: are the LiteLLM, OpenWebUI, and N8N instructions
   accurate given the current codebase and compose setup?
5. [scripts/council/clean-run.sh:270-290](scripts/council/clean-run.sh#L270)
   — verify_run_id fix in `export_round_runs_if_present`: confirm the fix is
   correct and doesn't produce unexpected side effects on success vs. failure paths
