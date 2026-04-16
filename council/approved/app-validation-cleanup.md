# Approved Plan — App Validation Cleanup (Round 41.6)

**Status:** Approved  
**Source round:** `council/round-41-6-app-validation-cleanup/`  
**Consensus basis:** All three syntheses (claude-synthesis.md, codex-synthesis.md,
gemini-synthesis.md) agree on the core approach without blocking technical
disagreements.

---

## Purpose

This round exists solely to make Round 41 honestly closeable. It addresses three
remaining gaps identified in the Round 41.5 closure audit:

1. The bootstrap overlay file is not self-contained on a clean VPS pull
2. `r41-3` is not stable enough for closure-grade proof (CF Secrets propagation lag)
3. No live proof of the Phase 2 import wizard path exists

The round makes no product code changes. All changes are harness-only (proof inputs
and verification scripts).

---

## Explicit Exclusions

These items must NOT be included in this round's implementation:

| Item | Reason |
|------|--------|
| Any change to `.env.bootstrap.example` | Leaks verifier assumptions into the operator template; `PROXY_ALLOWED_KEYS=` must remain empty for real operators |
| `temp/` workspace relocation | Identified as uncommitted local state in 41.5 audit; does not block Round 41 closure; belongs in a separate workflow cleanup task |
| Changes to Worker, bootstrap, or subumbra-keys | No product code changes permitted in this round |
| Changes to `docker-compose.yml` | Out of scope |
| Full OpenWebUI / N8N / LiteLLM matrix rerun | The Phase 2 minimum proof is one import wizard run + one live call only |
| Any changes to r41-1 or r41-2 harness logic | Docker-local checks; no CF dependency; copy verbatim from 41.5 |

---

## Verifier Prerequisites

Before running any clean-run or Phase 3 manual proof, confirm:

1. VPS `.env.bootstrap_bak` contains a valid `OPENAI_KEY` value. If `OPENAI_KEY` is
   absent, the bootstrap will not create an `openai_prod` record, and r41-3 will fail
   with a 502 rather than a 401 (an unrelated failure, not a flakiness case).

2. `OPENAI_KEY_ID` in `.env.bootstrap_bak` must be either absent, empty, or the string
   `openai_prod`. Any other value will cause the bootstrapped key_id to not match the
   `PROXY_ALLOWED_KEYS` set by the overlay.

3. For Phase 3 (manual proof only): the VPS must have a live LiteLLM stack with a real
   `/opt/litellm/.env` file containing at least one recognized provider key. Phase 3 is
   a manual session against the live stack — it is NOT run inside a clean-run workspace.

---

## Standard Clean-Run Command

```bash
./scripts/council/clean-run.sh \
  --round round-41-6-app-validation-cleanup \
  --agent <name> \
  --build bootstrap subumbra-ui \
  --bootstrap-overlay council/round-41-6-app-validation-cleanup/bootstrap-overlay.env
```

The `--build bootstrap subumbra-ui` flags must be kept until the VPS images have been
freshly rebuilt from commit `a9b01bf` or later. This is a verifier prerequisite note,
not a product constraint.

---

## Phase 1 — Self-Contained Proof Input

**Deliverable:** `council/round-41-6-app-validation-cleanup/bootstrap-overlay.env`

Create the file with the following exact content:

```bash
# Round 41.6 bootstrap overlay.
# Sets proxy key scope for r41-3 transparent-proxy proof.
# Requires OPENAI_KEY set in .env.bootstrap_bak.
# OPENAI_KEY_ID must be empty or "openai_prod" (default).
PROXY_ALLOWED_KEYS=openai_prod
```

The file must be committed with `git add -f` because `council/` is gitignored:

```bash
git add -f council/round-41-6-app-validation-cleanup/bootstrap-overlay.env
```

**Why `openai_prod` is correct:** `_default_key_id("openai")` at
`bootstrap/subumbra-bootstrap.py:366-367` returns `f"{provider}_prod"`, so any
clean-run with `OPENAI_KEY_ID=` empty (the `.env.bootstrap.example` default) will
bootstrap the OpenAI key as `openai_prod`. The overlay scope matches the default
key_id with no operator customization required.

**Path resolution:** `scripts/council/clean-run.sh:92-94` converts relative overlay
paths to absolute using `repo_root`. The relative path
`council/round-41-6-app-validation-cleanup/bootstrap-overlay.env` resolves correctly.

---

## Phase 2 — r41-3 Stabilization (Harness Only)

**Deliverable:** `council/round-41-6-app-validation-cleanup/verify-round.sh`

This script replaces the 41.5 version. r41-1 and r41-2 are copied verbatim from
`council/closed/round-41-5-app-validation/verify-round.sh`. Only the r41-3 section
changes.

The r41-3 fix is framed as **stabilize and re-prove** — not as a confirmed root-cause
fix. The retry loop is the correct mitigation for the observed intermittent 401 behavior
regardless of whether the underlying cause is CF isolate caching, secret consistency
lag, or another transient. A persistent failure after 5 attempts is unambiguous: it
indicates a real problem, not flakiness.

### Full `verify-round.sh` content

```bash
#!/usr/bin/env bash
set -euo pipefail

artifact_dir="${VERIFY_ARTIFACT_DIR:?VERIFY_ARTIFACT_DIR is required}"

network_artifact="${artifact_dir}/r41-1-subumbra-net-membership.txt"
litellm_artifact="${artifact_dir}/r41-2-bundled-litellm-absent.txt"
proxy_artifact="${artifact_dir}/r41-3-transparent-proxy-direct.txt"

# ── r41-1: subumbra-net membership ──────────────────────────────────────────

network_output="$(docker network inspect subumbra-net 2>&1)" || {
    printf '# PROOF: round 41 coexistence network check\n%s\n' "$network_output" >"$network_artifact"
    echo "subumbra-net inspect failed" >&2
    exit 1
}

{
    printf '# PROOF: round 41 coexistence network check\n'
    printf '%s\n' "$network_output"
} >"$network_artifact"

if ! grep -q 'subumbra-proxy' "$network_artifact"; then
    echo "subumbra-proxy is not attached to subumbra-net" >&2
    exit 1
fi
if grep -q 'subumbra-keys' "$network_artifact"; then
    echo "subumbra-keys must not be attached to subumbra-net" >&2
    exit 1
fi

# ── r41-2: bundled LiteLLM absent ───────────────────────────────────────────

bundled_ps="$(docker compose ps 2>&1)" || {
    printf '# PROOF: round 41 bundled LiteLLM absence\n%s\n' "$bundled_ps" >"$litellm_artifact"
    echo "docker compose ps failed" >&2
    exit 1
}

{
    printf '# PROOF: round 41 bundled LiteLLM absence\n'
    printf '%s\n' "$bundled_ps"
} >"$litellm_artifact"

if printf '%s\n' "$bundled_ps" | grep -Eq '(^|[[:space:]])litellm([[:space:]]|$)'; then
    echo "bundled litellm should not be running for round 41 coexistence proof" >&2
    exit 1
fi

# ── r41-3: transparent proxy — with retry for CF Secrets propagation lag ────
#
# Retry rationale: a fresh bootstrap pushes SUBUMBRA_ADAPTER_TOKENS to CF
# Secrets; new tokens may not propagate to all Worker isolates immediately.
# worker/src/worker.js:439-444 returns 401 when the token is structurally
# valid but not yet in the live secret — this is the propagation case, not
# misconfiguration (which returns 503 at line 435).
#
# Success condition: proof is stable and reproducible within 5 attempts.
# The artifact retains ALL attempt results so a verifier can distinguish
# genuine flakiness from a real auth failure.

: >"$proxy_artifact"
printf '# PROOF: round 41.6 direct transparent proxy request\n' >>"$proxy_artifact"
printf 'command: curl --compressed -sS -D - -o - -X POST http://127.0.0.1:8090/t/v1/chat/completions -H '"'"'Authorization: Bearer openai_prod'"'"' -H '"'"'Content-Type: application/json'"'"' -d '"'"'{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"Say test only."}],"max_tokens":5}'"'"'\n' >>"$proxy_artifact"

proxy_exit=0; proxy_status=""
for attempt in 1 2 3 4 5; do
    proxy_body="$(mktemp)"; proxy_headers="$(mktemp)"
    proxy_exit=0
    curl --compressed -sS -D "$proxy_headers" -o "$proxy_body" \
        -X POST http://127.0.0.1:8090/t/v1/chat/completions \
        -H 'Authorization: Bearer openai_prod' \
        -H 'Content-Type: application/json' \
        -d '{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"Say test only."}],"max_tokens":5}' \
        >/dev/null 2>&1 || proxy_exit=$?
    proxy_status="$(awk 'toupper($1) ~ /^HTTP\// {code=$2} END {print code}' "$proxy_headers")"
    printf 'attempt: %d  exit_code: %s  http_status: %s\n' \
        "$attempt" "$proxy_exit" "${proxy_status:-none}" >>"$proxy_artifact"
    if [[ "$proxy_exit" -eq 0 && "${proxy_status:-}" == "200" ]]; then
        printf 'response_body_excerpt:\n' >>"$proxy_artifact"
        sed -n '1,80p' "$proxy_body" | sed 's/^/  /' >>"$proxy_artifact"
        rm -f "$proxy_body" "$proxy_headers"
        break
    fi
    rm -f "$proxy_body" "$proxy_headers"
    [[ "$attempt" -lt 5 ]] && sleep 15
done

if [[ "$proxy_exit" -ne 0 || "${proxy_status:-}" != "200" ]]; then
    echo "direct transparent proxy round 41.6 proof failed after 5 attempts" >&2
    exit 1
fi
```

**Key requirements for this script:**
- `set -euo pipefail` at the top
- r41-1 and r41-2 sections are identical to the 41.5 version — no changes
- The artifact for r41-3 is initialized with `: >` (truncate) then built with `>>`
  throughout — never overwritten mid-loop
- All attempt results are captured (attempt number, exit code, HTTP status) before
  the final response body excerpt
- Response body excerpt is written only on success, immediately after the successful
  attempt line, before `break`
- Temp files (`proxy_body`, `proxy_headers`) are created fresh per attempt and
  deleted after each attempt
- `sleep 15` is skipped after attempt 5 (`[[ "$attempt" -lt 5 ]] && sleep 15`)
- The script must be committed with `git add -f`

---

## Phase 3 — Minimal Phase 2 Manual Proof (Live VPS, Separate from Clean-Run)

**Deliverable:**
`council/round-41-6-app-validation-cleanup/runs/phase2-import-proof/manual-migration-proof.txt`

This is a manual session. The implementing agent runs on the live VPS stack
(not a clean-run workspace). The live LiteLLM stack must be running with a real
`/opt/litellm/.env` present.

**Prerequisite:** Do not run Phase 3 inside or alongside a clean-run. It is a
separate manual session against the live running stack.

**Command to run:**

```bash
docker compose --profile bootstrap run --rm \
  -v /opt/litellm:/host_litellm:ro \
  -it bootstrap
```

**Required content of the proof artifact** (`manual-migration-proof.txt`):

1. The exact command run (with volume mount path)
2. Terminal output showing at least one provider key detected in `/host_litellm/.env`
3. Operator accepting and assigning a key_id (default `openai_prod` or explicit)
4. Bootstrap completion message
5. The curl command run to verify the imported key:
   ```bash
   curl -s -X POST http://127.0.0.1:8090/t/v1/chat/completions \
     -H 'Authorization: Bearer openai_prod' \
     -H 'Content-Type: application/json' \
     -d '{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"ping"}],"max_tokens":3}'
   ```
6. Response body showing HTTP 200 and a non-empty response

The proof file is a plain-text terminal transcript. It is committed to the round
folder. The `runs/phase2-import-proof/` directory must be created if it does not
exist; use `git add -f` to force-add it (gitignored path).

---

## Phase 4 — Re-Proof and Final Close Decision

After Phases 1–3 are committed:

1. Pull the branch on the VPS (`git pull`)
2. Run the standard clean-run command (see above)
3. Inspect `r41-3` proof artifact: confirm it shows a PASS within 5 attempts; confirm
   the artifact contains the attempt log lines (not just the final result)
4. Confirm r41-1 and r41-2 are PASS
5. Confirm the Phase 3 manual proof artifact is present in the round folder and
   contains the six required elements listed above
6. Council members produce verification reports in
   `council/round-41-6-app-validation-cleanup/` following the standard format
7. If all three council members verify PASS, Round 41 can be honestly closed

---

## Error / Logging Notes

**None required.** All changes in this round are harness-only (proof inputs and
verification scripts). No product code changes are made. No new operator-visible
signals, log lines, or error handling are introduced.

The r41-3 `stderr` message on failure is updated from:

```
direct transparent proxy round 41 proof failed
```

to:

```
direct transparent proxy round 41.6 proof failed after 5 attempts
```

This change is in the verification script only, not in any product component.

---

## Known Limitations Carried Forward

1. **r41-3 root cause not conclusively proven.** The retry loop stabilizes the proof
   by accommodating CF Secrets propagation lag, but the round does not conclusively
   prove the timing window as the only possible cause. The round requires "stabilize
   and re-prove," not root-cause closure.

2. **Phase 2 proof is manually captured, not automated.** The import wizard requires
   interactive input that cannot run headlessly inside clean-run. The manual transcript
   is the accepted minimum. Automating Phase 2 is deferred to a future round.

3. **Full real-app matrix not re-proven.** The Phase 2 proof covers the import wizard
   path (LiteLLM key import) only. OpenWebUI and N8N operator cutover paths are
   accepted as code-audited but not live-proven. This was the agreed minimum for Round
   41 closure.

4. **`temp/` workspace relocation deferred.** Clean-run workspaces still land in `/tmp`
   on the VPS. Not a closure blocker; belongs in a future workflow cleanup task.

5. **`IMPORT_PROVIDER_WHITELIST` comment inaccuracy** at
   `bootstrap/subumbra-bootstrap.py:216` ("7 providers" vs 8 aliases). Not a closure
   blocker; excluded from this round.
