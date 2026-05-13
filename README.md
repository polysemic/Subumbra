# Subumbra — Split-Trust Secret Mediation

Current version label: `0.0.1-alpha`

Subumbra keeps provider API keys split between **encrypted records** in
`subumbra-keys` and **decrypt authority** in a Cloudflare Worker + Durable
Object vault. The supported integration model is **app-owned installs**: core
stack under `/opt/subumbra`, apps in their own installs, apps call
`subumbra-proxy` on the transparent **`/t/<key_id>/...`** path. Routing and auth
live in operator-authored **`subumbra.json`**.

**Architecture (diagram + stack list):** [docs/architecture.md](docs/architecture.md)

**Planned and possible work:** [ROADMAP.md](ROADMAP.md) (living backlog; order shifts with feedback).

## Five-minute quickstart

1. **Clone** into `/opt/subumbra` (or your chosen path). See
   [docs/subumbra-install.md](docs/subumbra-install.md) for Docker install on Ubuntu.
2. **Manifest (required):** `subumbra.json` is **gitignored** — it must exist on
   disk before bootstrap. Copy a template, then edit:
   ```bash
   cp subumbra.minimal.json subumbra.json
   # or: cp subumbra.example.json subumbra.json
   ```
   The **minimal** template is a **single** OpenAI row using only a signed
   **`template`** (no inline `policy`). It is the smallest manifest bootstrap
   accepts; swap the template name or add more objects under `keys` when you
   need more providers. The **example** file lists **every** signed catalog
   template plus one inline policy row showing optional `deny`, `intent`,
   `response`, and `velocity` fields. Use minimal to get running fast; use the
   example when you want the full variable surface.
3. **Runtime env:** `cp .env.example .env` — leave optional CF Access / tunnel
   vars blank unless you use them.
4. **Bootstrap:** `./bootstrap.sh` (interactive TTY) or automation with
   `.env.bootstrap` from `.env.bootstrap.example` (see install guide).
5. **Stack:** after bootstrap, `docker compose up -d --force-recreate` (the
   wrapper usually runs this). **Health:** `curl -sS http://127.0.0.1:10199/health`
   on the host; apps **inside Docker** use `http://subumbra-proxy:8090/t/<key_id>/...`.

## Supported app contract

- `api_base: http://subumbra-proxy:8090/t/<key_id>/...` (or `http://127.0.0.1:10199/t/...` from the host)
- `api_key: <adapter token>` such as `${SUBUMBRA_TOKEN_LITELLM}`

Do **not** use callback-era `subumbra:<key_id>` values or raw key IDs in the
supported auth slot.

## Alpha notes

- Env ingestion supports multi-app deduplication; richer same-provider
  multi-secret import is deferred.
- There is **no** built-in provider catalog: `provider` is a label;
  `policy.target.host` and `policy.auth` are source of truth.

## Key properties

- Provider keys do not remain on the VPS in plaintext after bootstrap.
- `subumbra-keys` holds ciphertext + wrapped DEKs + metadata only.
- Cloudflare holds RSA private key custody in the vault DO; runtime secrets live in Worker config.
- Plaintext exists only transiently in Worker/DO execution and in transit to providers over HTTPS.
- Cloudflare deploy authority remains in the trust boundary; the split removes plaintext-at-rest on the VPS, not Cloudflare from the model.
- Proxy `/health` includes `worker_auth` (`ok` | `stale` | `unreachable`); for full semantics see [docs/operator-guide.md](docs/operator-guide.md) (“Proxy `/health` — `worker_auth` semantics”).

## UI authentication

| Mode | When | Configuration |
|------|------|-----------------|
| CF Tunnel + CF Access (recommended) | UI behind Tunnel + Access | Leave `UI_USERNAME` / `UI_PASSWORD` unset |
| HTTP Basic Auth | Direct UI on localhost | Set both `UI_USERNAME` and `UI_PASSWORD` in `.env` |

## Tested applications (docs)

- AnythingLLM: [install](docs/apps/anythingllm/install.md) · [takeover](docs/apps/anythingllm/takeover.md)
- OpenWebUI: [install](docs/apps/openwebui/install.md) · [takeover](docs/apps/openwebui/takeover.md)
- LiteLLM: [install](docs/apps/litellm/install.md) (standalone; see `litellm/README.md`)
- n8n: [workflow assets](docs/apps/n8n/README.md)
- LibreChat / Bifrost: see `docs/apps/*`

## Project layout

```
subumbra/
├── docker-compose.yml
├── .env.example
├── .env.bootstrap.example
├── subumbra.minimal.json
├── subumbra.example.json   # gold exemplar (copy to gitignored subumbra.json)
├── bootstrap/
├── subumbra-keys/
├── subumbra-proxy/
├── subumbra-probe/
├── ui/
├── worker/
├── litellm/                # standalone LiteLLM example only
└── docs/
```

## Next docs

- [docs/subumbra-install.md](docs/subumbra-install.md)
- [docs/subumbra-testing.md](docs/subumbra-testing.md)
- [docs/integration-recipes.md](docs/integration-recipes.md)
- [docs/adapter-contract.md](docs/adapter-contract.md)
- [docs/operator-guide.md](docs/operator-guide.md)
- [docs/subumbra-developer.md](docs/subumbra-developer.md)
