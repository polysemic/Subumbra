# Subumbra — Split-Trust Secret Mediation

Current version label: `0.0.1-alpha`

Subumbra is the core secret-mediation layer. It keeps provider API keys split
across:

- encrypted records in `subumbra-keys`
- decrypt authority in a Cloudflare Worker SQLite-backed Durable Object vault

The supported integration model is now **app-owned installs**:

- Subumbra core runs in `/opt/subumbra`
- LiteLLM or another app runs in its own install
- the app points at `subumbra-proxy`
- bootstrap routing/auth authority lives in operator-authored `subumbra.json`

## Core Runtime Shape

```
App-owned integration (LiteLLM, LibreChat, n8n, etc.)
      ↓
subumbra-proxy  (/t)
      ↓
subumbra-keys   → encrypted record fetch only
      ↓
Cloudflare Worker + Durable Object
      ↓
provider API
```

The current universal `/t` path is a **shared `subumbra-proxy` identity** at
the Worker boundary. Per-app Worker identities are future work.

## Core Stack

The default `/opt/subumbra` compose stack contains:

- `subumbra-keys`
- `subumbra-proxy`
- `subumbra-ui`
- `bootstrap` profile
- `subumbra-probe` profile
- optional `cloudflared` profile

Bundled LiteLLM is no longer part of the core stack.

## Supported App Contract

The current transparent contract is:

- `api_base: http://subumbra-proxy:8090/t/<key_id>/...`
- `api_key: <adapter token>` such as `${SUBUMBRA_TOKEN_LITELLM}`

Do **not** use callback-era `subumbra:<key_id>` values or raw key IDs in the
supported auth slot.

## Alpha Notes

- Env ingestion in 43-6-1 supports multi-app deduplication under the current
  bootstrap contract, but richer same-provider multi-secret import support is
  deferred to a future round.
- Built-in provider catalog authority is removed in the current manifest-era
  flow. `provider` is an operator label; `policy.target.host` and `policy.auth`
  are the routing/auth source of truth.

## Quick Start

### 1. Install the core stack

See:

- [docs/subumbra-install.md](docs/subumbra-install.md)

First-time bootstrap may run **interactively** (TTY, `subumbra.json` mounted, no `.env.bootstrap`) or **headlessly** with `.env.bootstrap`; see [CLAUDE.md](CLAUDE.md) § Bootstrap Process.

### 2. Configure a standalone LiteLLM example

See:

- [docs/apps/litellm/install.md](docs/apps/litellm/install.md)

### 3. Test the deployment

See:

- [docs/subumbra-testing.md](docs/subumbra-testing.md)

## Project Layout

```
subumbra/
├── docker-compose.yml
├── .env.example
├── bootstrap/
├── subumbra-keys/
├── subumbra-proxy/
├── subumbra-probe/
├── ui/
├── worker/
├── litellm/                ← example LiteLLM config artifacts for standalone installs
└── docs/
```

## Key Properties

- Real provider keys do not remain in plaintext on your VPS after bootstrap.
- `subumbra-keys` stores ciphertext, wrapped DEKs, and metadata only.
- Cloudflare generates and stores the RSA private key inside the vault DO, and
  keeps runtime auth material in Worker secrets.
- Provider plaintext exists transiently inside Cloudflare Worker / Durable
  Object execution while a live upstream request is being handled.
- Cloudflare deploy authority remains part of the trust boundary; the split
  design removes plaintext-at-rest on the VPS, not Cloudflare from the model.
- Proxy health now reports Worker-auth state via `worker_auth: ok|stale|unreachable`.
  - **`ok`:** the proxy recently verified the Worker with a successful auth ping within its TTL.
  - **`stale`:** the Worker is still reachable but the cached auth ping expired — often transient after restarts; not the same as “Cloudflare is down”.
  - **`unreachable`:** the proxy cannot reach the Worker health/auth path at all.
  - **CRITICAL-3 (operator model):** CF Access (and related) header stripping is enforced at the **Cloudflare Worker edge**; misconfiguration there can surface as `worker_auth` / proxy errors even when the VPS stack is healthy.

## UI Authentication

The Subumbra UI supports two authentication modes:

| Mode | When to use | Configuration |
|------|-------------|---------------|
| `CF Tunnel + CF Access` (recommended) | You route the UI through a Cloudflare Tunnel | Leave `UI_USERNAME` and `UI_PASSWORD` unset. CF Access enforces authentication at the edge. |
| `HTTP Basic Auth` | You access the UI directly without a CF Tunnel | Set both `UI_USERNAME` and `UI_PASSWORD` in `.env`. |

## Next Docs

- [docs/subumbra-install.md](docs/subumbra-install.md)
- [docs/apps/litellm/install.md](docs/apps/litellm/install.md)
- [docs/subumbra-testing.md](docs/subumbra-testing.md)
- [docs/adapter-contract.md](docs/adapter-contract.md)
- [docs/operator-guide.md](docs/operator-guide.md)
- [docs/subumbra-developer.md](docs/subumbra-developer.md)

## App Integrations

- AnythingLLM: [install](docs/apps/anythingllm/install.md) | [takeover](docs/apps/anythingllm/takeover.md)
- OpenWebUI: [install](docs/apps/openwebui/install.md) | [takeover](docs/apps/openwebui/takeover.md)
- LiteLLM: [install](docs/apps/litellm/install.md)
- n8n: [workflow assets](docs/apps/n8n/README.md)
