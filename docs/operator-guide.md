# KeyVault Operator Guide

*Round 26 operator notes for the explicit sidecar.*

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

- adding these providers still requires re-running bootstrap
- Gemini uses OpenAI-compatible mode in this project
- Together uses `TOGETHER_AI_API_KEY`

## 1. Bootstrap Walkthrough For New Providers

1. Add the provider entry to `worker/src/providers.json`.
2. Add the provider secret placeholder to `.env.bootstrap.example` or populate
   `.env.bootstrap` for headless use.
3. Run bootstrap:

```bash
docker compose --profile bootstrap run --rm bootstrap
./post-bootstrap.sh
```

This creates or updates forge records and redeploys the Worker bundle.

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

## 3. Worker Redeploy Requirement

Adding a provider requires re-running bootstrap.

Editing local `worker/src/providers.json` alone is **not enough**.

Why:

- the deployed Worker statically bundles the provider registry
- local file edits do not change the live Cloudflare Worker
- bootstrap re-runs the Worker deploy path through `wrangler deploy`

Operational rule:

- update `providers.json`
- run bootstrap
- then recreate the local containers

If you skip the bootstrap redeploy step, new provider requests will fail with
`403 target_url not allowed` even if the forge record exists locally.

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
  -H 'Authorization: Bearer github_prod' \
  -H 'Accept: application/json'
```

Operational notes:

- the sidecar derives the target hostname from the forge record, not from caller input
- caller query strings are preserved on the upstream request
- the transparent path currently supports JSON-only request bodies
- transparent AI-provider calls are not available unless the operator re-bootstrap-scopes those keys into `keyvault-proxy`
