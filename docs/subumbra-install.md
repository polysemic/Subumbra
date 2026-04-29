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
blank unless you already use them. `post-bootstrap.sh` fills in the generated
runtime values and `CF_WORKER_URL`.

> Do not `source .env` — `SUBUMBRA_ADAPTER_REGISTRY` is JSON and bash mangles it.

## 4. Cloudflare Prerequisites

You need:

- `CF_API_TOKEN` with `Workers Scripts: Edit` and `Workers KV Storage: Edit`
- `CF_ACCOUNT_ID`
- a Worker name, e.g. `subumbra-proxy`
- Workers Paid Plan enabled

The interactive bootstrap wizard prompts for these values.

## 5. Run Interactive Bootstrap

```bash
docker compose --profile bootstrap run --rm -it bootstrap
```

The wizard collects:

- Cloudflare credentials
- provider API keys and `key_id` labels
- built-in adapter scopes:
  - `subumbra-proxy`
  - `subumbra-ui` (metadata only)
- optional diagnostic adapter scope:
  - `subumbra-probe`

At Step 3, put the app-facing provider keys in `subumbra-proxy`. This is the
shared app-owned path used by standalone LiteLLM and other external apps.

The interactive wizard now stays RAM-only and manual. If you already have
machine-readable provider keys, use the automation path instead of an
interactive import prompt:

```bash
cp .env.bootstrap.example .env.bootstrap
# populate .env.bootstrap with CF credentials, provider keys, key_ids, and scopes
docker compose --profile bootstrap run --rm bootstrap
```

In a real TTY, when bootstrap detects environment credentials already present,
it now asks whether to continue in interactive RAM-only mode or proceed with the
automated environment-driven mode.

## 6. Run `post-bootstrap.sh`

```bash
./post-bootstrap.sh
```

This copies the generated Subumbra runtime values into `.env`:

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

Verify:

```bash
grep -E '^(SUBUMBRA_TOKEN_|CF_WORKER_URL|PROBE_ALLOWED_KEYS|UI_ALLOWED_KEYS)' .env
```

Probe values may be blank when probe was intentionally left unprovisioned.

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
- `api_key: <SUBUMBRA_TOKEN_YOUR_APP>` — adapter token from `.env`, not a plain key_id

## Next

- [docs/subumbra-testing.md](/home/eric/git/Subumbra/docs/subumbra-testing.md)
- [docs/apps/litellm/install.md](/home/eric/git/Subumbra/docs/apps/litellm/install.md)
- [docs/operator-guide.md](/home/eric/git/Subumbra/docs/operator-guide.md)
- [docs/subumbra-developer.md](/home/eric/git/Subumbra/docs/subumbra-developer.md)
