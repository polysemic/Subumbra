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
`provider` is now an operator-declared label, not a built-in routing lookup key.
Routing and auth authority come from `policy.target.host` and `policy.auth`.

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

`--provision` now reads the persisted checkpoint and internal key state. It does
not require `subumbra.json` to remain on disk after bootstrap.

If `--rotate`, `--push-registry`, or `--provision` reports missing embedded
authority fields or an embedded policy mismatch, stop and repair the local state
or re-run the full bootstrap. Those commands no longer reconstruct policy or
adapter bindings from bootstrap-era inputs.

## 6. Registry Publish Notes

Structured KV publication now uses only `key:` and `policy:` records plus the
schema marker:

```bash
./bootstrap.sh --push-registry
```

`--push-registry` now reads only from the persisted internal state under
`data/`. It does not require `subumbra.json` after bootstrap completes.

Bootstrap no longer reads routing or auth defaults from `providers.json`. If a
manifest record omits or misstates `policy.target.host` or `policy.auth`, the
bootstrap run fails closed and must be corrected in `subumbra.json`.

There is no longer a separate `--rotate-policy` workflow. If you change
manifest policy, adapter bindings, routing metadata, or vault layout, re-run
the full bootstrap and recreate the runtime services:

```bash
./bootstrap.sh
docker compose up -d --force-recreate
```
