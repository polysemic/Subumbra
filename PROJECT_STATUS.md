# PROJECT_STATUS
*Current state — updated 2026-05-14 (R65 launch docs — CLOSED; round-cleanup — CLOSED; r67-user-templates — CLOSED; r68-template-foundation — CLOSED; r69-template-lifecycle — CLOSED; r70-template-cleanup — CLOSED)*

---

## Architecture

V3 Asymmetric Envelope Encryption (deployed, all three council verifiers PASS).

- Asymmetric hybrid envelope: RSA-4096 wraps a per-record AES-256-GCM DEK; neither side can decrypt alone
- AAD binding: `subumbra:v3:<key_id>:<policy_hash>` prevents ciphertext transplant and policy replay
- Private key in CF Durable Object SQLite custody (non-extractable); public key on host for offline rotation
- Manifest-owned provider authority: operators declare routing, auth, and capability in `subumbra.yaml` (preferred) or `subumbra.json`; no hardcoded catalog at runtime
- **YAML manifest support (r67):** Bootstrap accepts `subumbra.yaml` or `subumbra.json`; local `./templates/<name>.yaml` operator workspace checked before signed built-in catalog
- V1 symmetric `MASTER_DECRYPTION_KEY` path fully removed; V2 records hard-rejected by Worker

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

**2. LiteLLM image pin**
Current pin: `main-latest@sha256:7c311546c25e7bb6e8cafede9fcd3d0d622ac636b5c9418befaa32e85dfb0186`
(LiteLLM `1.82.6`, verified 2026-03-29). Re-verify before updating.

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
| R65 | 2026-05-13 (CLOSED) | Launch docs: README quickstart + `docs/architecture.md`; gitignored `subumbra.json` with tracked `subumbra.minimal.json` / `subumbra.example.json`; `.env.bootstrap.example` + `.env.bootstrap_bak` note; `docs/integration-recipes.md` (merged guides + catalog curls); removed legacy root stubs + `docs/provider-catalog.md`; `litellm/README.md`; operator-guide `worker_auth` detail; path/link hygiene. VPS `existing-stack` proof **PASS**: `gemini-vps-20260513T022305Z` (SHA `b37481d`). `claude-verification.md` / `codex-verification.md` not on file (process note in `council/cleanup.md`). |
| round-cleanup | 2026-05-13 (CLOSED) | Code cleanup: bootstrap pre-mutation KV gate (no CF/.env mutation before abort); zero `SUBUMBRA_SETUP_TOKEN` in host `.env` after full bootstrap; proxy `worker_auth` `token_mismatch` for Worker 401; UI CSP + `Cache-Control`; Worker `HEAD /health`; `subumbra-verify-deploy` infers `CF_WORKER_NAME` from `CF_WORKER_URL`; remove stale `IMPORT_PATH_*` install doc + dead checkpoint cleanup; `verify-round.sh`. VPS `existing-stack` proofs **PASS**: `codex-vps-20260513T143105Z`, `Gemini-vps-20260513T142722Z` (SHA `05083d1`). Close-out documents accepted scope deviation (UI/README/install `worker_auth` follow-through; no `claude-verification.md`). Archive: `council/closed/round-cleanup/`. |
| r67-user-templates | 2026-05-13 (CLOSED) | Template Liberation: YAML manifest support (`subumbra.yaml` preferred, `subumbra.json` accepted); local `./templates/<name>.yaml` operator workspace checked before signed built-in catalog; `pyyaml==6.0.2` added; `bootstrap.sh` manifest auto-discovery; extension-free `/app/manifest` container path; `_load_local_template` with warn-on-error fallback + `info()` on success; `template_name` dead-field fix; `subumbra.minimal.yaml` tracked starter; docs + gitignore updated. VPS `existing-stack` proofs: Gemini **PASS** (`gemini-vps-20260513T190733Z`, SHA `b64308b`); Codex harness issues only (S2/S5 environmental — operator override: manual verification passes, `codex-vps-20260513T195138Z`, SHA `a196e4b`). Archive: `council/closed/r67-user-templates/`. |
| r68-template-foundation | 2026-05-13 (CLOSED) | Template Foundation cleanup: template-backed `_normalize_policy_doc()` failures now name the originating template; README and operator-guide starter descriptions now match the tracked multi-provider `subumbra.minimal.yaml`; round-local `verify-round.sh` added. Official proof artifacts: Claude round-hook scope checks **PASS** in `claude-vps-20260513T221410Z` (SHA `222f72f`); close-out accepted P9.6 Worker-unreachable baseline as `ENVIRONMENTAL` / harness-only, with real staging bootstrap scenarios passing. Archive: `council/closed/r68-template-foundation/`. |
| r69-template-lifecycle | 2026-05-13 (CLOSED) | Template lifecycle: signed built-in provider and adapter template library converted from JSON to YAML; catalog SHA entries updated and re-signed under the accepted release key; `_load_and_verify_catalog()` now parses verified YAML with existing `pyyaml`; read-only `./bootstrap.sh --status` reports `UP_TO_DATE`, `POLICY_DRIFT`, `NOT_DEPLOYED`, and `REVOKED`; `--add-adapter` / `--revoke-adapter` now offer bounded canonical `adapters: [...]` manifest sync with drift warnings when auto-rewrite is not possible; lifecycle docs updated. Verification source diffs **PASS** in `claude-vps-20260514T004721Z` and `gemini-vps-20260514T031424Z` (SHA `cf8151c`); close-out accepted fresh-install proof failure as `HARNESS_ISSUE` after manual and UI validation confirmed expected behavior. Archive: `council/closed/r69-template-lifecycle/`. |
| r70-template-cleanup | 2026-05-14 (CLOSED) | Template cleanup polish: local-template shadowing now warns when operator YAML overrides a signed built-in template; malformed local-template warnings now include the concrete `/app/user-templates/...` file path while preserving built-in fallback; operator-guide and project-memory were aligned to the post-r69 YAML template reality; a round-local existing-stack verifier replaced the prior impossible fresh-install assumption for template/day-2 proof work. Verification evidence: Gemini VPS proof **PASS** in `gemini-vps-20260514T051721Z` (SHA `8d26661`); Claude source-diff verification **PASS** with `claude-vps-20260514T043306Z` accepted as a local hook `HARNESS_ISSUE` due relative artifact-path capture, not product behavior. Archive: `council/closed/r70-template-cleanup/`. |

---

## Path Forward

For the detailed multi-round strategy and unaddressed technical debt, see the [ROADMAP.md](ROADMAP.md).

1. **Strategic roadmap**: Planned and possible work lives in the root [`ROADMAP.md`](ROADMAP.md). Themes there are grouped for scheduling (signal, policy, lifecycle, docs, Cloudflare, ecosystem, harness, research)—**not** a committed sequence.
2. **Council audit stubs**: `council/doc-cleanup.md` and `council/log-cleanup.md` now point at `ROADMAP.md` (their 2026-05-10 scan content was merged 2026-05-13). Optional local snapshots can be dropped under `council/archive/roadmap-baseline/` (see that folder’s `README.md`).
3. **Operator scratchpad**: `council/eric-questions.md` is for research notes only; backlog lines belong in `ROADMAP.md`.
4. **Deferred from round-cleanup (2026-05-13):** `subumbra-keys` SQLite **`velocity_counters`** table has no automatic pruning (medium priority — schedule an observability/maintenance round). Lower-priority hygiene (orphan `litellm/__pycache__/*.pyc`, VPS `subumbra.json` 664 perms, `subumbra.json.bak` gitignore gap, stricter UI CSP after moving `onclick` to JS) is noted in local `council/cleanup.md` from that round’s `deferred.md`.
5. **Deferred from r67-user-templates / r69-template-lifecycle / r70-template-cleanup (2026-05-14):** Top-level manifest `source:` routing and remote template prefetch remain deferred pending a safe trust/update model. Docker Hub/Portainer native template fetch remains a future round. Low-priority template lifecycle/doc polish items (community template sharing, hot-reload, formal JSON schema, `subumbra.example.yaml` parity, field-level `velocity` docs) remain in `council/cleanup.md`.
6. **Deferred from r68-template-foundation (2026-05-13):** Manifest field rename **`provider` → `label`** remains a breaking cross-service migration for a future round. Non-blocking `r68` verification/harness notes (shared P9.6 Worker baseline assumptions, `/opt/subumbra` stack-down environmental startup) are recorded in local `council/cleanup.md`.
7. **Deferred from r69-template-lifecycle (2026-05-13):** Harness alignment for day-2 template lifecycle rounds remains outstanding. Current `fresh-install` proof assumptions conflict with `--status` / adapter-mutation checks that need an initialized manifest plus `data/keys.json`; future harness work should either seed that state explicitly or use `existing-stack` for day-2-only verification.
