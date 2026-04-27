# Subumbra — Split-Trust Secret Mediation

Current version label: `0.0.1-alpha`

Subumbra is the core secret-mediation layer. It keeps provider API keys split
across:

- encrypted records in `subumbra-keys`
- decrypt authority in Cloudflare Worker secrets

The supported integration model is now **app-owned installs**:

- Subumbra core runs in `/opt/subumbra`
- LiteLLM or another app runs in its own install
- the app points at `subumbra-proxy`

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

- `api_base: http://subumbra-proxy:8090/t`
- `api_key: <key_id>` using a plain key ID

Do **not** use callback-era `subumbra:<key_id>` values on the supported path.

## Alpha Notes

- `DOUBLE-FETCH` remains an open known limitation and is not fixed in the
  43-6-1 alpha-polish round.
- Env ingestion in 43-6-1 supports multi-app deduplication under the current
  bootstrap contract, but richer same-provider multi-secret import support is
  deferred to a future round.

## Quick Start

### 1. Install the core stack

See:

- [docs/subumbra-install.md](docs/subumbra-install.md)

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

- Real provider keys never live in plaintext on your VPS after bootstrap.
- `subumbra-keys` stores ciphertext, wrapped DEKs, and metadata only.
- Cloudflare stores the RSA private key and runtime auth material only.
- Neither side alone can reconstruct provider keys.
- Proxy health now reports Worker-auth state via `worker_auth: ok|stale|unreachable`.

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
