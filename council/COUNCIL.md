# Council — Multi-LLM Review Workflow

This project uses three LLMs (Claude, Codex, Gemini) as a review council.
No single LLM's output is trusted without cross-validation.

## Directory Structure

```
council/
├── approved/               <- active: plans approved but not yet closed
│   └── topic-name.md       <- merged plan, ready to implement
│
├── closed/                 <- completed rounds, archived for reference
│   └── round-N-topic/      <- one folder per closed round
│       ├── claude-review.md
│       ├── codex-review.md
│       ├── gemini-review.md
│       ├── *-synthesis.md
│       ├── *-verification.md
│       └── approved-topic.md  <- snapshot of plan at close
│
└── round-N-topic/          <- active round (in progress)
    ├── claude-proposal.md  <- independent proposals (Phase 0)
    ├── codex-proposal.md
    ├── gemini-proposal.md
    ├── claude-proposal-2.md <- optional alignment pass
    ├── codex-proposal-2.md
    ├── gemini-proposal-2.md
    ├── claude-review.md    <- independent reviews (no peeking at others)
    ├── codex-review.md
    ├── gemini-review.md
    ├── *-synthesis.md      <- after reading all three reviews
    ├── *-verification.md   <- after performing a full verification
    └── disputes.md         <- unresolved items (if any)
```

## Workflow Rules

### Phase 0: Proposals
- Each LLM writes an independent proposal to `council/{ROUND}/{LLM}-proposal.md`
- If proposals diverge, an optional `{LLM}-proposal-2.md` alignment pass may be used
- No source code changes during proposal phase

### Phase 1: Independent Review
- Each LLM reviews the code/plan **independently** (no reading other reviews)
- Output goes to `council/round-N/llm-review.md`
- Reviews must cite file paths and line numbers as evidence

### Phase 2: Cross-Review & Synthesis
- Each LLM reads all three reviews
- Each writes a synthesis doc responding to the others' findings
- Disagreements must state: what was claimed, what the evidence shows, resolution

### Phase 3: Approval
- When all three agree on a path forward, the merged plan moves to `council/approved/`
- The approved doc becomes the implementation spec
- No code changes until a plan is in `approved/`

### Phase 3.5: Verification of Approved Plan
- Each LLM verifies the approved plan in `council/approved/{TOPIC}.md` is consistent with the synthesis documents
- If the approved plan is not consistent with the synthesis documents, the plan must be updated to be consistent with the synthesis documents
- If the approved plan is consistent with the synthesis documents, the plan is ready to be implemented

### Phase 4: Implementation
- One LLM implements from the approved spec

### Phase 5: Verification
- The other two verify the implementation matches the spec
- Results go in the same round folder

## Verification Policy

### Script Roles

| Script | Role | What It Is Not |
|--------|------|----------------|
| `scripts/council/clean-run.sh` | Preferred fresh-state proof wrapper for certification-style runs in a temporary workspace | The only verification path |
| `scripts/council/preflight.sh` | Readiness gate for health, reachability, and baseline service state | Functional proof |
| `scripts/council/reset.sh` | Fresh-state foundation for recreate / rebuild decisions and token-drift checks | Functional proof |
| `scripts/council/verify.sh` | Official proof capture into run-tagged artifacts | General-purpose diagnostics |

### Fresh-State Policy

- `clean-run.sh` is the preferred default for fresh-state, certification-style proof capture.
- Use direct `reset.sh` plus `verify.sh` for follow-up reruns, diagnostics, or when clean-run v1 is impractical.
- If an approved plan requires editing `.env.bootstrap` before bootstrap, make that host-repo edit before starting `clean-run.sh`, because the wrapper copies the host bootstrap file into its temporary workspace.
- `reset.sh --build <services>` is required when image-built service source changed.
- Plain `reset.sh` is required when token, auth, or bootstrap-affecting changes were made, or when the verifier cannot confidently confirm the running state already matches the implementation under test.
- A verifier may skip reset only if the running state is already known-good and the reason is documented explicitly in the verification report.

For the current rebuild distinction, use the help text in `scripts/council/reset.sh`.

### Evidence Taxonomy

- Official PASS evidence is the run-tagged proof artifacts created by `verify.sh`.
- Direct-run PASS artifacts live under `council/{round}/runs/{run-id}/`.
- Clean-run PASS artifacts also live under `council/{round}/runs/{verify-run-id}/`; the wrapper's step logs and diagnostics live alongside them in `council/{round}/runs/{clean-run-id}/`.
- `preflight.sh` output is readiness only.
- Logs, manual `curl`, and `docker exec` are diagnostic-only unless a round's approved plan explicitly requires an additional host-facing manual check.

### Harness Maintenance During Verification

If a verifier encounters a harness bug that prevents `verify.sh` from running:

- they may fix the harness bug
- they must document the fix in the verification report
- they must rerun proof capture after the fix
- the fix is maintenance, not a spec-bypass workaround

### Product Approved Plan Boundary

Product approved plans should define:

- what must be proven
- the proof checks and success conditions
- any round-specific prerequisites

Product approved plans should not restate:

- the full harness sequence policy
- the global PASS-versus-diagnostic taxonomy
- the general script role definitions

Reference:
- `docs/subumbra-testing.md`

### Phase 6: Close Out
- One LLM closes out the round
- Updates council.md with the round status
- Archives the round folder

## Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Folder | `round-N-topic` | `round-3-callbacks` |
| Proposal | `llm-proposal.md` | `codex-proposal.md` |
| Review | `llm-review.md` | `codex-review.md` |
| Synthesis | `llm-synthesis.md` | `claude-synthesis.md` |
| Approved | `topic-name.md` | `dynamic-resolver.md` |

## Current State

| Round | Topic | Status | Archive |
|-------|-------|--------|---------|
| 1 | Security review | Closed | `council/closed/round-1-security/` |
| 2 | Fresh re-review | Closed | `council/closed/round-2-re-review/` |
| 3 | Callback system | Closed | `council/closed/round-3-callbacks/` |
| 4 | Dynamic resolver | Closed | `council/closed/round-4-dynamic-resolver/` |
| 5 | Interactive bootstrap + key_id | Closed | `council/closed/round-5-interactive-bootstrap/` |
| 6–7 | Envelope encryption + per-key rotation | Closed | `council/closed/round-6-7-envelope-encryption/` |
| 7–8 | Truth-alignment cleanup | Closed | `council/closed/round-7-8-truth-alignment/` |
| 9 | Provider coupling | Closed | `council/closed/round-9-provider-coupling/` |
| 10 | Decoupling | Closed | `council/closed/round-10-decoupling/` |
| 11 | Host map centralization | Closed | `council/closed/round-11-host-map/` |
| 13 | Token sync guardrails | Closed | `council/closed/round-13-token-sync/` |
| 15 | Provider registry centralization | Closed | `council/closed/round-15-provider-coupling-2/` |
| 17 | Provider catalog | Closed | `council/closed/round-17-provider-catalog/` |
| 19 | Adapter contract definition | Closed | `council/closed/round-19-adapter-contract/` |
| Cleanup | Council workflow cleanup | Closed | `council/closed/council-cleanup/` |
| 20 | Path-Prefix Ownership | Closed | `council/closed/round-20-path-prefix-ownership/` |
| 21 | Transport Installation and Activation | Closed | `council/closed/round-21-transport-layer-migration/` |
| 22 | Transport Cut-Over and Dead-Code Removal | Closed | `council/closed/round-22-transport-cutover/` |
| 23 | Second Adapter Proof | Closed | `council/closed/round-23-second-adapter-proof/` |
| 24 | MVP Direction and Core Product Shape | Closed | `council/closed/round-24-mvp-direction/` |
| 25 | Explicit Sidecar Baseline | Closed | `council/closed/round-25-explicit-sidecar-baseline/` |
| 26 | Provider Expansion + Operator Usability | Closed | `council/closed/round-26-provider-expansion-operator-usability/` |
| 27 | Functional Proof of User-Facing Core | Closed | `council/closed/round-27-functional-proof/` |
| 28 | Fresh Verification Harness | Closed | `council/closed/round-28-verification-harness/` |
| 29 | Adapter Identity and Forge Access Scope | Closed | `council/closed/round-29-adapter-identity/` |
| 30 | Revocation And TTL Guardrails | Closed | `council/closed/round-30-revocation-ttl-guardrails/` |
| 31 | Structured Audit Trail | Closed | `council/closed/round-31-structured-audit-trail/` |
| 32 | Rotation And Recovery Ergonomics | Closed | `council/closed/round-32-rotation-recovery-ergonomics/` |
| 33 | Transparent Sidecar | Closed | `council/closed/round-33-transparent-sidecar/` |
| Clean Run Harness | Fresh-install harness v1 | Closed | `council/closed/clean-run-harness/` |
| Provider & Adapter Flexibility Roadmap | Planning round — Rounds 34-36 sequencing | Closed | `council/roadmap-provider-adapter-flexibility/` |
| 34 | Provider Flexibility | Closed | `council/closed/round-34-provider-flexibility/` |
| 35 | Adapter Flexibility | Closed | `council/closed/round-35-adapter-flexibility/` |
| 36 | Live Provider Registry | Closed | `council/closed/round-36-live-provider-registry/` |
| 37 | Cleanup Review | Closed | `council/closed/round-37-cleanup-review/` |
| 38 | System Review | Closed | `council/closed/round-38-system-review/` |
| 39 | POC Deployment Hardening | Closed | `council/closed/round-39-poc-deployment-hardening/` |
| 40 | Broader Decoupling And Security Hardening | Closed | `council/closed/round-40-broader-decoupling-and-security-hardening/` |
| 41 | Real App Validation | Closed | `council/closed/round-41-real-app-validation/` |
| 41.1 | VPS Test Environment Planning | Closed | (planning only; absorbed into Round 41.2 VPS setup) |
| 41.5 | App Validation Re-Verification | Closed | `council/closed/round-41-5-app-validation/` |
| 41.6 | App Validation Cleanup | Closed | `council/closed/round-41-6-app-validation-cleanup/` |
| 41.7 | Standalone LiteLLM Runtime Fix | Open | `council/round-41-7-standalone-litellm-runtime-fix/` |
| 42.2 | Runtime Auth Reconciliation | Closed | `council/closed/round-42-2-runtime-auth-reconciliation/` |
| 42.3 | App-Owned Integrations | Open | `council/round-42-3-app-owned-integrations/` |
| 41.2 | VPS Stabilization and Full E2E Verification | Closed | `council/closed/round-41-2-vps-testing/` |
| 41.3 | Subumbra Rebrand | Closed | `council/closed/round-41-3-rebrand/` |
| 41.4 | Full Subumbra Rebrand | Closed | `council/closed/round-41-4-full-rebrand/` |
| Clean Run v2 | Harness improvements + usage policy | Closed | `council/closed/clean-run-v2/` |

Historical round folders that previously remained at `council/` root as
pre-convention artifacts were moved into `council/closed/` on 2026-04-01 by
explicit user request during the council-cleanup close-out. Current convention
is that closed rounds are archived to `council/closed/` by the close-out step.

Roadmap alignment note:
- `council/roadmap-next-rounds/` contains the planning-only proposal/synthesis
  work that produced the approved next-rounds roadmap recorded in
  `council/approved/next-rounds-roadmap.md` on 2026-04-07. This was a roadmap
  merge, not an implementation round, so it remains outside the numbered round
  archive flow. The planning round is now closed; the folder remains in place
  as its working and verification record.

Harness usage alignment note:
- `council/harness-usage-alignment/` contained the planning-only proposal and
  synthesis work that produced `council/approved/harness-usage-alignment.md`
  and the related doc-only implementation on 2026-04-07. This was a non-numbered
  process round, not a product implementation round. It is now closed and
  archived under `council/closed/harness-usage-alignment/`.

Provider and adapter flexibility roadmap note:
- `council/roadmap-provider-adapter-flexibility/` contains the planning-only
  proposal, review, and investigation work that produced
  `council/approved/provider-adapter-flexibility-roadmap.md` on 2026-04-09.
  This was a roadmap merge, not an implementation round, so it remains outside
  the numbered archive flow. The planning round is now closed; the folder
  remains in place as its working and verification record.
