# PROJECT_STATUS
*Current state — updated 2026-05-12*

---

## Architecture

V3 Asymmetric Envelope Encryption (deployed, all three council verifiers PASS).

- Asymmetric hybrid envelope: RSA-4096 wraps a per-record AES-256-GCM DEK; neither side can decrypt alone
- AAD binding: `subumbra:v3:<key_id>:<policy_hash>` prevents ciphertext transplant and policy replay
- Private key in CF Durable Object SQLite custody (non-extractable); public key on host for offline rotation
- Manifest-owned provider authority: operators declare routing, auth, and capability in `subumbra.json`; no hardcoded catalog at runtime
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

Deferred by council consensus. Acceptable for current single-operator POC deployment.

| ID | Description | Rationale |
|----|-------------|-----------|
| MEDIUM-1 | Python memory scrubbing is best-effort; `bytearray` zeroing does not prevent copies in `os.environ` or immutable `str` objects | No code fix possible in CPython |
| MEDIUM-5 | `/health` leaks `keys_loaded` count unauthenticated | Acceptable on Docker internal network with no host exposure |
| G-MEDIUM-3 | CF Worker buffers full body with no size limit (128 MB CF cap) | Low risk for small-team internal use |
| AUDIT-RETENTION | SQLite audit trail is durable across restarts and row growth is capped by `AUDIT_MAX_ROWS`, but retention is still local only with no archival/export path | Accepted as current local-ops limit |
| CRITICAL-3 | CF Access header strip enforced at Worker edge only | Accepted as architectural constraint (Worker is version-controlled) |
| DEV-AUDIT | `npm audit` vulnerabilities in wrangler dev tooling | Dev-only; never deployed to CF production |
| DASH-COUNT | Occasional missing entries in dashboard request log | Root cause not yet investigated |
| DASH-FLICKER | Recent Requests table briefly shows fewer entries on some poll cycles | UI polling race; entries return on next poll |
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

---

## Path Forward

1. **Verification harness — Worker deploy gap**: existing-stack proof mode (`update_existing_stack`) does not call `wrangler deploy` after `docker compose up`; any round modifying `worker/src/worker.js` requires a separate manual deploy step with CF credentials. Consider adding `--deploy-worker` flag or a `council/{round}/deploy-worker.sh` hook.
2. **UI Basic Auth rate limit — Option C follow-on (deferred):** R59 implemented Dockerfile-only single-worker Gunicorn (`--workers 1 --threads 4`). A future round may still be needed if operators require a durable shared counter (SQLite on an existing volume), trusted reverse-proxy IP semantics, or other models explicitly deferred in R59 `deferred.md`.
3. **Bootstrap tombstone stubs**: `run_interactive_wizard()` and `_load_env_fallback()` in `bootstrap/subumbra-bootstrap.py` are tombstoned stubs (call `die()`/`_automation_fail()` immediately) but remain reachable from `main()`. Not dead code but candidates for tombstone-cleanup round.
4. **Verification harness portability**: consolidate fresh-install proof hooks around operator-first failure handling, dynamic `SUBUMBRA_PROXY_HOST_PORT`, and auth-aware preflight for secure-UI deployments.
