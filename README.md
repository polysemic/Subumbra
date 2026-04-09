# Subumbra — Split-Trust Secret Mediation

A Docker-based security layer that ensures API keys (Anthropic, OpenAI, Groq, DeepSeek)
**never exist in plaintext on any system you operate**. Keys are encrypted with per-record
envelope encryption — neither the host nor Cloudflare alone can decrypt.

---

## How It Works

```
Your App / OpenClaw
      ↓  OpenAI-compatible API call
LiteLLM  (sees only "forge:anthropic_prod" as api_key)
      ↓  custom callback fetches V2 envelope from forge-keys
forge-keys  (Docker internal network only)
      ↓  returns wrapped DEK + ciphertext + fingerprint
      ↓  (useless without RSA private key in CF Secrets)
Cloudflare Worker
      ↓  verifies key fingerprint
      ↓  RSA-OAEP unwraps per-record DEK
      ↓  AES-256-GCM decrypts API key (AAD-bound to key_id)
Durable Object  (fresh isolate per request)
      ↓  holds decrypted key ~100 ms
      ↓  makes direct API call
API Provider (Anthropic / OpenAI / Groq / DeepSeek)
      ↓  response streams back through CF Worker → LiteLLM → your app
```

**Security properties:**
- Real API keys never exist in plaintext on any system you operate
- `forge-keys` container: holds wrapped DEK + AES-GCM ciphertext only (useless alone)
- CF Secrets: holds RSA-4096 private key + fingerprint only (useless alone)
- Neither side alone can reconstruct a key
- AAD binding (`keyvault:v2:<key_id>`) prevents ciphertext transplant between records
- Decrypted key exists only in a CF Durable Object isolate for ~100 ms, then transits
  to the API provider over HTTPS
- One-shot bootstrap: keys entered in the interactive wizard pass through RAM only and are never written to disk by bootstrap (CI/headless path securely shreds its env file upon completion)
- Offline per-key rotation: no Cloudflare interaction, no service restart

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Docker + Compose | v2.20+ | `docker compose` (not `docker-compose`) |
| Cloudflare account | — | **Workers Paid Plan required** ($5/mo) — Durable Objects are not available on the free tier |
| CF API token | — | Permissions: Workers Scripts:Edit |

> **Cloudflare billing:** This project uses Durable Objects for per-request key isolation.
> Durable Objects require the [Workers Paid Plan](https://developers.cloudflare.com/workers/platform/pricing/).
> A free account will fail at the wrangler deploy step with a cryptic API error.

You do **not** need Node.js or wrangler locally — the bootstrap container includes them.

---

## Project Layout

```
subumbra/
├── docker-compose.yml           ← orchestration
├── .env.bootstrap.example       ← template (copy → .env.bootstrap, then shred)
├── .gitignore
│
├── bootstrap/                   ← one-shot key generation + CF Worker deploy
│   ├── Dockerfile
│   ├── keyvault-bootstrap.py
│   └── requirements.txt
│
├── forge-keys/                  ← encrypted blob store (internal network only)
│   ├── Dockerfile
│   ├── app.py
│   └── requirements.txt
│
├── worker/                      ← Cloudflare Worker + Durable Object
│   ├── wrangler.toml
│   ├── package.json
│   └── src/worker.js
│
├── litellm/                     ← LiteLLM proxy config + callback
│   ├── config.yaml
│   └── custom_callbacks.py
│
└── ui/                          ← management dashboard
    ├── Dockerfile
    ├── app.py
    ├── requirements.txt
    └── templates/index.html
```

---

## Setup — Step by Step

### Step 1 — Create your Cloudflare API token

1. Go to [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens)
2. Click **Create Token** → **Edit Cloudflare Workers** template
3. Required permissions:
   - `Account > Workers Scripts > Edit`
   - `Account > Workers KV Storage > Edit` *(for Durable Objects migrations)*
4. Copy the token — you'll need it in Step 2

Find your **Account ID** at the top-right of any Cloudflare dashboard page.

---

### Step 2 — Run Bootstrap

Bootstrap supports two modes. Use the **interactive wizard** for normal first-time setup.

#### Interactive mode (recommended — no files needed)

```bash
docker compose --profile bootstrap run --rm -it bootstrap
```

The wizard will prompt you for:
1. Your Cloudflare API Token (hidden input)
2. Your Cloudflare Account ID
3. CF Worker name (default: `keyvault-proxy`)
4. Each provider API key, with a `key_id` label of your choice (e.g. `anthropic_prod`)

All values exist in RAM only for the duration of the session. Nothing is written to disk until after you confirm on the summary screen.

#### Automation / CI mode (optional)

For headless environments, create `.env.bootstrap` first:

```bash
cp .env.bootstrap.example .env.bootstrap
# edit .env.bootstrap with your real values, then:
docker compose --profile bootstrap run --rm bootstrap
```

The bootstrap container detects populated environment variables and skips the wizard automatically.

---

#### What bootstrap does (both modes)

1. Collects credentials (wizard or env)
2. Confirms with the operator (interactive mode only)
3. Generates an RSA-4096 key pair (RAM only)
4. For each API key: generates a random 32-byte DEK, wraps DEK with the RSA public key,
   encrypts the API key with AES-256-GCM using AAD `keyvault:v2:<key_id>`
5. Writes `public_key.pem` to the data volume (for offline rotation)
6. Deploys the CF Worker via wrangler
7. Pushes `WORKER_PRIVATE_KEY`, `WORKER_KEY_FINGERPRINT`, `FORGE_ACCESS_TOKEN`,
   and `FORGE_HMAC_KEY` to CF Secrets
8. Deletes legacy `MASTER_DECRYPTION_KEY` from CF Secrets (V1 cleanup, best-effort)
9. Writes V2 records and runtime secrets to the shared volume (mode 0600)
10. Zeros sensitive memory and exits

Token values are **not** printed to stdout (to avoid CI/CD log capture). They are
written to `runtime.env` on the shared Docker volume.

---

### Step 3 — Run `post-bootstrap.sh`

```bash
./post-bootstrap.sh
```

This script:
1. Reads `FORGE_ACCESS_TOKEN`, `FORGE_HMAC_KEY`, and `CF_WORKER_URL` from the Docker volume
2. Writes them into your `.env` file
3. Verifies all three values landed correctly
4. Shreds `.env.bootstrap` if it exists (automation path only — wizard path has nothing to shred)

Your real API keys are now gone from your machine. The encrypted records in the shared volume are useless without the RSA private key, which only lives in CF Secrets.

---

### Step 4 — Start the Services

```bash
docker compose up -d --force-recreate
```

> **Note:** `--force-recreate` ensures all containers reload the new runtime tokens
> from `.env`. Skipping this after bootstrap leaves services with a stale token
> that the CF Worker will reject.

Check everything is healthy:

```bash
docker compose ps
```

Expected output:
```
NAME             STATUS          PORTS
forge-keys       Up (healthy)
litellm          Up              0.0.0.0:4000->4000/tcp
keyvault-ui      Up              127.0.0.1:8080->8080/tcp
```

> **Note:** `forge-keys` is intentionally not published to any host port.
> It is only reachable from within the Docker internal network.

---

### Step 6 — Verify End-to-End

**Check forge-keys health** (from inside the litellm container — forge-keys has no host port):
```bash
docker exec litellm curl -s http://forge-keys:9090/health
# → {"status":"ok","keys_loaded":4,"timestamp":"..."}
```

**Check CF Worker health:**
```bash
curl https://keyvault-proxy.your-subdomain.workers.dev/health
# → {"status":"ok","timestamp":"..."}
```

**Check LiteLLM:**
```bash
curl http://localhost:4000/health
# → {"status":"healthy",...}
```

**Send a test completion:**
```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4",
    "messages": [{"role": "user", "content": "say hi in 3 words"}],
    "max_tokens": 20
  }'
```

**Open the dashboard:**
```
http://localhost:8080
```

You should see all keys listed with request counts incrementing as you make calls.

---

## Network Architecture

```
Host machine
├── port 4000  →  litellm (OpenAI-compatible API)
└── port 8080  →  keyvault-ui (dashboard, localhost only)

Docker network: external  (has internet access)
├── litellm
└── cloudflared (optional, --profile tunnel)

Docker network: internal  (NO internet, isolated)
├── forge-keys   ← only reachable via Docker DNS from litellm/ui
├── litellm      ← also on external (needs both)
└── keyvault-ui
```

`forge-keys` has zero host port exposure. The only way to reach it is from another container on the `internal` network using the DNS name `forge-keys`.

---

## Adding / Changing Models

Edit [litellm/config.yaml](litellm/config.yaml) to add models. The only required change is the `model:` line — `api_key` always uses the `forge:` prefix pointing to the correct key ID:

```yaml
- model_name: my-new-model
  litellm_params:
    model: anthropic/claude-3-5-haiku-20241022
    api_key: "forge:anthropic_prod"
```

Restart LiteLLM to pick up the change:
```bash
docker compose restart litellm
```

### Custom Provider Path Prefixes

The callback dynamically resolves each provider's API path prefix using LiteLLM's
internal registry. If a provider isn't auto-detected (or you need to override the
default), set `KEYVAULT_PROVIDER_PREFIXES` in your `.env`:

```bash
KEYVAULT_PROVIDER_PREFIXES={"my_provider":"/api/v2"}
```

This is a JSON map of provider name to path prefix. The prefix is appended to the
CF Worker adapter base URL before the SDK adds its own endpoint path.

> **Important:** Setting a path prefix alone does NOT enable a new provider.
> You must also add the provider to `worker/src/providers.json` (the shared
> built-in provider registry used by bootstrap and the CF Worker), and add the
> relevant LiteLLM model configuration.

---

## Key Rotation

Subumbra supports two rotation modes:

### Single-key rotation (zero-downtime — no service restart)

To rotate one API key (e.g. after a suspected leak) without touching the RSA key pair
or runtime tokens:

1. Get the new key value from the provider dashboard
2. Run:
   ```bash
   docker compose --profile bootstrap run --rm -it bootstrap --rotate
   ```
   The wizard prompts for the key_id to rotate and the new API key value. It re-encrypts
   only that record using the existing `public_key.pem` on disk — no Cloudflare interaction.

No runtime tokens change. No service restart required because `forge-keys` automatically serves the new record on the very next request. The RSA key pair is unchanged. For the full recovery playbook, see `docs/operator-guide.md`.

### Full re-bootstrap (new key pair + new tokens — requires full restart)

To rotate the RSA key pair itself, or to add/remove providers:

1. Re-run bootstrap interactively — enter **all** keys you want to keep active:
   ```bash
   docker compose --profile bootstrap run --rm -it bootstrap
   ```
   The wizard will detect the existing `keys.json` (rotation mode) and warn you
   about any keys that will be permanently deleted because they were not re-entered.
2. Copy new runtime tokens into `.env`:
   ```bash
   ./post-bootstrap.sh
   ```
3. Restart ALL services (tokens have changed):
   ```bash
   docker compose up -d --force-recreate
   ```

> **CI/automation rotation:** Populate `.env.bootstrap` with all keys to retain, then run
> `docker compose --profile bootstrap run --rm bootstrap` followed by `./post-bootstrap.sh`.

---

## Troubleshooting

### `forge-keys` healthcheck failing

```bash
docker compose logs forge-keys
```

Common causes:
- Missing `FORGE_ACCESS_TOKEN` or `FORGE_HMAC_KEY` in `.env`
- `keys.json` not yet written (bootstrap hasn't run)

### LiteLLM returning 500 on forge: keys

```bash
docker compose logs litellm
```

Look for lines from `forge-callback`. Common causes:
- `CF_WORKER_URL` not set in `.env`
- CF Worker not deployed (run bootstrap)
- `forge-keys` container unhealthy

### Wrangler authentication on headless servers

On a server without a browser (e.g. Citadel, CI/CD, Docker), `wrangler` cannot
complete its OAuth flow and will hang or fail with an authentication error.

The fix is to set `CLOUDFLARE_API_TOKEN` in your environment — wrangler uses it
directly and skips the browser login entirely:

```bash
export CLOUDFLARE_API_TOKEN=your-cf-api-token
```

Apply this before any wrangler command run outside Docker:

```bash
# Tail live Worker logs from a headless server
export CLOUDFLARE_API_TOKEN=your-cf-api-token
cd worker && npx wrangler tail --name keyvault-proxy

# Manual Worker deploy from a headless server
export CLOUDFLARE_API_TOKEN=your-cf-api-token
cd worker && npx wrangler deploy
```

The bootstrap container sets this automatically from `CF_API_TOKEN` in
`.env.bootstrap`, so you only need this for commands run outside Docker.

---

### CF Worker decryption failures

```bash
# Check worker logs in CF dashboard, or via wrangler:
cd worker && npx wrangler tail --name keyvault-proxy
```

Common causes:
- `WORKER_PRIVATE_KEY` secret not set in CF (run bootstrap)
- Key fingerprint mismatch (re-bootstrap required)
- Ciphertext or wrapped DEK corrupted or truncated

### "Worker not configured" from CF Worker

Bootstrap hasn't pushed secrets yet. Run:
```bash
docker compose --profile bootstrap run --rm -it bootstrap
```

### Dashboard shows "forge-keys unreachable"

The UI container can't reach forge-keys. Check:
```bash
docker compose ps          # is forge-keys running?
docker compose logs ui     # any connection errors?
```

---

## Optional: Cloudflare Tunnel

To expose LiteLLM via a Cloudflare Tunnel (instead of a direct open port):

1. Create a tunnel in the CF dashboard and copy the tunnel token
2. Add to `.env`:
   ```
   TUNNEL_TOKEN=eyJ...
   ```
3. Start with the tunnel profile:
   ```bash
   docker compose --profile tunnel up -d
   ```

---

## Security Notes

- **`.env.bootstrap` contains real API keys — shred it immediately after bootstrap**
- **Windows users:** `shred` is not available — use `Remove-Item .env.bootstrap -Force`
  or Sysinternals [`sdelete`](https://learn.microsoft.com/en-us/sysinternals/downloads/sdelete)
- `forge-keys` is never accessible from the host or internet — Docker enforces this with `internal: true` on the network
- The CF Worker rejects requests with the wrong `FORGE_ACCESS_TOKEN` before any decryption is attempted
- The Worker validates `target_url` against an allowlist to prevent SSRF
- All token comparisons are constant-time to prevent timing oracles
- The Durable Object uses `newUniqueId()` — no state persists between requests
- The dashboard (`localhost:8080`) shows key IDs and request counts only — never ciphertext or key values
