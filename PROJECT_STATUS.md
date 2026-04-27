# PROJECT_STATUS
*Current state — updated 2026-04-26*
*Rounds 1–43.6 and 43-6-1 closed. Round 43-6-2 is next. See `council/COUNCIL.md` for round history and current status.*

---

## Architecture

V2 Asymmetric Envelope Encryption (deployed, verified by all three council members).

- RSA-4096 key pair: public key on host, private key in CF Secrets
- Per-record AES-256-GCM DEKs wrapped by RSA public key
- AAD binding: `subumbra:v2:<key_id>`
- Offline per-key rotation via `--rotate` (no CF interaction)
- App-owned integrations now use `subumbra-proxy` and the transparent `/t` path with plain key IDs; callback-era LiteLLM artifacts remain as legacy reference only
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
- App-owned integrations: `subumbra-proxy` as the current reference surface, with standalone LiteLLM as the first proven example and other apps following the same pattern

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
| MEDIUM-7 | `/api/status` unauthenticated | Bound to `127.0.0.1:6563` (localhost only); add basic auth before multi-user |
| G-MEDIUM-3 | CF Worker buffers full body with no size limit (128 MB CF cap) | Low risk for small-team internal use |
| AUDIT-RETENTION | SQLite audit trail is durable across restarts and row growth is capped by `AUDIT_MAX_ROWS`, but retention is still local only with no archival/export path | Accepted as current local-ops limit |
| LOW-5 | Dashboard loads Bootstrap CSS/JS from public CDN | Browser-only fetch; container is air-gapped |
| CRITICAL-3 | CF Access header strip enforced at Worker edge only | Accepted as architectural constraint (Worker is version-controlled) |
| DEV-AUDIT | `npm audit` vulnerabilities in wrangler dev tooling | Dev-only; never deployed to CF production |
| DASH-COUNT | Occasional missing entries in dashboard request log | Root cause not yet investigated |
| DASH-FLICKER | Recent Requests table briefly shows fewer entries on some poll cycles | UI polling race; entries return on next poll |
| DOUBLE-FETCH | `subumbra-proxy` makes two `GET /keys/<key_id>` calls to `subumbra-keys` per proxied request — confirmed in round 43-6 operator proof run (2026-04-25). Root cause not yet investigated; likely one fetch for key_id validation and a second for `/proxy` payload construction. Doubles subumbra-keys load and adds per-request latency. **High priority — dedicated investigation round remains open and is carried forward into Alpha.** | `council/cleanup.md` 2026-04-25 |
| PROVIDER-COUPLING | App-owned integrations still maintain their own model/provider declarations outside the core stack (for example `litellm/config.yaml`) | Full multi-adapter generalization remains a later round |
| TOKEN-SYNC | `post-bootstrap.sh` now detects and warns on stale container tokens for `subumbra-keys`, `subumbra-proxy`, and `ui`; bootstrap summary, `README.md`, and `CLAUDE.md` all require `docker compose up -d --force-recreate` after full bootstrap | Closed by Round 13 |
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

## Recent Round Status

- **Round 40 — Broader Decoupling And Security Hardening** (Closed): protocol and integration hardening baseline completed.
- **Round 41 — Real App Validation** (Closed): app-validation arc completed through the 41.x cleanup and verification sequence.
- **Round 41.7 — Standalone LiteLLM Runtime Fix** (Closed): resolved through the 42.x standalone and app-owned integration follow-up work.
- **Round 42 — Operator Hardening For Standalone Integrations** (Closed, superseded): follow-on work was absorbed by Rounds 42.2 and 42.3.
- **Round 42.2 — Runtime Auth Reconciliation** (Closed): runtime auth recovery and worker-auth validation completed.
- **Round 42.3 — App-Owned Integrations** (Closed): app-owned integration model and standalone LiteLLM example established as the supported path.
- **Round 43.1 — OpenWebUI App-Owned Validation** (Closed): standalone OpenWebUI is now a proven app-owned integration with env-authoritative proxy routing, LiteLLM aggregator proof, zero-restart rotation, and fail-closed negative validation.
- **Round 43.2 — AnythingLLM App-Owned Validation** (Closed): standalone AnythingLLM is now a proven app-owned integration with chat, embeddings, zero-restart rotation, and fail-closed negative validation through the proxy.
- **Round 43-5 — LibreChat Direct Subumbra Integration** (Closed): LibreChat is now a proven app-owned integration with staged-and-promoted install docs, routed OpenAI-compatible chat proof, model discovery via `models.fetch`, and fail-closed invalid-key verification.
- **Round 43-5-1 — LibreChat Takeover** (Closed): existing LibreChat installs are now proven for in-place takeover onto the supported Subumbra path with login continuity, conversation continuity, routed chat success, invalid-key fail-closed behavior, and restore proof.
- **Round 43-6-1 — Env Ingestion + Alpha Polish** (Closed): multi-app env ingestion, shared-key deduplication, alpha versioning, and promoted provider-matrix templates are now in place under the current single-provider-key bootstrap contract.
- **Round 43-6-2 — Identity Routing** (Closed): `subumbra-proxy` now enforces per-app secure routing with app-token identity, path-based `key_id` extraction, downstream token forwarding, secure-mode `403` passthrough, and transitional legacy pseudo-key compatibility.
- **Round 43-2 — Documentation and Templates Cleanup** (Closed): established the `docs/apps/` structure, split OpenWebUI guides, and promoted operational templates from council archives to tracked documentation.
- **Round 43-6 — Provider Matrix + UI Switching Guides** (Closed): all 9 providers tested across OpenWebUI, AnythingLLM, LibreChat, Bifrost, and N8N. Provider matrix, per-app switching guides, README updates, and N8N workflow JSONs promoted to `docs/`. Known exceptions: Bifrost/Together fails (Bifrost limitation); AnythingLLM named providers hardcoded (app design); Gemini deferred (path mismatch); N8N multi-provider not exhaustively tested (AI-node pattern generalizes).
- **Round 43-6-1 — Env Ingestion Script + Alpha 0.0.1 Polish** (Closed): env ingestion script for migrating existing app `.env` files into Subumbra bootstrap format; `subumbra-ui` health endpoint; multi-provider template updates; `0.0.1-alpha` version identifier. Scope remains intentionally narrow: multi-app ingestion and shared-key deduplication under the current single-canonical-key-per-provider bootstrap contract. Richer same-provider multi-secret import support is deferred.
- **Round 43-6-2 — Enforce Per-App Identity / Routing** (Queued): activate the app-scoped metadata generated by 43-6-1 through a secure-gateway cutover in `subumbra-proxy`, using bearer app identity plus requested `key_id` path routing while keeping a transitional legacy mode during cutover.
- **Round 43-6-3 — Richer Same-Provider Multi-Key Ingestion** (Planned): add explicit support for multiple distinct secrets for the same provider in one operator ingestion workflow, with exact collision semantics and a deliberately scoped bootstrap/input contract upgrade.
- **Round 43-6-4 — Operator Bootstrap UX Cleanup** (Planned): collapse the operator experience into a cleaner terminal-first bootstrap path that can read app env sources directly, summarize what Subumbra is ingesting, and delay destructive cleanup of legacy plaintext env files until post-validation.

## Path Forward

Round 43 arc closing sequence — targeting 0.0.1 Alpha:

1. **Round 43 — App-Owned Integration Validation**
   Continue the app-owned validation arc with `round-43-openclaw` and other later candidates once they meet the Round 43 filter. OpenWebUI and AnythingLLM are complete.
2. **Round 43-6-3 — Richer Same-Provider Multi-Key Ingestion**
   Build on the closed 43-6-1/43-6-2 foundation by adding explicit multi-secret same-provider import support without changing the secure routing contract.
3. **Round 43-6-4 — Operator Bootstrap UX**
   Consolidate the multi-step ingestion/bootstrap flow into a cleaner operator path after the underlying secure routing and key-ingestion semantics are proven.
1. **Round 43-6 (Closed)** — Provider matrix + UI switching guides. All 9 providers tested. Docs promoted.

2. **Round 43-6-1 (Closed)** — Env ingestion script, `subumbra-ui` health endpoint, multi-provider template updates, `0.0.1-alpha` version bump. Scope is limited to multi-app ingestion plus shared-key deduplication under the current bootstrap contract; richer same-provider multi-secret import support is deferred.

3. **Round 43-6-2 (Queued)** — Enforce per-app identity/routing in `subumbra-proxy`. Use the app-scoped metadata generated by 43-6-1, move toward bearer caller identity plus requested `key_id` path routing, and keep the cutover narrow and testable.

4. **Round 43-6-3 (Planned)** — Add richer same-provider multi-key ingestion. This round is the right place for multiple OpenAI/Anthropic/etc. secrets in one operator flow, with exact conflict semantics and a controlled bootstrap/input-contract upgrade.

5. **Round 43-6-4 (Planned)** — Improve the operator bootstrap UX. Aim for a cleaner single terminal workflow that can read source env paths directly, summarize decisions live, and postpone shredding/cleanup of legacy plaintext env files until the operator validates the cutover.

6. **Round 44 (Planned)** — Secure UI round. UI-based env ingestion, encrypted paste/input for browser security. See `council/round-44-secure-ui/`.

Guiding note:
- Language transitions from **POC** to **0.0.1 Alpha** at the close of round 43-6-1, with `DOUBLE-FETCH` still carried as an open known limitation.
- Prioritize deployment/testing readiness first, then the hardening needed for
  credible live testing, then real-app validation.
- Treat broader universality as part of the hardening path, not as a
  post-validation cleanup step.
