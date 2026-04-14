# Subumbra — Split-Trust Secret Mediation

A Docker-based security layer that ensures API keys for 10+ built-in AI providers
and non-AI API services **never exist in plaintext on any system you operate**.
Keys are encrypted with per-record envelope encryption — neither the host nor
Cloudflare alone can decrypt.

---

## How It Works

```
Your App / OpenClaw
      ↓  OpenAI-compatible API call
LiteLLM  (sees only "subumbra:anthropic_prod" as api_key)
      ↓  custom callback fetches V2 envelope from subumbra-keys
subumbra-keys  (Docker internal network only)
      ↓  returns wrapped DEK + ciphertext + fingerprint
      ↓  (useless without RSA private key in CF Secrets)
Cloudflare Worker
      ↓  verifies key fingerprint
      ↓  RSA-OAEP unwraps per-record DEK
      ↓  AES-256-GCM decrypts API key (AAD-bound to key_id)
Durable Object  (fresh isolate per request)
      ↓  holds decrypted key ~100 ms
      ↓  makes direct API call
API Provider (10+ AI providers / non-AI API services)
      ↓  response streams back through CF Worker → LiteLLM → your app
```

**Security properties:**
- Real API keys never exist in plaintext on any system you operate
- `subumbra-keys` container: holds wrapped DEK + AES-GCM ciphertext only (useless alone)
- CF Secrets: holds RSA-4096 private key + fingerprint only (useless alone)
- Neither side alone can reconstruct a key
- AAD binding (`subumbra:v2:<key_id>`) prevents ciphertext transplant between records
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
│   ├── subumbra-bootstrap.py
│   └── requirements.txt
│
├── subumbra-keys/                  ← encrypted blob store (internal network only)
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
├── subumbra-proxy/              ← universal sidecar for non-LiteLLM integrations
│   ├── Dockerfile
│   ├── app.py
│   └── requirements.txt
│
├── subumbra-probe/               ← second-adapter proof container (`--profile probe` only)
│   ├── Dockerfile
│   └── probe.py
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
3. CF Worker name (default: `subumbra-proxy`)
4. Each provider API key, with a `key_id` label of your choice (default suggestions like `anthropic_prod`)
5. Adapter key scopes for `litellm`, `subumbra-proxy`, and `subumbra-probe`

All values exist in RAM only for the duration of the session. Nothing is written to disk until after you confirm on the summary screen.

The `key_id` is the label your adapters use later. Examples:

- LiteLLM: `api_key: "subumbra:<key_id>"`
- Transparent sidecar: `Authorization: Bearer <key_id>`
- Explicit sidecar: `"key_id": "<key_id>"`

You may keep the default suggestions or enter your own labels such as
`anthropic_test` or `anthropic_prod`. Multiple keys for the same provider are valid
as long as each `key_id` is unique.

#### Automation / CI mode (optional)

For headless environments, create `.env.bootstrap` first:

```bash
cp .env.bootstrap.example .env.bootstrap
# edit .env.bootstrap with your real values, then:
docker compose --profile bootstrap run --rm bootstrap
```

The bootstrap container detects populated environment variables and skips the wizard automatically.

Optional automation-mode `key_id` overrides are available via matching `*_KEY_ID`
variables in `.env.bootstrap`. If omitted, bootstrap uses the same default
suggestions as the interactive wizard, such as `anthropic_prod`.

---

#### What bootstrap does (both modes)

1. Collects credentials (wizard or env)
2. Confirms with the operator (interactive mode only)
3. Generates an RSA-4096 key pair (RAM only)
4. For each API key: generates a random 32-byte DEK, wraps DEK with the RSA public key,
   encrypts the API key with AES-256-GCM using AAD `subumbra:v2:<key_id>`
5. Writes `public_key.pem` to the data volume (for offline rotation)
6. Creates or reuses the Cloudflare KV namespace for the live provider registry
7. Injects the KV binding into the temporary `wrangler.toml`
8. Deploys the CF Worker via wrangler
9. Publishes `provider_registry_v1` to Cloudflare KV
10. Pushes `WORKER_PRIVATE_KEY`, `WORKER_KEY_FINGERPRINT`, `SUBUMBRA_ADAPTER_TOKENS`,
    and `SUBUMBRA_HMAC_KEY` to CF Secrets
11. Deletes legacy `MASTER_DECRYPTION_KEY` from CF Secrets (V1 cleanup, best-effort)
12. Writes V2 records and runtime secrets to the shared volume (mode 0600)
13. Zeros sensitive memory and exits

Token values are **not** printed to stdout (to avoid CI/CD log capture). They are
written to `runtime.env` on the shared Docker volume.

#### Adapter key scopes

Bootstrap Step 3 assigns per-adapter `key_id` allowlists inside
`SUBUMBRA_ADAPTER_REGISTRY`. `subumbra-keys` enforces those lists when an adapter tries
to fetch a ciphertext record.

- `LiteLLM` scope:
  Use this for key IDs referenced by `subumbra:<key_id>` in
  `litellm/config.yaml`.
- `subumbra-proxy` scope:
  Use this for sidecar-driven keys such as GitHub, Slack, SendGrid, or any
  direct non-LiteLLM API calls routed through `subumbra-proxy`.
- `subumbra-probe` scope:
  Use this for proof and verification runs. In a test environment it is often
  broader than the other adapter scopes.
- `subumbra-ui`:
  Metadata only. It can list keys and stats, but it must never receive
  ciphertext fetch scope.

After a successful bootstrap, the summary prints copy/paste hints such as
`api_key: "subumbra:<key_id>"` for the key IDs scoped into LiteLLM.

---

### Step 3 — Create `.env` and run `post-bootstrap.sh`

If you do not already have a `.env`, create one from the template:

```bash
cp .env.example .env
```

Set `LITELLM_MASTER_KEY` to a strong random value (edit `.env` and replace the placeholder):

```bash
openssl rand -hex 32   # copy the output into .env as LITELLM_MASTER_KEY
```

Then run:

```bash
./post-bootstrap.sh
```

This script:
1. Reads `FORGE_TOKEN_*`, `SUBUMBRA_HMAC_KEY`, `CF_WORKER_URL`, and the adapter `*_ALLOWED_KEYS` lists from `runtime.env`
2. Writes them into your `.env` file
3. Verifies all required values landed correctly
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
subumbra-keys       Up (healthy)
litellm          Up              0.0.0.0:4000->4000/tcp
subumbra-proxy   Up              127.0.0.1:8090->8090/tcp
subumbra-ui      Up              127.0.0.1:8080->8080/tcp
```

> **Note:** `subumbra-keys` is intentionally not published to any host port.
> It is only reachable from within the Docker internal network.

---

### Step 5 — Verify End-to-End

Do **not** run `source .env`. This project stores `SUBUMBRA_ADAPTER_REGISTRY` as JSON
in `.env`, which can break shell parsing and later `docker compose` runs. Export
only the scalar values you actually need:

```bash
export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' .env)"
export CF_WORKER_URL="$(sed -n 's/^CF_WORKER_URL=//p' .env)"
```

**Check subumbra-keys health** (from inside the litellm container — subumbra-keys has no host port):
```bash
docker exec -i litellm python - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://subumbra-keys:9090/health").read().decode())
PY
# → {"status":"ok","keys_loaded":4,"timestamp":"..."}
```

**Check CF Worker health:**
```bash
curl -sS "$CF_WORKER_URL/health"
# → {"status":"ok","timestamp":"..."}
```

**Check LiteLLM:**
```bash
curl -sS \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  http://127.0.0.1:4000/health
# → {"status":"healthy",...}
```

**Send a test completion:**
```bash
curl -sS http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4",
    "messages": [{"role": "user", "content": "say hi in 3 words"}],
    "max_tokens": 20
  }'
```

Make sure the `subumbra:<key_id>` values in `litellm/config.yaml` exactly match the
key IDs you entered during bootstrap. The committed config uses the default
bootstrap suggestions such as `anthropic_prod`; if you chose different key IDs,
update the config before testing.

**Debug sidecar responses with status codes and headers:**
```bash
curl -i -sS -X GET \
  http://localhost:8090/t/user \
  -H 'Authorization: Bearer <your_github_key_id>' \
  -H 'Accept: application/vnd.github+json' \
  -H 'X-GitHub-Api-Version: 2022-11-28' \
  -H 'User-Agent: subumbra-test'
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
├── port 8090  →  subumbra-proxy (sidecar API, localhost only)
└── port 8080  →  subumbra-ui (dashboard, localhost only)

Docker network: external  (has internet access)
├── litellm
└── cloudflared (optional, --profile tunnel)

Docker network: internal  (NO internet, isolated)
├── subumbra-keys      ← reachable via Docker DNS from internal-network services
├── litellm         ← also on external (needs both)
├── subumbra-proxy
└── subumbra-ui
```

`subumbra-keys` has zero host port exposure. The only way to reach it is from another
container on the `internal` network using the DNS name `subumbra-keys`.

---

## Adding / Changing Models

Edit [litellm/config.yaml](litellm/config.yaml) to add models. The only required change is the `model:` line — `api_key` always uses the `subumbra:` prefix pointing to the correct key ID:

```yaml
- model_name: my-new-model
  litellm_params:
    model: anthropic/claude-3-5-haiku-20241022
    api_key: "subumbra:anthropic_prod"
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
> You must also add the provider to `worker/src/providers.json` (the bootstrap
> seed/template), republish the live registry via re-bootstrap or `--push-registry`,
> and add the relevant LiteLLM model configuration. See
> [docs/operator-guide.md](docs/operator-guide.md) for the live registry workflow.

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

No runtime tokens change. No service restart required because `subumbra-keys` automatically serves the new record on the very next request. The RSA key pair is unchanged. For the full recovery playbook, see `docs/operator-guide.md`.

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

### Token expiry and adapter authority recovery

If an adapter token has expired or been compromised, see
[`docs/operator-guide.md`](docs/operator-guide.md) §5-§6 for the recovery sequence.
Forge-side expiry stops new forge record fetches, but full revocation still requires
re-running bootstrap to rotate Worker-side token state.

### `subumbra-keys` healthcheck failing

```bash
docker compose logs subumbra-keys
```

Common causes:
- Missing `FORGE_TOKEN_LITELLM` (or another `FORGE_TOKEN_*` per-adapter token) or `SUBUMBRA_HMAC_KEY` in `.env`
- `keys.json` not yet written (bootstrap hasn't run)

### LiteLLM returning 500 on subumbra: keys

```bash
docker compose logs litellm
```

Look for lines from `forge-callback`. Common causes:
- `CF_WORKER_URL` not set in `.env`
- CF Worker not deployed (run bootstrap)
- `subumbra-keys` container unhealthy

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
cd worker && npx wrangler tail --name subumbra-proxy

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
cd worker && npx wrangler tail --name subumbra-proxy
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

### Dashboard shows "subumbra-keys unreachable"

The UI container can't reach subumbra-keys. Check:
```bash
docker compose ps          # is subumbra-keys running?
docker compose logs subumbra-ui     # any connection errors?
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
- `subumbra-keys` is never accessible from the host or internet — Docker enforces this with `internal: true` on the network
- The CF Worker rejects requests with the wrong forge adapter token before any decryption is attempted
- The Worker validates `target_url` against an allowlist to prevent SSRF
- All token comparisons are constant-time to prevent timing oracles
- The Durable Object uses `newUniqueId()` — no state persists between requests
- The dashboard (`localhost:8080`) shows key IDs and request counts only — never ciphertext or key values

For live registry operations, sidecar usage, and advanced custom-provider workflows,
see [docs/operator-guide.md](docs/operator-guide.md).
