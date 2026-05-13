# Subumbra Install Guide

*How to install and run the core Subumbra stack on a fresh Ubuntu 24.04 VPS.*

**Prerequisites:** complete the host baseline in
[docs/vps-deployment.md](vps-deployment.md) first.

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

## 2b. Create `subumbra.json` (gitignored)

`subumbra.json` is **not committed** (see `.gitignore`). You **must** create it
locally before bootstrap or the compose mount will point at a missing file and
bootstrap will fail.

```bash
cp subumbra.minimal.json subumbra.json
# or, for the fuller exemplar:
# cp subumbra.example.json subumbra.json
```

Edit `subumbra.json` to match your adapters and policies. The **minimal**
template is one OpenAI key using **`template` only** (no inline `policy`). The
**example** file lists **every** signed catalog template plus one inline policy
row demonstrating optional `deny`, `intent`, `response`, and `velocity` fields;
use it when you want the full variable surface or to copy additional providers
into `keys`.

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
Runtime `.env` does **not** retain `CF_API_TOKEN`, so you must re-supply it for
later Cloudflare-backed day-2 operations such as deploy-integrity verification.

## 5. Run Bootstrap

Subumbra bootstrap supports two operator paths. Choose one:

### 5a. Interactive RAM-only (recommended for first-time setup)

```bash
./bootstrap.sh
```

Enter values in the terminal; provider material is held in RAM for the session
(including an in-process map keyed by each manifest `secret_ref`). Nothing is
written as plaintext bootstrap state on disk.

The wizard collects:

- Cloudflare API token and account ID when not already in the environment; Worker
  name defaults from `CF_WORKER_NAME` or `CF_WORKER_URL` in `.env` (else
  `subumbra-proxy`), Enter to accept or type a new name
- Per-manifest-key `secret_ref` secrets (hidden prompts with confirmation), or uses
  values already present in the bootstrap environment for that `secret_ref`
- Policy, `unique_vault`, adapters, and `key_id` from `subumbra.json` only (no
  catalog-era menus)
- optional: skip a key for this session (`[Y/n]` decline) — omitted keys follow
  the same rotation removal rules as automation when not re-included

`subumbra-ui` remains metadata only (no key fetch scope). Optional `subumbra-probe`
follows the same manifest adapter rules as other adapters.

During full bootstrap, Cloudflare now generates the RSA key pair through the
one-shot `/setup/keygen` Worker path. The bootstrap container only receives the
returned public key, writes `public_key.pem`, and uses that public key for the
local V3 envelope records.

If a previous run already left Cloudflare vault or provider-registry state
behind, full bootstrap now stops and asks for explicit destructive
acknowledgement before it wipes that state and continues. In non-interactive
automation, pass `--nuke` only when you intend a true fresh start.

### 5b. Automation path (`.env.bootstrap`)

Use this path if you already have provider keys in a file, are scripting
installation, or are migrating from an existing deployment.

```bash
cp .env.bootstrap.example .env.bootstrap
# edit .env.bootstrap: CF credentials, values for each manifest secret_ref,
# optional TOKEN_TTL_DAYS (default 90 when unset), per-key UNIQUE_KEY_<key_id> flags
./bootstrap.sh
```

See `.env.bootstrap.example` for the full list of expected variables. Key format:
`{PROVIDER}_KEY=<value>` with matching `{PROVIDER}_KEY_ID=<key_id>` and
`{PROVIDER}_KEY_ADAPTERS=<adapter_ids>` entries per direct secret slot.
Optional `UNIQUE_KEY_<key_id>=true` provisions that key into its own
`vault-<key_id>` Durable Object; omitted or `false` keeps the key on the shared
`vault` instance.
Blank `*_ADAPTERS` is explicit compatibility/simple mode only.

`./bootstrap.sh` shreds `.env.bootstrap` after a successful full bootstrap.
`./bootstrap.sh --provision <key_id>` intentionally does **not** shred it so
you can complete additional repair steps; shred it manually after repairs.
After a successful full bootstrap, the Worker-side `SUBUMBRA_SETUP_TOKEN`
bootstrap authority is revoked; any host-side copy should be treated as a
reference record, not a reusable live credential.

**Host `.env` bind-mount.** Full bootstrap and `--provision` update the
repo-root `.env` through the bootstrap container path `/app/host-env`. If that
bind-mount is missing, bootstrap fails closed (it cannot persist generated
tokens). See [docs/operator-guide.md](operator-guide.md) (**Bootstrap Phase-2
recovery**) for half-state recovery (`--nuke`, `--push-registry`) and setup-token
notes.

## 6. Verify Generated Runtime Values

```bash
grep -E '^(SUBUMBRA_TOKEN_|CF_WORKER_URL|PROBE_ALLOWED_KEYS|UI_ALLOWED_KEYS)' .env
```

`bootstrap.sh` writes the generated Subumbra runtime values directly into `.env`:

- `SUBUMBRA_ADAPTER_REGISTRY`
- per-app tokens such as `SUBUMBRA_TOKEN_LITELLM` and `SUBUMBRA_TOKEN_OPENWEBUI`
- `SUBUMBRA_TOKEN_PROXY` for proxy transport and explicit compatibility/simple mode
- `SUBUMBRA_TOKEN_UI`
- `SUBUMBRA_HMAC_KEY`
- `CF_WORKER_URL`
- `PROXY_ALLOWED_KEYS` (intentionally empty after proxy lockdown)
- `UI_ALLOWED_KEYS`

If probe provisioning was enabled during bootstrap, this step also writes:

- `SUBUMBRA_TOKEN_PROBE`
- `PROBE_ALLOWED_KEYS`

Probe values may be blank when probe was intentionally left unprovisioned.

`public_key.pem` is written for the shared vault, and any unique-vault key also
gets `public_key_<key_id>.pem` in the bootstrap data volume for offline
single-key rotation. The Cloudflare-side private key never lands on the VPS.

## 7. Start The Core Stack

After a successful `./bootstrap.sh` or `./bootstrap.sh --nuke`, the host wrapper
already runs `docker compose up -d --force-recreate` and prints an adapter token
/ `key_id` summary from `.env`. If you skipped the wrapper or need to restart
services only:

```bash
docker compose up -d --force-recreate
docker compose ps
```

### Image-only updates (no re-bootstrap)

After `git pull` (or equivalent), rebuild runtime and bootstrap images and
recreate containers without touching Docker volumes:

```bash
./bootstrap.sh --upgrade
```

This does **not** run Cloudflare bootstrap, rotate keys, or change `.env`
(except that running containers pick up the current `.env` from disk). Optional
`subumbra-probe` / `cloudflared` use Compose profiles — rebuild or restart those
separately if you use them (see comments in `docker-compose.yml`).

Expected services:

- `subumbra-keys` (healthy)
- `subumbra-proxy` (healthy)
- `subumbra-ui`

### Existing volume migration

If you already have data in Docker's older doubled volume name, migrate it once
into the Compose-backed host volume (default project name `subumbra` →
`subumbra_keys_data`):

```bash
docker volume create subumbra_keys_data
docker run --rm \
  -v subumbra_subumbra_keys_data:/from \
  -v subumbra_keys_data:/to \
  alpine:3.21 sh -c "cp -a /from/. /to/"
```

After migration and `docker compose up`, you may remove the stale
`subumbra_subumbra_keys_data` volume **only** after confirming the stack is
healthy and data is present under the new volume.

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
# With UI Basic Auth enabled (`UI_USERNAME` + `UI_PASSWORD` in `.env`), unauthenticated
# `GET /api/status` returns HTTP 401. With both unset (CF Access / default), local
# `curl` may return HTTP 200 because `_require_auth` only enforces Basic when configured.
curl -sS http://127.0.0.1:6563/api/status
```

The Worker `curl` target and `subumbra-keys` `/health` return a minimal
`{"status":"ok"}` body. **`subumbra-proxy` `/health`** additionally returns
`worker_auth` (`ok`, `stale`, `token_mismatch`, or `unreachable`) describing the last Worker
auth-ping result — see `docs/operator-guide.md` ("Heartbeat, polling, and health cadence").

For deploy-integrity verification after install, export `CF_API_TOKEN`,
`CF_ACCOUNT_ID`, and `CF_WORKER_NAME`, then run
`./scripts/subumbra-verify-deploy` from the repo root. See the
[operator guide](operator-guide.md) for the exact
day-2 command and recovery notes.

If you lose Cloudflare-side vault custody, the supported recovery path is a
full re-bootstrap with the original `subumbra.json` and `.env.bootstrap`
inputs. See the recovery section in the
[operator guide](operator-guide.md).

## 9. Standalone LiteLLM

LiteLLM is no longer part of the core `/opt/subumbra` compose stack.

Use the standalone guide:

- [docs/apps/litellm/install.md](apps/litellm/install.md)

That guide shows the supported app-owned contract:

- `api_base: http://subumbra-proxy:8090/t/<key_id>/...`
- `api_key: <SUBUMBRA_TOKEN_LITELLM>` — use the LiteLLM app token from `.env`

## Next

- [docs/subumbra-testing.md](subumbra-testing.md)
- [docs/apps/litellm/install.md](apps/litellm/install.md)
- [docs/operator-guide.md](operator-guide.md)
- [docs/subumbra-developer.md](subumbra-developer.md)
- [docs/integration-recipes.md](integration-recipes.md)
