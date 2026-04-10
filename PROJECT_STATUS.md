# PROJECT_STATUS
*Current state — updated 2026-04-10*
*Rounds 1–34 closed. Round 35 not started. See `council/COUNCIL.md` for round history and current status.*

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

## Roadmap Arc (Rounds 34-36)

This arc focuses on evolving Subumbra from a static, bundled configuration into a flexible, operator-managed system. Approved 2026-04-09 in [provider-adapter-flexibility-roadmap.md](file:///home/eric/git/Subumbra/council/approved/provider-adapter-flexibility-roadmap.md).

### Round 34: Provider Flexibility (Closed 2026-04-10)
- **Focus**: Built-in provider catalog expansion on the current architecture.
- **Goal**: Add Cerebras, Gemini, Mistral, OpenRouter, Together, and xAI as bootstrapable LiteLLM providers.
- **Outcome**: Closed with official proof plus six-provider end-to-end verification; the built-in AI provider set now covers 10 providers on the current architecture.

### Round 35: Adapter Flexibility (Current Architecture)
- **Focus**: Identity/Token generalization across bootstrap and runtime.
- **Goal**: Move from 4 hardcoded apps to arbitrary named adapters.
- **Outcome**: Isolated authority for any gateway/app (Open WebUI, Portkey, etc.).

### Round 36: Live Provider Registry
- **Focus**: KV-backed Worker registry.
- **Goal**: Move allowlist to Cloudflare KV.
- **Outcome**: No Worker redeploys for new providers; fixes the "Custom Provider" wizard path.

---

**Cross-round invariants**:
- Split-decrypt boundary remains intact.
- No durable decrypt power on operator-controlled hosts.
- Worker-side hostname/provider validation must remain fail-closed.

