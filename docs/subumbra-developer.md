# Subumbra Developer Guide

*For repeated VPS testing, council rounds, clean resets, and operational
management. This is the deep-dive reference for contributors and council members.*

---

## 1. Branch Strategy

One branch per round or test effort.

```
main                    ← stable / known-good
VPS-Stabilization       ← active work branch (example)
round-42-topic          ← future round
```

Avoid long-lived generic `dev` branches. Round branches make it easy to answer:
what commit is the VPS actually running?

---

## 2. Local → GitHub → VPS Workflow

### Local (where code changes happen)

```bash
git checkout main && git pull --ff-only
git checkout -b round-42-topic
# make changes
git add <files>
git commit -m "Round 42: description"
git push -u origin round-42-topic
```

### VPS (pull + run + verify only)

```bash
ssh subumbra
cd /opt/subumbra
git fetch origin
git checkout round-42-topic
git pull --ff-only
git branch --show-current
git rev-parse --short HEAD   # confirm expected SHA
git status                   # must be clean
```

### Merge to main (only after VPS passes)

```bash
git checkout main
git pull --ff-only
git merge --ff-only round-42-topic
git push origin main
```

---

## 3. Rebuild / Restart Decision Tree

| What changed | Action |
|---|---|
| Docs only | Nothing |
| Mounted config / `.env` values | `docker compose up -d --force-recreate` |
| Image-built service code | `docker compose up -d --build --force-recreate` |
| Bootstrap / tokens / RSA key pair | Full bootstrap sequence (section 4) |

---

## 4. Full Bootstrap Sequence

Use after any change to bootstrap code, token rotation, or a clean reset:

```bash
docker compose --profile bootstrap build bootstrap   # only if bootstrap code changed
docker compose --profile bootstrap run --rm -it bootstrap
./post-bootstrap.sh
docker compose up -d --force-recreate
```

If `.env` does not exist yet:

```bash
cp .env.example .env
# set LITELLM_MASTER_KEY: openssl rand -hex 32
```

---

## 5. Full Reset — Clean Install From Scratch

Wipes all local state: containers, named volumes (keys.json, audit.db,
runtime.env, kv-config.json, public_key.pem), built images, and credential
files. Use when you want the server to behave exactly like a first-time install.

```bash
# Stop everything and remove named volumes
docker compose down --remove-orphans -v

# Remove built images
docker compose down --rmi all 2>/dev/null || true
docker rmi subumbra-bootstrap 2>/dev/null || true

# Remove local credential and runtime files
rm -f .env .env.bootstrap .env.bootstrap_bak
```

Then bootstrap fresh (section 4 above).

> **Cloudflare state is NOT wiped by this reset.**
>
> - The CF Worker, CF Secrets, and the KV namespace remain in your Cloudflare
>   account.
> - KV *content* (`provider_registry_v1`) is completely overwritten on every
>   bootstrap run — no leftovers from a previous set of keys.
> - Bootstrap always generates a fresh RSA key pair and pushes new CF Secrets,
>   so old ciphertext blobs from a wiped volume are unrecoverable by the new
>   Worker anyway.
>
> **Edge case:** if you delete the KV namespace in the CF dashboard without
> wiping the volume, the local `kv-config.json` holds a dead namespace ID.
> Fix:
> ```bash
> docker run --rm -v subumbra_forge_keys_data:/data alpine rm /data/kv-config.json
> ```
> Then rerun bootstrap.

---

## 6. Provider Registry Operations

### Add or update a built-in provider without redeploying the Worker

1. Update `worker/src/providers.json`
2. Push the updated registry to Cloudflare KV:

```bash
docker compose --profile bootstrap run --rm bootstrap --push-registry
```

Visibility window: ~90 seconds (KV `cacheTtl: 30` + CF eventual consistency).

### Add a custom provider permanently

Run the interactive wizard — it collects `target_host`, `auth_header`,
`auth_prefix`, and writes to `/app/data/custom-providers.json` on the volume.
Custom entries merge with built-ins on every `--push-registry` run.

### Minimal `.env.bootstrap` for `--push-registry` only

```text
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=subumbra-proxy
```

No provider API keys needed for registry-only publishes.

---

## 7. Rotation and Recovery

### Single-key rotation (one provider secret changed)

```bash
docker compose --profile bootstrap run --rm -it bootstrap --rotate
```

No service restart required after per-key rotation.

### Full re-bootstrap (new RSA key pair, new tokens)

```bash
docker compose --profile bootstrap run --rm -it bootstrap
./post-bootstrap.sh
docker compose up -d --force-recreate
```

Re-enter every key you want to keep. Omitted keys are removed from the registry.

### Token drift recovery

If `post-bootstrap.sh` warns about stale container tokens:

```bash
docker compose up -d --force-recreate
```

### Emergency adapter expiry (forge-side only)

```bash
./scripts/forge-expire-adapter.sh <adapter_id>
docker compose up -d --force-recreate subumbra-keys
```

This blocks new forge record fetches for that adapter. It does **not** revoke
Worker-side token authority. For full revocation, run full re-bootstrap.

---

## 8. Cloudflare Operational Notes

**Current observability defaults:**

```toml
[observability]
enabled            = true
head_sampling_rate = 1
```

Invocation logs and tracing are off by default (billable; enable only for
active debug sessions).

**Pricing references:**

- Workers Logs: `https://developers.cloudflare.com/workers/observability/logs/workers-logs/`
- Durable Objects: `https://developers.cloudflare.com/durable-objects/platform/pricing/`

**Tunnel routing note:** if the UI is exposed through cloudflared, route to the
Docker-internal service name (`http://subumbra-ui:8080`), not `localhost:8080`.
The UI binds to `127.0.0.1:8080` on the host but cloudflared inside the Docker
network resolves via Docker DNS.

---

## 9. Council Harness Reference

See [`docs/subumbra-testing.md`](./subumbra-testing.md) for harness usage,
evidence taxonomy, and the reporting template.

Council workflow rules: [`council/COUNCIL.md`](../council/COUNCIL.md)

Council prompt templates: [`council/COUNCIL_PROMPT.md`](../council/COUNCIL_PROMPT.md)

Fresh-session context files (read before starting council work):

1. `council/COUNCIL.md`
2. `council/COUNCIL_PROMPT.md`
3. `PROJECT_STATUS.md`
4. `CLAUDE.md`
5. `docs/council-memory.md`
6. `docs/project-memory.md`
7. Active round folder

---

## 10. Pre-Test Checklist

Before testing on the VPS, confirm:

1. What branch am I on locally?
2. Did I commit and push the changes?
3. Did the VPS pull that branch and SHA?
4. Did I rebuild/recreate if runtime code changed?
5. Is `git status` clean on the VPS?
6. Did I write down what passed or failed?
