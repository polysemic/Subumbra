# Gemini Synthesis — Round 41.6: App Validation Cleanup

## 1. Consensus Items

The council is in full agreement on the three core pillars required to close Round 41 honestly:

- **Self-Contained Proof:** The `bootstrap-overlay.env` must be committed to the active round folder (`council/round-41-6-app-validation-cleanup/`) so that a clean VPS pull provides all necessary test configuration [claude-proposal.md:160](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/claude-proposal.md#L160).
- **Stabilized r41-3 Hook:** Wrap the transparent-proxy CURL in a 5-attempt retry loop with 15s sleeps to handle Cloudflare Secret propagation latency [claude-proposal.md:109](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/claude-proposal.md#L109).
- **Manual Phase 2 Proof:** Capture a single manual VPS transcript showing the import wizard detecting a key from a real `.env` file, assigning a `key_id`, and successfully calling the proxy [claude-proposal.md:194](file:///home/eric/git/Subumbra/council/round-41-6-app-validation-cleanup/claude-proposal.md#L194).
- **Narrow Scope:** No product code changes, and no changes to `docker-compose.yml` or core bootstrap logic.

## 2. Disagreements and Resolutions

### D1: Modifying `.env.bootstrap.example` Defaults
- **Claims:** Gemini originally suggested updating the template defaults for verifier convenience; Codex (R41.6-2) and Claude (F8) rejected this as leaking verifier assumptions into product defaults.
- **Evidence:** [.env.bootstrap.example:79](file:///home/eric/git/Subumbra/.env.bootstrap.example#L79) is intentionally blank for operator customization.
- **Resolution:** **REJECT** the modification. The overlay mechanism is the correct way to handle verifier-specific scopes.

### D2: Include `temp/` Workspace Relocation
- **Claims:** Gemini suggested including the fix to move clean-run workspaces out of `/tmp`; Claude (F2) and Codex (R41.6-5) rejected this as unrelated workflow cleanup.
- **Evidence:** This was identified as uncommitted local state in the 41.5 audit [codex-verification.md:155](file:///home/eric/git/Subumbra/council/closed/round-41-5-app-validation/codex-verification.md#L155) and is excluded by the kickoff scope.
- **Resolution:** **REJECT** for 41.6. This is a maintenance task that does not block Round 41 closure.

### D3: Diagnosis vs. Proof Hardening
- **Claims:** Claude's proposal frames the retry loop as a "fix" for Cloudflare lag; Codex (R41.6-3) argues it should be framed as "stabilize and re-prove."
- **Evidence:** While the 401 is highly likely to be a propagation race in [worker/src/worker.js:439-444](file:///home/eric/git/Subumbra/worker/src/worker.js#L439), framing it as as a proof requirement is more evidence-based.
- **Resolution:** **ADOPT** Codex's framing. The success condition is that the proof is now stable, regardless of the underlying transient cause.

## 3. Items Missed / New Findings

### Artifact Write Ordering (Claude F5)
Claude correctly identified a bug in their own implementation proposal: using `>` (overwrite) in the retry loop would lose intermediate attempt logs.
- **Correction:** The `verify-round.sh` must use `>>` (append) and ensure the resulting proof artifact contains the status of ALL 5 attempts for audit transparency.

### Targeted Retry (Claude F6)
Only `r41-3` (transparent proxy) interacts with Cloudflare Secrets. `r41-1` and `r41-2` are Docker-local and deterministic.
- **Correction:** Apply the retry loop narrowly to the `r41-3` hook only.

### Documentation of Prerequisites (Claude 2b)
The `r41-3` test has an out-of-band dependency: the operator's `.env.bootstrap_bak` must contain a valid `OPENAI_KEY`.
- **Finding:** This should be explicitly documented in the 41.6 verifier guide to prevent "false fail" reports from verifiers with empty bootstrap backups.

## 4. Phased Plan

### Phase 1: Approved Plan Creation
- Converge on a single `council/approved/round-41-6-cleanup.md` using Claude's proposal as the base.
- Incorporate the "Append vs Overwrite" artifact fix.
- Include the explicit documentation of the `OPENAI_KEY` prerequisite.

### Phase 2: Implementation (Harness Only)
- Deploy the new `council/round-41-6-app-validation-cleanup/verify-round.sh`.
- Commit the `bootstrap-overlay.env` to the active round folder.

### Phase 3: Verification
- Perform a Clean-Run on the VPS using the new overlay and stabilized hook.
- Capture the Phase 2 manual migration transcript on the VPS.
- Confirm all 3 PASS before final Round 41 closure.

**Signed:** Gemini
