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

Preferred rule: push the branch to GitHub before asking another LLM to test it.
That keeps the VPS test target tied to a real commit SHA instead of a local-only
workspace state.

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

For council verification reports, always record:

- VPS path under test
- branch name
- commit SHA
- whether the checkout was clean or required a temporary staging path

Preferred verification rule:

- use `/opt/subumbra` for normal VPS verification, or
- use `./scripts/council/clean-run.sh` for isolated fresh-state proof

Do not bounce casually between `/opt/subumbra` and ad hoc `~/subumbra-*test`
checkouts in the same verification attempt. Pick one path, record it, and keep
the run self-consistent.

### If the VPS cannot pull the branch cleanly

Use this only as a fallback when the branch is not yet reachable from GitHub or
when you need to test a local commit exactly as-built.

1. Create a local bundle:

```bash
git bundle create subumbra-round.bundle <branch-name>
```

2. Copy the bundle to the VPS:

```bash
scp subumbra-round.bundle subumbra:/tmp/
```

3. On the VPS, fetch and check out a one-off staging branch in a one-off path:

```bash
ssh subumbra
mkdir -p ~/subumbra-stage
cd ~/subumbra-stage
git fetch /tmp/subumbra-round.bundle <branch-name>:<vps-test-branch>
git checkout <vps-test-branch>
```

Document this in the verification report as a staging workaround, and delete the
staging checkout after the run. Do not reuse long-lived `~/subumbra-r41test`
style directories across verifiers.

### If bootstrap files or harness files are missing on the VPS

If a full smoke test requires live bootstrap inputs or the council harness is
not present in the VPS checkout, use `scp` deliberately and document it:

```bash
scp .env.bootstrap_bak subumbra:/tmp/
scp -r council scripts/council subumbra:subumbra-stage/
```

This is acceptable for verification when:

- the missing file is an operator input such as `.env.bootstrap_bak`, or
- the missing file is harness scaffolding needed to run official proof capture

Any such copy step must be logged in the verification report.

### Copy proof artifacts back into the local round folder

After a VPS proof run succeeds, copy back only the round-scoped proof and
clean-run logs if the branch-local repo does not already contain them:

```bash
mkdir -p council/<round>/runs
scp -r subumbra:/opt/subumbra/council/<round>/runs/<run-id> council/<round>/runs/
```

Or use the helper:

```bash
./scripts/council/fetch-run-artifacts.sh <round> <run-id>
```

If you want to fetch and then remove the remote run directory after confirming
the local copy:

```bash
./scripts/council/fetch-run-artifacts.sh <round> <run-id> subumbra /opt/subumbra --delete-remote
```

Do not copy `/tmp/subumbra-clean-run-*` workspaces back to your machine. Those
are disposable server-side scratch space and should be deleted by the harness
or manually purged if `--keep-workspace` was used for debugging.

### Optional round-local verification hooks

`scripts/council/verify.sh` is the round-agnostic core verifier. If a round
needs extra proof beyond the shared baseline, add one of these local hook files:

- `council/<round>/verify-round.sh`
- `council/<round>/verify-round-*.sh`

The core verifier will run any matching hook scripts after the shared checks and
capture each hook's stdout/stderr into the current run folder as:

- `council/<round>/runs/<run-id>/verify-round.log`
- `council/<round>/runs/<run-id>/verify-round-<name>.log`

Hook scripts should write any round-specific proof artifacts into the provided
run directory via the `VERIFY_ARTIFACT_DIR` environment variable.

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

## 3.5 Verification Workflow Policy

Use different lanes for different goals. Do not pay the full fresh-install cost
for every tiny edit, but do require one clean proof before asking other council
members to verify.

### Lane A — local development

Use this while implementing and iterating quickly.

- edit code locally
- run the narrowest checks that prove the changed behavior
- use `docker compose up -d --force-recreate` or `./scripts/council/reset.sh`
  when the running state must be refreshed
- do not run `clean-run.sh` for every small edit unless the round touches
  install, bootstrap, reset, or proof-capture behavior

### Lane B — pre-push certification

Use this before handing the branch to another verifier.

- finish the implementation locally
- run targeted local checks first
- if the round changes fresh-state behavior, user-facing install flow, or the
  verification harness, run:

```bash
./scripts/council/clean-run.sh --round <round-dir-name> --agent <your-name>
```

> **Precondition:** local `clean-run.sh` fails immediately if any of
> `subumbra-keys`, `subumbra-proxy`, or `subumbra-ui` are already running.
> Stop the local stack first: `docker compose down` before running locally.
> VPS clean-run is the preferred lane for this round anyway — use Lane C.
>
> **Image rebuild:** if this round changed any image-built service
> (`bootstrap/`, `ui/`, `subumbra-keys/`, `subumbra-proxy/`), pass
> `--build <service>` so the workspace rebuilds from current source.
> Otherwise the clean-run uses whatever image is cached on the host.
>
> **Failing clean-run artifacts:** a failing `verify.sh` step still produces
> a run folder (e.g. `runs/claude-20260416T181938/`). Fetch it alongside the
> clean-run wrapper folder — both are useful for diagnosis.

- fix any issues found by the clean run
- rerun until the fresh-state path is clean
- then push the branch

This is the "do not waste the verifier's time" gate.

Run a local clean run before push when the round changes any of:

- `bootstrap/`
- `post-bootstrap.sh`
- `scripts/council/reset.sh`
- `scripts/council/verify.sh`
- `scripts/council/clean-run.sh`
- `docker-compose.yml`
- docs that claim exact install or verification steps
- token/bootstrap/fresh-install behavior

### Lane C — VPS verification

Use the VPS for the checks that cannot be reproduced credibly on the local
machine, especially real-app or real-environment validation.

- pull the branch on `/opt/subumbra`
- run one fresh-state `clean-run.sh` for official verification when the round
  requires certification-style proof
- if that first clean run exposes a small fix, patch locally, push, pull, then
  use `reset.sh` + `verify.sh` for focused follow-up reruns or diagnostics
- rerun VPS `clean-run.sh` only if the fix changed bootstrap, install, reset,
  or fresh-state behavior again

Examples of VPS-only proof:

- Open WebUI cutover
- n8n workflow execution
- standalone LiteLLM coexistence proof
- any host-specific or multi-app validation not available locally

### Practical default

Use this default sequence unless the round explicitly requires something else:

1. Implement locally.
2. Run fast local checks.
3. If the round touches fresh-state/install/harness behavior, run local
   `clean-run.sh`.
4. Push the branch only after that path is clean.
5. Pull on the VPS.
6. Run one fresh VPS `clean-run.sh` for official verification.
7. Use `reset.sh` + `verify.sh` only for focused follow-up reruns unless the
   fix changed fresh-state behavior again.

---

## 4. Full Bootstrap Sequence

Use after any change to bootstrap code, token rotation, or a clean reset:

```bash
docker compose --profile bootstrap build bootstrap   # only if bootstrap code changed
./bootstrap.sh
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
> docker run --rm -v subumbra_keys_data:/data alpine rm /data/kv-config.json
> ```
> Then rerun bootstrap.

---

## 5.5 VPS Sweep For Staging Leftovers

Use this on the VPS when previous verification attempts left behind one-off
staging directories, clean-run temp workspaces, or Docker resources tied to old
staging project names.

Inspect first:

```bash
./scripts/council/vps-sweep.sh
```

Purge the scoped leftovers:

```bash
./scripts/council/vps-sweep.sh --purge
```

Scope of this helper:

- `~/subumbra-stage`
- `~/subumbra-r41test*`
- `/tmp/subumbra-clean-run-*`
- Docker containers, networks, and volumes labeled with compose projects
  `subumbra-clean-run`, `subumbra-stage`, or `subumbra-r41test`

This helper is intended for verification leftovers only. It does not target the
normal `/opt/subumbra` checkout or a standard long-lived stack unless those
resources were started under one of the scoped staging project names above.

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
./bootstrap.sh
docker compose up -d --force-recreate
```

Re-enter every key you want to keep. Omitted keys are removed from the registry.

### Token drift recovery

If containers are still using stale tokens after bootstrap:

```bash
docker compose up -d --force-recreate
```

### Emergency adapter expiry (subumbra-keys-side only)

```bash
./scripts/subumbra-expire-adapter.sh <adapter_id>
docker compose up -d --force-recreate subumbra-keys
```

This blocks new subumbra record fetches for that adapter. It does **not** revoke
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
Docker-internal service name (`http://subumbra-ui:8080`), not `localhost:6563`.
The UI binds to `127.0.0.1:6563` on the host but cloudflared inside the Docker
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
7. If I used `scp`, bundle staging, or a temporary VPS checkout, did I log it?
8. Did I copy PASS proof artifacts back into the local round folder?
