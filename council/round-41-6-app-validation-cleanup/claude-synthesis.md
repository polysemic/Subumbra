# Claude Synthesis — Round 41.6: App Validation Cleanup

## 1. Consensus Items

All three reviewers (Claude, Codex, Gemini) agree on the following without residual
disagreement.

**C1 — Round scope is three items only**

The round exists to make Round 41 honestly closeable, nothing else:
- Move `bootstrap-overlay.env` to the active round folder so the proof path is
  self-contained on a clean pull
- Add a new `verify-round.sh` with a retry loop that stabilizes `r41-3`
- Capture one minimal Phase 2 live migration proof (import wizard + live call)

Evidence: [kickoff.md:7-29](council/round-41-6-app-validation-cleanup/kickoff.md#L7),
all three reviews agree on this scope without adding to it.

**C2 — Overlay must move to the active round folder**

The current overlay at `council/closed/round-41-real-app-validation/bootstrap-overlay.env`
is not reliably present on a clean VPS pull. Moving it to
`council/round-41-6-app-validation-cleanup/bootstrap-overlay.env` makes the clean-run
command self-contained. The `--bootstrap-overlay` resolution at
[scripts/council/clean-run.sh:92-94](scripts/council/clean-run.sh#L92) handles relative
paths from `repo_root` correctly. No product code changes required.

**C3 — r41-3 stabilization belongs in the round-local verify hook, not in product code**

The 401 pattern is consistent with CF Secrets propagation lag at
[worker/src/worker.js:439-444](worker/src/worker.js#L439). All three reviewers accept
a retry loop in the round-local `verify-round.sh` as the right fix. No changes to
the Worker, bootstrap, or subumbra-keys.

**C4 — Phase 2 minimum proof is: import wizard run + one successful live call**

The approved Round 41 plan at
[council/approved/real-app-validation.md:432-460](council/approved/real-app-validation.md#L432)
specified an operator cutover path that was never proven with a live run. All three
reviewers agree the minimum acceptable proof is: wizard detects a key from a mounted
`.env`, operator assigns a key_id, bootstrap completes, one curl through
`subumbra-proxy` using the imported key_id returns HTTP 200.

**C5 — Two items are explicitly excluded**

- Local workspace `temp/` relocation: not a closure blocker; identified as uncommitted
  local state in the 41.5 audit; excluded by the kickoff
- Any change to `.env.bootstrap.example` defaults: leaks verifier assumptions into the
  operator template; the empty `PROXY_ALLOWED_KEYS=` at
  [.env.bootstrap.example:79](.env.bootstrap.example#L79) is correct for operators

**C6 — r41-1 and r41-2 need no changes**

Both checks are Docker-local with no CF dependency. Only r41-3 requires a retry loop.

---

## 2. Disagreements

### D1 — How confidently to state the r41-3 root cause

**Gemini review (R41.6-G4):** states the CF Secrets propagation timing window as the
confirmed cause.

**Codex review (R41.6-3) and synthesis (§Disagreement B):** the timing diagnosis is
plausible and well-supported, but the approved plan should phrase the work as "stabilize
and re-prove" rather than claiming root-cause certainty before rerun evidence exists.

**Claude review (F4):** acknowledged the diagnosis is plausible and the evidence
supports it, but agreed with Codex's framing.

**My position:** Codex is right on the framing. The code at
[worker/src/worker.js:439-444](worker/src/worker.js#L439) confirms that `401` is
the not-in-valid-set path, not the misconfiguration path (503 at
[worker/src/worker.js:435](worker/src/worker.js#L435)). That's consistent with
propagation lag. But "consistent with" is not "proven." The retry loop is the right
fix regardless of whether the underlying cause is isolate caching, secret consistency
lag, or some third thing. The approved plan should require "stabilize and re-prove,"
not assert root-cause closure.

**This is a wording difference, not a substantive disagreement.** All three reviewers
want the retry. Only the framing differs.

### D2 — Retry timing parameters

My proposal said 5×15s. Gemini's proposal said 5×15s. Neither was challenged in the
reviews. Codex's synthesis accepted these parameters without objection.

**My position:** Accept 5×15s (75 seconds maximum). This is evidence-appropriate:
Codex's two runs in 41.5 alternated between 401 and 200 with no intentional delay
between them, meaning the propagation window was less than the time between two manual
sequential runs (likely 30-60 seconds). 75 seconds is conservative relative to that
observation. If a real failure mode is present (bad key_id, missing record, wrong
scope), 5 attempts will expose it rather than hide it — a persistent failure after 5
attempts is unambiguous.

This was OQ-1 in my proposal. It is now resolved: 5×15s.

### D3 — Phase 2 proof: import-only or import-plus-live-call

My proposal left this as OQ-2. Both the Codex review (§Minimal live proof should stay
minimal) and Codex synthesis (§C5) converge on import + live call. Gemini's review
agrees. My own review (F7) agreed.

**My position:** Import + live call is required. This closes the gap between code-audit
and runtime proof. The call should use the transparent proxy path
(`http://127.0.0.1:8090/t/v1/chat/completions` with `Authorization: Bearer <key_id>`)
because that is the exact path that OpenWebUI would use, and it is the same path proven
by r41-3. Using the same call shape as r41-3 makes the Phase 2 proof consistent with
the automated proof. If r41-3 passes in the clean-run, and Phase 2 shows the import
wizard produced the same key_id used in r41-3, the full chain is proven.

This was OQ-2 in my proposal. It is now resolved: import + transparent-proxy live call.

### D4 — `--build` flags long-term

OQ-3 in my proposal. Neither Codex nor Gemini raised an objection. My review
recommended keeping `--build bootstrap subumbra-ui` in the standard command until VPS
images are freshly rebuilt and confirmed.

**My position:** Keep `--build bootstrap subumbra-ui` in the standard clean-run command
for 41.6. Document the condition for omitting it: VPS images must have been built from
`a9b01bf` or later. This is a verifier prerequisite note, not a product constraint.

This was OQ-3 in my proposal. Resolved: keep `--build` in the standard command.

---

## 3. Anything The Others Missed

**Implementation bug in my own proposal that all reviews correctly caught:**

My `claude-proposal.md` retry shape (the example in §Change 1) had an artifact
write-ordering bug: intermediate attempt logs were appended with `>>` but the final
block used `>` (overwrite), destroying the intermediate evidence. Codex's review
(R41.6-3) surfaced this implicitly by saying "require re-proof after the retry change."
My own review (F5) identified it explicitly and proposed a corrected shape. Codex's
synthesis (§3) preserved it. The approved plan must specify that the artifact captures
all attempt results in order.

**An additional constraint neither proposal addressed explicitly:**

The Phase 2 proof requires the VPS to have a running LiteLLM stack with a real `.env`
file to mount. This is already a prerequisite for the original Round 41 testbed, but
it should be stated in the 41.6 verifier prerequisites so the implementing agent doesn't
assume the VPS is in clean-run state for Phase 2. Phase 2 is a manual session against
the live stack, run separately from the clean-run.

**Resolved open questions that Codex's synthesis also listed but did not close:**

Codex's synthesis left the retry parameters as accepted without confirming them. I
close OQ-1/2/3 explicitly above.

---

## 4. Phased Plan

These are implementation instructions for the implementing agent, ordered by dependency.

### Phase 1 — Self-contained proof input (no runtime dependency)

Deliverable: `council/round-41-6-app-validation-cleanup/bootstrap-overlay.env`

Content:
```bash
# Round 41.6 bootstrap overlay.
# Sets proxy key scope for r41-3 transparent-proxy proof.
# Requires OPENAI_KEY set in .env.bootstrap_bak.
# OPENAI_KEY_ID must be empty or "openai_prod" (default).
PROXY_ALLOWED_KEYS=openai_prod
```

The file must be committed force-added to git (`git add -f`). No other files change.

**Verifier prerequisites (document in the round folder):**
- VPS `.env.bootstrap_bak` must contain a valid `OPENAI_KEY`
- `OPENAI_KEY_ID` must be empty or `openai_prod` in that file

**Standard clean-run command:**
```bash
./scripts/council/clean-run.sh \
  --round round-41-6-app-validation-cleanup \
  --agent <name> \
  --build bootstrap subumbra-ui \
  --bootstrap-overlay council/round-41-6-app-validation-cleanup/bootstrap-overlay.env
```

### Phase 2 — r41-3 stabilization (harness only)

Deliverable: `council/round-41-6-app-validation-cleanup/verify-round.sh`

Inherited from the 41.5 version: r41-1 (network membership) and r41-2 (bundled litellm
absent) are copied verbatim. No retry logic on either.

r41-3 only: wrap the curl in a loop of up to 5 attempts, 15 seconds between attempts.
The proof artifact must contain ALL attempt results in order before the final outcome.
A clean implementation shape:

```bash
# Initialize artifact with header
: >"$proxy_artifact"
printf '# PROOF: round 41.6 direct transparent proxy request\n' >>"$proxy_artifact"

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

### Phase 3 — Minimal Phase 2 manual proof (live VPS, separate from clean-run)

Deliverable: `council/round-41-6-app-validation-cleanup/runs/phase2-import-proof/manual-migration-proof.txt`

This is a manual session. The implementing agent runs on the live VPS stack (not a
clean-run workspace).

Required content of the proof artifact:
1. Command run (with volume mount path):
   ```bash
   docker compose --profile bootstrap run --rm \
     -v /opt/litellm:/host_litellm:ro \
     -it bootstrap
   ```
2. Terminal output showing at least one provider key detected in `/host_litellm/.env`
3. Operator accepting and assigning a key_id (default `openai_prod` or explicit)
4. Bootstrap completion message
5. One curl to the transparent proxy using the imported key_id:
   ```bash
   curl -s -X POST http://127.0.0.1:8090/t/v1/chat/completions \
     -H 'Authorization: Bearer openai_prod' \
     -H 'Content-Type: application/json' \
     -d '{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"ping"}],"max_tokens":3}'
   ```
6. Response showing HTTP 200 and a non-empty response body

The proof file is committed to the round folder as a plain-text transcript.

### Phase 4 — Re-proof and final close decision

After Phases 1–3 are committed:
1. Pull the branch on the VPS
2. Run the standard clean-run command (Phase 1)
3. Confirm `r41-3` is now stable (PASS without alternate FAIL/PASS behavior)
4. Confirm all other proof artifacts are PASS
5. Verify the Phase 3 manual proof artifact is present and plausible
6. Council members produce verification reports in the 41.6 folder
7. If all three verify PASS, Round 41 can be honestly closed

---

## 5. Consensus Status

**Consensus is sufficient for approval.** There are no blocking technical disagreements.

The three live disagreements (D1 root-cause framing, D2 retry timing, D3 Phase 2 proof
depth) have all been resolved:
- Use "stabilize and re-prove" framing, not "root cause confirmed"
- 5 attempts × 15 seconds
- Import wizard + transparent-proxy live call

The approved plan should additionally specify:
- Artifact write ordering: all attempt results captured before final outcome
- Phase 2 prerequisite: VPS must have a live LiteLLM stack with a real `.env`
- `--build bootstrap subumbra-ui` in the standard command until images are freshly built
  from `a9b01bf` or later

---

## 6. What the Approved Plan Must Say

These are the specific points that must appear in the approved plan to prevent
re-litigating settled decisions in the implementation and verification phases:

| Point | Must say |
|-------|----------|
| Overlay location | `council/round-41-6-app-validation-cleanup/bootstrap-overlay.env` — active round, not archived path |
| r41-3 fix framing | "stabilize and re-prove" — not "root cause confirmed" |
| Retry loop scope | r41-3 curl only; r41-1 and r41-2 unchanged |
| Retry parameters | 5 attempts, 15s sleep between attempts |
| Artifact requirement | All attempt results (not just final) captured in order |
| Phase 2 proof | Import wizard + one transparent-proxy call through imported key_id |
| Phase 2 timing | Manual session against live stack; not inside the clean-run workspace |
| `.env.bootstrap.example` | No changes |
| `temp/` workspace move | Explicitly excluded |
| `--build` flags | Keep `--build bootstrap subumbra-ui` until images freshly rebuilt |
