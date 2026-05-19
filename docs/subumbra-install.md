# Subumbra Install Guide

*How to install and run the core Subumbra stack on a fresh Ubuntu 24.04 VPS.*

**Prerequisites:** complete the host baseline in
[docs/vps-deployment.md](vps-deployment.md) first.

## 1. Get Your Cloudflare Credentials First

Before you set up Docker or run bootstrap, prepare the minimum Cloudflare
values Subumbra needs:

- `CF_API_TOKEN`
- `CF_ACCOUNT_ID`
- a Worker name, for example `subumbra-proxy`
- an active Workers Paid plan (A free plan may work. I have not tested this and you may run into limitations.)

For the basic Worker + KV path, your API token should have:

- `Workers Scripts: Edit`
- `Workers KV Storage: Edit`

Create the token from:

- https://dash.cloudflare.com/profile/api-tokens

If you want the step-by-step Cloudflare walkthrough, optional Tunnel / Access
setup, or bootstrap auto-provision details, see
[docs/cloudflare-setup.md](cloudflare-setup.md).

## 2. Install Docker Engine + Compose

Follow the official Docker guide for your distro:

- **Docker Engine:** https://docs.docker.com/engine/install/
- **Linux post-install** (run Docker without `sudo`): https://docs.docker.com/engine/install/linux-postinstall/

The Compose plugin is included in Docker Engine packages. Verify before continuing:

```bash
docker compose version
docker run --rm hello-world
```

## 3. Clone Into `/opt/subumbra`

```bash
git clone https://github.com/polysemic/Subumbra.git /opt/subumbra
cd /opt/subumbra
```
> If `/opt` is restricted on your system, you may need `sudo mkdir -p /opt/subumbra && sudo chown -R "$USER":"$USER" /opt/subumbra` first, then run the clone.

## 3a. Create the shared Docker network

Subumbra's proxy container joins a pre-existing Docker network (`subumbra-net`)
so that app containers on other Compose stacks (LiteLLM, OpenWebUI, etc.) can
reach `subumbra-proxy` by container name. Create it once per host — it persists
across restarts and only needs to be created again if you prune all networks.

```bash
docker network create subumbra-net
```

**Connecting your app to Subumbra**

There are two ways an app container can reach the proxy, depending on whether
it joins `subumbra-net`:

| Method | When to use | `api_base` / proxy URL |
|--------|-------------|------------------------|
| **Docker network** (recommended) | App runs in Docker and you join it to `subumbra-net` | `http://subumbra-proxy:8090/t/<key_id>/...` |
| **Host port** | App runs on the host, in a VM, or you prefer not to modify its network | `http://127.0.0.1:10199/t/<key_id>/...` |

**To join an existing Docker app to `subumbra-net`**, add the network to its
`docker-compose.yml` under both the service and the top-level `networks` block:

```yaml
services:
  your-app:
    # ... existing config ...
    networks:
      - your-existing-network   # keep your existing networks
      - subumbra-net            # add this

networks:
  your-existing-network:        # keep your existing declaration
  subumbra-net:
    external: true              # tells Compose not to create it — it already exists
```

Then restart the app stack (`docker compose up -d`). The container can now
reach `subumbra-proxy` at `http://subumbra-proxy:8090/t/<key_id>/...`.

If you cannot or prefer not to modify the app's network config, use the host
port `http://127.0.0.1:10199/t/<key_id>/...` instead — no network changes
needed.

## 3b. Create `subumbra.yaml` (gitignored)

`subumbra.yaml` is **not committed** (see `.gitignore`). You **must** create it
locally before bootstrap or the compose mount will point at a missing file and
bootstrap will fail.

To use pre-built templates, use:
```bash
cp subumbra.minimal.yaml subumbra.yaml
```

To use custom providers or inline policies, use:
```bash
cp subumbra.example.yaml subumbra.yaml
```

Edit `subumbra.yaml` to match your providers, apps and policies. The **minimal**
file shows the simplest form: one or more LLM providers using signed built-in
templates. The **example** file lists every built-in template plus one inline
policy row showing all required and optional fields. Use minimal to get running
fast; use the example when you want full control over policy and add custom providers.

See [docs/provider-templates.md](provider-templates.md) for the full list of
built-in templates and how to customize them.

## 4. Core `.env`

`bootstrap.sh` creates `.env` from `.env.example` automatically.

You do not need to pre-edit `.env` to use Cloudflare Tunnel or CF Access.
`bootstrap.sh` creates `.env` from `.env.example` automatically on first run,
and the interactive wizard now collects Cloudflare Tunnel and Access runtime
credentials (`TUNNEL_TOKEN`, `CF_ACCESS_CLIENT_ID`,
`CF_ACCESS_CLIENT_SECRET`) and writes them to `.env` for you.

You also do not have to use Cloudflare auto-provisioning. You can provide existing
Tunnel / Access runtime secrets and skip Cloudflare lifecycle creation.

For automation (non-interactive) use, add these as optional lines in
`.env.bootstrap` (see `.env.bootstrap.example` for the template).

> Do not `source .env` after bootstrap — `SUBUMBRA_ADAPTER_REGISTRY` is a JSON blob that Bash mangles.

## 5. Run Bootstrap

Subumbra bootstrap supports two operator paths. Choose one:

### 5a. Optional security check: verify source integrity before entering secrets

This step is optional, but recommended if you want extra confidence that the
checkout you are about to bootstrap has not drifted in obvious or risky ways
before you paste in Cloudflare or provider credentials.

Run:

```bash
./scripts/subumbra-verify --verbose
```

This verifies things like:

- the Git worktree state
- unexpected drift in sensitive source files
- basic manifest / local state shape
- obvious leftover bootstrap secrets in local files

It is a read-only integrity check. It does **not** print your secret values or
send anything to Cloudflare.

`./bootstrap.sh` runs `./scripts/subumbra-verify --preflight` automatically
before it reads `.env.bootstrap`, prompts for secrets, or starts the bootstrap
container. This check validates the Git worktree, sensitive source files,
manifest shape, optional key-state shape, and obvious bootstrap-secret residue
without printing secret values.

For a stricter release-style check, use:

```bash
SUBUMBRA_REQUIRE_SIGNED_TAG=1 ./scripts/subumbra-verify --source-only
```

Current alpha tags are lightweight, so strict signed-tag mode will fail until a
future release uses signed annotated tags. By default, unsigned branches and
lightweight tags are warnings so development and council branches remain usable.

Once signed annotated releases are in use, operators can import the published
release public key from [docs/release-signing-key.pub](release-signing-key.pub)
and use that as part of their local tag-verification setup.

For read-only live Worker drift verification after install, use a Cloudflare API
token with **Workers Scripts: Read** plus `CF_ACCOUNT_ID`:

```bash
export CF_API_TOKEN=...
export CF_ACCOUNT_ID=...
./scripts/subumbra-verify --cloudflare
```

### 5b. Interactive RAM-only (recommended for first-time setup)

```bash
./bootstrap.sh
```

All secrets are entered at the terminal and held in RAM only — nothing sensitive
is written to disk. The wizard walks through the following steps:

**Step 1 — Cloudflare credentials**

`CF_API_TOKEN` and `CF_ACCOUNT_ID` from your Cloudflare account. Both inputs
are hidden and not stored anywhere after bootstrap.

```
Cloudflare API token: <hidden>
Cloudflare account ID: <hidden>
```

**Step 1b — Optional Cloudflare runtime credentials**

If you use Cloudflare Tunnel and/or Cloudflare Access, the wizard can also
collect:

- `TUNNEL_TOKEN`
- `CF_ACCESS_CLIENT_ID`
- `CF_ACCESS_CLIENT_SECRET`

These are optional. Leave them blank to skip, or provide them now so bootstrap
writes them into `.env` automatically.

**Step 1c — Optional Cloudflare auto-provisioning**

If you want bootstrap to create Cloudflare resources for you instead of using
BYOC runtime secrets, the wizard can also collect:

- `CF_ZONE_ID`
- `CF_TUNNEL_HOSTNAME`
- optional naming overrides for Tunnel / Access resources

Use this only when you want bootstrap-managed Cloudflare lifecycle. If you
already have a Tunnel token or Access service token, BYOC remains valid.

**Step 2 — Worker name**

The wizard shows the current default (read from `CF_WORKER_NAME` or inferred
from `CF_WORKER_URL` in `.env`, otherwise `subumbra-proxy`). Press Enter to
accept it or type a new name. This becomes the Cloudflare Worker script name
and is saved to `.env` after a successful bootstrap.

```
Cloudflare Worker name [default: subumbra-proxy] — press Enter to use default, or type a new name:
  >
```

**Step 3 — Per-key provider secrets**

For each key defined in `subumbra.yaml`, the wizard shows the `key_id`,
`provider`, and `secret_ref` label, then asks whether to provision it in this
session. Entering `n` skips the key without aborting.

```
Key: 'anthropic_prod'  provider='anthropic'  secret_ref='ANTHROPIC_KEY'
Provision a secret for this key in this session? [Y/n]:
```

If you answer Y, you are prompted to enter the API key twice (hidden, for
confirmation). A mismatch loops back to re-entry.

```
secret or API key for key_id 'anthropic_prod' (ANTHROPIC_KEY): <hidden>
same secret again to confirm for key_id 'anthropic_prod': <hidden>
```

If a `secret_ref` value is already present in the environment, the wizard skips
the prompt for that key and uses the existing value automatically.

At least one key must be accepted or bootstrap aborts.

**Step 4 — Automated provisioning**

Once credentials and secrets are collected the wizard runs without further
input:

1. Deploys the Cloudflare Worker and pushes adapter tokens and HMAC key as CF secrets
2. Calls the one-shot `/setup/keygen` Worker endpoint — Cloudflare generates the RSA-4096 key pair inside the Durable Object and returns only the public key; the private key never leaves Cloudflare
3. Encrypts each provider API key locally using the returned public key (AES-256-GCM with a per-key DEK wrapped by RSA-OAEP) and writes the V3 envelope records to `keys.json`
4. Publishes policy and key metadata to Cloudflare KV
5. Writes all generated runtime values (`SUBUMBRA_TOKEN_*`, `CF_WORKER_URL`, `CF_WORKER_NAME`, etc.) into `.env`
6. Deletes the transient `SUBUMBRA_SETUP_TOKEN` from Cloudflare secrets
7. Starts the core stack with `docker compose up -d --force-recreate` and prints an adapter token summary

If a previous bootstrap left Cloudflare vault or KV state behind, the wizard
stops and asks for explicit confirmation before wiping it. Pass `--nuke` to
skip that prompt in non-interactive automation only when you intend a full reset.

### 5c. Automation path (`.env.bootstrap`)

Use this path if you already have provider keys in a file, are scripting
installation, or are migrating from an existing deployment.

```bash
cp .env.bootstrap.example .env.bootstrap
# edit .env.bootstrap — see variable reference below
./bootstrap.sh
```

**`.env.bootstrap` variable reference:**

```bash
# ── Cloudflare (required) ─────────────────────────────────────────────────────
CF_API_TOKEN=REPLACE_ME       # API token with Workers Scripts: Edit and Workers KV Storage: Edit
CF_ACCOUNT_ID=REPLACE_ME      # your Cloudflare account ID
CF_WORKER_NAME=subumbra-proxy # the Cloudflare Worker script name to deploy

# Optional Cloudflare auto-provision inputs
# CF_ZONE_ID=REPLACE_ME
# CF_TUNNEL_NAME=subumbra-proxy-tunnel
# CF_TUNNEL_HOSTNAME=subumbra.example.com
# CF_ACCESS_APP_NAME=subumbra-proxy-worker-access
# CF_SERVICE_TOKEN_NAME=subumbra-proxy-service-token

# Optional BYOC runtime credentials
# TUNNEL_TOKEN=REPLACE_ME
# CF_ACCESS_CLIENT_ID=REPLACE_ME
# CF_ACCESS_CLIENT_SECRET=REPLACE_ME

# ── Bootstrap tuning (optional) ───────────────────────────────────────────────
TOKEN_TTL_DAYS=365            # how long adapter tokens are valid before expiry (see note below)

# ── Provider secrets ──────────────────────────────────────────────────────────
# One line per secret_ref declared in subumbra.yaml.
# The name must match secret_ref exactly.
OPENAI_KEY=REPLACE_ME
ANTHROPIC_KEY=REPLACE_ME
# add more as needed to match your subumbra.yaml
```

**`TOKEN_TTL_DAYS` — adapter token lifetime**

Adapter tokens (the credentials apps use to call `subumbra-keys`) are stamped
with an `issued_at` and `expires_at` at bootstrap time. `subumbra-keys` checks
expiry on every request — once a token expires it returns a 403 and the app can
no longer fetch encrypted records. The default is 365 days.

To renew expired tokens, re-run `./bootstrap.sh` (full bootstrap). This
generates fresh tokens with a new TTL window and restarts the stack.

> **Note:** TTL enforcement is implemented but has not yet been validated
> end-to-end with an actual expiry event. The simplest way to test it is to set
> `TOKEN_TTL_DAYS=1`, wait for expiry, and confirm requests are rejected with
> `adapter_expired`. Alternatively, manually set `expires_at` to a past
> timestamp inside `SUBUMBRA_ADAPTER_REGISTRY` in `.env` and restart
> `subumbra-keys`.

> **TTL and the interactive wizard:** The wizard does not prompt for
> `TOKEN_TTL_DAYS` — it reads the value from the environment only. To use a
> non-default value with the interactive path, set it before running bootstrap:
> `TOKEN_TTL_DAYS=180 ./bootstrap.sh`.

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
grep -E '^(SUBUMBRA_TOKEN_|CF_WORKER_URL|CF_WORKER_NAME|PROBE_ALLOWED_KEYS|UI_ALLOWED_KEYS)' .env
```

`bootstrap.sh` writes the generated Subumbra runtime values directly into `.env`:

- `SUBUMBRA_ADAPTER_REGISTRY`
- per-app tokens such as `SUBUMBRA_TOKEN_LITELLM` and `SUBUMBRA_TOKEN_OPENWEBUI`
- `SUBUMBRA_TOKEN_PROXY` for proxy transport and explicit compatibility/simple mode
- `SUBUMBRA_TOKEN_UI`
- `SUBUMBRA_HMAC_KEY`
- `CF_WORKER_URL`
- `CF_WORKER_NAME`
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
full re-bootstrap with the original `subumbra.yaml` and `.env.bootstrap`
inputs. See the recovery section in the
[operator guide](operator-guide.md).

## 9. Bootstrap And Day-2 Command Reference

Use this section after Subumbra is already installed and you want a quick
reference for the main bootstrap and maintenance commands.

If you want bootstrap to create Tunnel / DNS / Access resources for you, also
prepare:

- `CF_ZONE_ID`
- `CF_TUNNEL_HOSTNAME`
- one expanded `CF_API_TOKEN` that covers Worker deploy, KV edit, Tunnel
  lifecycle, DNS edit for the selected zone, and Access app / policy /
  service-token lifecycle

The interactive bootstrap wizard prompts for these values when needed. Treat
`CF_API_TOKEN` and `CF_ACCOUNT_ID` as bootstrap/deploy authority for Worker, KV,
and secret changes; keep them separate from any persistent runtime secrets you
enable later. Subumbra does **not** retain `CF_API_TOKEN` or `CF_ACCOUNT_ID` in
`.env`.

**CF credentials required** (deploys to or reads from Cloudflare):

- `./bootstrap.sh` — full bootstrap; deploys Worker, pushes KV and secrets
- `./bootstrap.sh --nuke` — destructive re-bootstrap; resets Cloudflare vault state first
- `./bootstrap.sh --provision <key_id>` — targeted key repair; pushes KV entry for one key
- `./bootstrap.sh --push-registry` — syncs local `keys.json` state to Cloudflare KV
- `./bootstrap.sh --revoke-key <key_id>` — removes key from live KV (omit `--offline` flag)
- `./bootstrap.sh --add-adapter <key_id> <adapter_id>` — re-encrypts and pushes updated policy
- `./bootstrap.sh --revoke-adapter <key_id> <adapter_id>` — re-encrypts and pushes updated policy
- `./bootstrap.sh --publish-policy <key_id>` — republishes a key's policy and adapters to KV

**CF credentials not required** (local operations only):

- `./bootstrap.sh --rotate` — re-encrypts using the on-disk RSA public key
- `./bootstrap.sh --upgrade` — rebuilds Docker images and recreates containers
- `./bootstrap.sh --revoke-key <key_id> --offline` — marks key revoked in `keys.json` only; run without `--offline` afterward to sync KV

**CF credentials not required — runtime credential rotation only:**

- `./bootstrap.sh --update-tunnel` — update `TUNNEL_TOKEN` in `.env`
- `./bootstrap.sh --update-access` — update `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` in `.env`
- `./bootstrap.sh --nuke-cloudflare` — deletes Cloudflare-managed Tunnel / DNS / Access resources tracked in `data/cf-resources.json`

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

## Next

- [docs/provider-templates.md](provider-templates.md)
- [docs/cloudflare-setup.md](cloudflare-setup.md)
- [docs/subumbra-testing.md](subumbra-testing.md)
- [docs/apps/litellm/install.md](apps/litellm/install.md)
- [docs/operator-guide.md](operator-guide.md)
- [docs/subumbra-developer.md](subumbra-developer.md)
- [docs/integration-recipes.md](integration-recipes.md)
