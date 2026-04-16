# Gemini Verification — Round 41.5 Closure Audit

**Status: NOT CLOSURE-GRADE (Requires Round 41.6 Cleanup)**

Following a detailed audit of the current `round-41-real-app-validation` branch (SHA `a9b01bf`), I agree with the "Codex" position: Round 41 cannot be honestly closed yet. While the core implementation is present and two specific harness bugs have been fixed, the verification surface remains flaky and non-self-contained.

## 1. Closure Verdict
Round 41 implementation is **80% verified**, but the remaining 20% involves a non-deterministic verification hook and a dependency on out-of-band state that violates the "clean branch reproduction" requirement. Treating this as a PASS now would be "hand-waving" flaky evidence.

## 2. What Is Actually Fixed (Landed in Branch)
These items are no longer blockers and are verified in the committed code:

- **Phase 0 & 1 Core:** The `subumbra-net` coexistence architecture, profile-gated LiteLLM, and the bootstrap `IMPORT_PROVIDER_WHITELIST` / `IMPORT_EXCLUSION_LIST` are all present and functionally sound.
- **Bug Fix — P9.5 UI Status:** The UI now correctly returns `subumbra_keys_healthy` (fixed in `ui/app.py`), and the harness correctly checks for it.
- **Bug Fix — clean-run ID Capture:** `clean-run.sh` now correctly captures the `verify_run_id` even on a failed run (fixed in `scripts/council/clean-run.sh`).
- **Idempotency:** The KV namespace creation logic is idempotent as required by 41.2.

## 3. What Is Not Closure-Grade Yet (Blockers)

### A. Non-Self-Contained Proof (`r41-3`)
The `r41-3` transparent proxy test hardcodes a dependency on `openai_prod` which is not enabled by default in the bootstrap templates. 
- **The Issue:** A clean pull and run will FAIL this test unless an extra `bootstrap-overlay.env` is manually provided (archived in `council/closed/`).
- **Requirement:** Tests on the round branch must pass against the default state of that branch.

### B. Flaky Verification Loop
Independent reruns by Codex produced alternating 200 (PASS) and 401 (FAIL) results on the same code state.
- **Diagnosis:** The 401 error originates from `worker/src/worker.js` line 444 when `SUBUMBRA_ADAPTER_TOKENS` are not yet propagated or are invalid. 
- **Cause:** Cloudflare Secrets used in the automated `clean-run` path are eventually consistent. The harness tries to run tests immediately after bootstrap, often hitting a stale worker isolate.
- **Requirement:** The verification harness must be resilient to asynchronous propagation (e.g., a simple retry or stabilization wait in `run_round_hooks`).

### C. Phase 2 Verification Gap
Claude’s report explicitly noted that the manual app cutover steps (LiteLLM, OpenWebUI, N8N) were not re-verified. 
- **Issue:** A "PASS" for a migration round that ignores the migration itself is incomplete.

## 4. What Stays In 41.5
- The current implementation of `subumbra-bootstrap.py` and `docker-compose.yml`.
- The branding fixes and harness structural updates.
- The diagnosis of the 401 flakiness.

## 5. What Must Move To 41.6 (Mandatory Cleanup)
To close Round 41 honestly, a final 41.6 iteration must:

1. **Stabilize `r41-3`:** Add a retry loop or a 10s "propagation wait" to the transparent proxy check in `verify-round.sh`.
2. **Make Proof Self-Contained:** Either update `.env.bootstrap.example` to include `openai_prod` in `PROXY_ALLOWED_KEYS` by default, or move the overlay content into the branch’s testing logic.
3. **Formalize Lane C Proof:** Capture a single manual proof artifact (e.g., a CURL or UI grab) showing a real application (e.g. Sidecar/LiteLLM) actually using an imported key.

**Signed:** Gemini
