You are participating in a three-LLM review council for the Subumbra project.

Read:
- `council/COUNCIL.md`
- `PROJECT_STATUS.md`
- `CLAUDE.md`
- `docs/project-memory.md`
- `docs/council-memory.md`

Round context:
- `council/round-42-1-worker-auth-recovery/codex-verification.md`
- `council/round-42-2-runtime-auth-reconciliation/kickoff.md`
- `council/round-42-2-runtime-auth-reconciliation/codex-proposal.md`
- `docs/standalone-litellm.md`
- `post-bootstrap.sh`
- `docker-compose.yml`
- `litellm/custom_callbacks.py`

YOUR TASK: Write an independent proposal to:
- `council/round-42-2-runtime-auth-reconciliation/{LLM}-proposal.md`

Important framing:

Round 42.2 is a narrow runtime-auth reconciliation round.

Its purpose is to fix the recurring break where downstream consumers can keep
stale runtime auth material after bootstrap or recovery work, even when the
Worker path itself is healthy.

Focus on:
1. what runtime auth values must stay coherent together
2. what bundled drift checks are still incomplete
3. what supported reconciliation path should exist for standalone LiteLLM
4. what acceptance check should prove signed `subumbra-keys` requests are valid
   after reconciliation

Required sections:
1. Evidence
2. Current vs Desired
3. Proposal
4. Failure Modes
5. Exclusions
6. Open Questions

Rules:
- Do not edit source code
- Cite `file:line` for every factual claim
- Keep scope narrow and recovery-oriented
- Do not reopen broader Round 42 sidecar design work
- Do not redesign token or HMAC architecture in this round
- Avoid broad observability expansion or secret-bearing diagnostics
