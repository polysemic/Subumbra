# PROJECT_STATUS
*Current state — updated 2026-04-09*
*Rounds 1–32 closed. Round 33 proposal phase active. See `council/COUNCIL.md` for round history and current status.*

---

## Architecture

V2 Asymmetric Envelope Encryption (deployed, verified by all three council members).

- RSA-4096 key pair: public key on host, private key in CF Secrets
- Per-record AES-256-GCM DEKs wrapped by RSA public key
- AAD binding: `keyvault:v2:<key_id>`
- Offline per-key rotation via `--rotate` (no CF interaction)
- LiteLLM now uses canonical `POST /proxy` to reach the Worker; the old header-gated compatibility route has been removed
- Worker upstream routing uses a host-keyed `UPSTREAM_REGISTRY`; provider-specific auth branches are removed from Worker/DO logic
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

- Core: `forge-keys` + Cloudflare Worker decrypt/proxy contract
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
| MEDIUM-4 | HMAC replay within the 30 s window requires a nonce store shared across gunicorn workers | Significant complexity for Docker-internal-only endpoint |
| MEDIUM-5 | `/health` leaks `keys_loaded` count unauthenticated | Acceptable on Docker internal network with no host exposure |
| MEDIUM-7 | `/api/status` unauthenticated | Bound to `127.0.0.1:8080` (localhost only); add basic auth before multi-user |
| G-MEDIUM-2 | Dashboard health reflects forge-keys only; no end-to-end health probe | Post-POC architecture change |
| G-MEDIUM-3 | CF Worker buffers full body with no size limit (128 MB CF cap) | Low risk for small-team internal use |
| AUDIT-RETENTION | SQLite audit trail is durable across restarts and row growth is capped by `AUDIT_MAX_ROWS`, but retention is still forge-local only with no archival/export path | Accepted as current local-ops limit |
| LOW-5 | Dashboard loads Bootstrap CSS/JS from public CDN | Browser-only fetch; container is air-gapped |
| CRITICAL-3 | CF Access header strip enforced at Worker edge only | Accepted as architectural constraint (Worker is version-controlled) |
| DEV-AUDIT | `npm audit` vulnerabilities in wrangler dev tooling | Dev-only; never deployed to CF production |
| DASH-COUNT | Occasional missing entries in dashboard request log | Likely silent LiteLLM retry; not investigated |
| DASH-FLICKER | Recent Requests table briefly shows fewer entries on some poll cycles | UI polling race; entries return on next poll |
| PROVIDER-COUPLING | Reduced further but not eliminated: the remaining meaningful adapter coupling is LiteLLM model declaration duplication in `litellm/config.yaml`; `api_base_path` in `worker/src/providers.json` is now vestigial after Round 22 cut-over | Full multi-adapter generalization remains a later round |
| LITELLM-UI | LiteLLM admin UI login non-functional (no DB) | Use keyvault UI at `localhost:8080` instead |
| TOKEN-SYNC | `post-bootstrap.sh` now detects and warns on stale container tokens for `litellm`, `forge-keys`, and `ui`; bootstrap summary, `README.md`, and `CLAUDE.md` all require `docker compose up -d --force-recreate` after full bootstrap | Closed by Round 13 |
| TTL-FORGE-ONLY | forge-keys TTL prevents new record fetches after token expiry but does not remove Worker-side token authority. Replay of previously captured records plus a stolen token remains possible until re-bootstrap rotates Worker-side token state | Intentionally deferred beyond Round 30 |

---

## Open Questions

**1. CRITICAL-3 — ACCEPTED**
CF Access header strip is enforced at Worker edge only. Strip is prominently commented
in both `custom_callbacks.py` and `worker.js`. Accepted as architectural constraint.

**2. LiteLLM image pin**
Current pin: `main-latest@sha256:7c311546c25e7bb6e8cafede9fcd3d0d622ac636b5c9418befaa32e85dfb0186`
(LiteLLM `1.82.6`, verified 2026-03-29). Re-verify before updating.

---

## Next Rounds

The current agreed roadmap is to move from the proven adapter contract toward a
reusable sidecar/service integration path without jumping straight into the
more speculative transparent-proxy behavior.

**1. Adapter Contract — defined (Round 19)**
The canonical Subumbra core API is `POST /proxy`. Documented in
`docs/adapter-contract.md`. LiteLLM is Adapter #1 using that contract today.

**2. Explicit Sidecar Baseline — completed (Round 25)**
The project now has a persistent sidecar/service that hides forge-fetch +
canonical `POST /proxy` from the caller while keeping the app-facing request
surface explicit and testable.

Round 25 in-bounds request surface:

- `key_id`
- `target_url`
- `method`
- `headers`
- `body`

Round 25 completed with:

- persistent sidecar/service
- explicit app-facing five-field request shape
- internal forge-fetch + canonical `/proxy`
- streaming passthrough

**3. Provider Expansion + Operator Usability — completed (Round 26)**
The explicit sidecar now supports three additional JSON-native non-AI providers:

- `github`
- `slack`
- `sendgrid`

Round 26 also completed:

- sidecar smoke-test proof for the added providers
- provider catalog / operator guide collateral
- sidecar-specific Docker Compose drop-in template
- explicit CF infrastructure response-header cleanup in the sidecar

**4. Fresh Verification Harness — completed (Round 28)**
Round 28 standardized repeated council mechanics so verification starts from a
consistent host-facing baseline rather than inheriting stale containers, stale
tokens, partial rebuilds, or inconsistent host-path state.

Round 28 completed with:

- `scripts/council/preflight.sh` for host-path readiness and UI WARN handling
- `scripts/council/reset.sh` for non-destructive recreate flow plus optional
  image-service rebuilds
- `scripts/council/verify.sh` for standard host-path proof capture and per-run
  artifacts
- fallback recreation of `.env.bootstrap` from `.env.bootstrap_bak`
- per-run unique IDs and archived artifacts/logs under the round folder
- explicit separation between official PASS evidence and diagnostic-only
  artifacts

See `council/closed/round-28-verification-harness/` for the close-out record.

**5. Adapter Identity And Forge Access Scope — completed (Round 29)**
Round 29 replaced shared unrestricted forge authority with per-adapter scoped
authority so one compromised adapter cannot fetch records outside its intended
scope.

Round 29 completed with:

- per-adapter forge credentials instead of one shared unrestricted token
- current-deployment-compatible scoping rather than a workload-identity platform leap
- enforcement that disallowed forge records cannot be fetched cross-adapter
- host-facing verification that scoped access is actually enforced

**6. Revocation And TTL Guardrails — completed (Round 30)**
Round 30 added bootstrap-time adapter TTL metadata, forge-side expiry enforcement, host-facing stale-authority proof, and the explicit carried-forward `TTL-FORGE-ONLY` limitation. See `council/closed/round-30-revocation-ttl-guardrails/` for the verification record.

**7. Structured Audit Trail -- completed (Round 31)**
Round 31 added a durable forge-local audit trail so secret access is auditable
without exposing secret-bearing material in logs.

Round 31 completed with:

- structured forge access events persisted in SQLite across restarts
- operator-visible `adapter_id` / `endpoint` / `key_id` / `verdict` / `reason_code`
  audit fields surfaced through the existing UI path
- forge-local audit kept separate from Cloudflare-side Worker logging
- no broad logging backend or observability platform decision

**8. Rotation And Recovery Ergonomics (Round 32)**
Completed. Round 32 made the existing rotation and recovery paths executable and
verifier-provable without changing the split-decrypt architecture.

Round 32 delivered:

- a working bootstrap `--rotate` entry path
- a validated `forge-expire-adapter.sh` helper for forge-side emergency expiry
- a consolidated recovery playbook in `docs/operator-guide.md`
- bounded forge-local audit retention via `AUDIT_MAX_ROWS`
- official harness proofs for rotation, expiry, retention, and playbook presence

**9. Transparent Sidecar (Round 33)**
Only after identity and TTL hardening are verifier-closed should the project
take on the higher-complexity transparent sidecar path.

Round 33 should include:

- sidecar-owned path rewriting / translation
- pseudo-key extraction
- zero-code-change integration ideas
- no sidecar-local durable decrypt power

Cross-round rule:
- no round in this sequence should weaken the split-decrypt boundary or move
  durable decrypt power onto operator-controlled hosts
