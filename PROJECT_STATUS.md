# PROJECT_STATUS
*Current state — updated 2026-06-09 (r94-1-schema-update verified and closed)*



---

## Architecture

V3 Asymmetric Envelope Encryption (deployed, all three council verifiers PASS).

- Asymmetric hybrid envelope: RSA-4096 wraps a per-record AES-256-GCM DEK; neither side can decrypt alone
- AAD binding: `subumbra:v3:<key_id>:<policy_hash>` prevents ciphertext transplant and policy replay; as of r75 the Worker uses the live registry `policy_hash` as the decrypt-time authority rather than trusting the client-supplied request field
- Private key in CF Durable Object SQLite custody (non-extractable); public key on host for offline rotation
- Manifest-owned provider authority: operators declare routing, auth, and capability in `manifest.yaml` required; no hardcoded catalog at runtime
- **YAML manifest support (r67):** Bootstrap accepts `manifest.yaml`; local `./templates/<name>.yaml` operator workspace checked before signed built-in catalog
- V1 symmetric `MASTER_DECRYPTION_KEY` path fully removed; V2 records hard-rejected by Worker
- **Session lockdown (r82, verified):** new deployments now initialize a local `sessions.db` with `lockdown_enabled=1`; `GET /keys/<id>` and Worker `POST /proxy` both fail closed until the operator opens one bounded session with `./bootstrap.sh --session start ...`, and read-only session state is visible through `GET /sessions` and the dashboard.
- **Multi-session isolation (r83, verified):** bootstrap now allows multiple concurrent active sessions when their effective `(consumer_id, key_id)` coverage stays disjoint, writes per-session shadow KV keys shaped as `session_token:<session_id>:<consumer_id>`, maintains consumer-level Worker gates as `active_consumer:<consumer_id>`, and exposes list-shaped `active_sessions` state to `subumbra-keys` clients and the dashboard.
- **SSH agent bridge (r85, verified):** the stack now ships a local `subumbra-agent` service exposing `/run/subumbra/ssh-agent.sock`, reading metadata-only SSH identities from mounted `endpoint.json`, and forwarding sign requests through `subumbra-proxy` to Worker `POST /ssh/sign`; end-to-end `ssh`, `git`, `scp`, and `rsync` flows were proven against the live VPS and GitHub fixtures under the existing session gate.
- **SSH daily-use follow-through (r85-1, verified):** transition to non-root execution via host `${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock` is complete, day-2 SSH key management commands (`--add-ssh-key`, `--rotate-ssh-key`, `--revoke-ssh-key`) are fully implemented, and durable `ssh_sign` audit events are persisted and surfaced in the operator dashboard.
- **SSH destination binding hardening (r85-2, verified):** the local agent now parses native OpenSSH `session-bind@openssh.com` frames, restricted SSH keys may declare `allow.hosts`, bootstrap resolves those host labels into stored SSH host-key fingerprints, and the SSH sign path now fail-closes with `host_required` / `host_not_allowed` when a restricted key lacks verified destination context or is used against the wrong host.
- **SSH sign quota and audit (r85-3, verified):** `max_sign_ops` SSH-session quota is now enforced at the Worker/VaultDO signing boundary — signatures issued by Subumbra count against the quota, not downstream acceptance. The DO `ssh_session_quota` table is the sole counter authority; `session_sign_limit_reached` (403) enforces exhaustion; `session_quota_unavailable` (503) fails closed on state errors. Bootstrap writes `ssh_session_scope:{consumer_id}:{key_id}` KV entries on session start and cleans them on session end. SSH audit events now include `target_host` (verified host fingerprint when present); `subumbra-keys` supports server-side `endpoint=ssh_sign` and `target_host=SHA256:...` filters. The dashboard renders SSH quota labels and `target_host` in the SSH audit drill-down.
- **Gate approval flow (r87, verified and closed):** selected HTTP and SSH requests route through a dedicated `SubumbraJanus` Durable Object that stores pending approval state, browser-push subscriptions, and expiry alarms; `subumbra-proxy` polls Worker gate status and re-submits only approved requests; the dashboard exposes push subscription and pending gate visibility. The entire flow has been verified under a 16-thread high-concurrency stress test with zero failures.
- **Bootstrap services and UI auth (r88, verified and closed):** bootstrap now resolves top-level manifest `services.ui.deploy`, `services.ui.auth.mode`, and `services.ssh_agent.deploy`; runtime `.env` now persists `DEPLOY_UI`, `DEPLOY_SSH`, `UI_USERNAME`, `UI_PASSWORD_HASH`, and `CF_ACCESS_PROTECTED`; `bootstrap.sh --upgrade` re-applies the corresponding Compose profiles; `./bootstrap.sh --update-ui-auth` rotates UI auth without a full re-bootstrap; and `ui/app.py` now fails closed unless either hashed Basic Auth or `CF_ACCESS_PROTECTED=true` is configured.
- **Secure UI console (r88-secure-ui, verified and closed):** the single-page UI is replaced by a multi-page console (Overview, Sessions, Vault, Consumers, Policies, Audit, Cloudflare, Observability, Settings). Auth integrates PBKDF2 hashing via `_hash_utils.verify_ui_password()`. Gate Approvals panel preserved on Overview. Live data wiring partitions API and SSH keys, populates Cloudflare env from runtime variables, and degrades gracefully when the Worker is unreachable. Security hardening: `Cross-Origin-Opener-Policy`, `Permissions-Policy`, `_require_json` 415 guard on all write routes, and per-IP key-session rate limiting.
- **UI read integration (r89, verified and closed):** `subumbra-keys` now exposes read-only `/consumers` and `/observability` endpoints for the Secure UI; the Consumers, Policies, Observability, and Overview attention surfaces now derive from live reads instead of the remaining mock console sections; adapter snippets are built from the existing adapter template metadata and externalized with `CF_WORKER_URL`; and the Consumers page now ships hidden-by-default token reveal/copy blocks plus copyable proxy URLs and config snippets.
- **UI read polish (r90, verified and closed):** the Secure UI vault and policy detail flows now use focused `?select=` navigation plus server-rendered drawer panes, the vault SSH detail path now shows real key metadata and fingerprints from live `public_key` / `algorithm` fields, and adapter proxy/config snippets now present local-host plus Docker-internal proxy routes instead of Worker `/t/` URLs. Release closeout for `1.2.1-alpha` landed in `VERSION`, `CHANGELOG.md`, `ROADMAP.md`, and `CLAUDE.md`.
- **npm publish brokering (r92, verified and closed):** `type: npm_token` records are now a first-class manifest type alongside `api_key` and `ssh_key`; tokens are encrypted with V3 RSA envelope custody in the Cloudflare vault; the Worker intercepts `npm publish` PUT requests before forwarding to inspect the tarball's `_attachments[*].data` field — base64-decoded, gunzipped, tar-parsed — against operator-declared safe-literal `deny.publish_path_patterns` and `deny.publish_content_patterns`; package identity is enforced across the URL path, `_id`, and `name` body fields; `allow.scopes` restricts which package scope prefixes are authorised; `--rotate-npm-token` provides offline token rotation matching the SSH and API key pattern; and path-scoped `.npmrc` auth is documented so the developer machine never holds the real npm token. Proof run IDs: `claude-vps-20260531T221448Z`, `gemini-vps-20260531T222725Z`, `codex-vps-20260601T000807Z`.
- **npm professional controls (r93, verified and closed):** npm policies now support validated `allow.npm_operations` and explicit `deny.max_tarball_bytes`; the Worker classifies wire-visible npm operations into `publish`, `query`, `dist-tag`, `owner`, `access`, and `unpublish`, defaulting absent/empty operation lists to `publish` plus `query`; oversized tarballs are denied before and after gzip decode using the configured limit; and `./bootstrap.sh --show npm` now renders substituted path-scoped `.npmrc` lines rather than literal `{key_id}` placeholders. `docs/apps/npm/install.md` now includes the verified `actions/setup-node@v4` / `NODE_AUTH_TOKEN` path-scoped flow and the new npm policy fields. Verification outcome: Gemini PASS with remediation dossier `manual-20260603T105710`; Claude captured accepted harness-only issues in `claude-vps-20260601T050600Z`.
- **Schema authority unification (r94-1, verified and closed):** bootstrap manifest parsing, embedded `endpoint.json` records, SSH/npm record helpers, session storage, and the `subumbra-keys` `/consumers` listing contract now use `consumers` / `allowed_consumers` as the canonical caller-authority field names. Starter manifests, `docs/subumbra-install.md`, and `scripts/subumbra-env-ingest.py` now emit `consumers:` and `allow.consumers`. Fresh-install proof run IDs: `claude-vps-20260610T015657Z`, `gemini-vps-20260610T014748Z` (verified SHA `3cfd9d5`).



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
| R46 | 2026-05-07 | Alpha app identity and rotation: per-app consumer binding, V3-only rotation |
| R46.5 | 2026-05-07 | Vault granularity: shared vault default + opt-in per-key isolated vault |
| R47 | 2026-05-08 | Runtime contract cleanup: legacy `SubumbraProxy` removed, non-root runtime, staged bootstrap pipeline |
| R48 | 2026-05-08 | Intent attestation: `intent.trust` guardrails active; response-side `deny_patterns` deferred to R48-5 |
| R48-1 | 2026-05-08 | Config manifest unification arc planning |
| R48-2 | 2026-05-08 | Manifest ingest: manifest-driven bootstrap input; `IMPORT_PATH_*` retired |
| R48-3 | 2026-05-09 | Internal state authority: day-2 commands run from embedded record state |
| R48-4 | 2026-05-09 | Provider catalog removal: `subumbra.json` policy owns all routing/auth declarations |
| R48-5 | 2026-05-09 | Response enforcement: `deny_patterns` active for buffered `application/json`/`text/plain` |
| R48-6 | 2026-05-10 | Bootstrap UX: nuke-and-pave, shared-vault reuse, `keys_data` volume rename |
| R49 | 2026-05-10 | Velocity limits and circuit breakers: `consumer_rpm`, `key_rpm`, `breaker_*` manifest fields |
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
| R60 | 2026-05-12 | Harness improvements: `--deploy-worker` flag, dynamic proxy port resolution, mode-aware P9.5 skipping |
| R61 | 2026-05-12 | Bootstrap checkpoint removal: plaintext checkpoint eliminated; staged `secret_ref` resolution; fail-closed host env sync; operator recovery docs |
| R62 | 2026-05-12 | Interactive manifest bootstrap wizard: RAM-only `_WIZARD_SECRETS`; `verify-round.sh` round hook introduced |
| R63 | 2026-05-12 | Observability consistency: SQLite-backed `/stats` and `/keys` per-key usage; volatile RAM counters removed; proxy logging ISO alignment |
| R64 | 2026-05-13 | Launch polish: `/audit` filters; `worker_auth` dashboard copy; `subumbra-keys` Gunicorn `--no-control-socket`; `verify-round.sh` S1–S6 |
| R65 | 2026-05-13 | Launch docs: README quickstart, `docs/architecture.md`, `manifest.minimal.yaml` / `manifest.example.yaml`, `docs/integration-recipes.md`; legacy stubs removed |
| round-cleanup | 2026-05-13 | Post-launch hardening: bootstrap pre-mutation KV gate; zero `SUBUMBRA_SETUP_TOKEN` residue; proxy `token_mismatch`; Worker `HEAD /health`; `CF_WORKER_NAME` inference |
| r67-user-templates | 2026-05-13 | Template liberation: YAML manifest support; local `./templates/<name>.yaml` operator workspace checked before signed built-in catalog; `manifest.minimal.yaml` tracked starter |
| r68-template-foundation | 2026-05-13 | Template foundation cleanup: error messages name originating template; starter descriptions aligned to `manifest.minimal.yaml` |
| r69-template-lifecycle | 2026-05-13 | Template lifecycle: built-in catalog converted to YAML; `--status` reports drift/revocation; `--add-consumer` / `--revoke-consumer` offer manifest sync |
| r70-template-cleanup | 2026-05-14 | Template polish: shadowing warns on operator override; malformed-template warnings include file path; existing-stack verifier replaces impossible fresh-install assumption |
| r71-security-hardening | 2026-05-17 | Worker security headers (`Cache-Control: no-store`, HSTS, CORP, XCTO); pre-parse rejection on admin routes; DO-backed per-IP throttling on auth/admin surfaces; velocity defaults in signed templates |
| r72-cloudflare-updates | 2026-05-18 | Cloudflare BYOC runtime credentials: `--update-tunnel`, `--update-access` day-2 verbs; `docs/cloudflare-tunnel-access.md` |
| r73-cloudflare-autoprovision | 2026-05-18 | Cloudflare auto-provision: Tunnel, DNS CNAME, Access app/policy/token from one `CF_API_TOKEN`; `data/cf-resources.json` tracking; `--nuke-cloudflare` teardown |
| r74-subumbra-verify | 2026-05-18 | Source-trust verifier: `scripts/subumbra-verify` preflight (Git state, sensitive-file drift, `.env` residue); `SUBUMBRA_ALLOW_UNVERIFIED_SOURCE` break-glass |
| r75-shannon-patch | 2026-05-20 | Shannon hardening: Worker uses live registry `policy_hash` for AAD (not client-supplied); `1.1.1-alpha` release closeout |
| r76-response-injection-fix | 2026-05-21 | Response-injection hardening: `redirect: "manual"` on upstream fetch; `deny_patterns` case-insensitive; explicit `text/event-stream` bypass |
| r77-response-header-policy | 2026-05-21 | Header-policy enforcement: `allow.request_headers` and `response.allow_headers` in policy/schema/Worker/proxy; signed template defaults for Anthropic, OpenAI, Groq |
| r78-ssrf-port-validation | 2026-05-21 | SSRF port lockdown: Worker rejects non-443 `target_url` ports |
| r79-keys-auth-scoping | 2026-05-22 | Keys-service auth scoping: `Cache-Control: no-store` on all responses; `/keys`, `/stats`, `/audit` scoped to `allowed_keys`; `can_list_all_keys` for UI |
| r80-keys-auth-hardening | 2026-05-22 | Keys auth hardening: SQLite-backed rate limiting; nonce replay globally blocked; HMAC contract length-prefixed across proxy/probe/keys |
| r81-keys-auth-internals | 2026-05-22 | Keys auth internals: fail-closed on audit-store unavailability; `paused` enforcement; `consumer_id`-bound HMAC; collapsed 400/401 oracle |
| r82-session-lockdown | 2026-05-22 | Session lockdown: `sessions.db` persisted; global lockdown + single active-session scope; `--session start|end|status|list`; `RLock` deadlock fix |
| r83-multi-session | 2026-05-23 | Multi-session isolation: disjoint `(consumer_id, key_id)` coverage enforced; per-session shadow KV keys; `active_consumer:<consumer_id>` aggregated gate |
| r84-ssh-vault | 2026-05-24 | SSH key custody: `SubumbraVault` DO holds Ed25519 SSH keys; `POST /setup/ssh-keygen`, `/setup/ssh-import`, `/ssh/sign` Worker routes; `subumbra_ssh.py` bootstrap module |
| r85-ssh-agent | 2026-05-24 | Local SSH agent bridge: `subumbra-agent` service; OpenSSH agent protocol for Ed25519 identities and sign ops; `/t/{key_id}/ssh/sign` proxy route |
| r85-1-ssh-daily-use | 2026-05-25 | SSH daily-use hardening: non-root agent socket; `--add-ssh-key`, `--rotate-ssh-key`, `--revoke-ssh-key`; SQLite SSH audit trail |
| r85-2-ssh-hardening | 2026-05-25 | SSH destination binding: `session-bind@openssh.com` frame parsing; `allow.hosts` fingerprint enforcement; `host_required` / `host_not_allowed` (403) |
| r85-3-ssh-quota-audit | 2026-05-26 | SSH sign quota: `max_sign_ops` enforced at VaultDO; `session_sign_limit_reached` (403); `target_host` in SSH audit events; server-side `endpoint=ssh_sign` filter |
| r85-4-ssh-cleanup | 2026-05-26 | SSH release cleanup: auth-path rate limiting on `/proxy`; expired SSH session-scope fails closed; `manifest.example.yaml` SSH examples; targeted session-ID close default |
| r86-modularize | 2026-05-27 | Bootstrap modularization: 6800-line monolith split into `subumbra_core`, `subumbra_cf`, `subumbra_session`, `subumbra_adapters`, `subumbra_keys`; no behavior change |
| r87-gate-do | 2026-05-27 | Janus approval flow: `SubumbraJanus` DO with SQLite state, VAPID browser push, expiry alarms; proxy polling loop; 16-thread stress test PASS |
| r88-bootstrap-services | 2026-05-28 | Bootstrap service selection: `services.ui.deploy` / `services.ssh_agent.deploy` manifest fields; profile-gated optional services; `--update-ui-auth` verb |
| r88-secure-ui | 2026-05-28 | Secure UI multi-page console: 11-route console replaces single-page UI; PBKDF2 auth; COOP/Permissions-Policy hardening; per-IP key-session rate limit |
| r89-ui-read-integration | 2026-05-29 | UI read integration: live `/consumers` and `/observability` endpoints; console replaces mock arrays with live backend reads; masked token reveal/copy |
| r90-ui-read-polish | 2026-05-29 | UI read polish: `?select=` drawer navigation; deep-linked audit/policy/overview; host-local + Docker-internal proxy topology in snippets |
| r91-doc-updaes | 2026-05-31 | Comparison atlas: `docs/comparisons/` covering secret vaults, API brokers, SSH agents, MCP security, threat-model failure modes |
| r92-npm-credential | 2026-06-01 | npm publish brokering: `type: npm_token` with V3 envelope custody; Worker tarball inspection; `allow.scopes`; `--rotate-npm-token`; path-scoped `.npmrc` |
| r93-npm-professional | 2026-06-03 | npm professional controls: `allow.npm_operations` classification (`publish`, `query`, `dist-tag`, `owner`, `access`, `unpublish`); `deny.max_tarball_bytes`; `.npmrc` show substitution |
| r94-naming-consistency | 2026-06-08 | Naming cutover: `adapter`→`consumer`; `Gate`→`Janus`; `subumbra.yaml`→`manifest.yaml`; `keys.json`→`endpoint.json`; split-trust language throughout |
| r94-1-schema-update | 2026-06-09 | Schema authority cleanup: top-level `consumers`, `allow.consumers`, `allowed_consumers`, `/consumers`, and `endpoint.json` caller-authority naming |



---

## Path Forward

For the detailed multi-round strategy and unaddressed technical debt, see the [ROADMAP.md](ROADMAP.md).

1. **Strategic roadmap**: Planned and possible work lives in the root [`ROADMAP.md`](ROADMAP.md). Themes there are grouped for scheduling—**not** a committed sequence. The Cloudflare arc is complete as of `r73-cloudflare-autoprovision`: BYOC runtime credentials, day-2 credential management, optional API-driven Tunnel/DNS/Access provisioning, and teardown are implemented and verified. Cloudflare is no longer an active theme; future work is debug- or request-driven only.
2. **Council audit stubs**: `council/doc-cleanup.md` and `council/log-cleanup.md` now point at `ROADMAP.md` (their 2026-05-10 scan content was merged 2026-05-13). Optional local snapshots can be dropped under `council/archive/roadmap-baseline/` (see that folder’s `README.md`).
3. **Operator scratchpad**: `council/eric-questions.md` is for research notes only; backlog lines belong in `ROADMAP.md`.
4. **Deferred from round-cleanup (2026-05-13):** `subumbra-keys` SQLite **`velocity_counters`** table has no automatic pruning (medium priority — schedule an observability/maintenance round). Lower-priority hygiene (orphan `litellm/__pycache__/*.pyc`, VPS `subumbra.json` 664 perms, `subumbra.json.bak` gitignore gap, stricter UI CSP after moving `onclick` to JS) is noted in local `council/cleanup.md` from that round’s `deferred.md`.
5. **Deferred from r67-user-templates / r69-template-lifecycle / r70-template-cleanup (2026-05-14):** Top-level manifest `source:` routing and remote template prefetch remain deferred pending a safe trust/update model. Docker Hub/Portainer native template fetch remains a future round. Low-priority template lifecycle/doc polish items (community template sharing, hot-reload, formal JSON schema, `manifest.example.yaml` parity, field-level `velocity` docs) remain in `council/cleanup.md`.
6. **Deferred from r68-template-foundation (2026-05-13):** Manifest field rename **`provider` → `label`** remains a breaking cross-service migration for a future round. Non-blocking `r68` verification/harness notes (shared P9.6 Worker baseline assumptions, `/opt/subumbra` stack-down environmental startup) are recorded in local `council/cleanup.md`.
7. **Deferred from r69-template-lifecycle (2026-05-13):** Harness alignment for day-2 template lifecycle rounds remains outstanding. Current `fresh-install` proof assumptions conflict with `--status` / adapter-mutation checks that need an initialized manifest plus `data/endpoint.json`; future harness work should either seed that state explicitly or use `existing-stack` for day-2-only verification.
8. **Deferred from r71-security-hardening (2026-05-17):** Adapter `/proxy` velocity semantics under omitted, tight, and high-capacity policy values remain a future validation matrix. Cloudflare-native auth/admin rate limiting and round-hook Scenario 9 template-key detection are follow-up items rather than blockers for the shipped R71 security behavior.
9. **Deferred from r74-subumbra-verify (2026-05-18):** Current alpha release tags are lightweight, so `SUBUMBRA_REQUIRE_SIGNED_TAG=1` is intentionally opt-in and will fail until the project ships signed annotated release tags. Schedule a signed-release / release-trust-root round before claiming high-assurance bootstrap-source provenance.
10. **Deferred from r77-response-header-policy (2026-05-21):** Query-param authority (`allow.query_params`) remains a dedicated future policy round. Provider-specific header defaults beyond the evidence-backed Anthropic/OpenAI/Groq set remain deferred until live completion/message probes confirm them. Optional per-key header escape hatches and streaming-path `response.deny_patterns` enforcement remain follow-on work. Raw query-string normalization in `subumbra-proxy/app.py` also remains deferred until targeted compatibility proof confirms a safe remediation path.
11. **Deferred from r82-session-lockdown (2026-05-22):** Dashboard session close button and write controls remain deferred until the hardened management API exists (council/rTBD-structure-upgrade). Worker-side max_queries enforcement via KV/DO (atomic increment) deferred — keys service is the sole quota counter for now. Clock drift leeway buffer between Docker host and CF edge TTL is a nice-to-have, not a blocker.
12. **Deferred from the r84-r87 SSH / Gate arc (updated 2026-05-27 post-r87):** `confirm_each_sign` per-sign approval shipped in `r87-gate-do`. Remaining follow-up items are narrower: `request.deny_patterns` remains a separate outbound request-scanning round rather than part of Gate DO action-gating, `host_verified` boolean surfacing and KV gate auto-deletion on per-key quota exhaustion remain deferred (see `council/r85-3-ssh-quota-audit/deferred.md`), and a stale CF-side session shadow-KV cleanup gap observed during the r85 end-to-end battery still belongs in a follow-up hardening arc. Passphrase-protected OpenSSH Ed25519 key ingestion (bcrypt dependency) remains a follow-up once the operator dependency story is clear. RSA SSH (rsa-sha2-256/512), ECDSA, GPG/git-commit signing, SSH CA, and GitHub Apps JWT remain lower-priority future capabilities.
13. **Deferred from r86-modularize (2026-05-27):** `scripts/subumbra-verify` sensitive-file preflight must be updated to track the five new sibling bootstrap modules (`subumbra_core.py`, `subumbra_cf.py`, `subumbra_session.py`, `subumbra_adapters.py`, `subumbra_keys.py`) alongside `subumbra-bootstrap.py` to prevent untracked source mutations from bypassing the integrity check. `scripts/security/bandit/scan.sh` must include the same new modules to preserve automated vulnerability detection. Both items were explicitly excluded from r86 scope per the approved plan; schedule as part of the next security-tooling or bootstrap-hardening round.
14. **Deferred from r88-secure-ui (2026-05-28):** The `data.attention` panel on the Overview page remains mock-only — no live source or aggregation contract was defined. A future round should define an attention-item aggregation contract (key usage anomalies, audit spikes, policy drift signals). CSP `'unsafe-inline'` in `style-src` and session-cookie-based auth replacement remain lower-priority deferred items tracked in local `council/cleanup.md`.
15. **Deferred from r89-ui-read-integration (2026-05-29):** Gate approve/deny decisions are never written to the `subumbra-keys` audit DB (no Worker `/audit` callback on approval or denial). `velocity_rpm` remains None for all live keys due to lack of a time-bounded counting logic in the keys service. Additionally, copyable proxy URLs on the Consumers page render using `CF_WORKER_URL` which does not route `/t/...` proxy paths (needs local-host/internal-Compose URL fallback).
16. **Post-r94-1 operator follow-up (2026-06-09):** tracked starter manifests and bootstrap schema are now canonical on `consumers:` / `allow.consumers`, but any operator-owned working `manifest.yaml` files that still use legacy `adapters:` must be updated before the next bootstrap or fresh-install run. This is an operator migration note, not a new product round.
