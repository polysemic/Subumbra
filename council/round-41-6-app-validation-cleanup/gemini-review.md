# Gemini Review — Round 41.6: App Validation Cleanup

## 1. Findings Table

| ID | Severity | Finding | Evidence |
|:---|:---|:---|:---|
| R41.6-G1 | High | **Consensus on Blockers:** Both Claude and Gemini correctly identify the same three blockers preventing Round 41 closure: flakiness in `r41-3`, non-self-contained overlay, and the Phase 2 manual proof gap. | [claude-proposal.md:20-82](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/claude-proposal.md#L20), [gemini-proposal.md:12-25](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/gemini-proposal.md#L12) |
| R41.6-G2 | High | **Scope Creep (Rejected):** Codex correctly identifies that Gemini's suggestion to modify `.env.bootstrap.example` defaults is scope creep and leaks verifier data into product templates. I agree with Codex here: the overlay should remain external to the primary `.env.bootstrap.example`. | [codex-review.md:43-45](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/codex-review.md#L43), [.env.bootstrap.example:79](file:///home/eric/git/Subumbra/.env.bootstrap.example#L79) |
| R41.6-G3 | High | **Workflow Cleanup (Rejected):** My previous proposal to include the `temp/` workspace fix in Round 41.6 should be rejected. While functionally useful, it is "unrelated workflow cleanup" explicitly excluded by the kickoff. | [kickoff.md:26](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/kickoff.md#L26), [gemini-proposal.md:73-75](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/gemini-proposal.md#L73) |
| R41.6-G4 | Medium | **Retry Loop Robustness:** Claude's 5x15s retry loop proposal is well-reasoned and addresses the Cloudflare Secret propagation timing window effectively. No product code changes are required. | [claude-proposal.md:109-158](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/claude-proposal.md#L109), [worker/src/worker.js:444](file:///home/eric/git/Subumbra/worker/src/worker.js#L444) |
| R41.6-G5 | Medium | **Manual Proof Depth:** There is consensus that a single manual VPS transcript showing the wizard import + live call is sufficient to bridge the Phase 2 gap for the "Real App" validation promise. | [claude-proposal.md:194-221](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/claude-proposal.md#L194), [gemini-proposal.md:69-71](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/gemini-proposal.md#L69) |

## 2. Detailed Analysis

### 2.1 Stability and Self-Containment
All proposals agree that the 401 error in `r41-3` is a timing issue in the automated harness ([worker/src/worker.js:444](file:///home/eric/git/Subumbra/worker/src/worker.js#L444)) and that the `bootstrap-overlay.env` is currently out-of-band. 

Claude’s proposal to move the overlay to the active round folder is the most portable and least intrusive. I retract my suggestion to modify the product template `.env.bootstrap.example` and concur with Codex: keeping verifier data in the round-specific folder maintains the proper boundary between the product and the certification harness.

### 2.2 Proof of Migration (Phase 2)
The 41.5 audits confirmed that the interactive migration path in `bootstrap/subumbra-bootstrap.py` ([line 414](file:///home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L414)) was only code-audited, not proven at runtime. The proposal to capture a manual transcript of a real `.env` import on the VPS is the correct way to close this gap without reopening the full testbed automation scope.

### 2.3 Exclusion of Workflow Fixes
I agree with the council that the `temp/` folder change is a workflow improvement that should not be bundled into a closure round. While it prevents disk-full errors for verifiers, it is not a blocker for "honestly closing" the Round 41 feature set. It should move to a separate cleanup task.

## 3. Recommendations

1. **Approved Path:** Adopt **Claude’s Proposal** as the primary blueprint for Round 41.6.
2. **Strict Scope:** Maintain the focus only on:
    - Stabilization of `r41-3` via retry loop.
    - Active-round self-contained overlay.
    - Minimal manual Phase 2 proof artifact.
3. **No Product Contamination:** Ensure that `.env.bootstrap.example` remains a clean template for operators, not a hardcoded "green list" for the verifier hook.
4. **Verification:** Require a new Clean-Run on the VPS after these changes to verify the flakiness is indeed eliminated.

**Signed:** Gemini
