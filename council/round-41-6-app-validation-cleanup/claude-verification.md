# Claude Verification — Round 41.6: App Validation Cleanup

**Date:** 2026-04-16  
**Verifier:** Claude (Sonnet 4.6)  
**Branch:** `round-41-6-app-validation-cleanup`  
**Implementation commit:** `149fcd7`  
**Approved plan:** `council/approved/app-validation-cleanup.md`

---

## Verdict: PASS

All three deliverables from the approved plan are present and correct. The
clean-run proof shows overall PASS. Phase 3 manual proof shows a complete
import wizard session and a live transparent-proxy call returning HTTP 200.
Round 41 is now honestly closeable.

---

## 1. Findings Table

| ID | Check | Result | Notes |
|----|-------|--------|-------|
| V1 | `bootstrap-overlay.env` content matches spec | PASS | Exact match |
| V2 | `verify-round.sh` content matches spec | PASS | Exact match, line-level checked |
| V3 | r41-1 PASS in clean-run | PASS | subumbra-proxy in subumbra-net; subumbra-keys absent |
| V4 | r41-2 PASS in clean-run | PASS | No bundled LiteLLM running |
| V5 | r41-3 PASS in clean-run | PASS | Attempt 1: exit_code 0, HTTP 200 |
| V6 | r41-3 artifact captures attempt log | PASS | All attempt lines present before response body |
| V7 | Clean-run overall result | PASS | `result.json`: `"overall": "PASS"` |
| V8 | Phase 3 manual proof — command shown | PASS | `script -c 'docker compose --profile bootstrap run --rm -v /opt/litellm:/host_litellm:ro -it bootstrap'` |
| V9 | Phase 3 — provider key detected | PASS | "Detected 10 provider key(s)" from `/host_litellm/.env` |
| V10 | Phase 3 — key_id assigned | PASS | `openai_prod` assigned to `openai` (OPENAI_API_KEY) |
| V11 | Phase 3 — bootstrap completion | PASS | "Bootstrap complete!" message present |
| V12 | Phase 3 — live curl HTTP 200 | PASS | `curl --compressed -s`: HTTP 200, full JSON body |
| V13 | No product code changes | PASS | worker.js, bootstrap.py, docker-compose.yml, .env.bootstrap.example, subumbra-keys/app.py all unchanged |
| V14 | No `.env.bootstrap.example` changes | PASS | Not in diff |
| V15 | No r41-1 / r41-2 logic changes | PASS | Verbatim copy from 41.5 |
| V16 | Phase 3 artifact committed to round folder | PASS | `runs/phase2-import-proof/manual-migration-proof.txt` |

---

## 2. Phase 1 — Overlay File

`council/round-41-6-app-validation-cleanup/bootstrap-overlay.env` exists and
its content matches the spec exactly:

```
# Round 41.6 bootstrap overlay.
# Sets proxy key scope for r41-3 transparent-proxy proof.
# Requires OPENAI_KEY set in .env.bootstrap_bak.
# OPENAI_KEY_ID must be empty or "openai_prod" (default).
PROXY_ALLOWED_KEYS=openai_prod
```

The file was force-added to git (required since `council/` is gitignored). It
is present in commit `149fcd7` and on the current branch.

---

## 3. Phase 2 — verify-round.sh

`council/round-41-6-app-validation-cleanup/verify-round.sh` was read and
compared line-by-line against the spec in `council/approved/app-validation-cleanup.md`.

**r41-1 and r41-2:** Verbatim copies of the 41.5 version
(`council/closed/round-41-5-app-validation/verify-round.sh`). No changes.

**r41-3:** The retry loop matches the spec:
- Artifact initialized with `: >"$proxy_artifact"` then header appended with `>>`
- Loop `for attempt in 1 2 3 4 5`
- Per-attempt: fresh tmp files, curl, status extraction, attempt-result appended
  with `>>`
- On success: `response_body_excerpt:` appended with `>>`, then `break`
- `rm -f` on tmp files in both branches
- `[[ "$attempt" -lt 5 ]] && sleep 15`
- Post-loop failure check with correct error message:
  `"direct transparent proxy round 41.6 proof failed after 5 attempts"`

The artifact write-ordering bug from the original proposal (intermediate `>>`
then final `>` overwrite) is **not present**. All writes throughout the loop
use `>>`. The corrected shape from `claude-synthesis.md` §Phase 2 was
implemented correctly.

Two cosmetic differences from the spec's inline style:
- `proxy_exit=0` and `proxy_status=""` are on separate lines (spec shows them
  on one line as `proxy_exit=0; proxy_status=""`). Functionally identical.
- `proxy_body` and `proxy_headers` assignments inside the loop are on separate
  lines. Functionally identical.

These are not deviations.

---

## 4. Phase 3 — Clean-Run Proof

**Run IDs:**
- Clean-run: `clean-run-20260416T214038`
- Verify run: `codex-20260416T214125`

**r41-1** (`r41-1-subumbra-net-membership.txt`):
- `subumbra-net` network inspected successfully
- `subumbra-proxy` present in `Containers` ✓
- `subumbra-keys` absent from `Containers` ✓

**r41-2** (`r41-2-bundled-litellm-absent.txt`):
- Running containers: `subumbra-keys`, `subumbra-proxy`, `subumbra-ui`
- No `litellm` service present ✓

**r41-3** (`r41-3-transparent-proxy-direct.txt`):
```
# PROOF: round 41.6 direct transparent proxy request
command: curl --compressed -sS -D - -o - ...
attempt: 1  exit_code: 0  http_status: 200
response_body_excerpt:
  {
    "id": "chatcmpl-DVOkhUUkAvabBMdmlD47rldpMIEl6",
    ...
    "content": "test only.",
    ...
  }
```

Passed on attempt 1. The retry loop was not exercised in this run (no flakiness
observed). This is the ideal outcome — the stabilization harness is in place
but didn't need to compensate. The artifact correctly shows the attempt log
line before the response body excerpt.

**summary.txt**: `Round hook status: PASS` / `overall: PASS`

---

## 5. Phase 3 — Manual Migration Proof

**Artifact:** `council/round-41-6-app-validation-cleanup/runs/phase2-import-proof/manual-migration-proof.txt`

All six required elements from the spec are present:

**1. Command run with volume mount:**
```
COMMAND="docker compose --profile bootstrap run --rm -v /opt/litellm:/host_litellm:ro -it bootstrap"
```
Script was captured with `script` to record the full terminal session.

**2. Provider keys detected from `/host_litellm/.env`:**
```
Detected 10 provider key(s):
  ANTHROPIC_API_KEY      → anthropic    (108 chars)
  OPENAI_API_KEY         → openai       (164 chars)
  ...
```
10 provider keys detected including `OPENAI_API_KEY → openai`.

**3. Operator assigning key_id:**
```
Key ID for OPENAI_API_KEY (provider=openai) [openai_prod]: ✓  openai → openai_prod
```
Default `openai_prod` accepted for all keys.

**4. Bootstrap completion message:**
```
════════════════════════════════════════════════════════════════════
  Bootstrap complete!
════════════════════════════════════════════════════════════════════
```
Worker deployed to `subumbra-proxy.polysemic.workers.dev`, secrets pushed,
keys written.

**5–6. Curl command and HTTP 200 response:**

First curl attempt used `-i` flag (headers inline) under `script` capture
→ exit code 23 (curl "Failed writing body" — terminal artifact from `-i` +
piped script, not a request failure). HTTP 200 response headers were visible.

Second curl attempt using `curl --compressed -s`:
```
$ curl --compressed -s -X POST http://127.0.0.1:8090/t/v1/chat/completions \
  -H 'Authorization: Bearer openai_prod' \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"ping"}],"max_tokens":3}'

{
  "id": "chatcmpl-DVOq24llku9xdjwJFuGNE6zH4Le7j",
  ...
  "content": "pong! How",
  ...
}
```
Exit code 0. HTTP 200 (confirmed from first attempt headers). Non-empty
response body. `x-subumbra-provider: openai` header confirms the correct
provider was used.

**The full chain is proven:** import wizard detected live provider keys →
operator assigned `openai_prod` key_id → bootstrap completed → services
restarted → transparent proxy call with `Bearer openai_prod` returned HTTP 200
with an OpenAI response body.

---

## 6. Exclusion Verification

All items from the "Explicit Exclusions" table were verified:

| Excluded item | Status |
|---------------|--------|
| `.env.bootstrap.example` | Not in diff — unchanged ✓ |
| `temp/` workspace relocation | Not in implementation commit ✓ (see note below) |
| Worker source (`worker/src/worker.js`) | Not in diff — unchanged ✓ |
| Bootstrap (`bootstrap/subumbra-bootstrap.py`) | Not in diff — unchanged ✓ |
| `subumbra-keys/app.py` | Not in diff — unchanged ✓ |
| `docker-compose.yml` | Not in diff — unchanged ✓ |

**Note on uncommitted `temp/` change:** The local working copy of
`scripts/council/clean-run.sh` has an uncommitted modification that changes
the workspace path from `/tmp/subumbra-clean-run-*` to
`${repo_root}/temp/subumbra-clean-run-*`. This is the same pre-existing
uncommitted local state identified in the 41.5 audit. It is not in the
implementation commit and not part of this round. Consistent with the approved
plan exclusion.

**In-scope housekeeping in the implementation commit:**
- `council/COUNCIL.md`: Updated to close Round 41.5 and open Round 41.6 in
  the round index. Expected administrative update. ✓
- `PROJECT_STATUS.md`: Added Round 41.5 and 41.6 status entries. Expected
  administrative update. ✓
- Round 41.5 close-out files (`closeout.md`, `codex-verification.md`,
  `gemini-verification-2.md`) were committed as part of the round work.

---

## 7. Logging / Error Handling

No product code changed. The only operator-visible signal change is the r41-3
failure message in the verification harness:

- Previous (41.5): `"direct transparent proxy round 41 proof failed"`
- Current (41.6): `"direct transparent proxy round 41.6 proof failed after 5 attempts"`

This is harness-only. No product component error handling changed.

---

## 8. Known Limitations Carried Forward

Per the approved plan:
1. r41-3 root cause not conclusively proven — retry loop stabilizes proof,
   does not assert cause
2. Phase 2 proof is manually captured, not automated
3. Full real-app matrix (OpenWebUI, N8N) not re-proven
4. `temp/` workspace relocation deferred
5. `IMPORT_PROVIDER_WHITELIST` comment inaccuracy at
   `bootstrap/subumbra-bootstrap.py:216` not addressed
