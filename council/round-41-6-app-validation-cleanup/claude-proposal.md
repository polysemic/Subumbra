# Claude Proposal — Round 41.6: App Validation Cleanup

## 1. Evidence

### What is confirmed fixed in committed code

From the 41.5 closure audits (all three verifiers agree):

- Phase 0 docker-compose.yml changes: present and verified by independent reruns
  ([docker-compose.yml:13-18](docker-compose.yml#L13), [:80-81](docker-compose.yml#L80),
  [:180](docker-compose.yml#L180))
- Phase 1 bootstrap import implementation: present, syntactically valid, code-audited
  ([bootstrap/subumbra-bootstrap.py:200-504](bootstrap/subumbra-bootstrap.py#L200))
- P9.5 UI field alignment: `subumbra_keys_healthy` in committed `ui/app.py`; confirmed
  PASS by Codex independent rerun
- `verify_run_id` null-on-failure fix: present in
  [scripts/council/clean-run.sh:283-289](scripts/council/clean-run.sh#L283); confirmed
  working by Codex rerun 1's `result.json`

### Blocker A — r41-3 is flaky

**Source:** `codex-verification.md` §Rerun 1 and §Rerun 2.

Codex ran the same clean-run command on the same VPS at the same SHA twice in sequence.
Rerun 1 (`codex-20260416T192605`): r41-3 returned `401 {"error":"unauthorized"}`.
Rerun 2 (`codexr2-20260416T192730`): r41-3 returned `200` with a real OpenAI response.

The 401 path in the Worker is at
[worker/src/worker.js:439-444](worker/src/worker.js#L439): the Worker validates
`X-Subumbra-Token` against `SUBUMBRA_ADAPTER_TOKENS` from CF Secrets. The clean-run
bootstraps fresh secrets, starts containers with the new token, then immediately runs
`verify.sh` without any propagation wait. CF Secrets are eventually consistent.
If the Worker isolate has not yet picked up the newly pushed `SUBUMBRA_ADAPTER_TOKENS`,
the new proxy token is not in the valid set → `401`.

**This is not a product bug.** It is a pre-existing timing window exposed by r41-3 being
the first round hook to make a live CF Worker call immediately after fresh bootstrap. The
product itself works (rerun 2 proves it). The problem is that "can be made to pass once"
is not the same as "deterministic proof."

**Key diagnostic fact:** The 401 body is `{"error":"unauthorized"}`, not `{"error":"worker
not configured"}` (which would indicate `SUBUMBRA_ADAPTER_TOKENS` is missing or unparseable,
the 503 path at [worker/src/worker.js:435](worker/src/worker.js#L435)). This confirms the
secret reached the Worker; the isolate simply had not picked up the new value yet.

### Blocker B — Bootstrap overlay is not self-contained on a clean pull

**Source:** `codex-verification.md` §3 (overlay dependency).

The `bootstrap-overlay.env` needed to run r41-3 successfully lives at
`council/closed/round-41-real-app-validation/bootstrap-overlay.env`. Codex ran the
clean-run with `--bootstrap-overlay council/closed/round-41-real-app-validation/bootstrap-overlay.env`
and got "bootstrap overlay file not found." The file was absent from the VPS after a clean
pull. Codex had to `scp` it manually before the run could proceed.

**Why this happens:** The file was force-added to git in the active round folder (commit
`98f4206`), then moved to `council/closed/` by `git mv` (commit `e21b88d`). `council/` is
in `.gitignore`. Force-added files are tracked and do update on `git pull`, but the move
happened in a subsequent commit that the VPS may not have pulled. The exact failure is
a commit-lag issue, but the structural problem is that the clean-run command points to an
archived path that verifiers are unlikely to pull correctly.

**What the overlay contains:** `PROXY_ALLOWED_KEYS=openai_prod`. This sets the sidecar
proxy's key scope to allow fetching the `openai_prod` record during bootstrap. The
`.env.bootstrap.example` line 79 defaults to `PROXY_ALLOWED_KEYS=` (empty).

**Why `openai_prod` is the right default for the proof:** `_default_key_id("openai")`
at [bootstrap/subumbra-bootstrap.py:366-367](bootstrap/subumbra-bootstrap.py#L366) returns
`"openai_prod"`. Automation mode uses this default unless `OPENAI_KEY_ID` is set
explicitly in `.env.bootstrap` (line 41 of `.env.bootstrap.example`). A standard
clean-run with an operator's `.env.bootstrap_bak` that leaves `OPENAI_KEY_ID=` empty will
bootstrap the key as `openai_prod`, making the overlay correct for the standard case.

### Phase 2 gap — import wizard not proven end-to-end

**Source:** All three 41.5 audits agree.

The Phase 1 import wizard code is present and code-audited. Nobody has run it
interactively and proved it works against a real `.env` file. This is not automated by
the clean-run (interactive wizard cannot run headlessly). The approved plan's Phase 2
was always operator steps, not code. But a round called "Real App Validation" that never
proved its interactive migration path end-to-end is not fully closed.

**Minimum bar:** A single manual session showing the wizard detecting at least one provider
key from a mounted `.env` file, operator assigning a key_id, and a successful API call
through the resulting key. Not a full LiteLLM/OpenWebUI/N8N testbed re-run.

---

## 2. Current vs Desired

| Dimension | Current | Desired |
|-----------|---------|---------|
| r41-3 proof attempt count | 1 (immediate single shot post-bootstrap) | Retry loop; exits on first 200 or after N attempts |
| r41-3 failure diagnosis | No intermediate artifact before proof file | Same |
| Overlay location | `council/closed/round-41-real-app-validation/bootstrap-overlay.env` (archived, may miss on clean pull) | `council/round-41-6-app-validation-cleanup/bootstrap-overlay.env` (active round, committed, pull-reliable) |
| Clean-run command reproducibility | Requires manual `scp` of overlay on fresh VPS | Pull branch, run command, done |
| Phase 2 interactive proof | None | One committed manual artifact showing import + live call |
| `IMPORT_PROVIDER_WHITELIST` comment | "7 providers" (inaccurate, 8 aliases present) | "8 providers" (non-blocking, cleanup only) |

---

## 3. Proposal

Three changes. All harness/proof. None touch product code.

### Change 1 — New verify-round.sh with retry loop for r41-3

**What:** Write a new `council/round-41-6-app-validation-cleanup/verify-round.sh` that
is identical to the 41.5 version except the r41-3 curl is wrapped in a retry loop.

**Why a new file and not editing the closed 41.5 version:** 41.5 is closed. The 41.6
round gets its own verify-round.sh.

**Retry specification:**
- Up to 5 attempts
- 15 seconds between attempts (covers observed CF propagation lag; CF secret push to
  isolate reload is typically under 30 seconds in practice)
- On each failure, log the attempt number, exit code, and HTTP status to the proof artifact
  before retrying
- On final failure (attempt 5 still not 200), exit non-zero and emit the diagnostic

**Shape:**

```bash
proxy_body="$(mktemp)"
proxy_headers="$(mktemp)"
proxy_exit=0
proxy_status=""

for attempt in 1 2 3 4 5; do
    > "$proxy_body"; > "$proxy_headers"; proxy_exit=0
    curl --compressed -sS -D "$proxy_headers" -o "$proxy_body" \
        -X POST http://127.0.0.1:8090/t/v1/chat/completions \
        -H 'Authorization: Bearer openai_prod' \
        -H 'Content-Type: application/json' \
        -d '{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"Say test only."}],"max_tokens":5}' \
        >/dev/null 2>&1 || proxy_exit=$?
    proxy_status="$(awk 'toupper($1) ~ /^HTTP\// {code=$2} END {print code}' "$proxy_headers")"
    if [[ "$proxy_exit" -eq 0 && "${proxy_status:-}" == "200" ]]; then break; fi
    if [[ "$attempt" -lt 5 ]]; then
        printf 'attempt %d: exit_code=%s http_status=%s — retrying in 15s\n' \
            "$attempt" "$proxy_exit" "${proxy_status:-none}" >> "$proxy_artifact"
        sleep 15
    fi
done
```

Proof artifact content remains the same (the final attempt's output). Intermediate
attempt log lines are prepended in the artifact so a reviewer can see how many attempts
were needed.

**What this proves:** The transparent proxy call succeeds within the CF propagation
window. It is the same assertion as before; the retry simply removes the race between
secret push and isolate reload.

**What this does not prove:** That the system is broken if it takes >1 attempt. Multiple
attempts are expected and acceptable for fresh-bootstrap runs.

### Change 2 — Commit bootstrap-overlay.env to active round folder

**What:** Commit `council/round-41-6-app-validation-cleanup/bootstrap-overlay.env` with
content:

```bash
# Round 41.6 bootstrap overlay.
# Sets proxy key scope for r41-3 transparent-proxy proof.
# Requires OPENAI_KEY_ID= (empty) or OPENAI_KEY_ID=openai_prod in .env.bootstrap_bak
# so that the bootstrapped key_id matches what r41-3 expects.
PROXY_ALLOWED_KEYS=openai_prod
```

**Clean-run command for 41.6 verifiers:**

```bash
./scripts/council/clean-run.sh \
  --round round-41-6-app-validation-cleanup \
  --agent <name> \
  --build bootstrap subumbra-ui \
  --bootstrap-overlay council/round-41-6-app-validation-cleanup/bootstrap-overlay.env
```

This is self-contained: pull the branch, run the command, no manual file transfers.

**Prerequisite documented in verifier guide:** The operator's `.env.bootstrap_bak` must
have `OPENAI_KEY` set to a valid key and either `OPENAI_KEY_ID=` (empty, defaults to
`openai_prod`) or `OPENAI_KEY_ID=openai_prod` explicitly. This is the only operator-input
dependency and should be documented in the round's proof prerequisites section.

**What NOT to change:** Do not set `PROXY_ALLOWED_KEYS=openai_prod` as a default in
`.env.bootstrap.example`. The empty default is correct for real deployments; operators
set their own key scope. The overlay is for the verification proof path only.

### Change 3 — Phase 2 minimum proof artifact

**What:** One manual VPS session, not a clean-run. Committed as a text transcript to
`council/round-41-6-app-validation-cleanup/runs/phase2-import-proof/`.

**Minimum required content:**

1. Command run:
   ```bash
   docker compose --profile bootstrap run --rm \
     -v /opt/litellm:/host_litellm:ro \
     -it bootstrap
   ```
2. Wizard output showing at least one provider key detected in `/host_litellm/.env`
3. Operator accepting and assigning a key_id
4. Successful bootstrap completion message
5. One curl through `subumbra-proxy` (or litellm) using the imported key_id showing HTTP 200

**This is a verifier-side proof artifact, not a code change.** The implementing verifier
for 41.6 produces it on the VPS. The other two council members confirm it is present and
plausible in their verification passes.

**What this does and does not prove:**
- Proves: the import wizard detects provider keys from a real mounted `.env` file, the
  operator flow works as designed, and the imported key produces a successful API call.
- Does not prove: OpenWebUI and N8N cutover. Those were always operator steps documented
  in the approved plan, not code under test. They are accepted as out-of-scope for the
  automated proof path.

---

## 4. Failure Modes

### r41-3 retry loop exhausted

If all 5 attempts return non-200 after 60 seconds total wait time, the proof fails as
before. The proof artifact will contain all 5 attempt logs. This failure mode means
either: (a) the proxy is not running, (b) the key record does not exist (wrong key_id
in `.env.bootstrap_bak`), (c) the overlay was not applied (scope denied), or (d) a
genuine CF outage. The diagnostic log distinguishes these cases.

Specifically: a persistent `401` means token not in valid set (secret not yet updated,
or wrong token); a persistent `502` means the proxy got `403` from subumbra-keys (scope
not set, overlay missing or wrong key_id). These are distinguishable from each other and
from a product regression.

### Key_id mismatch between overlay and .env.bootstrap_bak

If the operator's `.env.bootstrap_bak` has `OPENAI_KEY_ID=my_custom_id`, the overlay
`PROXY_ALLOWED_KEYS=openai_prod` will set the wrong scope, and the bootstrapped key
won't be accessible by the proxy. r41-3 will return 502 (403 from subumbra-keys, scope
denied). This is a verifier setup error, not a product bug. The prerequisite note in
Change 2 prevents this.

### Phase 2 proof requires a real .env with a real API key

The Phase 2 proof cannot use a dummy value for the API key — the round hook calls the
real provider. The VPS `.env.bootstrap_bak` must have a valid `OPENAI_KEY` (or
equivalent). This is already a prerequisite for r41-3.

---

## 5. Exclusions

- No changes to `bootstrap/subumbra-bootstrap.py` (code-audited and confirmed correct)
- No changes to `docker-compose.yml` (all Phase 0 changes verified present)
- No changes to `worker/src/worker.js` (the 401 is a timing issue, not a Worker bug)
- No changes to `.env.bootstrap.example` defaults (empty `PROXY_ALLOWED_KEYS` is
  correct for real deployments)
- No re-running the full OpenWebUI or N8N cutover testbed (these are operator steps
  documented in the approved plan; not in the automated proof path)
- No new product features, architecture changes, or scope expansion

The `IMPORT_PROVIDER_WHITELIST` comment inaccuracy ("7 providers" vs 8 aliases at
[bootstrap/subumbra-bootstrap.py:216](bootstrap/subumbra-bootstrap.py#L216)) is a
cleanup item, not a blocker. It does not need to be resolved for Round 41 to close
honestly. Defer to a future cleanup pass.

---

## 6. Open Questions

### OQ-1: Retry attempt count and sleep duration

I proposed 5 attempts with 15-second sleeps (75 seconds maximum wait). Is this
enough to outlast any observed CF propagation? Gemini's 41.5 report mentioned "a simple
retry or stabilization wait" without specifying timing. Codex's two runs spanned some
minutes in real time. 15 seconds per attempt appears conservative based on observed
behavior (rerun 2 passed without any additional wait in the Codex session), but a larger
sleep would reduce variance at the cost of clean-run duration.

**Council decision needed:** Accept 5 × 15s (75s max), or adjust?

### OQ-2: Phase 2 proof — import-only or import-plus-live-call?

I proposed "detected and imported key_id + one successful API call through that key."
An alternative minimum is "detected and imported key_id only" — proving the wizard works
without requiring a successful external API call in the Phase 2 artifact.

The difference: the live call is also proven by r41-3 (the clean-run transparent proxy
check). Phase 2 is specifically about the import path. A strict reading says Phase 2
needs to prove the wizard, not the proxy call. A fuller reading says prove the whole
chain once manually.

**Council decision needed:** Require import-only, or import-plus-live-call?

### OQ-3: Should `--build` flags be required long-term?

The current clean-run command requires `--build bootstrap subumbra-ui` because VPS
images may be stale. Once the VPS is rebuilt from current source, `--build` could be
omitted. But we can't enforce when that happens. Should the verifier guide document
"omit `--build` only when you have confirmed both images were built from `a9b01bf`
or later"? Or should `--build` remain in the standard command regardless?

**Not a blocker.** Proposing we keep `--build bootstrap subumbra-ui` in the standard
verifier command until the VPS images are freshly rebuilt and explicitly confirmed.
