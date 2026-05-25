# PROJECT_STATUS
*Current state — updated 2026-05-25 (r85-1-ssh-daily-use closed)*

---

## Architecture

V3 Asymmetric Envelope Encryption (deployed, all three council verifiers PASS).

- Asymmetric hybrid envelope: RSA-4096 wraps a per-record AES-256-GCM DEK; neither side can decrypt alone
- AAD binding: `subumbra:v3:<key_id>:<policy_hash>` prevents ciphertext transplant and policy replay; as of r75 the Worker uses the live registry `policy_hash` as the decrypt-time authority rather than trusting the client-supplied request field
- Private key in CF Durable Object SQLite custody (non-extractable); public key on host for offline rotation
- Manifest-owned provider authority: operators declare routing, auth, and capability in `subumbra.yaml` (preferred) or `subumbra.json`; no hardcoded catalog at runtime
- **YAML manifest support (r67):** Bootstrap accepts `subumbra.yaml`; local `./templates/<name>.yaml` operator workspace checked before signed built-in catalog
- V1 symmetric `MASTER_DECRYPTION_KEY` path fully removed; V2 records hard-rejected by Worker
- **Session lockdown (r82, verified):** new deployments now initialize a local `sessions.db` with `lockdown_enabled=1`; `GET /keys/<id>` and Worker `POST /proxy` both fail closed until the operator opens one bounded session with `./bootstrap.sh --session start ...`, and read-only session state is visible through `GET /sessions` and the dashboard.
- **Multi-session isolation (r83, verified):** bootstrap now allows multiple concurrent active sessions when their effective `(adapter_id, key_id)` coverage stays disjoint, writes per-session shadow KV keys shaped as `session_token:<session_id>:<adapter_id>`, maintains adapter-level Worker gates as `active_adapter:<adapter_id>`, and exposes list-shaped `active_sessions` state to `subumbra-keys` clients and the dashboard.
- **SSH agent bridge (r85, verified):** the stack now ships a local `subumbra-agent` service exposing `/run/subumbra/ssh-agent.sock`, reading metadata-only SSH identities from mounted `keys.json`, and forwarding sign requests through `subumbra-proxy` to Worker `POST /ssh/sign`; end-to-end `ssh`, `git`, `scp`, and `rsync` flows were proven against the live VPS and GitHub fixtures under the existing session gate.
- **SSH daily-use follow-through (r85-1, verified):** transition to non-root execution via host `${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock` is complete, day-2 SSH key management commands (`--add-ssh-key`, `--rotate-ssh-key`, `--revoke-ssh-key`) are fully implemented, and durable `ssh_sign` audit events are persisted and surfaced in the operator dashboard.


---

## Vision

Subumbra is intended to become a universal zero-trust key broker. Every layer should be treated as potentially compromised: app servers, containers, config files, and hosts should never be trusted with enough material to recover provider secrets in usable form on their own.

The long-term architecture goal is:

- Core: `subumbra-keys` + Cloudflare Worker decrypt/proxy contract
- Provider policy: explicit host/auth allowlist and request validation
- App-owned integrations: `subumbra-proxy` as the current reference surface.

The design standard is that a partial compromise should yield only useless
fragments. Applications should request narrow capability through hardened
adapters, while decrypt authority, provider policy, and fail-closed validation
remain inside the Subumbra core boundary.

---

## Known Limitations

Deferred by council consensus.

| ID | Description | Rationale |
|----|-------------|-----------|
| MEDIUM-1 | Python memory scrubbing is best-effort; `bytearray` zeroing does not prevent copies in `os.environ` or immutable `str` objects | No code fix possible in CPython |
| MEDIUM-5 | `/health` leaks `keys_loaded` count unauthenticated | Acceptable on Docker internal network with no host exposure |
| G-MEDIUM-3 | CF Worker buffers full body with no size limit (128 MB CF cap) | Low risk for small-team internal use |
| AUDIT-RETENTION | SQLite audit trail is durable across restarts and row growth is capped by `AUDIT_MAX_ROWS`, but retention is still local only with no archival/export path | Accepted as current local-ops limit |
| CRITICAL-3 | CF Access header strip enforced at Worker edge only | Accepted as architectural constraint (Worker is version-controlled) |
| DEV-AUDIT | `npm audit` vulnerabilities in wrangler dev tooling | Dev-only; never deployed to CF production |
| DASH-COUNT / DASH-FLICKER | **Resolved (R63 2026-05-12):** dashboard per-key `request_count` / `last_access` now read from SQLite `audit_events` via `subumbra-keys` `/stats` and `/keys`, eliminating per-Gunicorn-worker divergence | Previously: in-memory stats per worker caused flicker |
| PROVIDER-COUPLING | App-owned integrations still maintain their own model/provider declarations outside the core stack | Full multi-adapter generalization remains a later round |
| TTL-EXPIRY-ONLY | subumbra-keys TTL prevents new record fetches after token expiry but does not remove Worker-side token authority | Intentionally deferred beyond Round 30 |
| NONCE-STORE | `nonce_store_failure reason=nonce_store_error` reports were not reproduced in Round 44.5.1 under the current WAL + `busy_timeout` stack | Watch item only |

---

## Open Questions

**1. CRITICAL-3 — ACCEPTED**
CF Access header strip is enforced at Worker edge only. Accepted as architectural constraint.

---

## Round History

| Round | Closed | Summary |
|-------|--------|---------|
| R1–R33 | pre-2026-04-09 | Foundation: symmetric encryption, Flask/FastAPI services, Docker networking, CF Worker basics, envelope encryption (R6.7), early app-owned integration experiments. See `council/archive/approved-pre-r34/`. |
| R34 | 2026-04-10 | Provider flexibility: built-in provider catalog expanded to 10 providers |
| R35 | 2026-04-10 | Adapter flexibility: arbitrary named adapters replace 4 hardcoded apps |
| R36 | 2026-04-11 | Live provider registry: provider validation moved to Cloudflare KV |
| R38 | 2026-04-11 | System review: doc truth-alignment and bootstrap reliability |
| R39 | 2026-04-11 | POC deployment hardening: Worker health visibility, runbook, optional localhost auth |
| R40 | 2026-04-11 | Broader decoupling and security hardening baseline |
| R41 | 2026-04-11 | Real app validation arc |
| R41.7 | — | Standalone LiteLLM runtime fix |
| R42 | — | Operator hardening for standalone integrations (superseded by R42.2/R42.3) |
| R42.2 | — | Runtime auth reconciliation and worker-auth validation |
| R42.3 | — | App-owned integration model and standalone LiteLLM example established |
| R43.1 | — | OpenWebUI app-owned validation |
| R43.2 | — | AnythingLLM app-owned validation |
| R43-5 | — | LibreChat direct Subumbra integration |
| R43-5-1 | — | LibreChat in-place takeover proof |
| R43-6 | — | Provider matrix + UI switching guides; all 9 providers across 5 apps |
| R43-6-1 | — | Env ingestion + Alpha 0.0.1 polish |
| R43-6-2 | — | Identity routing: per-app adapter-token secure routing |
| R43-6-3 | — | Multi-key same-provider ingestion |
| R43-6-4-1 | 2026-04-29 | Proxy lockdown: legacy raw-`key_id` transparent auth removed |
| R43-6-4-2 | 2026-04-29 | Probe role decoupling: `subumbra-probe` now optional |
| R43-6-4-UX | 2026-04-29 | Bootstrap UX cleanup: env-aware defaults, multi-key prompts |
| R44-1 | 2026-04-30 | Security quick wins: strict `pub_key_fp` enforcement, generic decryption failures |
| R44-2 | 2026-04-30 | Decrypt moved into existing `SubumbraProxy` DO |
| R44-3 | 2026-05-01 | CF-side key generation; SQLite-backed `SubumbraVault` DO private-key custody |
| R44-4 | 2026-05-01 | Bootstrap Docker finalization; `post-bootstrap.sh` retired; `bootstrap.sh` owns host flow |
| R44-5-1 | 2026-05-01 | Code cleanup: retired `post-bootstrap.sh` refs, public docs contract alignment |
| R44-5-3 | 2026-05-02 | `handleSetupKeygen` fails closed with structured `503` when vault binding unavailable |
| R44-5-4 | 2026-05-02 | `SubumbraVault` constructor degradation caught; Worker `/health` reports `vault_configured` |
| R44-5-5 | 2026-05-03 | Pre-R45 hardening: KV namespace ID re-validation against live CF account |
| R44-5-6 | 2026-05-03 | Final doc compaction pass; six resolved cleanup sections archived |
| R44-6 | 2026-05-03 | Doc cleanup: adapter-token and `SubumbraVault` contract truth-aligned across public docs |
| R45 | 2026-05-03 | Structure upgrade planning: five-round REST foundation arc approved |
| R45-1 | 2026-05-03 | Policy schema, threat model, structured KV decision, V3 `policy_hash` binding |
| R45-2 | 2026-05-03 | Bootstrap policy ingestion, policy-less refusal, Worker code pinning, `system-integrity.json` |
| R45-3 | 2026-05-04 | V3 AAD binding, structured KV publication (`policy:<id>`, `key:<id>`, `registry_version`) |
| R45-4 | 2026-05-04 | Worker REST enforcement: adapter/method/path/content-type/body-size; two Gemini-found bugs fixed |
| R46 | 2026-05-07 | Alpha app identity and rotation: per-app adapter binding, V3-only rotation |
| R46.5 | 2026-05-07 | Vault granularity: shared vault default + opt-in per-key isolated vault |
| R47 | 2026-05-08 | Runtime contract cleanup: legacy `SubumbraProxy` removed, non-root runtime, staged bootstrap pipeline |
| R48 | 2026-05-08 | Intent attestation: `intent.trust` guardrails active; response-side `deny_patterns` deferred to R48-5 |
| R48-1 | 2026-05-08 | Config manifest unification arc planning |
| R48-2 | 2026-05-08 | Manifest ingest: `subumbra.json` as single bootstrap input; `IMPORT_PATH_*` retired |
| R48-3 | 2026-05-09 | Internal state authority: day-2 commands run from embedded record state |
| R48-4 | 2026-05-09 | Provider catalog removal: `subumbra.json` policy owns all routing/auth declarations |
| R48-5 | 2026-05-09 | Response enforcement: `deny_patterns` active for buffered `application/json`/`text/plain` |
| R48-6 | 2026-05-10 | Bootstrap UX: nuke-and-pave, shared-vault reuse, `keys_data` volume rename |
| R49 | 2026-05-10 | Velocity limits and circuit breakers: `adapter_rpm`, `key_rpm`, `breaker_*` manifest fields |
| R50 | 2026-05-10 | Management API: pause/unpause, `SUBUMBRA_MANAGEMENT_TOKEN`, durable audit rows |
| R51 | 2026-05-10 | Signed provider template catalog: Ed25519-signed `catalog.json`, `"template"` manifest key |
| R52 | 2026-05-10 | Read-only policy UI refresh: V3 policy metadata in dashboard, heartbeat-only `/api/events` |
| R53 | 2026-05-11 | Recovery, authority lifecycle, and alpha release gate; `subumbra-verify-deploy` host-first |
| R54 | 2026-05-11 | Secure UI hardening: Basic Auth, per-IP rate limiting, fail-closed, static asset closure |
| R55 | 2026-05-11 | Worker health signal (`worker_auth`), CF Access UI mode, audit hygiene, stats persistence |
| R56 | 2026-05-11 | Stale pruning: doc cleanup, cleanup.md compaction, pre-R34 archive |
| R57 | 2026-05-11 | System cleanup: dead-code prune (6 bootstrap symbols, management-audit route), hygiene artifacts, SEC-1 doc, SEC-3 JS hardening |
| R58 | 2026-05-12 | Operator tuning: documented cadence/SEC-4, Compose log rotation on proxy/probe/bootstrap, volume migration doc accuracy, tombstone annotations only, removed `litellm/custom_callbacks.py` |
| R59 | 2026-05-12 | Rate-limit hardening: unified Basic Auth failure counting under `--workers 1 --threads 4` Gunicorn model; documented thread-pool and IP masking semantics |
| R60 | 2026-05-12 (CLOSED) | Harness improvements: `--deploy-worker` flag for automated existing-stack updates, dynamic proxy port resolution, and mode-aware P9.5 skipping |
| R61 | 2026-05-12 (CLOSED) | Bootstrap: remove plaintext `bootstrap-checkpoint.json` path; defer `secret_ref` resolution until post-deploy encrypt; phase-1 `call_setup_keygen` per vault; `run_provision_key` uses manifest + host env only; fail-closed host env sync; operator recovery docs. Verified `fresh-install` VPS proof `codex-vps-20260512T174950Z`. |
| R62 | 2026-05-12 (CLOSED) | Interactive manifest bootstrap wizard (RAM `_WIZARD_SECRETS`, 8-tuple `main()` unpack, post-wizard policy tail removed); round hook `verify-round.sh`. Council close-out: Claude VPS proof **PASS** (`claude-vps-20260512T191443Z`); formal `codex-verification.md` / `gemini-verification.md` not on file (process note in `council/cleanup.md`); Gemini isolated `fresh-install` proof **FAIL** at `remote-install` — not treated as product regression vs static gates. |
| R63 | 2026-05-12 (CLOSED) | Observability consistency: SQLite-backed `/stats` and `/keys` per-key usage; volatile RAM counters removed; proxy logging ISO alignment; dead SSE `status` listener removed; `verify-round.sh` stability artifacts. VPS `existing-stack` proofs **PASS**: `claude-vps-20260512T233235Z`, `gemini-vps-20260512T234111Z` (SHA `0d403ef`). `codex-verification.md` not on file (process note in `council/cleanup.md`). |
| R64 | 2026-05-13 (CLOSED) | Launch polish: `GET /audit` optional `key_id` / `verdict` filters; dashboard worker health copy uses `worker_auth`; `fresh-start.sh` audit volume name fix; `subumbra-keys` Gunicorn `--no-control-socket`; Path Forward trim + operator `worker_auth` / CRITICAL-3 docs; `verify-round.sh` S1–S6. VPS `existing-stack` proof **PASS**: `gemini-vps-20260513T005931Z` (`--build subumbra-keys subumbra-ui`, SHA `a0722d6`). `claude-verification.md` / `codex-verification.md` not on file (process note in `council/cleanup.md`). |
| R65 | 2026-05-13 (CLOSED) | Launch docs: README quickstart + `docs/architecture.md`; gitignored `subumbra.yaml` with tracked `subumbra.minimal.yaml` / `subumbra.example.yaml`; `.env.bootstrap.example` + `.env.bootstrap_bak` note; `docs/integration-recipes.md` (merged guides + catalog curls); removed legacy root stubs + `docs/provider-catalog.md`; `litellm/README.md`; operator-guide `worker_auth` detail; path/link hygiene. VPS `existing-stack` proof **PASS**: `gemini-vps-20260513T022305Z` (SHA `b37481d`). `claude-verification.md` / `codex-verification.md` not on file (process note in `council/cleanup.md`). |
| round-cleanup | 2026-05-13 (CLOSED) | Code cleanup: bootstrap pre-mutation KV gate (no CF/.env mutation before abort); zero `SUBUMBRA_SETUP_TOKEN` in host `.env` after full bootstrap; proxy `worker_auth` `token_mismatch` for Worker 401; UI CSP + `Cache-Control`; Worker `HEAD /health`; `subumbra-verify-deploy` infers `CF_WORKER_NAME` from `CF_WORKER_URL`; remove stale `IMPORT_PATH_*` install doc + dead checkpoint cleanup; `verify-round.sh`. VPS `existing-stack` proofs **PASS**: `codex-vps-20260513T143105Z`, `Gemini-vps-20260513T142722Z` (SHA `05083d1`). Close-out documents accepted scope deviation (UI/README/install `worker_auth` follow-through; no `claude-verification.md`). Archive: `council/closed/round-cleanup/`. |
| r67-user-templates | 2026-05-13 (CLOSED) | Template Liberation: YAML manifest support (`subumbra.yaml` preferred, `subumbra.json` accepted); local `./templates/<name>.yaml` operator workspace checked before signed built-in catalog; `pyyaml==6.0.2` added; `bootstrap.sh` manifest auto-discovery; extension-free `/app/manifest` container path; `_load_local_template` with warn-on-error fallback + `info()` on success; `template_name` dead-field fix; `subumbra.minimal.yaml` tracked starter; docs + gitignore updated. VPS `existing-stack` proofs: Gemini **PASS** (`gemini-vps-20260513T190733Z`, SHA `b64308b`); Codex harness issues only (S2/S5 environmental — operator override: manual verification passes, `codex-vps-20260513T195138Z`, SHA `a196e4b`). Archive: `council/closed/r67-user-templates/`. |
| r68-template-foundation | 2026-05-13 (CLOSED) | Template Foundation cleanup: template-backed `_normalize_policy_doc()` failures now name the originating template; README and operator-guide starter descriptions now match the tracked multi-provider `subumbra.minimal.yaml`; round-local `verify-round.sh` added. Official proof artifacts: Claude round-hook scope checks **PASS** in `claude-vps-20260513T221410Z` (SHA `222f72f`); close-out accepted P9.6 Worker-unreachable baseline as `ENVIRONMENTAL` / harness-only, with real staging bootstrap scenarios passing. Archive: `council/closed/r68-template-foundation/`. |
| r69-template-lifecycle | 2026-05-13 (CLOSED) | Template lifecycle: signed built-in provider and adapter template library converted from JSON to YAML; catalog SHA entries updated and re-signed under the accepted release key; `_load_and_verify_catalog()` now parses verified YAML with existing `pyyaml`; read-only `./bootstrap.sh --status` reports `UP_TO_DATE`, `POLICY_DRIFT`, `NOT_DEPLOYED`, and `REVOKED`; `--add-adapter` / `--revoke-adapter` now offer bounded canonical `adapters: [...]` manifest sync with drift warnings when auto-rewrite is not possible; lifecycle docs updated. Verification source diffs **PASS** in `claude-vps-20260514T004721Z` and `gemini-vps-20260514T031424Z` (SHA `cf8151c`); close-out accepted fresh-install proof failure as `HARNESS_ISSUE` after manual and UI validation confirmed expected behavior. Archive: `council/closed/r69-template-lifecycle/`. |
| r70-template-cleanup | 2026-05-14 (CLOSED) | Template cleanup polish: local-template shadowing now warns when operator YAML overrides a signed built-in template; malformed local-template warnings now include the concrete `/app/user-templates/...` file path while preserving built-in fallback; operator-guide and project-memory were aligned to the post-r69 YAML template reality; a round-local existing-stack verifier replaced the prior impossible fresh-install assumption for template/day-2 proof work. Verification evidence: Gemini VPS proof **PASS** in `gemini-vps-20260514T051721Z` (SHA `8d26661`); Claude source-diff verification **PASS** with `claude-vps-20260514T043306Z` accepted as a local hook `HARNESS_ISSUE` due relative artifact-path capture, not product behavior. Archive: `council/closed/r70-template-cleanup/`. |
| r71-security-hardening | 2026-05-17 (CLOSED) | Security hardening: Worker-generated JSON/auth/error responses now send `Cache-Control: no-store`, `Pragma: no-cache`, `X-Content-Type-Options: nosniff`, `Cross-Origin-Resource-Policy: same-origin`, and HSTS; `/setup/keygen` and `/internal/*` now reject before body parsing; Worker-edge setup-token equality checks were added; non-proxy auth/admin surfaces (`/auth-ping`, `/setup/keygen`, `/internal/*`, `/manage/key/*`) now use DO-backed per-IP throttling with `429 rate_limit_exceeded_auth`; built-in signed provider templates now ship active default `velocity` controls and the signed catalog was refreshed. Verification evidence: Gemini fresh-install + LiteLLM path **PASS** in `gemini-verification-2.md`; Claude manual adversarial verification **PASS** in `claude-verification-2.md`; merged to `main` at SHA `dc23c6d`. Archive: `council/closed/r71-security-hardening/`. |
| r72-cloudflare-updates | 2026-05-18 (CLOSED) | Cloudflare runtime UX: bootstrap now ingests optional BYOC `TUNNEL_TOKEN`, `CF_ACCESS_CLIENT_ID`, and `CF_ACCESS_CLIENT_SECRET`; interactive bootstrap offers optional Tunnel/Access prompts with reuse of existing `.env` values; new day-2 `./bootstrap.sh --update-tunnel` and `./bootstrap.sh --update-access` verbs update runtime credentials without a full re-bootstrap; new `docs/cloudflare-tunnel-access.md` landed; Cloudflare planning moved out of active themes. VPS `fresh-install` proofs **PASS**: `claude-vps-20260518T010954Z`, `gemini-vps-20260518T014318Z` (SHA `e4279e9`). Archive: `council/closed/r72-cloudflare-updates/`. |
| r73-cloudflare-autoprovision | 2026-05-18 (CLOSED) | Cloudflare lifecycle finish-out: bootstrap can auto-provision Cloudflare Tunnel, DNS CNAME, Access app, service-auth policy, and service token from one expanded `CF_API_TOKEN`; generated runtime credentials are written to `.env`, non-secret resource IDs are tracked in `data/cf-resources.json`, and `./bootstrap.sh --nuke-cloudflare` stops `cloudflared`, deletes tracked Cloudflare resources, clears runtime credentials, and removes the manifest. New `cf-api-provision` proof lane added to council workflow. VPS isolated proofs **PASS**: `claude-vps-20260518T063801Z`, `gemini-vps-20260518T131058Z` (verified SHA `703a610`; post-proof docs/hook/security workflow commits accepted as non-runtime hygiene). Archive: `council/closed/r73-cloudflare-autoprovision/`. |
| r74-subumbra-verify | 2026-05-18 (CLOSED) | Source-trust verifier: new `scripts/subumbra-verify` checks Git/source state, sensitive-file drift, optional local state shape, `.env` residue, `cf-resources.json` secret-like keys, and optional read-only Worker drift via `subumbra-verify-deploy`; `bootstrap.sh` runs `--preflight` before `.env.bootstrap` or secret prompts, with explicit `SUBUMBRA_ALLOW_UNVERIFIED_SOURCE=I_ACCEPT_RISK` break-glass. Docs updated for normal, strict signed-tag, and developer flows. VPS `existing-stack` proofs **PASS**: `claude-vps-20260519T000507Z`, `gemini-vps-20260519T000056Z` (SHA `c013518`). Archive: `council/closed/r74-subumbra-verify/`. |
| r75-shannon-patch | 2026-05-20 (CLOSED) | Shannon follow-up hardening: Worker `/proxy` now ignores client-supplied `policy_hash` for decrypt-time AAD selection and uses the live registry `policy_hash` instead, restoring server-authoritative V3 replay binding. Release-closeout updates prepared `1.1.1-alpha` (`VERSION`, `CHANGELOG.md`, roadmap/release notes, and sanitized public Shannon summary). Verification close-out accepted a staging-shape `HARNESS_ISSUE` in the Shannon proof hook after manual VPS proof confirmed the patched runtime behavior. Archive: `council/closed/r75-shannon-patch/`. |
| r76-response-injection-fix | 2026-05-21 (CLOSED) | Response-injection hardening: the Worker Durable Object upstream fetch now uses `redirect: "manual"`, buffered `response.deny_patterns` scanning now applies to buffered response types with an explicit `text/event-stream` bypass, and deny-pattern matching is now case-insensitive. `docs/adapter-contract.md` was truth-aligned to the explicit SSE bypass. VPS `existing-stack` proofs **PASS**: `claude-vps-20260521T054029Z`, `gemini-vps-20260521T055756Z` (SHA `190903b`). Archive: `council/closed/r76-response-injection-fix/`. |
| r77-response-header-policy | 2026-05-21 (CLOSED) | Header-policy hardening: policy/schema/runtime now support explicit `allow.request_headers` and `response.allow_headers`, Worker and proxy response forwarding now enforce those allowlists when present, built-in signed template defaults landed for Anthropic, OpenAI, and Groq, and response filtering now depends on `subumbra-keys` returning the embedded `policy` object to proxy callers. Verification `existing-stack` proofs **PASS**: `claude-vps-20260521T175331Z`, `gemini-vps-20260521T174803Z` (SHA `86f3cca`). Archive: `council/closed/r77-response-header-policy/`. |
| r78-ssrf-port-validation | 2026-05-21 (CLOSED) | SSRF port lockdown: Worker `/proxy` now rejects `target_url` ports other than default/explicit `443`, while preserving normal HTTPS behavior and explicit `:443`; `docs/adapter-contract.md` was updated to document the port constraint. VPS `existing-stack` proofs **PASS**: `claude-vps-20260521T212334Z`, `gemini-vps-20260521T212334Z` (SHA `1600a54`). Archive: `council/closed/r78-ssrf-port-validation/`. |
| r79-keys-auth-scoping | 2026-05-22 (CLOSED) | Keys-service auth scoping: `subumbra-keys` now sends `Cache-Control: no-store` on all responses, scopes `/keys`, `/stats`, and `/audit` to adapter `allowed_keys` by default, and normalizes `GET /keys/<id>` denied vs nonexistent responses to the same HTTP 403 body. The UI preserves broad operational visibility through the new `can_list_all_keys` capability, parsed in `subumbra-keys` and published for `subumbra-ui` by bootstrap. VPS `existing-stack` proofs **PASS**: `claude-vps-20260522T040931Z`, `gemini-vps-20260522T041351Z` (SHA `e9ad6ca`). Archive: `council/closed/r79-keys-auth-scoping/`. |
| r80-keys-auth-hardening | 2026-05-22 (CLOSED) | Keys auth hardening: `subumbra-keys` now enforces SQLite-backed auth-path throttling with exact `429 {"error":"rate limit exceeded"}` plus `Retry-After: 60`, nonce replay is globally blocked through a data-preserving single-column nonce migration, and the HMAC signer/verifier contract is now length-prefixed across `subumbra-keys`, `subumbra-proxy`, `subumbra-probe`, and `docs/adapter-contract.md`. Verification outcome: Gemini VPS proof **PASS** (`gemini-vps-20260522T054220Z`); Claude VPS proof confirmed the shipped behavior with one accepted `HARNESS_ISSUE` on a first-run-only migration-log grep (`claude-vps-20260522T054751Z`). Archive: `council/closed/r80-keys-auth-hardening/`. |
| r81-keys-auth-internals | 2026-05-22 (CLOSED) | Keys auth internals: `subumbra-keys` now fails closed when the auth-path audit store is unavailable, enforces `paused` keys, binds HMAC verification to `adapter_id`, scopes `/stats` recent-log and `/audit` reads for non-`list_all` adapters, and collapses the staged 400/401 HMAC oracle. `subumbra-proxy` and `subumbra-probe` now sign adapter-bound HMAC payloads, and the probe reads `SUBUMBRA_ADAPTER_ID` only at request/sign time rather than startup. Verification `existing-stack` proofs **PASS**: `claude-vps-20260522T154712Z`, `gemini-vps-20260522T160425Z` (SHA `2a9098f`). Archive: `council/closed/r81-keys-auth-internals/`. |
| r82-session-lockdown | 2026-05-22 (CLOSED) | Session lockdown: `subumbra-keys` now persists `sessions.db`, enforces global lockdown plus single active-session scope on `GET /keys/<id>`, and exposes read-only `GET /sessions`; Worker `POST /proxy` now requires `session_token:<adapter_id>` in KV; bootstrap adds `./bootstrap.sh --session start|end|status|list`; the UI shows read-only lockdown/session state. Bug found and fixed: `_session_lock` was a non-reentrant `Lock()` causing deadlock in `_try_consume_session_query()` — changed to `RLock()`. VPS `existing-stack` proof **PASS**: `claude-vps-20260522T213055Z` (SHA `fab1ffc`). Archive: `council/closed/r82-session-lockdown/`. |
| r83-multi-session | 2026-05-23 (CLOSED) | Multi-session isolation: bootstrap now supports multiple concurrent active sessions when their effective `(adapter_id, key_id)` coverage stays disjoint, rejects overlapping sessions before any KV mutation, writes per-session shadow KV keys (`session_token:<session_id>:<adapter_id>`), and maintains aggregated Worker adapter gates as `active_adapter:<adapter_id>`. `subumbra-keys` now matches against all active rows, `/sessions` returns `active_sessions`, and the dashboard renders zero/one/many active sessions. VPS proofs **PASS**: `claude-vps-20260523T042453Z` (`existing-stack`) and `gemini-vps-20260523T053924Z` (`fresh-install`, merged main `8febbac`). Archive: `council/closed/r83-multi-session/`. |
| r84-ssh-vault | 2026-05-24 (CLOSED) | SSH private key custody: `SubumbraVault` DO now holds Ed25519 SSH keys in a new `ssh_keys` SQLite table (PKCS#8 private blob, raw+OpenSSH public key); three new Worker routes (`POST /setup/ssh-keygen`, `POST /setup/ssh-import`, `POST /ssh/sign`) enable key generation, unencrypted key import, and signing under the same active-session gate as `/proxy`; `bootstrap/subumbra_ssh.py` new module handles bootstrap-side SSH provisioning; `subumbra-keys` returns metadata-only responses for `type: ssh_key` records; `--status` and `--push-registry` are type-aware for mixed SSH+API record sets; GitHub adapter template added. Non-consensus: passphrase-protected SSH key ingestion (bcrypt) excluded per Codex vote — deferred to follow-up. VPS `fresh-install` proofs **PASS**: `claude-vps-20260524T055556Z`, `gemini-vps-20260524T060526Z` (SHA `ed97687`). Archive: `council/closed/r84-ssh-vault/`. |
| r85-ssh-agent | 2026-05-24 (CLOSED) | Local SSH agent bridge: new `subumbra-agent` service implements the minimal OpenSSH agent surface for identities and sign requests, mounts metadata-only SSH identities from `keys.json`, and forwards `ssh-ed25519` sign operations through the new `subumbra-proxy` `/t/{key_id}/ssh/sign` route to the existing Worker `/ssh/sign` backend. Bootstrap SSH provisioning now prints `SSH_AUTH_SOCK` / `IdentityAgent` bring-up hints. Verification **PASS** on the live `existing-stack` lane against real fixtures: GitHub repo `polysemic/Subumbra-SSH-Test`, VPS user `subumbra`, key `github_vps_test`, adapter `sshtest`. Official proof run: `gemini-vps-20260524T213427Z`; Claude additionally captured `claude-ssh-e2e-20260524T212312Z` with `git push`, `ssh`, `scp`, and `rsync` all green (implementation `a34c3d5`). Archive: `council/closed/r85-ssh-agent/`. |
| r85-1-ssh-daily-use | 2026-05-25 (CLOSED) | SSH daily-use hardening: transitioned the local agent to fully non-root execution bound to host `${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock`, added programmatic Day-2 SSH key operations (`--add-ssh-key`, `--rotate-ssh-key`, `--revoke-ssh-key`), implemented a secure `--deploy-worker` CLI command to handle worker updates without KV drift, and enabled durable SQLite audit trails for the `/ssh/sign` pathway surfaced in the status dashboard. Verification **PASS** on the live `existing-stack` lane against real GitHub and VPS SSH targets. Proof runs: Claude `claude-vps-20260525T042604Z` and Gemini `gemini-vps-20260525T043741Z`. Archive: `council/closed/r85-1-ssh-daily-use/`. |


---

## Path Forward

For the detailed multi-round strategy and unaddressed technical debt, see the [ROADMAP.md](ROADMAP.md).

1. **Strategic roadmap**: Planned and possible work lives in the root [`ROADMAP.md`](ROADMAP.md). Themes there are grouped for scheduling—**not** a committed sequence. The Cloudflare arc is complete as of `r73-cloudflare-autoprovision`: BYOC runtime credentials, day-2 credential management, optional API-driven Tunnel/DNS/Access provisioning, and teardown are implemented and verified. Cloudflare is no longer an active theme; future work is debug- or request-driven only.
2. **Council audit stubs**: `council/doc-cleanup.md` and `council/log-cleanup.md` now point at `ROADMAP.md` (their 2026-05-10 scan content was merged 2026-05-13). Optional local snapshots can be dropped under `council/archive/roadmap-baseline/` (see that folder’s `README.md`).
3. **Operator scratchpad**: `council/eric-questions.md` is for research notes only; backlog lines belong in `ROADMAP.md`.
4. **Deferred from round-cleanup (2026-05-13):** `subumbra-keys` SQLite **`velocity_counters`** table has no automatic pruning (medium priority — schedule an observability/maintenance round). Lower-priority hygiene (orphan `litellm/__pycache__/*.pyc`, VPS `subumbra.json` 664 perms, `subumbra.json.bak` gitignore gap, stricter UI CSP after moving `onclick` to JS) is noted in local `council/cleanup.md` from that round’s `deferred.md`.
5. **Deferred from r67-user-templates / r69-template-lifecycle / r70-template-cleanup (2026-05-14):** Top-level manifest `source:` routing and remote template prefetch remain deferred pending a safe trust/update model. Docker Hub/Portainer native template fetch remains a future round. Low-priority template lifecycle/doc polish items (community template sharing, hot-reload, formal JSON schema, `subumbra.example.yaml` parity, field-level `velocity` docs) remain in `council/cleanup.md`.
6. **Deferred from r68-template-foundation (2026-05-13):** Manifest field rename **`provider` → `label`** remains a breaking cross-service migration for a future round. Non-blocking `r68` verification/harness notes (shared P9.6 Worker baseline assumptions, `/opt/subumbra` stack-down environmental startup) are recorded in local `council/cleanup.md`.
7. **Deferred from r69-template-lifecycle (2026-05-13):** Harness alignment for day-2 template lifecycle rounds remains outstanding. Current `fresh-install` proof assumptions conflict with `--status` / adapter-mutation checks that need an initialized manifest plus `data/keys.json`; future harness work should either seed that state explicitly or use `existing-stack` for day-2-only verification.
8. **Deferred from r71-security-hardening (2026-05-17):** Adapter `/proxy` velocity semantics under omitted, tight, and high-capacity policy values remain a future validation matrix. Cloudflare-native auth/admin rate limiting and round-hook Scenario 9 template-key detection are follow-up items rather than blockers for the shipped R71 security behavior.
9. **Completed Cloudflare arc (r72-r73):** `r72-cloudflare-runtime-ux` delivered Cloudflare BYOC operator inputs, day-2 credential rotation, and updated docs. `r73-cloudflare-autoprovision` delivered API-driven Tunnel/DNS/Access provisioning, `cf-resources.json` resource tracking, `--nuke-cloudflare` teardown, and the `cf-api-provision` verification lane. Remaining Cloudflare work is request/debug-driven only; focus returns to core hardening.
10. **Deferred from r74-subumbra-verify (2026-05-18):** Current alpha release tags are lightweight, so `SUBUMBRA_REQUIRE_SIGNED_TAG=1` is intentionally opt-in and will fail until the project ships signed annotated release tags. Schedule a signed-release / release-trust-root round before claiming high-assurance bootstrap-source provenance.
11. **Deferred from r77-response-header-policy (2026-05-21):** Query-param authority (`allow.query_params`) remains a dedicated future policy round. Provider-specific header defaults beyond the evidence-backed Anthropic/OpenAI/Groq set remain deferred until live completion/message probes confirm them. Optional per-key header escape hatches and streaming-path `response.deny_patterns` enforcement remain follow-on work. Raw query-string normalization in `subumbra-proxy/app.py` also remains deferred until targeted compatibility proof confirms a safe remediation path.
12. **Deferred from r82-session-lockdown (2026-05-22):** Dashboard session close button and write controls remain deferred until the hardened management API exists (council/rTBD-structure-upgrade). Worker-side max_queries enforcement via KV/DO (atomic increment) deferred — keys service is the sole quota counter for now. Clock drift leeway buffer between Docker host and CF edge TTL is a nice-to-have, not a blocker.
13. **Deferred from r84-ssh-vault / r85-ssh-agent (2026-05-24):** SSH hardening/docs follow-up is now the next priority slice: `allow.hosts` policy enforcement, `confirm_each_sign`, `max_sign_ops` SSH-session quota, day-2 SSH rotation flows, and broader SSH operator docs/snippet hardening are r87-priority items. Bootstrap monolith modularization (session, CF, keys, adapters) remains r86-priority. A stale CF-side session shadow-KV cleanup gap observed during the r85 end-to-end battery also belongs in that follow-up hardening arc. Passphrase-protected OpenSSH Ed25519 key ingestion (bcrypt dependency) remains a follow-up once the operator dependency story is clear. RSA SSH (rsa-sha2-256/512), ECDSA, GPG/git-commit signing, SSH CA, and GitHub Apps JWT remain lower-priority future capabilities.
