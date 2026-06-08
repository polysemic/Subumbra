# Subumbra — Universal Split-Trust Secret Broker

## Project Purpose
A docker-compose-based split-trust secret-broker core for applications that need to
use API keys without storing those keys in plaintext on the app server. Keys
are split across two systems — neither side can decrypt alone.

Subumbra's current reference integration surface is `subumbra-proxy`. LiteLLM
is a proven app-owned example, not the bundled product boundary. LiteLLM
operator docs and example config live under `docs/apps/litellm/`.

## Architecture

```
App-owned integration (LiteLLM, LibreChat, n8n, etc.)
    ↓ api_base: http://subumbra-proxy:8090/t/<key_id>/...  (consumer token as api_key)
subumbra-proxy
    ↓ fetches encrypted record metadata and packages canonical POST /proxy
subumbra-keys (docker internal network only)
    ↓ returns V3 envelope: ciphertext + wrapped_dek + pub_key_fp + policy_hash + vault_instance
    ↓ (useless without the matching RSA private key in the selected SubumbraVault DO)
Cloudflare Worker + Durable Object
    ↓ optional Gate DO approval hold for selected `/proxy` or `/ssh/sign` calls
    ↓ verifies fingerprint, unwraps DEK, decrypts provider key, injects auth
API Provider
    ↓ streams response
    ↓ back through Worker → proxy → app
```

## Security Properties
- Real API keys never exist in plaintext on any system you operate
- Decrypted keys exist briefly in CF Durable Object memory (~100ms) and transit to API providers over HTTPS
- Asymmetric hybrid envelope encryption (V3): RSA-4096 wraps per-record AES-256-GCM DEKs
- subumbra-keys container: holds wrapped DEK + AES-GCM ciphertext only (useless without RSA private key)
- SubumbraVault DO: holds RSA-4096 private key in SQLite custody (never extractable after import)
- SubumbraJanus DO: holds only pending approval state, browser subscriptions, and expiry timers
- Neither side can reconstruct keys alone
- Shared-vault keys use vault instance `vault`; opt-in isolated keys use `vault-<key_id>`
- AAD binding (`subumbra:v3:<key_id>:<policy_hash>`) prevents ciphertext transplant and policy replay
- pub_key_fp verified by Worker before decryption — mismatched keys fail fast
- Decrypted key exists only in CF Durable Object memory for ~100ms
- One-shot bootstrap process: keys exist in RAM only during generation
- Offline per-key rotation: uses public key on disk, no CF interaction needed

## R45 Threat Model

These threats motivate the R45 policy schema. Each is addressed structurally
by the five-round core arc; none requires a configuration workaround.

| Threat | Description | Mitigation |
|--------|-------------|------------|
| **Skeleton key misuse** | A compromised consumer token can request any `key_id` the attacker knows, across any provider. | Policy `allow.consumers` binds each token to specific records; `capability_class` narrows the semantic scope of what can be called. Implemented in R45-4. |
| **Prompt-injection capability abuse** | An LLM-mediated app can be manipulated into making requests that exceed its intended scope (e.g. a chat app making payment API calls). | `capability_class` + `allow.methods`/`path_prefixes` prevent cross-capability use at the Worker boundary. R48 adds optional `intent.trust` initiator and content-source gating. |
| **Worker replacement via stolen deploy authority** | An attacker who obtains a Cloudflare deploy token can replace the Worker with one that does not enforce policy, exfiltrates keys, or strips auth. | Bootstrap captures the deployed Worker bundle SHA-256 to `system-integrity.json`; `subumbra-verify-deploy` detects drift. Implemented in R45-2. |
| **Response-side exfiltration** | The Worker (or a replaced Worker) could inspect or redirect upstream API responses before returning them to the adapter. | R48-5 activates `response.deny_patterns` scanning for buffered response types (`application/json`, `text/plain`). Patterns are validated at bootstrap ingestion using the safe-pattern vocabulary (anchored literals/alternation only). A matching response body is denied with `response_deny_pattern_match` (403); no matched content is included in the error. `text/event-stream` and other streaming types pass through unchanged — streaming-path scanning is explicitly deferred beyond R48-5. |
| **Cross-adapter replay** | A ciphertext obtained by one adapter could be replayed through a different adapter or against a different provider's endpoint. | V2 AAD `subumbra:v2:<key_id>` already prevents ciphertext transplant. V3 AAD `subumbra:v3:<key_id>:<policy_hash>` additionally binds to the policy in effect at encryption time, preventing replay after policy change. Implemented in R45-3. |
| **V2→V3 drift** | V2 records have no policy binding. Mixing V2 and V3 records in a V3-enforcing Worker could allow policy bypass via a V2 record. | Grace window (V2 accepted through R45-4); hard-reject with structured deprecation error in R45-5. No silent downgrade. |
| **UI as second plaintext authority** | A UI with write access to key material or token configuration becomes a second path that bypasses the broker architecture. | UI remains read-only until a hardened management API exists. Secure UI input is deferred behind the full five-round core arc and the management API (council/rTBD-structure-upgrade/kickoff.md). |

## Project Structure
```
subumbra/
├── CLAUDE.md                    ← this file
├── docker-compose.yml           ← main orchestration
├── .env.bootstrap.example       ← template for bootstrap env
├── .env.example                 ← full runtime env shape and optional values
├── .gitignore                   ← never commit real keys
├── README.md
├── bootstrap.sh                 ← host wrapper: mounts .env, compose up after full bootstrap, `--upgrade`, shreds bootstrap input
│
├── bootstrap/                   ← one-shot key generation container
│   ├── Dockerfile
│   ├── subumbra-bootstrap.py    ← thin CLI entrypoint / flag dispatch
│   ├── subumbra_core.py         ← shared bootstrap constants and generic helpers
│   ├── subumbra_cf.py           ← Cloudflare deploy / KV / Access / tunnel operations
│   ├── subumbra_session.py      ← session lifecycle + KV gate reconciliation
│   ├── subumbra_adapters.py     ← adapter listing / show / mutation commands
│   ├── subumbra_keys.py         ← bootstrap, provision, rotate, revoke, and status pipeline
│   ├── _hash_utils.py           ← stdlib PBKDF2 helpers for bootstrap-managed UI auth
│   └── requirements.txt
│
├── subumbra-keys/                  ← encrypted blob storage service
│   ├── Dockerfile
│   ├── app.py                   ← Flask REST API, Docker internal only
│   ├── requirements.txt
│   └── endpoint.json                ← V3 envelope records (generated by bootstrap, safe to store)
│
├── worker/                      ← Cloudflare Worker
│   ├── wrangler.toml
│   ├── package.json
│   └── src/
│       └── worker.js            ← decrypts + proxies API calls via Durable Object
│
├── subumbra-proxy/              ← transparent sidecar (primary integration path)
│   ├── Dockerfile
│   ├── app.py                   ← FastAPI; secure transparent /t route; minimal /health
│   └── requirements.txt
│
├── subumbra-probe/              ← optional diagnostic proof container
│   ├── Dockerfile
│   ├── probe.py
│   └── requirements.txt
│
├── scripts/
│   ├── council/                 ← verification harness scripts
│   └── subumbra-expire-adapter.sh  ← operational adapter expiry tool
│
└── ui/                          ← multi-page management console
    ├── Dockerfile
    ├── app.py                   ← Flask console; auth, Gate integration, hardening
    ├── console_data.py          ← mock/merge base dataset, NAV, ORG structures
    ├── _hash_utils.py           ← stdlib PBKDF2 helpers for in-process UI auth
    ├── requirements.txt
    ├── requirements.in
    ├── static/
    │   ├── css/                 ← tokens.css, shell.css, components.css, pages.css
    │   ├── js/                  ← api.js, components.js, pages.js
    │   ├── push.js              ← browser push subscription client
    │   └── sw.js                ← service worker for Gate push notifications
    └── templates/
        ├── base.html            ← app shell: sidebar, topbar, VAPID key injection
        ├── overview.html        ← vault posture + Gate Approvals panel
        ├── sessions.html, vault_api.html, vault_ssh.html
        ├── adapters.html, policies.html, audit.html
        ├── cloudflare.html, observability.html, settings.html, upcoming.html
        └── README.md
```

## Dependency Maintenance Note

- Subumbra prefers exact pinned Python dependencies and pip-compiled hashed lockfiles.
- When security scans flag a transitive dependency, re-check whether the parent package has published an updated tested stack before carrying a long-lived override.
- Periodically review both direct pins and any temporary transitive pins so the repo does not stay indefinitely behind upstream dependency maintenance.

## Key Design Decisions

### Docker Networking
- `internal` network: subumbra-keys, bootstrap, ui, subumbra-probe, subumbra-proxy — NO internet access
- `external` network: cloudflared, subumbra-proxy, subumbra-probe — internet access
- subumbra-keys is reachable from services on the internal network via Docker DNS (`http://subumbra-keys:9090`)
- subumbra-keys never exposed to host or internet

### App-Owned Integration Contract
- The current transparent entry point is `http://subumbra-proxy:8090/t`
- Apps send a consumer token as the credential and carry the requested `key_id`
  in the path after `/t/`
- `subumbra-proxy` fetches the encrypted record, packages canonical `POST /proxy`, and owns the Worker-facing request boundary
- The app never sees the decrypted provider key
- Provider-specific path suffixes in app examples are upstream API path requirements; `/t` is the Subumbra transparent route root

### Legacy Callback Reference
- Callback-era Python (`litellm/custom_callbacks.py`) was removed in R58.
- Standalone LiteLLM examples now live under `docs/apps/litellm/` and use the transparent sidecar path.
- Callback-style `subumbra:<key_id>` values are not the current primary integration contract.

### Bootstrap Process (one-shot)
1. Run bootstrap through the host wrapper:
   `./bootstrap.sh`
   **Automation / CI:** create `.env.bootstrap` with the manifest `secret_ref` variables, then run the same wrapper (non-interactive).
   **Interactive (TTY):** when `.env.bootstrap` is absent or incomplete, bootstrap runs a **manifest wizard**: `manifest.yaml is required, and `bootstrap.sh` mounts the chosen file at `/app/manifest`; Cloudflare credentials and each provider secret are prompted (`getpass` / short prompts). Secrets are held in RAM only (including an in-process `_WIZARD_SECRETS` map keyed by `secret_ref`); they are **not** written to a plaintext bootstrap resume file. Resolution still uses `_resolve_manifest_secret`, which checks that cache before `os.environ`, so provider material is not required in the process environment for the wizard path.
   The manifest remains the source of truth for `policy.target.host`, `policy.auth`, adapters, and `unique_vault`.
2. Bootstrap container:
   - Reads manifest-declared secret refs from env (RAM only)
   - Treats `policy.target.host` and `policy.auth` in the manifest as the routing/auth source of truth
   - Creates or reuses the provider-registry KV namespace and persists its namespace ID in `/app/data/kv-config.json`
   - Injects the `[[kv_namespaces]]` binding into the temporary deploy copy of `wrangler.toml`
   - Deploys CF Worker via wrangler
   - Pushes `SUBUMBRA_CONSUMER_TOKENS`, `SUBUMBRA_HMAC_KEY`, and a transient `SUBUMBRA_SETUP_TOKEN`
   - Uses a staged pipeline (no plaintext checkpoint file): infra once, phase-1 `/setup/keygen` per vault instance, then per-key encryption with `secret_ref` resolved only in that phase, then atomic `endpoint.json` write
   - Calls one-shot `POST /setup/keygen` against `vault` or `vault-<key_id>` so Cloudflare generates and stores the RSA-4096 key pair in the targeted vault DO
   - Receives `public_key.pem` / `public_key_<key_id>.pem` plus `pub_key_fp` from Cloudflare
   - For each key: generates random 32-byte DEK, wraps DEK with the returned RSA public key, encrypts API key with AES-256-GCM using AAD `subumbra:v3:<key_id>:<policy_hash>`
   - Writes V3 records (ciphertext + wrapped_dek + pub_key_fp + enc_version + policy_hash + vault_instance) to subumbra-keys volume
   - Writes `public_key.pem` for shared vault and `public_key_<key_id>.pem` for unique vaults
   - Publishes structured KV entries (`policy:<id>`, `key:<id>`, `registry_version`) to Cloudflare KV via `wrangler kv bulk put`
   - Supports targeted repair via `./bootstrap.sh --provision <key_id>` when a key-specific provisioning step fails
   - Deletes transient `SUBUMBRA_SETUP_TOKEN` and legacy `MASTER_DECRYPTION_KEY` / `WORKER_PRIVATE_KEY` / `WORKER_KEY_FINGERPRINT` secrets after successful completion
   - Exits
3. `bootstrap.sh` writes runtime env values into repo-local `.env`, shreds `.env.bootstrap` after success, runs `docker compose up -d --force-recreate`, and prints an adapter / key_id summary. For image-only updates without re-bootstrap, use `./bootstrap.sh --upgrade`.
   Bootstrap also writes resolved optional-service state (`DEPLOY_UI`, `DEPLOY_SSH`) plus UI auth state (`UI_USERNAME`, `UI_PASSWORD_HASH`, `CF_ACCESS_PROTECTED`) into `.env`; `--upgrade` re-applies Compose profiles from those persisted values and `--update-ui-auth` rotates UI auth without a full re-bootstrap.
4. Real keys existed only in RAM, never written to disk

### Per-Key Rotation (offline)
- Run: `./bootstrap.sh --rotate`
- Uses `public_key.pem` for shared keys or `public_key_<key_id>.pem` for unique keys — no CF interaction needed
- Generates new DEK, re-wraps with existing public key, re-encrypts single key
- Atomically updates only the target record in `endpoint.json`

### Cloudflare Worker
- Receives: canonical `/proxy` JSON-body requests from LiteLLM and future adapters
- Reads provider security metadata from the live Cloudflare KV provider registry
- Validates `target_url` hostname against the live registry entry (fail-closed)
- Validates `provider` matches the resolved registry entry
- Routes `/setup/keygen` and `/internal/rotate` to a named SQLite-backed `SubumbraVault` DO instance selected by `vault_instance`
- Verifies: pub_key_fp matches the stored vault custody row
- Unwraps: per-record DEK via RSA-OAEP using the non-extractable private key cached in the selected vault DO
- Decrypts: AES-256-GCM with AAD `subumbra:v3:<key_id>:<policy_hash>`
- Hard-rejects: non-V3 records, fingerprint mismatches
- Resolves auth policy from the registry and passes generic auth config into the vault DO
- Uses: one named shared vault DO plus optional per-key vault DO instances
- DO calls: API provider directly with decrypted key
- Durable Object no longer branches on provider identity to choose auth headers
- Worker accepts canonical `/proxy` requests only
- Returns: streaming response back to the adapter
- Strips: all X-Subumbra-* headers before upstream calls

### Adapter Contract

The canonical Subumbra core API is `POST /proxy` — see
[`docs/adapter-contract.md`](docs/adapter-contract.md)
for the full normative contract.

The current primary integration contract is the explicit transparent sidecar
(`subumbra-proxy`) using the `/t` route. Callback-era LiteLLM artifacts remain
as legacy reference only and are not the current adapter hierarchy.

### subumbra-keys Service
- Minimal Flask API
- Binds to Docker internal network only
- Validates: X-Subumbra-Token header
- Returns: V3 record metadata including `provider`, `target_host`, `ciphertext`, `wrapped_dek`, `pub_key_fp`, `enc_version`, `policy_hash`, and `vault_instance`
- Dashboard list/read path now also exposes read-only policy metadata (`policy_id`, `policy_hash`, auth metadata, target/base-path, capability class, and allowlist adapter/method/path data) without returning ciphertext, wrapped DEKs, fingerprints, or raw policy blobs
- Logs: every access attempt with timestamp

## Provider Declarations
- Subumbra no longer ships a hardcoded provider-routing catalog as runtime/bootstrap authority.
- Operators declare provider labels, `policy.target.host`, and `policy.auth` explicitly in `manifest.yaml` (or JSON compatibility form).
- The Worker remains generic: it validates `target.host` against embedded policy/key authority and executes auth by generic `bearer`, `basic`, `header`, or `query` policy semantics.

## Environment Variables

### Bootstrap (.env.bootstrap — DELETE AFTER USE)
```
ANTHROPIC_KEY=<your_anthropic_key>
OPENAI_KEY=<your_openai_key>
GROQ_KEY=<your_groq_key>
DEEPSEEK_KEY=<your_deepseek_key>
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=subumbra-proxy
UI_USERNAME=<optional bootstrap-managed UI username>
UI_PASSWORD=<optional bootstrap-managed UI password; hashed before writing .env>
UI_AUTH_MODE=<basic|cf_access|both>
```

### Runtime docker-compose (non-sensitive)
```
SUBUMBRA_CONSUMER_REGISTRY=<generated by bootstrap>
SUBUMBRA_TOKEN_PROXY=<generated by bootstrap>
SUBUMBRA_TOKEN_UI=<generated by bootstrap>
SUBUMBRA_TOKEN_PROBE=<generated by bootstrap>
SUBUMBRA_HMAC_KEY=<generated by bootstrap>
CF_WORKER_URL=https://subumbra-proxy.your-subdomain.workers.dev
# Optional: CF_WORKER_NAME=<same script name> — used by day-2 ./bootstrap.sh commands when CF token is not in .env; if unset, inferred from CF_WORKER_URL when host is *.workers.dev
PROXY_ALLOWED_KEYS=<intentionally empty after proxy lockdown>
PROBE_ALLOWED_KEYS=<generated by bootstrap.sh>
UI_ALLOWED_KEYS=<generated by bootstrap.sh>
UI_USERNAME=<bootstrap-managed when services.ui.deploy=true>
UI_PASSWORD_HASH=pbkdf2-sha256:<salt_hex>:<hash_hex>
CF_ACCESS_PROTECTED=<true|false>
DEPLOY_UI=<true|false>
DEPLOY_SSH=<true|false>
```

Optional:
```
CF_ACCESS_CLIENT_ID=<from CF Access dashboard>
CF_ACCESS_CLIENT_SECRET=<from CF Access dashboard>
```

## UI Console Features (r90+)
- Multi-page console: Overview, Sessions, Vault (API/SSH), Consumers, Policies, Audit, Cloudflare, Observability, Settings
- Auth: PBKDF2-SHA256 (`UI_PASSWORD_HASH` + `_hash_utils.verify_ui_password()`); fail-closed startup (`sys.exit(1)`) when neither `UI_PASSWORD_HASH` nor `CF_ACCESS_PROTECTED=true` is configured
- Gate Approvals panel on Overview: subscription count, pending count, pending request table; degrades gracefully when Worker is unreachable
- VAPID public key injected via `data-gate-vapid-public-key` on `<body>` for service-worker push subscription
- `GET /sw.js` route serves service-worker asset with `Cache-Control: no-store`
- Live data wiring: SSH keys partitioned from API keys (`data["ssh_keys"]` vs `data["keys"]`); Gate state from Worker; Cloudflare env (`CF_WORKER_URL`, `CF_WORKER_NAME`) in `data["cloudflare"]`
- Security hardening: `Cross-Origin-Opener-Policy: same-origin`, `Permissions-Policy: clipboard-read=()`, `_require_json` guard (415) on all write routes, per-IP sliding-window rate limit on `GET /api/key-session` (10 req/60s)
- Read-only visibility into V3 policy metadata including `policy_id`, `policy_hash`, capability class, auth scheme, and per-key allowlist relationships
- Heartbeat-only `/api/events` plus 30-second `/api/status` polling fallback for console freshness
- Vault drawer sub-tabs render separate content panes: API key drawers have 5 panes (Overview, Policy, Allow, Velocity, Audit); SSH key drawers have 4 panes (Overview, Hosts, Quota, Audit); tab clicks swap pane visibility via `.drawer__pane`/`.is-hidden` CSS
- `?select=<id>` query parameter accepted by `/vault`, `/vault/ssh`, `/policies`, `/consumers` routes to pre-select a specific key, policy, or adapter on load
- Adapter proxy URL snippets show dual-topology entries: Docker-internal (`http://subumbra-proxy:8090/t/<key_id>`) and host-local (`http://127.0.0.1:10199/t/<key_id>`) — CF Worker URL is not used for transparent `/t/` routing
- Cross-page navigation links: audit log key/adapter columns, overview activity stream, and vault Policy/Allow panes link to focused `?select=` views

## Build Order
1. docker-compose.yml skeleton
2. subumbra-keys/app.py (simplest component)
3. bootstrap/subumbra-bootstrap.py + bootstrap/subumbra_*.py (bootstrap CLI + helper domains)
4. worker/src/worker.js (CF Worker with Durable Object)
5. subumbra-proxy/app.py (transparent sidecar)
6. ui/app.py (dashboard)
7. standalone app examples and docs
8. Test end-to-end

### Error / Logging Check
For any new or changed flow, briefly state:
1. What new failure modes this round introduces
2. Which of those need an operator-visible signal
3. What should remain terse or silent to external callers
4. What must never be logged
5. What observability work is explicitly deferred

## Testing
- Unit: encrypt → store → fetch → decrypt round trip
- Integration: full flow with test API key
- CF Worker: wrangler dev for local testing
- Docker: `./bootstrap.sh` (ends with stack recreate + adapter summary) or `./bootstrap.sh --upgrade` after pulling code

## Notes
- Python 3.12+ for all Python components
- Node 22+ in the **bootstrap** image for wrangler deploy (self-contained; host Node not required)
- wrangler v4+ pinned in bootstrap Dockerfile and worker/package.json
- All Python deps pinned in requirements.txt
- No real keys in git ever — .gitignore covers this
- https://semver.org/ for semantic versioning. Rounds must decide if they are a major, minor, or patch release and increment the appropriate number.
