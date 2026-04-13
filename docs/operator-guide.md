# KeyVault Operator Guide

*Operator notes for the explicit sidecar and live provider registry.*

Round 34 expands the built-in AI provider set to:

- Anthropic
- OpenAI
- Groq
- DeepSeek
- Cerebras
- Gemini
- Mistral
- OpenRouter
- Together
- xAI

Operational notes:

- Gemini uses OpenAI-compatible mode in this project
- Together uses `TOGETHER_AI_API_KEY`

## 1. Live Provider Registry

Provider validation now comes from Cloudflare KV rather than the Worker bundle.

### Adding a built-in provider without redeploying the Worker

1. Add the provider entry to `worker/src/providers.json`.
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
docker compose --profile bootstrap run --rm -it bootstrap
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
docker compose run --rm -u 0 -T forge-keys cat /app/data/kv-config.json
```

### Minimal `.env.bootstrap` for `--push-registry`

After full bootstrap has shredded the original automation file, a standalone
registry publish needs only:

```text
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=keyvault-proxy
```

No provider API keys are required for `--push-registry`.

## 2. Sidecar Startup

Start the sidecar stack with the normal project Compose file:

```bash
docker compose up -d --force-recreate forge-keys keyvault-proxy
```

The sidecar listens on:

- `http://localhost:8090/health`
- `http://localhost:8090/v1/request`

Applications call the sidecar using the five-field request contract:

- `key_id`
- `target_url`
- `method`
- `headers`
- `body`

`key_id` must exactly match the key ID chosen during bootstrap for that record.

## 3. Registry Publish And Removal Guidance

Editing local `worker/src/providers.json` alone is **not enough**.

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

To rotate a provider token:

1. update the secret value for that provider in your bootstrap input flow
2. rerun bootstrap
3. run `./post-bootstrap.sh`
4. recreate the local services if needed

This keeps forge records, local env state, and the deployed Worker configuration
aligned.

## 5. Recovery Playbook

### Single-Key Rotation

Use this when only one provider secret needs to change.

```bash
docker compose --profile bootstrap run --rm -it bootstrap --rotate
```

The wizard prompts for the `key_id` and replacement secret. After a successful
per-key rotation, no service restart is required.

### Full Re-Bootstrap

Use this when rotating Worker/forge runtime tokens, replacing the RSA key pair,
or rebuilding the retained provider set.

```bash
docker compose --profile bootstrap run --rm -it bootstrap
./post-bootstrap.sh
docker compose up -d --force-recreate
```

During a full bootstrap, re-enter every key you want to keep. Any omitted key is
removed from the retained set.

### Custom Adapters (Round 35)

Automation-mode bootstrap can add custom adapters with `ADAPTER_IDS` in
`.env.bootstrap`. This is additive-only: the built-in adapters remain
provisioned automatically, and each custom adapter uses a matching
`<NORMALIZED_ID>_ALLOWED_KEYS` variable, where normalization means uppercase
with `-` replaced by `_`.

Example:

```bash
ADAPTER_IDS=open-webui
OPEN_WEBUI_ALLOWED_KEYS=github_main
```

Custom adapters are a CI/automation-mode feature in this round. The interactive
bootstrap wizard still supports only the built-in adapters.

### Emergency Adapter Expiry

Use this only to force forge-side denial for a specific adapter.

```bash
./scripts/forge-expire-adapter.sh <adapter_id>
docker compose up -d --force-recreate forge-keys
```

Warning:

- this is forge-side only
- it blocks new forge record fetches for that adapter
- it does not revoke Worker-side authority or remove already issued Worker tokens

### Token Drift Recovery

If `./post-bootstrap.sh` warns that container tokens are stale, recreate the
containers so they pick up the new runtime state:

```bash
docker compose up -d --force-recreate
```

## 6. Adapter Authority Expiry And Emergency Expiry

Round 30 adds `issued_at` and `expires_at` to each `FORGE_ADAPTER_REGISTRY`
entry. `forge-keys` is the enforcement gate for this expiry metadata.

- `issued_at`: when the adapter authority was issued during bootstrap
- `expires_at`: when `forge-keys` should stop honoring that adapter token for new
  record fetches

Routine refresh and full revocation still mean re-running bootstrap so the local
runtime state and Cloudflare-side Worker token state rotate together.

Forge-side emergency expiry is narrower:

- it blocks new forge record fetches for the targeted adapter
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
- keep the single `provider_registry_v1` object
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
./scripts/forge-expire-adapter.sh <adapter_id>
docker compose up -d --force-recreate forge-keys
```

Important warning:

- forge-side emergency expiry stops new forge record fetches only
- it does **not** remove the token from the Cloudflare Worker
- if an attacker has a stolen token plus previously captured record material,
  replay remains possible until full re-bootstrap rotates Worker-side token state

## 6. Slack Host-Only Trust Tradeoff

Slack is approved under the current host-only trust model.

The Worker validates `target_url` by hostname, not by path prefix. Registering
`slack.com` therefore permits any HTTPS path on `slack.com`, not only
`/api/...`.

This is a conscious Round 26 policy tradeoff. Path-level enforcement is
deferred.

## 7. JSON-Only Limitation

The current Worker/Durable Object path supports JSON-style upstream bodies only.

That is why Stripe is still deferred:

- much of Stripe’s API depends on `application/x-www-form-urlencoded`
- the current core path serializes bodies as JSON

Round 26 only adds JSON-native providers:

- GitHub
- Slack
- SendGrid

## 8. Structured Audit Trail (Round 31)

Round 31 adds a forge-local durable audit trail stored in SQLite at:

- `/app/audit/audit.db`

Operationally this means:

- recent dashboard activity now comes from forge `/audit` (durable), not only in-memory `/stats` recent logs
- audit entries are structured with operator-safe fields such as:
  - `timestamp`
  - `adapter_id`
  - `endpoint`
  - `key_id`
  - `verdict`
  - `reason_code`
  - `remote`
- client-facing deny bodies remain terse (`401` / `403` / `404`), while reason detail stays in operator audit data

What is intentionally not in the audit trail:

- decrypted provider secrets
- forge auth headers/tokens
- `ciphertext` payloads
- `wrapped_dek` values
- `FORGE_HMAC_KEY`

Current limitation carried forward:

- durable audit storage is forge-local and row-capped by `AUDIT_MAX_ROWS`, but there is still no archival or export system

## 9. Transparent Sidecar (Round 33)

Round 33 adds a bounded transparent ingress route:

- `http://localhost:8090/t/{path}`

Accepted pseudo-key header forms:

- `Authorization: Bearer <key_id>`
- `Authorization: <key_id>`
- `x-api-key: <key_id>`

Precedence rule:

- if both `Authorization` and `x-api-key` are present, `Authorization` wins

Default proof example:

```bash
curl -sS -X GET \
  http://localhost:8090/t/user \
  -H 'Authorization: Bearer github_main' \
  -H 'Accept: application/json'
```

Operational notes:

- the sidecar derives the target hostname from the forge record, not from caller input
- caller query strings are preserved on the upstream request
- the transparent path currently supports JSON-only request bodies
- transparent AI-provider calls are not available unless the operator re-bootstrap-scopes those keys into `keyvault-proxy`
