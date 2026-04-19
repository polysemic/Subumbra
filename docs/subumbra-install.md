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
  - `subumbra-probe`
  - `subumbra-ui` (metadata only)

At Step 3, put the app-facing provider keys in `subumbra-proxy`. This is the
shared app-owned path used by standalone LiteLLM and other external apps.

If you already have provider keys in an app-owned `.env` file, you may mount it
read-only and import the provider keys during the wizard:

```bash
docker compose --profile bootstrap run --rm \
  -v /opt/litellm:/host_litellm:ro \
  -it bootstrap
```

Then enter `/host_litellm/.env` when prompted.

## 6. Run `post-bootstrap.sh`

```bash
./post-bootstrap.sh
```

This copies the generated Subumbra runtime values into `.env`:

- `SUBUMBRA_ADAPTER_REGISTRY`
- `SUBUMBRA_TOKEN_PROXY`
- `SUBUMBRA_TOKEN_UI`
- `SUBUMBRA_TOKEN_PROBE`
- `SUBUMBRA_HMAC_KEY`
- `CF_WORKER_URL`
- `PROXY_ALLOWED_KEYS`
- `PROBE_ALLOWED_KEYS`
- `UI_ALLOWED_KEYS`

Verify:

```bash
grep -E '^(SUBUMBRA_TOKEN_|CF_WORKER_URL|PROXY_ALLOWED_KEYS|PROBE_ALLOWED_KEYS|UI_ALLOWED_KEYS)' .env
```

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
- `subumbra-proxy` — `127.0.0.1:8090`
- `subumbra-ui` — `127.0.0.1:8080`

## 8. Verify The Core Stack

```bash
export CF_WORKER_URL="$(sed -n 's/^CF_WORKER_URL=//p' .env)"

docker compose ps
curl -sS "$CF_WORKER_URL/health"
curl -sS http://127.0.0.1:8090/health
curl -sS http://127.0.0.1:8080/api/status
```

The proxy health response should now include `worker_auth`.

## 9. Standalone LiteLLM

LiteLLM is no longer part of the core `/opt/subumbra` compose stack.

Use the standalone guide:

- [docs/standalone-litellm.md](/home/eric/git/Subumbra/docs/standalone-litellm.md)

That guide shows the supported app-owned contract:

- `api_base: http://subumbra-proxy:8090/t`
- `api_key: <key_id>` using a plain key ID

## Next

- [docs/subumbra-testing.md](/home/eric/git/Subumbra/docs/subumbra-testing.md)
- [docs/standalone-litellm.md](/home/eric/git/Subumbra/docs/standalone-litellm.md)
- [docs/operator-guide.md](/home/eric/git/Subumbra/docs/operator-guide.md)
- [docs/subumbra-developer.md](/home/eric/git/Subumbra/docs/subumbra-developer.md)
