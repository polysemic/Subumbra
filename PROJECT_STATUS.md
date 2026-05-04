# PROJECT_STATUS
*Current state — updated 2026-05-04*
*Rounds 1–43.6, 43-6-3, 43-6-4-1, 43-6-4-2, and 43-6-4-bootstrap-ux closed. See `council/COUNCIL.md` for round history and current status.*

---

## Architecture

V2 Asymmetric Envelope Encryption (deployed, verified by all three council members).

- RSA-4096 key pair: public key on host, private key in Cloudflare Durable Object custody
- Per-record AES-256-GCM DEKs wrapped by RSA public key
- AAD binding: `subumbra:v2:<key_id>`
- Offline per-key rotation via `--rotate` (no CF interaction)
- App-owned integrations now use `subumbra-proxy` with adapter token as the app credential and the requested `key_id` carried in the `/t/<key_id>/...` path
- The legacy raw-`key_id` transparent auth path has been removed
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
| PROVIDER-COUPLING | App-owned integrations still maintain their own model/provider declarations outside the core stack (for example `litellm/config.yaml`) | Full multi-adapter generalization remains a later round |
| TTL-EXPIRY-ONLY | subumbra-keys TTL prevents new record fetches after token expiry but does not remove Worker-side token authority. Replay of previously captured records plus a stolen token remains possible until re-bootstrap rotates Worker-side token state | Intentionally deferred beyond Round 30 |
| NONCE-STORE | Historical `subumbra-keys` `nonce_store_failure reason=nonce_store_error` reports were not reproduced in Round 44.5.1 under the current WAL + `busy_timeout` stack (12 concurrent signed key fetches) | Keep as a watch item; only reopen source changes if a current reproducer returns |

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

This arc focuses on evolving Subumbra from a static, bundled configuration into a flexible, operator-managed system. Approved 2026-04-09 in [provider-adapter-flexibility-roadmap.md](/home/eric/git/Subumbra/council/approved/provider-adapter-flexibility-roadmap.md).

- **Round 34: Provider Flexibility (Closed 2026-04-10)**  
  **Focus**: Built-in provider catalog expansion on the current architecture.  
  **Goal**: Add Cerebras, Gemini, Mistral, OpenRouter, Together, and xAI as bootstrapable LiteLLM providers.  
  **Outcome**: Closed with official proof plus six-provider end-to-end verification; the built-in AI provider set now covers 10 providers on the current architecture.

- **Round 35: Adapter Flexibility (Closed 2026-04-10)**  
  **Focus**: Identity/Token generalization across bootstrap and runtime.  
  **Goal**: Move from 4 hardcoded apps to arbitrary named adapters.  
  **Outcome**: Closed with official multi-verifier PASS; bootstrap, post-bootstrap, and proof capture now support additive custom adapters such as Open WebUI or Portkey without changing the core runtime architecture.

- **Round 36: Live Provider Registry (Closed 2026-04-11)**  
  **Focus**: KV-backed Worker registry.  
  **Goal**: Move allowlist to Cloudflare KV.  
  **Outcome**: Closed with verification PASS. Provider validation now comes from a live Cloudflare KV registry, `--push-registry` republishes without a Worker redeploy, custom provider metadata persists in `/app/data/custom-providers.json`, and Worker-side hostname/provider validation remains fail-closed.

**Cross-round invariants**:
- Split-decrypt boundary remains intact.
- No durable decrypt power on operator-controlled hosts.
- Worker-side hostname/provider validation must remain fail-closed.

- **Round 38: System Review (Closed 2026-04-11)**  
  **Focus**: Documentation truth-alignment and bootstrap reliability.  
  **Goal**: Sync README.md, CLAUDE.md, and docker-compose.yml with current post-Round 36 architecture; triage wrangler secret race conditions.  
  **Outcome**: Closed with verification PASS. Public and operator docs now correctly describe the 13+ supported providers, the live KV registry model, and the subumbra-proxy sidecar. Bootstrap race condition identified as transient/environmental.

- **Round 39: POC Deployment Hardening (Closed 2026-04-11)**  
  **Focus**: Deployment readiness for the current POC.  
  **Goal**: Add end-to-end Worker health visibility, clarify recovery/runbook paths, optionally tighten the localhost UI surface, and clean up the duplicate Round 38 entry.  
  **Outcome**: Closed with verification PASS. The dashboard now surfaces independent Worker reachability, README points operators to the authority-recovery runbook, optional minimal Basic Auth can protect the localhost UI, and the duplicate Round 38 status entry was removed.

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
- **Round 43-6 — Provider Matrix + UI Switching Guides** (Closed): all 9 providers tested across OpenWebUI, AnythingLLM, LibreChat, Bifrost, and N8N. Provider matrix, per-app switching guides, README updates, and N8N workflow JSONs promoted to `docs/`.
- **Round 43-6-1 — Env Ingestion + Alpha 0.0.1 Polish** (Closed): multi-app env ingestion, shared-key deduplication, alpha versioning, and promoted provider-matrix templates are now in place under the current single-provider-key bootstrap contract.
- **Round 43-6-2 — Identity Routing** (Closed): `subumbra-proxy` now enforces per-app secure routing with app-token identity, path-based `key_id` extraction, downstream token forwarding, secure-mode `403` passthrough, and transitional legacy pseudo-key compatibility.
- **Round 43-6-3 — Richer Same-Provider Multi-Key Ingestion** (Closed): multi-key same-provider import support now exists in both bootstrap automation and env-ingest planning under the secure transparent contract.
- **Round 43-6-4-1 — Proxy Lockdown** (Closed 2026-04-29): removed legacy raw-`key_id` transparent auth, requires adapter-token identity on `/t`, empties generated `PROXY_ALLOWED_KEYS`, retires `/v1/request` as a supported app-facing sidecar surface, and aligns the promoted app docs to the secure transparent contract.
- **Round 43-6-4-2 — Probe Role Decoupling** (Closed 2026-04-29): `subumbra-probe` is now optional by default, bootstrap/post-bootstrap/reset no longer require probe provisioning, and install/testing docs now frame probe as optional direct Worker-path diagnostics rather than baseline runtime.
- **Round 43-6-4-Bootstrap-UX — Operator Bootstrap UX Cleanup** (Closed 2026-04-29): bootstrap now uses an env-aware Worker-name default, clearer multi-key/app-label prompts, per-key summary lines, numbered allowed-key selection, and clearer optional probe wording. `docs/subumbra-install.md` now separates interactive wizard and `.env.bootstrap` automation walkthroughs.
- **Round 44-1 — Security Quick Wins** (Closed 2026-04-30): strict `pub_key_fp` enforcement is now fail-closed, caller-visible fingerprint mismatch detail is removed, and Worker/docs comments are aligned with the current plaintext boundary.
- **Round 44-2 — Decrypt In Existing DO** (Closed 2026-04-30): the decrypt execution boundary now lives in the existing `SubumbraProxy` Durable Object, the Worker→DO hop carries the encrypted envelope instead of plaintext `apiKey`, and public `/proxy` validation/error behavior remains unchanged.
- **Round 44-3 — CF-Side Key Generation And Custody** (Closed 2026-05-01): bootstrap now uses one-shot Cloudflare `/setup/keygen`, the SQLite-backed `SubumbraVault` DO holds persistent private-key custody, active `/proxy` execution routes through the named vault instance, and offline `public_key.pem` rotation remains intact.
- **Round 44-4 — Bootstrap Docker Finalization** (Closed 2026-05-01): `bootstrap.sh` now owns the host-side bootstrap flow, repo-local `.env` is finalized directly during bootstrap, `post-bootstrap.sh` is retired to a deprecation shim, and automation-mode app imports support explicit `IMPORT_PATH_<n>` / `IMPORT_APP_LABEL_<n>` inputs including the Gemini `GOOGLE_API_KEY` alias.
- **Round 44-5-1 — Code Cleanup Alpha Blockers** (Closed 2026-05-01): retired `post-bootstrap.sh` references are removed from the scoped runtime/docs surfaces, public adapter-token and vault-custody docs/config now match the live contract, provider-registry publish/read naming is aligned on `subumbra_registry_v1`, `/stats` and `/audit` deny-paths now audit symmetrically, proxy fetch errors stay generic, probe headers honor optional CF Access env, and the historical nonce-store issue was not reproduced on the current WAL + `busy_timeout` stack.
- **Round 44-5-3 — Code Cleanup Scope-Lock Follow-Up** (Closed 2026-05-02): `handleSetupKeygen` now fails closed with a structured `503` when the setup vault binding is unavailable, Worker-local no-vault proof coverage exists, and the round-local verification hook proves unchanged auth/initialized behavior plus bootstrap-helper visibility of the terse JSON failure.
- **Round 44-5-4 — Code Cleanup Final Prune/Archive Pass** (Closed 2026-05-02): `SubumbraVault` constructor degradation is now caught and surfaced as a structured `503`, Worker `/health` now reports stateless `vault_configured` readiness, and setup-keygen internal failures log fixed strings only. Live Cloudflare verification passed against the deployed Worker.
- **Round 44-5-5 — Pre-R45 Operational Hardening** (Closed 2026-05-03): bootstrap now re-validates saved `kv-config.json` namespace IDs against the active Cloudflare account before falling back to the existing title-scan/create path, repo-local `.env` persists `SUBUMBRA_SETUP_TOKEN` for operator reference after bootstrap, and independent live-Cloudflare verification passed all four proof scenarios.
- **Round 44-5-6 — Final Doc Compaction Pass** (Closed 2026-05-03): live council/status docs were compacted by moving six resolved cleanup sections into `council/archive/cleanup.md`, removing consumed Round 44.5/44.6 synthesis-marker ballast from `council/cleanup.md` and `PROJECT_STATUS.md`, and sanitizing `council/COUNCIL.md` for the post-44.5 archival state.
- **Round 44-6 — Doc Cleanup** (Closed 2026-05-03): release-facing docs now reflect the adapter-token + `SubumbraVault` contract across the website, `CLAUDE.md`, install/operator guides, and harness docs; the misleading testbed shredding claim and the Cerebras example mismatch were also corrected.
- **Round 45 — Structure Upgrade Planning** (Closed 2026-05-03): the council locked a five-round universal REST foundation arc, staged `council/r45-1-policy-schema/` through `council/r45-5-rest-auth-proofs/`, and created `council/rTBD-structure-upgrade/` as the deferred post-R45 scoping folder.
- **Round 45-1 — Policy Schema, Threat Model, And Storage Decision** (Closed 2026-05-03): the R45 policy schema, rejection rules, reserved `intent`/`response`/`velocity` blocks, safe pattern vocabulary, V3 `policy_hash` binding rule, structured KV key-shape contract, and R45 threat model are now documented; the round-local verifier passed independently for both Claude and Gemini.
- **Round 45-2 — Bootstrap Policy Ingestion And Worker Code Pinning** (Closed 2026-05-03): bootstrap now accepts optional `SUBUMBRA_POLICY_PATH`, imported secrets require matching policy documents, built-in direct secrets retain a narrow in-memory auto-compat path for continuity, bootstrap writes `system-integrity.json`, `scripts/subumbra-verify-deploy` checks live Worker drift, and proxy request logs now emit `target_host` / `target_path` instead of raw `target_url`.
- **Round 45-3 — V3 Binding And Structured KV Publication** (Closed 2026-05-04): bootstrap now writes V3 records with `policy_id` / baseline-bound `policy_hash`, publishes structured KV entries (`policy:<id>`, `key:<id>`, `template:<name>`, `registry_version`), Worker reads structured KV instead of `subumbra_registry_v1`, and `--rotate-policy` re-encrypts through the Worker without host plaintext recovery.
- **Round 45-4 — Worker REST Enforcement Foundation** (Closed 2026-05-04): `SUBUMBRA_ADAPTER_TOKENS` now carries `{id, token}` objects so the Worker resolves adapter identity from the authenticated token; `getRegistryEntry` returns the full `allow` block; `handleProxy` enforces adapter scope, method, path prefix, content-type, and body size for V3 records (V2 grace path skips all allow-block checks); `authorization`, `x-api-key`, and `x-api-key-id` stripped by `HOP_BY_HOP_HEADERS`; structural `intent` metadata logged when present. Two code bugs found and fixed by Gemini: CT enforcement bypass when proxy sends null body (committed `20d5061`), bootstrap DO cold-start 503 not retried (committed `37d59c7`). Council fixture remediation: `policies.json` in correct format, `bootstrap-overlay.env` uses `SUBUMBRA_POLICY_PATH` + key slots 3/4/5, `verify-round.sh` V6 does active fixture rewrite via `cryptography`, DNS readiness poll added. Clean-run proof `codexremed-20260504T215118`: V1–V8 all PASS. All C1–C10 diff checks PASS.

## Path Forward

Immediate follow-up sequence — targeting 0.0.1 Alpha:

1. **Nonce-store watch item**
   Reproduce `subumbra-keys` `nonce_store_failure reason=nonce_store_error` on the current stack before attempting further source changes; Round 44.5.1 did not reproduce it under concurrent signed key fetches.
2. **Round 44 Security Arc (Approved sequence)**
   The council planning round in `council/closed/round-44-security-review/` converged on a four-round implementation arc:
   - `council/closed/round-44-1-security-quick-wins/` — closed 2026-04-30; strict `pub_key_fp` enforcement, generic decryption failures, and truth-aligned Worker/docs comments
   - `council/closed/round-44-2-decrypt-in-existing-do/` — closed 2026-04-30; decrypt now runs inside the existing `SubumbraProxy` DO, and the Worker→DO hop keeps the original encrypted envelope intact
   - `council/closed/round-44-3-cf-keygen-custody/` — closed 2026-05-01; CF-side key generation and custody landed in the SQLite-backed `SubumbraVault` DO while preserving offline no-restart rotation
   - `council/closed/round-44-4-bootstrap-docker-finalization/` — closed 2026-05-01; bootstrap is now host-wrapper driven, `post-bootstrap.sh` is retired, and Docker-only env finalization is the documented flow
   - Future high-priority follow-up: define backup/export/recovery policy for CF-generated vault keys before broader production-facing deployment claims
3. **Round 44.5 cleanup arc** — Complete. Rounds 44-5-1 through 44-5-6 all
   closed; see Recent Round Status above for per-round summaries.
4. **Round 45 Structure Upgrade Arc (Approved sequence)**
   The council planning round in `council/closed/r45-structure-upgrade/` converged on a five-round implementation arc:
   - `council/closed/r45-1-policy-schema/` — closed 2026-05-03; policy schema, threat model, structured KV decision, and V3 binding contract are now documented and verified
   - `council/closed/r45-2-bootstrap-policy-ingestion/` — closed 2026-05-03; policy-aware bootstrap ingestion, policy-less refusal, Worker code pinning, and URL logging normalization
   - `council/closed/r45-3-v3-binding-kv-publication/` — closed 2026-05-04; V3 `policy_hash` binding, structured KV publication, DO-mediated `--rotate-policy`, and one-round V2 grace bridge
   - `council/closed/r45-4-worker-rest-enforcement/` — closed 2026-05-04; Worker-side adapter/method/path/content-type/body-size enforcement, structural `intent` logging, V2 grace path; two code bugs found and fixed by Gemini (CT enforcement bypass, bootstrap DO cold-start 503); all V1–V8 scenarios pass in clean-run proof
   - `council/r45-5-rest-auth-proofs/` — generic auth schemes, GitHub REST + Stripe test-mode + custom header-auth proof, and V2 removal
   - Deferred follow-on staging: `council/rTBD-structure-upgrade/` for post-R45 topics such as intent/response enforcement, rate limiting, management API, UI follow-ons, webhook verification, raw-body expansion, `git_https`, and D1 re-evaluation if needed

Guiding note:
- Language transitions from **POC** to **0.0.1 Alpha** as the Round 43 arc closes.
- Prioritize deployment/testing readiness first, then the hardening needed for credible live testing, then real-app validation.
- Treat broader universality as part of the hardening path, not as a post-validation cleanup step.
