# Gate DO

Subumbra Gate adds per-request human approval in front of selected Worker
`/proxy` and `/ssh/sign` calls without moving ciphertext, wrapped DEKs, SSH
challenge blobs, or provider auth material out of the existing Vault flow.

## What it does

- Policy `gate.require_approval` rules match selected HTTP or SSH requests.
- The Worker returns `202 Accepted` with a `request_id` and poll URL.
- `subumbra-proxy` polls Worker status and re-submits the original request only
  after the approval row is marked `approved`.
- Gate state lives in the `SubumbraGate` Durable Object, separate from
  `SubumbraVault`.
- Approval links are one-time capability URLs protected by
  `SUBUMBRA_GATE_HMAC_KEY`.

## Runtime pieces

- Worker secrets:
  - `SUBUMBRA_GATE_HMAC_KEY`
  - `SUBUMBRA_GATE_VAPID_PRIVATE_JWK`
- Host/runtime env:
  - `SUBUMBRA_GATE_VAPID_PUBLIC_KEY`
  - `SUBUMBRA_SIGN_TIMEOUT` for SSH gate wait ceilings

## Day-2 update path

After pulling a round that changes Gate behavior:

```bash
./bootstrap.sh --deploy-worker
./bootstrap.sh --update-gate
docker compose up -d --force-recreate
```

`--update-gate` is the bounded day-2 path that ensures Gate secrets, writes the
public VAPID key into the repo-local `.env`, and provisions the narrow
Cloudflare Access bypass apps for `/gate/approve/*` and `/gate/deny/*`.

## Manifest examples

HTTP policy gate:

```yaml
gate:
  require_approval:
    - when:
        method: POST
        path_prefix: /v1/messages
      timeout_seconds: 60
```

SSH key gate:

```yaml
gate:
  require_approval:
    - when:
        any_request: true
      timeout_seconds: 90
```

## Exclusions in r87

- No `request.deny_patterns`
- No SSE or long-held Worker wait streams
- No Slack/email/webhook notification channels
- No multi-approver or force-release workflow
