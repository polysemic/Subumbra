# Scripts

> [!CAUTION]
> **Warning: Use at your own risk.**
> These scripts are primarily intended for use by the project owner and the automated council verification harness. While developers and operators may find them useful, they are provided **as-is** and without warranty. Some scripts perform destructive actions (like `fresh-start.sh`) or require specific environment states (live VPS, running Docker stack, Cloudflare credentials).
> **Always read the source code and usage blocks before running any script.**

---

## Operator & Developer Scripts

These scripts are intended for general maintenance and diagnostics of a Subumbra deployment.

### `fresh-start.sh`

Complete Subumbra teardown and Cloudflare cleanup. Destroys all Subumbra Docker
containers, named volumes, local `.env`, the Cloudflare Worker, and the KV
namespace. Does **not** touch app installs (LiteLLM, OpenWebUI, etc.),
`cloudflared` tunnel config, or the git repository itself.

```bash
./scripts/fresh-start.sh              # interactive — prompts at each step
./scripts/fresh-start.sh --force      # skip all confirmation prompts
./scripts/fresh-start.sh --no-cf      # skip Cloudflare teardown (Docker only)
./scripts/fresh-start.sh --dry-run    # print what would happen, do nothing
```

**Intended outcome:** clean slate ready for a fresh `./bootstrap.sh` run.

---

### `subumbra-expire-adapter.sh`

Immediately expires a named adapter token in `.env` by setting its `expires_at`
to the past. The adapter returns `403 adapter_expired` on the next request.
No re-bootstrap required.

```bash
./scripts/subumbra-expire-adapter.sh <adapter_id>
# example:
./scripts/subumbra-expire-adapter.sh litellm
```

**Intended outcome:** the named adapter can no longer fetch keys. Useful for
revoking a specific app's access without rotating all tokens.

---

### `subumbra-print-adapters.py`

Summarizes adapter tokens and their allowed `key_id`s from the repo-local `.env`.
When run in a TTY, prints full token values with a warning. When piped or
redirected, prints only env var names and key IDs (no token values).

```bash
python3 scripts/subumbra-print-adapters.py
```

**Intended outcome:** quick human-readable summary of which adapters exist and
which providers each one can reach.

---

### `subumbra-verify-deploy`

Verifies the live Cloudflare Worker bundle against the SHA-256 captured at
bootstrap time (`system-integrity.json`). Detects if the deployed Worker has
been modified or replaced since bootstrap.

```bash
export CF_API_TOKEN=...
export CF_ACCOUNT_ID=...
export CF_WORKER_NAME=subumbra-proxy
./scripts/subumbra-verify-deploy
```

**Intended outcome:** pass means the live Worker matches the bootstrap snapshot;
any mismatch is printed and the script exits non-zero.

---

### `subumbra-env-ingest.py`

Generates a draft `subumbra.yaml.proposed` and `env.bootstrap.proposed` from
one or more existing app `.env` files. Useful when migrating an existing
deployment or setting up a new manifest from known keys.

```bash
python3 scripts/subumbra-env-ingest.py <path/to/app.env> [<path/to/app2.env> ...]
```

**Intended outcome:** reviewable draft files you can inspect and rename before
running bootstrap. Does not write to `.env` or `subumbra.yaml` directly.

---

### `vps-user-provider-smoke.py`

Exercises every `key_id` in the running stack through the real transparent proxy
path. For each key, picks a valid non-expired adapter, sends one minimal live
request, and checks for HTTP 2xx. Reads state from running containers via
`docker compose exec` — no `.env` sourcing needed.

```bash
# Run from repo root on the VPS:
python3 scripts/vps-user-provider-smoke.py
```

**Intended outcome:** exit 0 only if every resolvable `(adapter, key_id)` pair
returns a successful response from the upstream provider. A failure means a
specific key or adapter is broken end-to-end.

---

### `sign-catalog.py`

Generates `bootstrap/templates/catalog.json` (SHA-256 hashes of all provider
template YAML files) and signs it with an Ed25519 private key, writing the
signature to `bootstrap/templates/catalog.sig`. Run this after modifying any
file in `bootstrap/templates/`.

```bash
python3 scripts/sign-catalog.py \
  --key-file council/catalog-release-key.pem \
  --templates-dir bootstrap/templates/
```

Prints the Ed25519 public key hex on success.

**Intended outcome:** updated `catalog.json` and `catalog.sig` that bootstrap
will accept when verifying template integrity.

---

## Council & Verification Scripts (`scripts/council/`)

These scripts power the "Council" verification harness. They are highly specialized for the project's multi-LLM review process and generally assume a VPS environment with specific SSH aliases (`ssh subumbra`).

### `council/verify.sh`

Runs the baseline verification checks plus any round-specific `verify-round.sh`
hook for a named council round. Writes structured artifacts (manifest, preflight
log, summary) to `council/<round>/runs/<run-id>/`.

```bash
scripts/council/verify.sh <round-dir-name>
# example:
AGENT=codex scripts/council/verify.sh round-45-policy-schema
```

**Intended outcome:** a pass/fail summary with artifacts for each check in the
baseline and round hook. Used to sign off a round before merge.

---

### `council/preflight.sh`

Polls Docker health status for all Subumbra containers until they are healthy
or a timeout is reached. Used as a gate before running verification checks.

```bash
scripts/council/preflight.sh
PREFLIGHT_TIMEOUT_SECONDS=120 scripts/council/preflight.sh
```

**Intended outcome:** exits 0 when all required containers report healthy; exits
1 if any container fails or the timeout expires.

---

### `council/clean-run.sh`

Runs a full isolated clean-run proof in a temp workspace on the local machine.
Builds images, bootstraps a fresh stack, runs verify, and tears down. Used to
confirm a round works from a clean state without touching the live stack.

```bash
./scripts/council/clean-run.sh
./scripts/council/clean-run.sh --round round-45-policy-schema --agent codex
./scripts/council/clean-run.sh --build subumbra-keys subumbra-proxy
./scripts/council/clean-run.sh --keep-workspace   # leave temp dir after run
```

**Intended outcome:** pass means the round installs and verifies cleanly from
scratch in an isolated environment. Artifacts are written to the round's
`runs/` directory.

---

### `council/vps-proof-run.sh`

Runs a live-VPS verification proof for a named round and branch. Syncs the
council directory to the VPS over SSH, runs the appropriate install or
existing-stack path, collects artifacts, and copies them back locally.

```bash
scripts/council/vps-proof-run.sh \
  --round round-45-policy-schema \
  --agent codex \
  --branch feature-branch \
  --mode existing-stack          # or fresh-install
```

**Intended outcome:** a full round verification run with artifacts, executed
against the real VPS deployment. Used for rounds that require live Cloudflare
integration.

---

### `council/reset.sh`

Stops and recreates the Subumbra Docker stack on the current machine. Optionally
rebuilds one or more service images before recreating.

```bash
scripts/council/reset.sh
scripts/council/reset.sh --build subumbra-keys
scripts/council/reset.sh --build subumbra-ui subumbra-proxy
```

**Intended outcome:** a fresh running stack with the current image and config
state, without running a full bootstrap.

---

### `council/capture-probe.sh`

Runs a single test command and records its stdout, stderr, exit code, and
metadata as a structured probe artifact. Used inside round hook scripts to
attach independent security or behavior probes to a verification run.

```bash
scripts/council/capture-probe.sh <probe-name> \
  --hypothesis "what this checks" \
  --expected "expected secure behavior" \
  --classification PASS \
  -- <command to run>
```

**Intended outcome:** a JSONL index entry and artifact files written under the
probe artifact directory. The probe wrapper always exits 0 unless `--fail-on-error`
is set.

---

### `council/fetch-run-artifacts.sh`

Copies a council verification run's artifacts from a remote VPS into the local
repo. Used after `vps-proof-run.sh` when artifacts were not automatically
copied back (e.g. after a partial run).

```bash
scripts/council/fetch-run-artifacts.sh <round> <run-id> [remote_host] [remote_repo]
scripts/council/fetch-run-artifacts.sh round-45-policy-schema codex-20260501T120000 subumbra /opt/subumbra
scripts/council/fetch-run-artifacts.sh round-45-policy-schema codex-20260501T120000 --delete-remote
```

**Intended outcome:** artifacts land in `council/<round>/runs/<run-id>/` locally.
Pass `--delete-remote` only after confirming the local copy is complete.

---

### `council/vps-sweep.sh`

Inspects or purges leftover council verification artifacts on the VPS — staging
directories, clean-run temp workspaces, and Docker resources tied to known
staging or clean-run compose project labels. Does **not** touch `/opt/subumbra`
or the normal live stack.

```bash
scripts/council/vps-sweep.sh           # list leftovers only
scripts/council/vps-sweep.sh --purge   # remove them
```

**Intended outcome:** a clean VPS between verification runs, without disturbing
the production stack.
