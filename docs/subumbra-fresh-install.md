# Subumbra Fresh Install

*Clean reference install for a fresh Ubuntu 24.04 VPS after completing the host
baseline in [`docs/vps-deployment.md`](./vps-deployment.md).*

This guide is the **official clean-run path** for the current project state.
It assumes:

- a fresh or freshly rolled back VPS
- Docker is not yet part of the host baseline
- you want to install Subumbra in its own dedicated directory
- you do **not** want to risk overwriting unrelated host `.env` files or other
  existing service configs

This guide does **not** attempt to coexist with pre-existing Docker stacks,
Portainer/Dockge installs, or other application layouts. Those should be
handled in a separate compatibility/conflict guide.

---

## 1. What This Guide Covers

This is the current reference path for:

- installing Docker Engine and Compose on the VPS
- cloning the Subumbra repo into a dedicated location
- creating a **project-local** `.env`
- running interactive bootstrap
- running `post-bootstrap.sh`
- starting the Compose stack
- verifying the stack locally before any tunnel or public app exposure

This is the safest current path because the project still relies on:

- `docker compose --profile bootstrap run ...`
- `post-bootstrap.sh` on the host
- runtime token injection into the repo-local `.env`

---

## 2. Important Safety Rules

Before you start:

- do **not** run this inside another application directory
- do **not** reuse some unrelated host `.env`
- do **not** point nginx, a tunnel, or external DNS at the stack yet
- do **not** add Portainer/Dockge first and then try to infer the install path

Recommended install location:

```text
/opt/subumbra
```

This keeps all project-local files in one place:

- Git checkout
- repo-local `.env`
- optional `.env.bootstrap`
- Compose commands
- helper scripts like `post-bootstrap.sh`

---

## 3. Install Docker Engine + Compose

On Ubuntu 24.04, install Docker using the official repository.

Remove any older distro packages first:

```bash
sudo apt remove -y docker docker-engine docker.io containerd runc
```

Install prerequisites:

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
```

Add Docker’s GPG key:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
```

Add the Docker apt repository:

```bash
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

Install Docker:

```bash
sudo apt update
sudo apt install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin
```

Enable Docker:

```bash
sudo systemctl enable docker
sudo systemctl start docker
sudo systemctl status docker
```

Allow your normal user to run Docker:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

Verify:

```bash
docker --version
docker compose version
docker run --rm hello-world
```

---

## 4. Create A Dedicated Install Directory

Create the application directory:

```bash
sudo mkdir -p /opt/subumbra
sudo chown -R "$USER":"$USER" /opt/subumbra
cd /opt/subumbra
```

Clone the repo there:

```bash
git clone https://github.com/your-org-or-fork/subumbra.git .
```

Or if you are copying it from another machine, sync it into `/opt/subumbra`
first and then work only from that directory.

Sanity check:

```bash
pwd
ls -la
```

Expected:

- you are inside `/opt/subumbra`
- the repo contains `docker-compose.yml`
- the repo contains `post-bootstrap.sh`
- the repo contains `.env.bootstrap.example`

---

## 5. Create A Project-Local `.env`

Subumbra’s helper script writes runtime values into a file named:

```text
.env
```

That file is expected to live in the **repo root**, next to
`docker-compose.yml` and `post-bootstrap.sh`.

This is why you should install into a dedicated directory like `/opt/subumbra`:
the `.env` belongs to **this repo only**, not to the host globally and not to
some other application directory.

### If `.env` already exists

If you see an existing `.env` inside `/opt/subumbra`, stop and decide why.

Check:

```bash
ls -la .env
```

If it belongs to a previous Subumbra run and you want a clean restart, back it
up first:

```bash
cp .env .env.bak.$(date +%Y%m%d-%H%M%S)
```

Do **not** delete or overwrite unrelated `.env` files in other host
directories. This guide assumes you are working only inside `/opt/subumbra`.

### Create the minimal `.env`

There is currently no committed `.env.example`, so create a minimal one
manually:

```bash
cat > .env <<'EOF'
LITELLM_MASTER_KEY=change-this-to-a-long-random-value
AUDIT_MAX_ROWS=10000
CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=
TUNNEL_TOKEN=
EOF
```

Replace `LITELLM_MASTER_KEY` with a strong random value.

Example:

```bash
openssl rand -hex 32
```

Then edit `.env`:

```bash
nano .env
```

Notes:

- `post-bootstrap.sh` will later add the generated `FORGE_*` runtime values and
  `CF_WORKER_URL` plus the adapter `*_ALLOWED_KEYS` lists into this same
  repo-local `.env`
- leaving `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`, and `TUNNEL_TOKEN`
  blank is fine for a fresh local install
- this file is gitignored and should remain local to this checkout

---

## 6. Prepare Cloudflare Credentials

You will need:

- `CF_API_TOKEN`
- `CF_ACCOUNT_ID`
- a Worker name, typically `keyvault-proxy`

Required Cloudflare API token permissions:

- `Account > Workers Scripts > Edit`
- `Account > Workers KV Storage > Edit`

The interactive bootstrap wizard will prompt you for these values directly, so
you do **not** need to create `.env.bootstrap` for the normal fresh-install
path.

---

## 7. Run Interactive Bootstrap

From `/opt/subumbra`:

```bash
docker compose --profile bootstrap run --rm -it bootstrap
```

The wizard will prompt for:

- Cloudflare API token
- Cloudflare account ID
- Worker name
- provider API keys and their `key_id`s
- adapter scope choices for `litellm`, `keyvault-proxy`, and `adapter-probe`

Notes:

- bootstrap suggests default key IDs such as `anthropic_prod`, but you may enter
  your own labels such as `anthropic_test`
- the `key_id` is the value your adapters use later, for example
  `api_key: "forge:<key_id>"` in `litellm/config.yaml`
- the adapter scope choices decide which key IDs each adapter may fetch from
  `forge-keys`

During bootstrap, the container will:

- generate the RSA key pair
- encrypt provider secrets into forge records
- deploy the Worker with wrangler
- create or reuse the live provider-registry KV namespace
- publish the provider registry to Cloudflare KV
- push Worker secrets to Cloudflare
- write runtime state into the shared Docker volume

If bootstrap fails, do **not** continue to the next step. Read the error and
rerun bootstrap after fixing the issue.

---

## 8. Run `post-bootstrap.sh`

Still from `/opt/subumbra`:

```bash
./post-bootstrap.sh
```

This script reads runtime values from Docker volume state and writes them into
the repo-local `.env`.

It does **not** read your raw provider API keys. It writes only runtime values
needed by the Docker services, such as:

- `FORGE_ADAPTER_REGISTRY`
- `FORGE_TOKEN_*`
- `FORGE_HMAC_KEY`
- `CF_WORKER_URL`
- `LITELLM_ALLOWED_KEYS`
- `PROXY_ALLOWED_KEYS`
- `PROBE_ALLOWED_KEYS`

After it completes, verify that `.env` now contains those values:

```bash
grep -E '^(LITELLM_MASTER_KEY|FORGE_|CF_WORKER_URL)' .env
```

If `post-bootstrap.sh` reports token drift later, that is expected after a
re-bootstrap and is resolved by recreating the services.

---

## 9. Start The Stack

Start the steady-state services:

```bash
docker compose up -d --force-recreate
```

Why `--force-recreate` matters:

- bootstrap generated fresh runtime tokens
- the services need to reload those new values from `.env`
- skipping recreate can leave containers with stale auth state

Check status:

```bash
docker compose ps
```

Expected core services:

- `forge-keys`
- `litellm`
- `keyvault-proxy`
- `keyvault-ui`

Important host exposure notes from the current Compose file:

- `forge-keys` is **not** exposed to host ports
- `keyvault-ui` binds to `127.0.0.1:8080`
- `keyvault-proxy` binds to `127.0.0.1:8090`
- `litellm` currently publishes on host port `4000`

If your VPS firewall allows only `22`, `80`, and `443`, port `4000` will remain
blocked from the public internet even though Docker publishes it on the host.

---

## 10. Verify Locally Before Any Public Exposure

Check container state:

```bash
docker compose ps
```

> **WARNING — do not `source .env`**
>
> `FORGE_ADAPTER_REGISTRY` is a JSON blob stored unquoted in `.env`. Running
> `source .env` or `set -a; source .env; set +a` mangles the JSON in your shell
> environment. Docker Compose then passes the broken value to the `forge-keys`
> container, which crashes at startup with `RuntimeError: FORGE_ADAPTER_REGISTRY
> must be valid JSON`. If you have already run `source .env` in this shell,
> open a new shell before running any `docker compose` command.
>
> Export only the scalar values you need:

```bash
export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' .env)"
export CF_WORKER_URL="$(sed -n 's/^CF_WORKER_URL=//p' .env)"
```

Check forge health from inside the LiteLLM container:

```bash
docker exec litellm python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://forge-keys:9090/health').read().decode())"
```

Check Worker health:

```bash
curl -sS "$CF_WORKER_URL/health"
```

Check LiteLLM health:

```bash
curl -sS \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  http://127.0.0.1:4000/health
```

Check sidecar health:

```bash
curl -sS http://127.0.0.1:8090/health
```

Check UI status:

```bash
curl -sS http://127.0.0.1:8080/api/status
```

If all of those work, the install is in a good local state.

Do not point nginx or a Cloudflare Tunnel at the stack until local validation
passes first.

---

## 11. Optional First Functional Check

If you configured a model in `litellm/config.yaml`, send a test request:

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

Important:

- the `forge:<key_id>` values in `litellm/config.yaml` must exactly match the key
  IDs you entered during bootstrap
- the committed config uses the bootstrap default suggestions such as
  `anthropic_prod`
- if you entered custom key IDs such as `anthropic_test`, update
  `litellm/config.yaml` before testing

Open the local dashboard from an SSH tunnel or local reverse proxy later if
needed:

```text
http://127.0.0.1:8080
```

---

## 12. What To Avoid On A Fresh Install

Avoid these on the first pass:

- Portainer-first deployment
- Dockge-first deployment
- custom systemd wrappers before the stack is proven
- editing compose port mappings before the reference path works
- introducing Cloudflare Tunnel before local checks pass
- installing inside another app directory that already has its own `.env`

Those may become fine later, but they make first-install debugging much harder.

---

## 13. Rollback-Friendly Notes

This guide works best with a VPS snapshot workflow:

1. complete the host baseline
2. create a clean VPS snapshot
3. perform the Subumbra install from `/opt/subumbra`
4. if the test goes sideways, roll back to the clean host snapshot

That gives you a known-good boundary between:

- generic host setup
- application-specific install state

---

## 14. Next Docs

After this clean reference install works, the next logical docs are:

- Docker/host conflict checks
- coexistence with existing Docker stacks
- optional Cloudflare Tunnel exposure
- optional nginx reverse proxying
- optional day-2 management via Portainer/Dockge

Those should be layered on top of this reference path instead of replacing it.
