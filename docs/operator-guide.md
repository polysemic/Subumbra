# KeyVault Operator Guide

*Round 26 operator notes for the explicit sidecar.*

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

## 5. Slack Host-Only Trust Tradeoff

Slack is approved under the current host-only trust model.

The Worker validates `target_url` by hostname, not by path prefix. Registering
`slack.com` therefore permits any HTTPS path on `slack.com`, not only
`/api/...`.

This is a conscious Round 26 policy tradeoff. Path-level enforcement is
deferred.

## 6. JSON-Only Limitation

The current Worker/Durable Object path supports JSON-style upstream bodies only.

That is why Stripe is still deferred:

- much of Stripe’s API depends on `application/x-www-form-urlencoded`
- the current core path serializes bodies as JSON

Round 26 only adds JSON-native providers:

- GitHub
- Slack
- SendGrid
