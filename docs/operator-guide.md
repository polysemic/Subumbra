# Subumbra Operator Guide

This guide covers day-to-day operation of a running Subumbra stack — sessions, key management, health monitoring, recovery, and security. For initial setup, start with [docs/subumbra-install.md](subumbra-install.md).

---

## Table of Contents

1. [Sessions — opening and managing access](#1-sessions--opening-and-managing-access)
2. [Key management](#2-key-management)
3. [Health and monitoring](#3-health-and-monitoring)
4. [Dashboard (UI)](#4-dashboard-ui)
5. [Security model](#5-security-model)
6. [Recovery](#6-recovery)
7. [Verification and integrity](#7-verification-and-integrity)
8. [Advanced: templates and policy](#8-advanced-templates-and-policy)
9. [Internal timing reference](#9-internal-timing-reference)

---

## 1. Sessions — opening and managing access

After setup, Subumbra is **locked by default** — no keys are handed out even to apps you've configured. Sessions are how you temporarily lift the lockdown. When no session is active, any request to fetch a key returns `system_locked`.

### Opening a session

```bash
./bootstrap.sh --session start --ttl 8h --adapters all
```

If you run this on a terminal without `--ttl` and `--adapters`, an interactive wizard guides you through the choices.

**All available options:**

| Option | Required | What it does |
|--------|----------|-------------|
| `--ttl <duration>` | Yes | How long to stay open. Format: `30m`, `2h`, `8h`, `1d` etc. |
| `--adapters <csv\|all>` | Yes | Which apps to allow — `all`, or a comma-separated list like `litellm,openwebui` |
| `--keys <csv\|all>` | No | Which key IDs to allow. Defaults to `all` if omitted. |
| `--name <label>` | No | A human-readable label shown in session history. |
| `--max-queries <n>` | No | Auto-close the session after this many requests. |

**Opening a session requires Cloudflare credentials** (`CF_API_TOKEN` and `CF_ACCOUNT_ID`) because it writes session gates to Cloudflare KV. This is an added security measure to prevent sessions from being created or terminated by bad actors. Supply them in your shell before running.

### Checking and closing sessions

```bash
./bootstrap.sh --session status       # current lockdown state + all active session details
./bootstrap.sh --session list         # recent session history
./bootstrap.sh --session end          # close the active session (picker shown if multiple)
./bootstrap.sh --session end --all    # close every active session immediately
./bootstrap.sh --session end <id>     # close one specific session by its session ID
```

### Multiple concurrent sessions

You can have more than one session open at the same time. Each session must have a non-overlapping scope — if a new session's `(adapter, key_id)` pairs overlap with an already-active session, Subumbra rejects it before writing anything to Cloudflare.

### Keeping things open for a home lab

There's no permanent "always open" mode. If you want something effectively always-on, open a long session (`--ttl 30d`) and renew it before it expires. Be aware that a longer TTL means a longer window of exposure if something goes wrong.

### Session visibility in the dashboard

The dashboard shows the current locked or active state, and lists all active sessions with their adapter/key scope, TTL remaining, and query count. The `GET /sessions` endpoint on `subumbra-keys` also returns this data for adapters with `can_read_stats=true`.

---

## 2. Key management

### Rotating a key

If a provider API key is compromised or just due for rotation, re-encrypt it with a new value using the existing on-disk RSA public key — no Cloudflare credentials needed:

```bash
./bootstrap.sh --rotate
```

The wizard will ask which key to rotate and prompt for the new secret value.

### Revoking a key

Remove a key from active use:

```bash
./bootstrap.sh --revoke-key <key_id>
```

This marks the key as revoked, removes the live `key:<id>` entry from Cloudflare KV, and prevents future `--push-registry` runs from resurrecting it.

If you don't have Cloudflare credentials available right now, use `--offline` to update `keys.json` locally first, then sync to Cloudflare later:

```bash
./bootstrap.sh --revoke-key <key_id> --offline     # local update only
./bootstrap.sh --revoke-key <key_id>               # run again later to sync KV
```

### Pausing and unpausing a key

Pause temporarily blocks a key from being used without revoking it. Unlike session management, pause/unpause goes directly through the Worker's management API:

```bash
# Pause — use the management token, not an adapter token
curl -sS -X POST https://<worker-url>/manage/key/pause \
  -H "Authorization: Bearer $SUBUMBRA_MANAGEMENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key_id": "<key_id>"}'

# Unpause
curl -sS -X POST https://<worker-url>/manage/key/unpause \
  -H "Authorization: Bearer $SUBUMBRA_MANAGEMENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key_id": "<key_id>"}'
```

Allow up to 90 seconds for Cloudflare KV propagation after pause/unpause before treating a stale result as a failure.

`SUBUMBRA_MANAGEMENT_TOKEN` is in your `.env` after bootstrap. It is separate from adapter tokens — treat it as a privileged operator secret. If you ever lose both the live Worker copy and your local `.env` copy, run a full bootstrap to reissue it.

### Adding or removing an app from a key

To grant a new app access to a key, or remove an app's access:

```bash
./bootstrap.sh --add-adapter <key_id> <adapter_id>
./bootstrap.sh --revoke-adapter <key_id> <adapter_id>
```

Both commands re-encrypt the key with the updated policy and push the new binding to Cloudflare KV. They also attempt to update the `adapters` line in your `subumbra.yaml` automatically — this works for the standard single-line format. If your manifest uses a multiline adapters block, bootstrap warns and leaves the file alone; update it manually.

### Republishing a key's policy

If you change policy fields in `subumbra.yaml` (rate limits, response patterns, intent settings) without changing the underlying secret, push the updated policy to Cloudflare:

```bash
./bootstrap.sh --publish-policy <key_id>
```

How it works under the hood: if you only changed `velocity`, `intent`, or `response.deny_patterns` fields, the key is not re-encrypted — just the policy metadata is updated. If you changed anything in `allow.*`, `target.host`, or `auth.*`, the key is fully re-encrypted to bind the new policy cryptographically.

### Syncing everything to Cloudflare KV

If local `keys.json` state and Cloudflare KV get out of sync (e.g. after a failed bootstrap or a recovery step), push everything from local state:

```bash
./bootstrap.sh --push-registry
```

This preserves any `paused: true` flags already set on live keys — it won't re-enable a key that was paused.

### SSH key custody and signing

Subumbra can also hold `type: ssh_key` records in `keys.json`. In this round:

- `key_source: generated` creates an ed25519 keypair inside the Cloudflare vault
- `key_source: provided` accepts an **unencrypted** OpenSSH ed25519 private key
- `GET /keys/<key_id>` returns SSH metadata only, never ciphertext fields
- `POST /ssh/sign` signs a challenge blob only when a matching session is open

The signing route uses the same adapter-token and active-session model as the
core proxy path. If no matching session is open, `/ssh/sign` returns
`system_locked`.

Encrypted / passphrase-protected SSH private keys are not supported in this
round. Provide an unencrypted OpenSSH ed25519 key if you are using
`key_source: provided`.

For the day-to-day SSH workflow, socket setup, host-scoped SSH config, and
GitHub deploy-key usage, use the dedicated guide:

- [docs/ssh-guide.md](ssh-guide.md)
- [docs/apps/github/install.md](apps/github/install.md)

### Checking drift

Compare your manifest against what's actually deployed:

```bash
./bootstrap.sh --status
```

This prints `UP_TO_DATE`, `POLICY_DRIFT`, `NOT_DEPLOYED`, or `REVOKED` for each key. No Cloudflare credentials needed — it reads from local state.

### Repairing a single key after a partial bootstrap

If one key failed during bootstrap and the rest succeeded, you don't need to redo everything:

```bash
./bootstrap.sh --provision <key_id>
```

This re-runs the keygen and encrypt steps for just that key. It requires that `CF_WORKER_URL` and `SUBUMBRA_SETUP_TOKEN` are still live in your `.env`, and that the matching `public_key*.pem` exists on the data volume. If the public key file is gone, you'll need a full bootstrap.

> **Note:** After a successful full bootstrap, `SUBUMBRA_SETUP_TOKEN` is deleted from Cloudflare. The copy in your `.env` may no longer work for new `--provision` runs in a fresh install. For mid-repair multi-key provisioning, keep the file intact until you're done.

---

## 3. Health and monitoring

### Checking the stack

```bash
docker compose ps                           # all services running?
curl -sS http://127.0.0.1:10199/health     # proxy health + worker auth status
curl -sS http://127.0.0.1:6563/api/status  # dashboard status
```

### Proxy health — `worker_auth` values

The proxy health endpoint returns a `worker_auth` field in addition to `status`:

| Value | Meaning |
|-------|---------|
| `ok` | The proxy recently verified the Worker successfully. Everything is working. |
| `stale` | The auth ping cache expired but no new ping has run yet. Often transient after restarts — wait a moment and check again. |
| `token_mismatch` | The Worker rejected the proxy's auth token with a 401. The adapter token in the proxy's environment doesn't match what Cloudflare holds. This is permanent until tokens are re-synchronized (re-run `./bootstrap.sh --nuke` or push new tokens via wrangler). **Not the same as `stale`.** |
| `unreachable` | The proxy can't reach the Worker at all. Check Cloudflare status and your network. |

> **Cloudflare Access note:** CF Access header enforcement happens at the Cloudflare edge. If you use CF Access and misconfigure it, errors can look like `worker_auth` failures even when your VPS stack is healthy. If proxy health looks wrong but your services are up, check your Tunnel and Access settings first.

### Checking for policy drift

```bash
./bootstrap.sh --status
```

Compares your manifest against deployed records and prints the status of each key. Good to run after any manifest edit to confirm everything is in sync.

### Checking which apps are configured

```bash
./bootstrap.sh --list-adapters     # lists all integrations, token status, and authorized key IDs
./bootstrap.sh --show <adapter_id> # paste-ready config for a specific app (e.g. --show litellm)
./bootstrap.sh --list-key-ids      # lists all key IDs in your manifest
```

---

## 4. Dashboard (UI)

The read-only dashboard is at `http://127.0.0.1:6563`. It shows active keys, request counts, last access times, session state, and policy metadata.

### Authentication modes

| Mode | When to use | How to configure |
|------|-------------|-----------------|
| **Cloudflare Tunnel + Access** (recommended) | You route the UI through a Cloudflare Tunnel | Leave `UI_USERNAME` and `UI_PASSWORD` unset in `.env` |
| **HTTP Basic Auth** | Direct access without a Tunnel | Set both `UI_USERNAME` and `UI_PASSWORD` in `.env`, then restart the UI container |

Brute-force rate limiting applies in Basic Auth mode (5 failures per 60-second window per IP). Setting one of `UI_USERNAME` / `UI_PASSWORD` without the other is an error — the UI won't start.

---

## 5. Security model

### What's protected and what's not

| Protected | How |
|-----------|-----|
| API keys at rest on your server | Encrypted immediately; only ciphertext is stored locally |
| API keys in app configs | Apps only ever see an adapter token, never your real key |
| Per-app access | Each app has its own token — revoke one without affecting others |
| Policy enforcement | Which paths, methods, and providers each key can serve is policy-bound |
| Cloudflare KV tampering | Policy and encryption are cryptographically bound — editing KV directly doesn't help an attacker |

| Not protected | Notes |
|---------------|-------|
| Cloudflare itself | The private key lives inside Cloudflare — Cloudflare is in the trust boundary |
| Full server compromise | An attacker with root access can read running container memory and environment variables |
| Billing and rate limits | Subumbra doesn't cap spend — set limits at the provider level |

### Policy-bound encryption

When you bootstrap a key, the rules you define — which apps can use it, which paths are allowed, which provider it routes to — are **cryptographically bound** to the encrypted key. This is sometimes called AAD (Additional Authenticated Data).

What this means practically: if someone gained access to your Cloudflare KV and tried to swap in a permissive policy, the Worker would detect that the policy no longer matches the encryption seal and refuse to decrypt the key. No silent downgrade is possible.

The trade-off: whenever you change a foundational policy field (`allow.*`, `target.host`, `auth.*`), Subumbra must re-encrypt the key with the new policy hash. Commands like `--add-adapter`, `--revoke-adapter`, and `--publish-policy` handle this automatically.

### Container environment and secrets

Subumbra runtime secrets (`SUBUMBRA_ADAPTER_REGISTRY`, `SUBUMBRA_HMAC_KEY`, `SUBUMBRA_TOKEN_*`) are injected from your host `.env` into container environment variables. Any process inside a container can read them.

**Practical mitigations:**
- Set your `.env` file to `600` permissions (`chmod 600 .env`)
- Keep Docker images minimal and don't install extra tools in running containers
- Avoid `docker exec` into containers in production unless actively debugging
- Treat the container filesystem and memory as within reach of anyone who can run workloads on the same host

---

## 6. Recovery

### Stack not starting after bootstrap

Check that `.env` has the expected values:

```bash
grep -E '^(SUBUMBRA_TOKEN_|CF_WORKER_URL|CF_WORKER_NAME)' .env
```

If values are missing, the bootstrap may have exited before writing them. Check the bootstrap output for errors, then re-run.

### Half-state recovery (bootstrap stopped midway)

| Situation | What to do |
|-----------|------------|
| `keys.json` not updated (bootstrap failed before writing) | Data volume may be out of sync with Cloudflare. Run `./bootstrap.sh --nuke` and re-bootstrap from scratch. |
| `keys.json` updated but Cloudflare KV is missing or partial | Re-publish from local state: `./bootstrap.sh --push-registry` |

There is no checkpoint file to clean up — Subumbra doesn't write intermediate state to disk during bootstrap.

### Vault loss (Cloudflare-side data gone)

If Cloudflare Durable Object vault state is lost and you initialize a fresh vault, **ciphertext produced under the previous vault cannot be decrypted by the new one.** The private key was generated inside Cloudflare and never touched your server — there's no local backup of it.

The supported recovery path is a full re-bootstrap with your original inputs:

1. Keep your `subumbra.yaml` and any provider secrets accessible
2. Run `./bootstrap.sh --nuke` to reset Cloudflare state
3. Re-bootstrap and restart the stack

Cloudflare may offer Durable Object restore features at the platform level, but those are outside Subumbra's control — verify independently before depending on them.

### Updating runtime tunnel or access credentials

If your Cloudflare Tunnel token or Access service token changes, update `.env` without a full re-bootstrap:

```bash
./bootstrap.sh --update-tunnel    # update TUNNEL_TOKEN in .env
./bootstrap.sh --update-access    # update CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET in .env
```

### Rebuilding images after a code update

After `git pull`, rebuild images and restart containers without touching keys or secrets:

```bash
./bootstrap.sh --upgrade
```

This does not re-run Cloudflare bootstrap, rotate keys, or change `.env`.

### Removing Cloudflare-managed resources

If you want to tear down Cloudflare Tunnel, DNS, and Access resources that bootstrap created:

```bash
./bootstrap.sh --nuke-cloudflare
```

This only removes resources tracked in `data/cf-resources.json` — things bootstrap created on your behalf. It does not affect your Worker, KV, or encrypted key records.

---

## 7. Verification and integrity

### Source integrity check

Before entering secrets or running bootstrap, you can verify the local checkout hasn't drifted in obvious ways:

```bash
./scripts/subumbra-verify --verbose
```

`./bootstrap.sh` runs a lighter preflight version of this automatically before reading `.env.bootstrap` or prompting for secrets. It's read-only and sends nothing to Cloudflare.

For a stricter check against a signed release tag:

```bash
SUBUMBRA_REQUIRE_SIGNED_TAG=1 ./scripts/subumbra-verify --source-only
```

Current alpha releases use lightweight tags, so this will fail until signed annotated tags are in use. The default behavior warns on unsigned tags instead of failing hard.

### Worker deploy integrity check

After installation (or after a `git pull` and `--upgrade`), verify that what's actually running on Cloudflare matches the bundle hash recorded at deploy time:

```bash
export CF_API_TOKEN=...
export CF_ACCOUNT_ID="$(sed -n 's/^CF_ACCOUNT_ID=//p' .env)"
export CF_WORKER_NAME="$(sed -n 's/^CF_WORKER_NAME=//p' .env)"
./scripts/subumbra-verify-deploy
```

This requires a Cloudflare API token with `Workers Scripts: Read`. It's a read-only check — nothing is modified.

---

## 8. Advanced: templates and policy

### Using built-in provider templates

Instead of writing a full inline policy, you can use a named template for any supported provider:

`anthropic`, `openai`, `groq`, `gemini`, `deepseek`, `mistral`, `openrouter`, `together`, `xai`, `github`, `slack`, `sendgrid`

Templates are signed with the project's Ed25519 release key and verified before use. Bootstrap ships them inside the container — no network fetch occurs.

Basic template usage in `subumbra.yaml`:

```yaml
keys:
  - key_id: openai_prod
    provider: openai
    secret_ref: OPENAI_KEY
    adapters: [litellm]
    unique_vault: false
    template: openai
```

### Overriding a template field

Add an inline `policy` block alongside `template` to override specific fields. The `allow.adapters` list is always taken from the manifest's `adapters` field — you can't override it through the policy block:

```yaml
keys:
  - key_id: openai_prod
    provider: openai
    secret_ref: OPENAI_KEY
    adapters: [litellm]
    unique_vault: false
    template: openai
    policy:
      allow:
        max_body_bytes: 524288
```

### User-owned templates

Place `<name>.yaml` files in a `./templates/` directory next to your manifest. Bootstrap mounts that directory and checks it before the signed built-in catalog — so `./templates/openai.yaml` will shadow the built-in `openai` template. User-owned templates are **not** signature-verified. You own and trust them.

### Policy fields and re-encryption

Not all policy changes require re-encrypting the key. Understanding which ones do helps you pick the right day-2 command:

| Change type | Command to use |
|-------------|---------------|
| `velocity`, `intent`, `response.deny_patterns` | `--publish-policy <key_id>` (no re-encryption) |
| `allow.*`, `target.host`, `auth.*` | `--publish-policy <key_id>` (triggers re-encryption automatically) |
| Adding or removing an adapter | `--add-adapter` / `--revoke-adapter` (re-encrypts) |
| New provider secret value | `--rotate` |

### Request and response headers

- `allow.request_headers` — which adapter-supplied request headers are forwarded (after Subumbra strips internal and hop-by-hop headers)
- `response.allow_headers` — which upstream response headers are exposed back to the adapter

If omitted, current pass-through behavior is preserved. Anthropic requests require `anthropic-version` — add it here if you see missing-header errors. After changing either list, run `./bootstrap.sh --publish-policy <key_id>`.

### Unique vault vs shared vault

Each key record sets `unique_vault: true` or `unique_vault: false`:

- **Shared vault** (`false`): the key's DEK is wrapped by the shared `vault` Durable Object's RSA key. Simpler, and fine for most deployments.
- **Unique vault** (`true`): a dedicated `vault-<key_id>` Durable Object is created for this key. Stronger isolation — a compromise of one vault doesn't affect others — but uses more Cloudflare resources.

### Worker rate limits

The following Worker surfaces have per-minute rate limits. If you hit them, the Worker returns `429` with `rate_limit_exceeded_auth`:

- `GET /auth-ping`
- `POST /setup/keygen`
- `POST /internal/rotate`
- `POST /internal/vault-status`
- `POST /internal/vault-reset`
- `POST /manage/key/pause`
- `POST /manage/key/unpause`

These are intended for protection against automation abuse, not normal operational traffic.

---

## 9. Internal timing reference

These values are fixed in the current release (not env-tunable). Useful if you're diagnosing stale dashboard data or health signal lag.

| Item | Value |
|------|-------|
| Proxy Worker auth cache TTL | 60 seconds |
| Proxy Worker auth-ping HTTP timeout | 2 seconds |
| Dashboard `/api/status` poll interval | 30 seconds |
| Dashboard SSE `/api/events` heartbeat | 30 seconds between frames |
| `subumbra-keys` Compose healthcheck | interval: 30s, timeout: 5s, retries: 5, start_period: 10s |
| `subumbra-ui` Compose healthcheck | interval: 30s, timeout: 5s, retries: 3 |
| `subumbra-proxy` Compose healthcheck | interval: 30s, timeout: 5s, retries: 3 |

**Dashboard stats** (request counts, last access times) are aggregated from `subumbra-keys` SQLite audit events — not from per-Gunicorn-worker memory. This means they're consistent across restarts.

**Multiple dashboard tabs:** the SSE `/api/events` stream holds a thread for each open tab. With many tabs open simultaneously, you may notice the 30-second `/api/status` polling becomes slightly slower as threads compete.

**Rate-limit identity on localhost:** when the UI is accessed via `127.0.0.1:6563`, `request.remote_addr` is often the Docker bridge gateway IP, so all host-originated traffic shares one rate-limit bucket.
