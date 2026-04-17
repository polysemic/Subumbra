# PROJECT_STATUS
*Current state — updated 2026-04-13*
*Rounds 1–36 closed. See `council/COUNCIL.md` for round history and current status.*

---

## Architecture

V2 Asymmetric Envelope Encryption (deployed, verified by all three council members).

- RSA-4096 key pair: public key on host, private key in CF Secrets
- Per-record AES-256-GCM DEKs wrapped by RSA public key
- AAD binding: `subumbra:v2:<key_id>`
- Offline per-key rotation via `--rotate` (no CF interaction)
- LiteLLM now uses canonical `POST /proxy` to reach the Worker; the old header-gated compatibility route has been removed
- Worker-side provider validation and upstream routing policy now come from the live Cloudflare KV provider registry; provider-specific auth branches are removed from Worker/DO logic
- V1 symmetric `MASTER_DECRYPTION_KEY` path fully removed from code
- Current status of project is proof of concept with no userbase. No backward compatibility required or should be considered, unless required for functionality or security. This will be updated as the project grows into an MVP with a userbase.

See `council/closed/round-6-7-envelope-encryption/` for the full verification record.

---

## Vision

Subumbra is intended to become a universal zero-trust key broker, not a
LiteLLM-specific add-on. Every layer should be treated as potentially
compromised: app servers, containers, config files, and hosts should never be
trusted with enough material to recover provider secrets in usable form on
their own.

The long-term architecture goal is:

- Core: `subumbra-keys` + Cloudflare Worker decrypt/proxy contract
- Provider policy: explicit host/auth allowlist and request validation
- Adapters: LiteLLM as Adapter #1, sidecar/service next, other adapters later

The design standard is that a partial compromise should yield only useless
fragments. Applications should request narrow capability through hardened
adapters, while decrypt authority, provider policy, and fail-closed validation
remain inside the Subumbra core boundary.

---

## Known Limitations

Deferred by council consensus. Acceptable for current single-operator POC deployment.

| ID | Description | Rationale |
|----|-------------|-----------|
| MEDIUM-1 | Python memory scrubbing is best-effort; `bytearray` zeroing does not prevent copies in `os.environ` or immutable `str` objects | No code fix possible in CPython |
| MEDIUM-5 | `/health` leaks `keys_loaded` count unauthenticated | Acceptable on Docker internal network with no host exposure |
| MEDIUM-7 | `/api/status` unauthenticated | Bound to `127.0.0.1:8080` (localhost only); add basic auth before multi-user |
| G-MEDIUM-2 | Dashboard health reflects subumbra-keys only; no end-to-end health probe | Post-POC architecture change |
| G-MEDIUM-3 | CF Worker buffers full body with no size limit (128 MB CF cap) | Low risk for small-team internal use |
| AUDIT-RETENTION | SQLite audit trail is durable across restarts and row growth is capped by `AUDIT_MAX_ROWS`, but retention is still local only with no archival/export path | Accepted as current local-ops limit |
| LOW-5 | Dashboard loads Bootstrap CSS/JS from public CDN | Browser-only fetch; container is air-gapped |
| CRITICAL-3 | CF Access header strip enforced at Worker edge only | Accepted as architectural constraint (Worker is version-controlled) |
| DEV-AUDIT | `npm audit` vulnerabilities in wrangler dev tooling | Dev-only; never deployed to CF production |
| DASH-COUNT | Occasional missing entries in dashboard request log | Likely silent LiteLLM retry; not investigated |
| DASH-FLICKER | Recent Requests table briefly shows fewer entries on some poll cycles | UI polling race; entries return on next poll |
| PROVIDER-COUPLING | Reduced further but not eliminated: the remaining meaningful adapter coupling is LiteLLM model declaration duplication in `litellm/config.yaml`; `api_base_path` in `worker/src/providers.json` is now vestigial after Round 22 cut-over | Full multi-adapter generalization remains a later round |
| LITELLM-UI | LiteLLM admin UI login non-functional (no DB) | Use subumbra UI at `localhost:8080` instead |
| TOKEN-SYNC | `post-bootstrap.sh` now detects and warns on stale container tokens for `litellm`, `subumbra-keys`, and `ui`; bootstrap summary, `README.md`, and `CLAUDE.md` all require `docker compose up -d --force-recreate` after full bootstrap | Closed by Round 13 |
| TTL-EXPIRY-ONLY | subumbra-keys TTL prevents new record fetches after token expiry but does not remove Worker-side token authority. Replay of previously captured records plus a stolen token remains possible until re-bootstrap rotates Worker-side token state | Intentionally deferred beyond Round 30 |

---

## Open Questions

**1. CRITICAL-3 — ACCEPTED**
CF Access header strip is enforced at Worker edge only. Strip is prominently commented
in both `custom_callbacks.py` and `worker.js`. Accepted as architectural constraint.

**2. LiteLLM image pin**
Current pin: `main-latest@sha256:7c311546c25e7bb6e8cafede9fcd3d0d622ac636b5c9418befaa32e85dfb0186`
(LiteLLM `1.82.6`, verified 2026-03-29). Re-verify before updating.

---

## Roadmap Arc (Rounds 34-36)

This arc focuses on evolving Subumbra from a static, bundled configuration into a flexible, operator-managed system. Approved 2026-04-09 in [provider-adapter-flexibility-roadmap.md](file:///home/eric/git/Subumbra/council/approved/provider-adapter-flexibility-roadmap.md).

### Round 34: Provider Flexibility (Closed 2026-04-10)
- **Focus**: Built-in provider catalog expansion on the current architecture.
- **Goal**: Add Cerebras, Gemini, Mistral, OpenRouter, Together, and xAI as bootstrapable LiteLLM providers.
- **Outcome**: Closed with official proof plus six-provider end-to-end verification; the built-in AI provider set now covers 10 providers on the current architecture.

### Round 35: Adapter Flexibility (Closed 2026-04-10)
- **Focus**: Identity/Token generalization across bootstrap and runtime.
- **Goal**: Move from 4 hardcoded apps to arbitrary named adapters.
- **Outcome**: Closed with official multi-verifier PASS; bootstrap, post-bootstrap, and proof capture now support additive custom adapters such as Open WebUI or Portkey without changing the core runtime architecture.

### Round 36: Live Provider Registry (Closed 2026-04-11)
- **Focus**: KV-backed Worker registry.
- **Goal**: Move allowlist to Cloudflare KV.
- **Outcome**: Closed with verification PASS. Provider validation now comes from a live Cloudflare KV registry, `--push-registry` republishes without a Worker redeploy, custom provider metadata persists in `/app/data/custom-providers.json`, and Worker-side hostname/provider validation remains fail-closed.

---

**Cross-round invariants**:
- Split-decrypt boundary remains intact.
- No durable decrypt power on operator-controlled hosts.
- Worker-side hostname/provider validation must remain fail-closed.

### Round 38: System Review (Closed 2026-04-11)
- **Focus**: Documentation truth-alignment and bootstrap reliability.
- **Goal**: Sync README.md, CLAUDE.md, and docker-compose.yml with current post-Round 36 architecture; triage wrangler secret race conditions.
- **Outcome**: Closed with verification PASS. Public and operator docs now correctly describe the 13+ supported providers, the live KV registry model, and the subumbra-proxy sidecar. Bootstrap race condition identified as transient/environmental.

### Round 39: POC Deployment Hardening (Closed 2026-04-11)
- **Focus**: Deployment readiness for the current POC.
- **Goal**: Add end-to-end Worker health visibility, clarify recovery/runbook paths, optionally tighten the localhost UI surface, and clean up the duplicate Round 38 entry.
- **Outcome**: Closed with verification PASS. The dashboard now surfaces independent Worker reachability, README points operators to the authority-recovery runbook, optional minimal Basic Auth can protect the localhost UI, and the duplicate Round 38 status entry was removed.

## Path Forward

Current direction after Round 39:

1. **Round 40 — Broader Decoupling And Security Hardening**
   Focus on the protocol and integration hardening needed before meaningful
   live stress testing across additional systems and users.

   Direction:
   - resolve replay-hardening scope honestly across the subumbra protocol and its
     current producers
   - reduce remaining LiteLLM- or test-shaped assumptions in the integration layer
   - improve drop-in support so new apps can inherit hardening through the
     adapter/sidecar path rather than custom application logic

2. **Round 41 — Real App Validation**
   Focus on testing Subumbra through real applications and realistic workflows
   after the Round 40 hardening baseline is in place.

   Sub-rounds:
   - **Round 41.1 — VPS Test Environment Planning** (Closed): approved
     `council/approved/vps-test-environment.md`; `docs/vps-deployment.md` and
     `docs/cursor-github-vps-workflow.md` created.
   - **Round 41.2 — VPS Stabilization** (Approved): approved plan at
     `council/approved/vps-stabilization.md`. One code change (bootstrap KV
     namespace idempotency) and five doc patches. Pending implementation and
     verification.
   - **Round 41.5 — App Validation Re-Verification** (Closed): closure-audit
     round completed. Council consensus: Round 41 core implementation largely
     landed, but Round 41 was not yet closure-grade. Remaining blockers were
     narrowed into Round 41.6 rather than reopening the full round.
   - **Round 41.6 — App Validation Cleanup** (Open): narrow follow-up round to
     make Round 41 honestly closeable by stabilizing the proof path,
     eliminating out-of-band verifier dependencies, and deciding any minimal
     remaining live-app recheck.

   Known Round 41.3 candidates:
   - Bootstrap wizard validates adapter key_id inputs only at final submission,
     not immediately at the entry step. A typo forces a full wizard restart.
     Fix: validate each adapter scope on entry; re-prompt on mismatch. Companion
     UX upgrade: replace free-text name entry with numbered list selection.
     (`bootstrap/subumbra-bootstrap.py`, adapter scope collection step)

   **Immediate investigation — LiteLLM config alignment:**
   Bootstrap generates key_ids interactively (or from `*_KEY_ID` env vars in
   automation mode). `litellm/config.yaml` is a static file that must use
   `subumbra:<key_id>` values matching exactly what was entered at bootstrap.
   Bootstrap now prints copy/paste hints at the end of a run, but the operator
   must still edit the file manually and there is no validation at stack start
   that `litellm/config.yaml` key_ids match the keys registered in subumbra-keys.
   A mismatch causes silent 403 `key_scope_denied` failures.
   Candidates: auto-generate `litellm/config.yaml` model entries from bootstrap
   output, or add a preflight check that compares `subumbra:key_id` values in the
   config against the live registry. Council investigation before Round 42.

   Intended validation targets (remaining):
   - full LiteLLM deployment in normal app usage
   - OpenWebUI as a practical integration target
   - real non-AI service flows for GitHub, Slack, and SendGrid
   - sidecar/drop-in behavior as experienced by actual applications

Guiding note:
- Keep project language as **POC** for now.
- Prioritize deployment/testing readiness first, then the hardening needed for
  credible live testing, then real-app validation.
- Treat broader universality as part of the hardening path, not as a
  post-validation cleanup step.
