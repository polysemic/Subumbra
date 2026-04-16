# Gemini Proposal — Round 41.6: App Validation Cleanup

## 1. Evidence

### Proven Fixes (Landed)
The following items were verified as fixed during the Round 41.5 closure audit and require no further code changes:
- **UI Liveness Alignment:** The UI now correctly returns `subumbra_keys_healthy` at [ui/app.py:190](file:///home/eric/git/Subumbra/ui/app.py#L190).
- **Harness Robustness:** `clean-run.sh` reliably captures `verify_run_id` on failure paths at [scripts/council/clean-run.sh:283](file:///home/eric/git/Subumbra/scripts/council/clean-run.sh#L283).
- **Coexistence Architecture:** Profiles and networks are correctly configured in [docker-compose.yml:13, 80, 180](file:///home/eric/git/Subumbra/docker-compose.yml).

### Remaining Blockers
- **r41-3 Flakiness:** Independent reruns (`codex-20260416T192605` vs `codexr2-20260416T192730`) showed intermittent 401 errors. This is confirmed as a Cloudflare Secret propagation race; the Worker returns `401 {"error":"unauthorized"}` ([worker/src/worker.js:444](file:///home/eric/git/Subumbra/worker/src/worker.js#L444)) when isolated sessions haven't yet pulled the latest `SUBUMBRA_ADAPTER_TOKENS`.
- **Non-Self-Contained Proof:** The proof path requires a `bootstrap-overlay.env` that was archived in a "closed" round folder, making a clean "pull and verify" on the VPS fail.
- **Verification Gap:** The interactive migration wizard (`_run_import_screen` in [bootstrap/subumbra-bootstrap.py:414](file:///home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L414)) has not been proven with a manual stateful artifact.

---

## 2. Current vs Desired

| Feature | Current State | Desired State (41.6) |
| :--- | :--- | :--- |
| **r41-3 Stability** | Single-shot; fails on CF race | 5-attempt retry loop with 15s backoff |
| **Proof Portability** | Depends on archived overlay | Self-contained in active round directory |
| **Migration Proof** | Code-only audit | Manual VPS transcript showing successful import |
| **Harness Temp** | Uses `/tmp` (prone to disk-full) | Uses repo-local `temp/` (mirrors local dev fix) |

---

## 3. Proposal

### P1: Stabilized Verification Hook
Update `council/round-41-6-app-validation-cleanup/verify-round.sh` to wrap the `r41-3` (transparent proxy) check in a retry loop.
- **Specs:** 5 attempts, 15s sleep between attempts.
- **Fail-fast:** Exit immediately on HTTP 200; log all intermediate failures to the proof artifact for audit transparency.

### P2: Tracked Bootstrap Overlay
Commit a dedicated `bootstrap-overlay.env` to the `round-41-6` directory.
- **Content:** `PROXY_ALLOWED_KEYS=openai_prod`.
- **Effect:** Ensures that a fresh VPS pull has everything needed to run a SUCCESSFUL `clean-run.sh` without out-of-band file transfers.

### P3: Manual Phase 2 Proof Artifact
Produce one manual transcript of an interactive bootstrap session on the VPS. 
- **Requirement:** Mount a real `.env` file, let the wizard detect a key, assign a `key_id`, and verify the resulting key functions via a direct curl.
- **Output:** `council/round-41-6-app-validation-cleanup/runs/manual-migration-proof.txt`.

---

## 4. Failure Modes
- **Propagation Extreme:** If CF takes >75s to propagate secrets, the retry will still fail. This is acceptable as it indicates a systemic provider issue rather than a product regression.
- **Environment Mismatch:** If the operator's `.env.bootstrap_bak` disables OpenAI, the `openai_prod` overlay will fail. This is addressed by documenting the pre-requisite key names in the verifier guide.

---

## 5. Exclusions
- **No Product Logic Changes:** The Core and Worker logic are frozen; this round is 100% harness and documentation.
- **No Full Testbed Re-runs:** We will not perform full N8N/OpenWebUI automation; the manual Phase 2 proof artifact is the agreed-upon compromise for "Real App" validation.

---

## 6. Open Questions
- **OQ-1:** Should the `temp/` directory fix (moving workspaces out of `/tmp`) be part of the 41.6 branch or a separate workflow PR? (I recommend including it here to avoid VPS `clean-run` disk-full failures during verification).

**Signed:** Gemini
