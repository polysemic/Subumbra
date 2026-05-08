# Subumbra Operator Guide

*Operational reference for the live provider registry, sidecar, rotation,
recovery, and Cloudflare deployment defaults.*

## 1. Live Provider Registry

Provider validation now comes from Cloudflare KV rather than the Worker bundle.

### Adding a built-in provider without redeploying the Worker

1. Add the provider entry to root `providers.json`.
2. Run:

```bash
docker compose --profile bootstrap run --rm bootstrap --push-registry
```

3. No Worker redeploy is required for the new provider to become visible.

Expected visibility window:

- KV `cacheTtl: 30`
- plus Cloudflare KV eventual consistency
- about 90 seconds worst-case before every Worker isolate sees the new entry

### Adding a custom provider permanently

Run the interactive bootstrap wizard:

```bash
./bootstrap.sh
```

For a custom provider, the wizard now collects:

- `target_host`
- `auth_header`
- `auth_prefix`

Custom provider metadata is written to:

- `/app/data/custom-providers.json`

That file is merged with built-ins on every subsequent `--push-registry` run.

### Diagnostic access to the KV namespace ID

```bash
docker compose run --rm -u 0 -T subumbra-keys cat /app/data/kv-config.json
```

### Minimal `.env.bootstrap` for `--push-registry`

After full bootstrap has shredded the original automation file, a standalone
registry publish needs only:

```text
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=subumbra-proxy
```

No provider API keys are required for `--push-registry`.

## 2. Sidecar Startup

Start the sidecar stack with the normal project Compose file:

```bash
docker compose up -d --force-recreate subumbra-keys subumbra-proxy
```

The sidecar listens on:

- `http://localhost:10199/health`
- `http://localhost:10199/t/<key_id>/...`

Applications now use the secure transparent contract:

- present the adapter token in `Authorization` or `X-API-Key`
- put the requested `key_id` in the first path segment after `/t/`
- let `subumbra-proxy` package the canonical Worker `/proxy` request internally

Example:

```bash
OPENWEBUI_TOKEN="$(sed -n 's/^SUBUMBRA_TOKEN_OPENWEBUI=//p' .env)"

curl -sS \
  -H "Authorization: Bearer $OPENWEBUI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}' \
  http://localhost:10199/t/openai_prod/v1/chat/completions
```

## 3. Registry Publish And Removal Guidance

Editing local `providers.json` alone is **not enough**.

Operational rule for built-ins:

- update `providers.json`
- run `docker compose --profile bootstrap run --rm bootstrap --push-registry`

If you skip the registry publish step, new provider requests will still fail with
`403 target_url not allowed`.

Secure provider removal note:

- removing a provider from KV is not an immediate global revoke
- expect the same bounded staleness window as additions
- for immediate security-intent removal, update the registry and redeploy the Worker

## 4. Rotation / Update Guidance

To rotate or refresh a provider record:

1. For an existing V3 key secret change, run `docker compose --profile bootstrap run --rm -it bootstrap --rotate`
2. For an existing V3 policy-hash refresh, run `docker compose --profile bootstrap run --rm -it bootstrap --rotate-policy`
3. For any V2 migration or retained-set rebuild, rerun `./bootstrap.sh`
4. Recreate the local services after a full bootstrap if needed

This keeps subumbra records, local env state, and the deployed Worker configuration
aligned.

Full bootstrap also re-runs the one-shot Cloudflare setup path for a fresh
vault key pair. Preserve `public_key.pem` for shared-vault keys and any
`public_key_<key_id>.pem` files for unique-vault keys if you intend to use
offline single-key rotation after the bootstrap completes.

## 5. Recovery Playbook

### Single-Key Rotation

Use this when only one existing V3 provider secret needs to change.

```bash
docker compose --profile bootstrap run --rm -it bootstrap --rotate
```

The wizard prompts for an existing `key_id` and replacement secret. After a successful
per-key rotation, no service restart is required.

### Full Re-Bootstrap

Use this when rotating Worker/subumbra runtime tokens, replacing the Cloudflare
vault RSA key pair, or rebuilding the retained provider set.

```bash
./bootstrap.sh
docker compose up -d --force-recreate
```

During a full bootstrap, re-enter every key you want to keep. Any omitted key is
removed from the retained set.

For policy-backed bootstrap ingestion, you may also define
`SUBUMBRA_POLICY_PATH=/opt/subumbra/policies.json` in `.env.bootstrap`.
`./bootstrap.sh` mounts that host JSON file read-only into the bootstrap
container and passes the in-container path automatically. Built-in direct
provider secrets can still use synthesized app-bound or compatibility/simple
policies when no external policy entry is supplied, but imported secrets now
require a matching policy document.

For automation-mode imports from app-owned `.env` files, define
`IMPORT_PATH_<n>` together with the required `IMPORT_APP_LABEL_<n>` entries in
`.env.bootstrap`, then run `./bootstrap.sh`.

Full bootstrap now also writes deploy-integrity state to
`/app/data/system-integrity.json`. That artifact records the deployed Worker
name, URL, bundle hash, hash algorithm, and capture timestamp.

Verify the currently deployed Worker against that integrity artifact with:

```bash
./scripts/subumbra-verify-deploy
```

Use `--integrity-file <path>` to test a copied or staged integrity file
without mutating the live artifact.

### App Adapters

Interactive and automation bootstrap now both support app-bound adapters.

- declare custom app adapter IDs up front
- bind each direct secret to one or more declared adapters with per-key
  `*_ADAPTERS` values
- leave `*_ADAPTERS` blank only for explicit compatibility/simple mode, which
  binds that key to `subumbra-proxy`

Example:

```bash
ADAPTER_IDS=litellm,openwebui
OPENAI_KEY=...
OPENAI_KEY_ID=openai_litellm_1
OPENAI_KEY_ADAPTERS=litellm
UNIQUE_KEY_openai_litellm_1=false
OPENAI_KEY_2=...
OPENAI_KEY_ID_2=openai_openwebui_1
OPENAI_KEY_2_ADAPTERS=openwebui
UNIQUE_KEY_openai_openwebui_1=true
```

### Targeted Bootstrap Repair

If a fresh bootstrap partially succeeds and writes a retryable checkpoint, rerun
only the failed key:

```bash
./bootstrap.sh --provision <key_id>
```

This reuses the staged checkpoint, provisions only the named missing vault/key,
and atomically merges the repaired record into `keys.json`.

### Emergency Adapter Expiry

Use this only to force subumbra-side denial for a specific adapter.

```bash
./scripts/subumbra-expire-adapter.sh <adapter_id>
docker compose up -d --force-recreate subumbra-keys
```

Warning:

- this is subumbra-keys-side only
- it blocks new subumbra record fetches for that adapter
- it does not revoke Worker-side authority or remove already issued Worker tokens

### Token Drift Recovery

After full bootstrap, recreate the containers so they pick up the new runtime
state:

```bash
docker compose up -d --force-recreate
```

## 6. Adapter Authority Expiry And Emergency Expiry

Round 30 adds `issued_at` and `expires_at` to each `SUBUMBRA_ADAPTER_REGISTRY`
entry. `subumbra-keys` is the enforcement gate for this expiry metadata.

- `issued_at`: when the adapter authority was issued during bootstrap
- `expires_at`: when `subumbra-keys` should stop honoring that adapter token for new
  record fetches

Routine refresh and full revocation still mean re-running bootstrap so the local
runtime state and Cloudflare-side Worker token state rotate together.

Subumbra-keys-side emergency expiry is narrower:

- it blocks new record fetches for the targeted adapter
- it does not remove the token from the Cloudflare Worker
- it is not full revocation

## 7. Cloudflare Deployment Defaults

### Pricing Links

- Workers Logs pricing:
  `https://developers.cloudflare.com/workers/observability/logs/workers-logs/`
- Durable Objects pricing:
  `https://developers.cloudflare.com/durable-objects/platform/pricing/`

### Current Observability Defaults

The committed Worker config currently uses:

```toml
[observability]
enabled            = true
head_sampling_rate = 1
```

Current default posture:

- basic Worker observability is on
- invocation logs are **not** enabled by default
- tracing is **not** enabled by default

### Manual Tunnel / Domain Steps

A recommended deployment topology may use:

- `api.subumbra.<domain>`
- `ui.subumbra.<domain>`

In this round, those are documentation targets only. DNS records and tunnel
ingress must be configured manually in the Cloudflare dashboard or equivalent
Cloudflare API workflow.

Important routing note:

- if the UI is exposed through a tunnel, route cloudflared to the
  Docker-internal UI service path rather than assuming host-loopback routing

### Cloudflare Decisions For Now

- keep the current single KV namespace design
- keep the single `subumbra_registry_v1` object
- do **not** split provider registry KV by provider
- do **not** enable tracing by default
- do **not** enable verbose invocation logs by default

### Cloudflare Questions Disposition

- Data Studio is not relevant to the current Durable Object design because the
  current DO is ephemeral and does not use persistent storage
- Actors are future work
- Workers VPC is future work
- real-time logs are future work
- log-based cost analytics are future work

Use the helper:

```bash
./scripts/subumbra-expire-adapter.sh <adapter_id>
docker compose up -d --force-recreate subumbra-keys
```

Important warning:

- subumbra-keys-side emergency expiry stops new record fetches only
- it does **not** remove the token from the Cloudflare Worker
- for full revocation, run full re-bootstrap to rotate Worker-side token state

## 6. Audit Trail

The subumbra-keys audit trail is stored in SQLite at `/app/audit/audit.db`.

Audit entry fields: `timestamp`, `adapter_id`, `endpoint`, `key_id`, `verdict`,
`reason_code`, `remote`.

What is intentionally never logged: decrypted provider secrets, subumbra tokens,
`ciphertext`, `wrapped_dek`, `SUBUMBRA_HMAC_KEY`.

See [`docs/subumbra-testing.md`](./subumbra-testing.md) for audit query examples.

## 7. Transparent Sidecar Route

Bounded transparent ingress at `http://localhost:10199/t/<key_id>/...`.

Accepted app-facing credential header forms:

- `Authorization: Bearer <adapter_token>`
- `Authorization: <adapter_token>`
- `x-api-key: <adapter_token>`

The first path segment after `/t/` is the requested `key_id`.

`Authorization` takes precedence if both headers are present.

Notes:

- sidecar derives target hostname from the subumbra record, not caller input
- caller query strings are preserved on the upstream request
- JSON-only request bodies currently supported
- AI provider keys are not available on the transparent route unless scoped into
  `subumbra-proxy` during bootstrap

---

## R45 Structured KV Key Shape

Starting in R45-3, the Cloudflare KV namespace uses structured keys in place of
the single `subumbra_registry_v1` blob.

### Key Shape

| Key pattern | Value | Description |
|-------------|-------|-------------|
| `policy:<policy_id>` | JSON policy object | Declarative policy for one record |
| `key:<key_id>` | JSON key record (V3 format) | Encrypted record metadata |
| `template:<name>` | JSON routing template | Reusable routing defaults (`provider_id`, `target_host`, `auth_header`, `auth_prefix`, `api_base_path`) |
| `registry_version` | String | Fixed schema marker `"1"`; written last and read by the Worker on startup |

### Migration Path

The current `subumbra_registry_v1` blob (a host-indexed JSON array) is the
pre-R45 storage shape. During the R45 arc:

- R45-1 (this round): defines and locks the structured key shape above
- R45-2: bootstrap reads/validates against the new policy shape but does not
  yet write structured keys
- R45-3: bootstrap publishes to structured KV; Worker reads structured keys;
  `subumbra_registry_v1` is retired

### Publish Rule

R45-3 publication is two-phase:

1. compile all `key:*`, `policy:*`, and `template:*` entries
2. write them with `wrangler kv bulk put`
3. verify readback
4. write `registry_version = "1"` last

If publication fails before the `registry_version` write, the Worker treats the
namespace as misconfigured and fails closed.

### Policy Rotation

`--rotate-policy` recomputes the baseline-bound `policy_hash` for each targeted
record, asks the Worker to decrypt with the old binding and re-encrypt with the
new V3 binding, then writes the updated ciphertext back to `keys.json`. The
host never receives plaintext API keys during this flow.
