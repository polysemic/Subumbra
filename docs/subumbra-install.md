# Subumbra Install Guide

*How to install and run Subumbra on a fresh Ubuntu 24.04 VPS.*

**Prerequisites:** complete the host baseline in
[`docs/vps-deployment.md`](./vps-deployment.md) first — SSH hardening, UFW,
Docker not yet installed.

---

## 1. Install Docker Engine + Compose

Remove any older packages:

```bash
sudo apt remove -y docker docker-engine docker.io containerd runc
```

Install:

```bash
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
docker compose version   # must be v2.20+
docker run --rm hello-world
```

---

## 2. Clone Into A Dedicated Directory

```bash
sudo mkdir -p /opt/subumbra
sudo chown -R "$USER":"$USER" /opt/subumbra
cd /opt/subumbra
git clone https://github.com/your-org/subumbra.git .
```

Everything from here runs inside `/opt/subumbra`.

---

## 3. Create `.env`

```bash
cp .env.example .env
```

Set `LITELLM_MASTER_KEY` to a strong random value:

```bash
openssl rand -hex 32
# paste the output into .env
nano .env
```

Leave `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`, and `TUNNEL_TOKEN` blank
for now. `post-bootstrap.sh` fills in all generated runtime values and `CF_WORKER_URL`
automatically.

> **Do not `source .env`** — `SUBUMBRA_ADAPTER_REGISTRY` is a JSON blob that bash
> mangles. Export only what you need:
> ```bash
> export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' .env)"
> export CF_WORKER_URL="$(sed -n 's/^CF_WORKER_URL=//p' .env)"
> ```

---

## 4. Cloudflare Prerequisites

You need:

- `CF_API_TOKEN` — permissions: `Workers Scripts: Edit`, `Workers KV Storage: Edit`
- `CF_ACCOUNT_ID` — found in the Cloudflare dashboard right sidebar
- A Worker name, e.g. `subumbra-proxy` (becomes `<name>.workers.dev`)
- Workers Paid Plan enabled — Durable Objects are not on the free tier

The interactive bootstrap wizard prompts for these values. You do not need to
create `.env.bootstrap` for the normal install path.

---

## 5. Run Interactive Bootstrap

```bash
docker compose --profile bootstrap run --rm -it bootstrap
```

The wizard collects:

- Cloudflare credentials
- Provider API keys and `key_id` labels (defaults like `anthropic_prod` are
  suggested; you may enter custom labels)
- Adapter scope choices (which key_ids each adapter may fetch)

At the end, bootstrap prints copy/paste hints for `litellm/config.yaml`. Write
these down — you will need them in step 7.

Bootstrap will:

- generate a fresh RSA-4096 key pair
- encrypt provider secrets into forge records
- deploy the Worker via wrangler
- create or reuse the KV namespace
- push Worker secrets to Cloudflare
- write runtime state to the Docker volume

If bootstrap fails, read the error and rerun. Do not continue until it succeeds.

---

## 6. Run `post-bootstrap.sh`

```bash
./post-bootstrap.sh
```

This reads runtime state from the Docker volume and writes it into `.env`:
`SUBUMBRA_ADAPTER_REGISTRY`, `FORGE_TOKEN_*`, `SUBUMBRA_HMAC_KEY`, `CF_WORKER_URL`,
and the `*_ALLOWED_KEYS` lists.

Verify it landed:

```bash
grep -E '^(FORGE_|CF_WORKER_URL|LITELLM_MASTER_KEY)' .env
```

---

## 7. Update `litellm/config.yaml`

The committed config uses the bootstrap default `key_id` suggestions
(`anthropic_prod`, `openai_prod`, etc.). If you entered custom labels during
bootstrap, update the `subumbra:<key_id>` values to match before starting the stack.

Use the copy/paste hints bootstrap printed at the end of step 5.

Example: if you named your Anthropic key `anthropic_test`, change:

```yaml
api_key: "subumbra:anthropic_prod"
```

to:

```yaml
api_key: "subumbra:anthropic_test"
```

---

## 8. Start The Stack

```bash
docker compose up -d --force-recreate
```

Check status:

```bash
docker compose ps
```

Expected services: `subumbra-keys` (healthy), `subumbra-proxy` (healthy),
`subumbra-ui`, `litellm`.

Port exposure notes:

- `subumbra-keys` — internal only, no host port
- `subumbra-ui` — `127.0.0.1:8080` only
- `subumbra-proxy` — `127.0.0.1:8090` only
- `litellm` — `0.0.0.0:4000` (blocked by UFW unless you open it)

---

## 9. Verify Locally

```bash
export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' .env)"
export CF_WORKER_URL="$(sed -n 's/^CF_WORKER_URL=//p' .env)"

# Forge health (from inside LiteLLM container — subumbra-keys is internal only)
docker exec litellm python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://subumbra-keys:9090/health').read().decode())"

# Worker health
curl -sS "$CF_WORKER_URL/health"

# Sidecar health
curl -sS http://127.0.0.1:8090/health

# LiteLLM health
curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://127.0.0.1:4000/health

# UI status
curl -sS http://127.0.0.1:8080/api/status
```

All five should return healthy/ok responses. Do not expose the stack publicly
until these pass.

---

## 10. Optional: First Functional Test

Send a real request through the full stack:

```bash
curl http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4",
    "messages": [{"role": "user", "content": "say hi in 3 words"}],
    "max_tokens": 20
  }'
```

A streaming response from the provider confirms end-to-end flow is working.

---

## 11. Optional: Cloudflare Tunnel

Once local verification passes, you can expose the stack through a Cloudflare
Tunnel instead of opening VPS ports directly.

1. Create a tunnel in Cloudflare Zero Trust dashboard
2. Copy the tunnel token into `.env`: `TUNNEL_TOKEN=<token>`
3. Configure DNS CNAME records and ingress rules in the CF dashboard
4. Start the tunnel service:

```bash
docker compose --profile tunnel up -d cloudflared
```

See [`docs/operator-guide.md`](./operator-guide.md) for tunnel routing notes.

---

## Next

- [`docs/subumbra-testing.md`](./subumbra-testing.md) — how to run tests and verify correctness
- [`docs/operator-guide.md`](./operator-guide.md) — provider registry, rotation, recovery
- [`docs/subumbra-developer.md`](./subumbra-developer.md) — git/VPS workflow, full reset, council harness
