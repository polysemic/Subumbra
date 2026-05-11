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
- either `policy` (full inline policy object) **or** `template` (named catalog template), following the merge rules in the next section

`secret_ref` names the environment variable that will hold the provider secret
during bootstrap. The manifest itself should not contain plaintext secrets.
`provider` is now an operator-declared label, not a built-in routing lookup key.
Routing and auth authority come from `policy.target.host` and `policy.auth`
when using an inline policy, or from the expanded template plus optional
operator overrides when using `template`.

## 2. Using Provider Templates

Instead of an inline `policy` object, a record may set `"template": "<name>"`
where `<name>` is one of the bundled provider templates:

`anthropic`, `openai`, `groq`, `gemini`, `deepseek`, `mistral`, `openrouter`,
`together`, `xai`, `github`, `slack`, `sendgrid`.

Merge rules:

1. The template supplies provider-determined fields (`protocol`, `capability_class`,
   `target`, `auth`, default `allow` limits, and optional `response` / `intent` /
   `velocity` / `deny`).
2. The operator always supplies `key_id`, `secret_ref`, `adapters`, and
   `unique_vault` on the manifest record. Bootstrap injects `allow.adapters`
   from the manifest’s `adapters` list (after normalization); **`allow.adapters`
   is never taken from the template** and cannot be overridden via an optional
   inline `policy` fragment.
3. An optional inline `"policy"` object may appear alongside `"template"` to
   override any template field except `key_id`, `source`, and `allow.adapters`.

Trust model and offline behavior:

- The catalog (`catalog.json`) is signed with the project’s offline Ed25519
  release key; the public key is pinned in `bootstrap/subumbra-bootstrap.py` as
  `CATALOG_RELEASE_PUBKEY_HEX`. Bootstrap verifies the detached signature and
  every listed template file’s SHA-256 before any template contributes to policy.
- Templates ship inside the bootstrap container image under `/app/templates/`; no
  network fetch of a catalog URL is performed.

Minimal example using only a template:

```json
{
  "key_id": "my-openai-key",
  "provider": "openai",
  "secret_ref": "OPENAI_KEY",
  "adapters": ["my-proxy-token"],
  "unique_vault": false,
  "template": "openai"
}
```

Example with partial override:

```json
{
  "key_id": "my-openai-key",
  "provider": "openai",
  "secret_ref": "OPENAI_KEY",
  "adapters": ["my-proxy-token"],
  "unique_vault": false,
  "template": "openai",
  "policy": {
    "allow": {
      "max_body_bytes": 524288
    }
  }
}
```

Adapter JSON files under `bootstrap/templates/adapters/` are signed for
integrity and operator documentation; bootstrap does not expand them into policy.

## 3. Create The Secret Bootstrap File

Copy the example and fill in only secret values and bootstrap credentials:

```bash
cp .env.bootstrap.example .env.bootstrap
```

The bootstrap file is intentionally short:

- provider secret values referenced by `secret_ref`
- Cloudflare bootstrap credentials
- optional bootstrap settings such as `TOKEN_TTL_DAYS`

Cloudflare authority lifecycle at this stage:

- `CF_API_TOKEN` is bootstrap, deploy, and deploy-integrity authority. It is
  intentionally **not** retained in runtime `.env`, so you must re-supply it
  for later Cloudflare-backed day-2 operations such as
  `scripts/subumbra-verify-deploy`.
- `SUBUMBRA_SETUP_TOKEN` is the one-shot bootstrap authority for
  `/setup/keygen`. After a successful full bootstrap, the Worker rejects that
  route even if you still have a host-side reference copy.
- `SUBUMBRA_MANAGEMENT_TOKEN` is the continuing Worker management bearer for
  `/manage/key/pause` and `/manage/key/unpause`.

`./bootstrap.sh` shreds `.env.bootstrap` after a successful full bootstrap.
Successful `./bootstrap.sh --provision <key_id>`, `--add-adapter`,
`--revoke-adapter`, or `--publish-policy <key_id>` runs intentionally retain
the file so you can finish additional secure mutation steps; shred it manually
when repairs are complete.

## 4. Run Bootstrap

```bash
./bootstrap.sh
```

Bootstrap reads `subumbra.json`, resolves the referenced secret values from
`.env.bootstrap`, deploys the Worker, encrypts the retained keys, and writes the
runtime state under `data/`.

If bootstrap detects existing Cloudflare vault or KV state for the current
manifest, it stops and requires an explicit destructive acknowledgement before
continuing. Interactive runs prompt `y/N`; non-interactive runs must be rerun
with `--nuke` if you truly want a fresh Cloudflare reset.

If bootstrap stops before completion, fix the reported input error and rerun the
full bootstrap from the same repo checkout.

## 4.5 Recovery And Vault Loss

Subumbra does **not** provide a VPS-local vault backup that can recreate the
Cloudflare-side decrypt authority by itself. If Cloudflare-side vault custody
is lost and you initialize a brand-new vault state, ciphertext produced under
the previous vault state cannot be decrypted by that new state.

The supported recovery path is:

1. keep the original operator inputs (`subumbra.json` plus `.env.bootstrap`)
2. re-run a full bootstrap to provision fresh Cloudflare-side custody
3. recreate the runtime services so they load the new runtime state

Cloudflare may offer Durable Object restore or PITR features at the platform
level, but this guide does **not** claim that they are enabled for your account.
Treat them as external recovery options you must verify independently before
depending on them.

## 5. Recreate Runtime Services

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

## UI Authentication

The Subumbra UI supports two authentication modes:

| Mode | When to use | Configuration |
|------|-------------|---------------|
| **CF Tunnel + CF Access** (recommended) | You route the UI through a Cloudflare Tunnel | Leave `UI_USERNAME` and `UI_PASSWORD` unset. CF Access enforces authentication at the edge. |
| **HTTP Basic Auth** | You access the UI directly without a CF Tunnel | Set both `UI_USERNAME` and `UI_PASSWORD` in `.env`. |

### Switching modes

- **CF Access mode:** ensure `UI_USERNAME` and `UI_PASSWORD` are absent or empty in `.env`. The UI starts with one info log and accepts all requests without local auth.
- **Basic Auth mode:** set both `UI_USERNAME` and `UI_PASSWORD` in `.env`, then restart the UI container. Brute-force rate limiting (5 failures per 60-second window per IP) applies.
- **Partial configuration is an error:** if `UI_USERNAME` is set but `UI_PASSWORD` is absent (or vice versa), the UI container will not start.

## 6. Rotation And Repair

Use the existing single-key rotation command when only a stored V3 secret value
needs to change:

```bash
./bootstrap.sh --rotate
```

If a fresh bootstrap leaves a retryable checkpoint, repair a single missing key:

```bash
./bootstrap.sh --provision <key_id>
```

`--provision` now reads the persisted checkpoint and internal key state. It does
not require a complete checkpoint record for the target key if `subumbra.json`
and `.env.bootstrap` still provide the missing authority. If both the repair
authority and the local public key are gone, rerun the full bootstrap instead.

If `--rotate`, `--push-registry`, `--provision`, `--revoke-key`,
`--add-adapter`, `--revoke-adapter`, or `--publish-policy` reports missing
embedded authority fields or an embedded policy mismatch, stop and repair the
local state or re-run the full bootstrap. Those commands no longer reconstruct
policy or adapter bindings from bootstrap-era inputs.

### Management Authority

Bootstrap now generates and stores a separate management bearer token:

- host env key: `SUBUMBRA_MANAGEMENT_TOKEN`
- Worker secret: `SUBUMBRA_MANAGEMENT_TOKEN`

Use that token only for Worker management routes such as pause/unpause. It is
independent from adapter auth and should be treated like a privileged operator
secret.

If you need to rotate or recover it after bootstrap, overwrite the Worker
secret and the host `.env` value together:

```bash
NEW_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
export NEW_TOKEN

printf '%s\n' "$NEW_TOKEN" | wrangler secret put SUBUMBRA_MANAGEMENT_TOKEN --name "$CF_WORKER_NAME"
python3 - <<'PY'
from pathlib import Path
path = Path(".env")
lines = path.read_text().splitlines()
needle = "SUBUMBRA_MANAGEMENT_TOKEN="
replaced = False
out = []
for line in lines:
    if line.startswith(needle):
        out.append(needle + __import__("os").environ["NEW_TOKEN"])
        replaced = True
    else:
        out.append(line)
if not replaced:
    out.append(needle + __import__("os").environ["NEW_TOKEN"])
path.write_text("\n".join(out) + "\n")
PY
```

If you lose both the live Worker secret and the local `.env` copy, run a full
bootstrap so the management authority is reissued coherently.

### Deploy Integrity Verification

`scripts/subumbra-verify-deploy` compares the recorded Worker bundle hash
against the live Cloudflare deployment. Supply Cloudflare authority at runtime:

```bash
export CF_API_TOKEN=...
export CF_ACCOUNT_ID="$(sed -n 's/^CF_ACCOUNT_ID=//p' .env)"
export CF_WORKER_NAME="$(sed -n 's/^CF_WORKER_NAME=//p' .env)"
./scripts/subumbra-verify-deploy
```

The helper first checks the requested `--integrity-file` on the host. If that
path does not exist, it falls back to reading
`/app/data/system-integrity.json` from the live `subumbra-keys` container. Use
`--keys-container` or `--container-integrity-path` only if your install uses
non-default names.

## 7. Registry Publish Notes

Structured KV publication now uses only `key:` and `policy:` records plus the
schema marker:

```bash
./bootstrap.sh --push-registry
```

`--push-registry` now reads only from the persisted internal state under
`data/`. It does not require `subumbra.json` after bootstrap completes, and it
must preserve an already-live `paused: true` flag on any structured `key:<id>`
entry instead of clearing it during republish.

Before `./bootstrap.sh --push-registry`, rewrite any legacy anchored
`response.deny_patterns` values such as `^test$` to bare substring literals
such as `test`. Runtime compatibility for the old anchored form is no longer
preserved.

Bootstrap no longer reads routing or auth defaults from `providers.json`. If a
manifest record omits or misstates `policy.target.host` or `policy.auth`, the
bootstrap run fails closed and must be corrected in `subumbra.json`.

There is no longer a separate `--rotate-policy` workflow. Day-2 command
coverage is now:

```bash
./bootstrap.sh --push-registry
./bootstrap.sh --provision <key_id>
./bootstrap.sh --revoke-key <key_id>
./bootstrap.sh --add-adapter <key_id> <adapter_id>
./bootstrap.sh --revoke-adapter <key_id> <adapter_id>
./bootstrap.sh --publish-policy <key_id>
./bootstrap.sh --rotate
```

- `--revoke-key` marks the fat record as revoked, deletes the live `key:<id>`
  KV entry, and future `--push-registry` runs skip revoked records so the key
  does not resurrect.
- `--add-adapter` and `--revoke-adapter` are secure hybrid mutations: they use
  the local V3 record plus plaintext authority from `subumbra.json` /
  `.env.bootstrap`, re-encrypt, rewrite `keys.json`, and republish KV.
- `--publish-policy` has two branches:
  - non-baseline update for `intent`, `velocity`, or `response.deny_patterns`
    only: update fat-record policy and republish with no re-encryption
  - baseline update touching `allow.*`, `target.host`, or `auth.*`: re-encrypt
    and republish

Pause/unpause is the one Worker-native write path in this round. After a
successful `/manage/key/pause` or `/manage/key/unpause`, allow up to 90 seconds
for worst-case Cloudflare KV propagation before treating a stale proxy result as
a failure.

If you change routing metadata or broader retained bootstrap state beyond those
day-2 command boundaries, re-run the full bootstrap and recreate the runtime
services:

```bash
./bootstrap.sh
docker compose up -d --force-recreate
```

### Existing volume migration

If your VPS already uses Docker's doubled legacy volume name, migrate it once
before recreating the stack:

```bash
docker volume create keys_data
docker run --rm \
  -v subumbra_subumbra_keys_data:/from \
  -v keys_data:/to \
  alpine:3.21 sh -c "cp -a /from/. /to/"
```
