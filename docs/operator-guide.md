# Subumbra Operator Guide

This guide covers the supported Round 1 operator flow:

1. author `subumbra.json`
2. provide a secret-only `.env.bootstrap`
3. run `./bootstrap.sh`
4. recreate the runtime services

## 1. Create The Manifest

Start from the checked-in example:

```bash
cp subumbra.example.json subumbra.json
```

Each manifest record declares:

- `key_id`
- `provider`
- `secret_ref`
- `adapters`
- `unique_vault`
- `policy`

`secret_ref` names the environment variable that will hold the provider secret
during bootstrap. The manifest itself should not contain plaintext secrets.

## 2. Create The Secret Bootstrap File

Copy the example and fill in only secret values and bootstrap credentials:

```bash
cp .env.bootstrap.example .env.bootstrap
```

The bootstrap file is intentionally short:

- provider secret values referenced by `secret_ref`
- Cloudflare bootstrap credentials
- optional bootstrap settings such as `TOKEN_TTL_DAYS`

`./bootstrap.sh` shreds `.env.bootstrap` after the run completes.

## 3. Run Bootstrap

```bash
./bootstrap.sh
```

Bootstrap reads `subumbra.json`, resolves the referenced secret values from
`.env.bootstrap`, deploys the Worker, encrypts the retained keys, and writes the
runtime state under `data/`.

If bootstrap stops before completion, fix the reported input error and rerun the
full bootstrap from the same repo checkout.

## 4. Recreate Runtime Services

After a full bootstrap, recreate the local services so they load the generated
runtime tokens and registry state:

```bash
docker compose up -d --force-recreate
```

The transparent proxy contract stays the same:

- health check: `http://127.0.0.1:10199/health`
- transparent route: `http://127.0.0.1:10199/t/<key_id>/...`

Example:

```bash
LITELLM_TOKEN="$(sed -n 's/^SUBUMBRA_TOKEN_LITELLM=//p' .env)"

curl -sS \
  -H "Authorization: Bearer $LITELLM_TOKEN" \
  http://127.0.0.1:10199/t/anthropic_litellm/v1/models
```

## 5. Rotation And Repair

Use the existing single-key rotation command when only a stored V3 secret value
needs to change:

```bash
./bootstrap.sh --rotate
```

Use a full bootstrap whenever you change retained keys, adapter bindings,
manifest policy, vault layout, or Cloudflare bootstrap state:

```bash
./bootstrap.sh
docker compose up -d --force-recreate
```

If a fresh bootstrap leaves a retryable checkpoint, repair a single missing key:

```bash
./bootstrap.sh --provision <key_id>
```

## 6. Registry Publish Notes

Structured KV publication still uses the current `key:`, `policy:`, and
`template:` records. If a manifest-era installation needs those entries
republished, rerun the full bootstrap from the same checkout before using the
registry publish helper.
