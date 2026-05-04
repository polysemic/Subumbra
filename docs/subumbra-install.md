# Subumbra Install Guide

*How to install and run the core Subumbra stack on a fresh Ubuntu 24.04 VPS.*

**Prerequisites:** complete the host baseline in
[docs/vps-deployment.md](/home/eric/git/Subumbra/docs/vps-deployment.md) first.

## 1. Install Docker Engine + Compose

```bash
sudo apt remove -y docker docker-engine docker.io containerd runc
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker "$USER"
newgrp docker
```

Verify:

```bash
docker compose version
docker run --rm hello-world
```

## 2. Clone Into `/opt/subumbra`

```bash
sudo mkdir -p /opt/subumbra
sudo chown -R "$USER":"$USER" /opt/subumbra
cd /opt/subumbra
git clone https://github.com/your-org/subumbra.git .
```

## 3. Create Core `.env`

```bash
cp .env.example .env
```

Leave `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`, and `TUNNEL_TOKEN`
blank unless you already use them. `bootstrap.sh` fills in the generated
runtime values and `CF_WORKER_URL`.

> Do not `source .env` — `SUBUMBRA_ADAPTER_REGISTRY` is JSON and bash mangles it.

## 4. Cloudflare Prerequisites

You need:

- `CF_API_TOKEN` with `Workers Scripts: Edit` and `Workers KV Storage: Edit`
- `CF_ACCOUNT_ID`
- a Worker name, e.g. `subumbra-proxy`
- Workers Paid Plan enabled

The interactive bootstrap wizard prompts for these values. Treat
`CF_API_TOKEN` as bootstrap/deploy authority for Worker, KV, and secret
changes; keep it separate from any persistent runtime secrets you enable later.

## 5. Run Bootstrap

Subumbra bootstrap supports two operator paths. Choose one:

### 5a. Interactive RAM-only (recommended for first-time setup)

```bash
./bootstrap.sh
```

Enter keys one at a time in the terminal. Nothing touches disk until the
wizard finishes - keys exist in RAM only during the session.

The wizard collects:

- Cloudflare credentials (API token, account ID, worker name)
- provider API keys and `key_id` labels (one key per prompt, hidden input)
- built-in adapter scopes via numbered selection:
  - `subumbra-proxy` - the shared app-facing sidecar
  - `subumbra-ui` - metadata only (no key fetch scope)
- optional: `subumbra-probe` - an optional diagnostic container for verifying your
  deployment; its scope is usually the same as or narrower than `subumbra-proxy`

At Step 3, put the app-facing provider keys in `subumbra-proxy`. This is the
shared app-owned path used by standalone LiteLLM and other external apps.

During full bootstrap, Cloudflare now generates the RSA key pair through the
one-shot `/setup/keygen` Worker path. The bootstrap container only receives the
returned public key, writes `public_key.pem`, and uses that public key for the
local V2 envelope records.

### 5b. Automation path (`.env.bootstrap`)

Use this path if you already have provider keys in a file, are scripting
installation, or are migrating from an existing deployment.

```bash
cp .env.bootstrap.example .env.bootstrap
# edit .env.bootstrap: CF credentials, provider keys, key_ids, adapter scopes,
# and optional IMPORT_PATH_<n> / IMPORT_APP_LABEL_<n> entries
./bootstrap.sh
```

See `.env.bootstrap.example` for the full list of expected variables. Key format:
`{PROVIDER}_KEY=<value>` with optional `KEY_ID_SUFFIX` and `ALLOWED_KEYS` entries
per adapter. App-owned imports use `IMPORT_PATH_<n>` plus required
`IMPORT_APP_LABEL_<n>` entries; `bootstrap.sh` mounts those files readonly into
the container. The automation path does not start an interactive wizard.

## 6. Verify Generated Runtime Values

```bash
grep -E '^(SUBUMBRA_TOKEN_|CF_WORKER_URL|PROBE_ALLOWED_KEYS|UI_ALLOWED_KEYS)' .env
```

`bootstrap.sh` writes the generated Subumbra runtime values directly into `.env`:

- `SUBUMBRA_ADAPTER_REGISTRY`
- `SUBUMBRA_TOKEN_PROXY`
- `SUBUMBRA_TOKEN_UI`
- `SUBUMBRA_HMAC_KEY`
- `CF_WORKER_URL`
- `PROXY_ALLOWED_KEYS` (intentionally empty after proxy lockdown)
- `UI_ALLOWED_KEYS`

If probe provisioning was enabled during bootstrap, this step also writes:

- `SUBUMBRA_TOKEN_PROBE`
- `PROBE_ALLOWED_KEYS`

Probe values may be blank when probe was intentionally left unprovisioned.

`public_key.pem` is also written into the bootstrap data volume for offline
single-key rotation. The Cloudflare-side private key never lands on the VPS.

## 7. Start The Core Stack

```bash
docker compose up -d --force-recreate
docker compose ps
```

Expected services:

- `subumbra-keys` (healthy)
- `subumbra-proxy` (healthy)
- `subumbra-ui`

Port exposure:

- `subumbra-keys` — internal only
- `subumbra-proxy` — `127.0.0.1:10199`
- `subumbra-ui` — `127.0.0.1:6563`

## 8. Verify The Core Stack

```bash
export CF_WORKER_URL="$(sed -n 's/^CF_WORKER_URL=//p' .env)"

docker compose ps
curl -sS "$CF_WORKER_URL/health"
curl -sS http://127.0.0.1:10199/health
curl -sS http://127.0.0.1:6563/api/status
```

The proxy health response should now include `worker_auth`.

## 9. Standalone LiteLLM

LiteLLM is no longer part of the core `/opt/subumbra` compose stack.

Use the standalone guide:

- [docs/apps/litellm/install.md](/home/eric/git/Subumbra/docs/apps/litellm/install.md)

That guide shows the supported app-owned contract:

- `api_base: http://subumbra-proxy:8090/t/<key_id>/...`
- `api_key: <SUBUMBRA_TOKEN_PROXY>` — use the value of `SUBUMBRA_TOKEN_PROXY` from `.env`

## Next

- [docs/subumbra-testing.md](/home/eric/git/Subumbra/docs/subumbra-testing.md)
- [docs/apps/litellm/install.md](/home/eric/git/Subumbra/docs/apps/litellm/install.md)
- [docs/operator-guide.md](/home/eric/git/Subumbra/docs/operator-guide.md)
- [docs/subumbra-developer.md](/home/eric/git/Subumbra/docs/subumbra-developer.md)
