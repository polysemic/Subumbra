# Cloudflare Tunnel and CF Access — Operator Guide

## Decision tree

- Use **Cloudflare Tunnel** to expose the Subumbra UI through a public hostname without opening VPS ports.
- Use **CF Access** to protect the Subumbra Worker with a service token that the proxy can present upstream.
- Both features are optional. Subumbra runs without them.
- `r73` adds two supported operator paths:
  - **BYOC**: provide `TUNNEL_TOKEN` and/or `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` yourself
  - **Auto-provision**: provide one expanded `CF_API_TOKEN`, `CF_ACCOUNT_ID`, `CF_ZONE_ID`, and `CF_TUNNEL_HOSTNAME`, and let bootstrap create the Cloudflare resources

## Required Cloudflare API scopes for auto-provision

Use one expanded `CF_API_TOKEN`. Group the scopes by what bootstrap needs to create:

- **Worker deploy / runtime bootstrap**
  - `Workers Scripts: Edit`
  - `Workers KV Storage: Edit`
- **Tunnel lifecycle**
  - account-scoped Tunnel create / delete permissions
- **DNS lifecycle**
  - zone-scoped DNS edit permissions for the zone named by `CF_ZONE_ID`
- **CF Access lifecycle**
  - account-scoped Access application / policy / service-token create and delete permissions

Subumbra does not retain `CF_API_TOKEN` in `.env`.

## Bootstrap inputs

### Always required for Subumbra bootstrap

```bash
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=subumbra-proxy
```

### Required only for Cloudflare auto-provision

```bash
CF_ZONE_ID=...
CF_TUNNEL_HOSTNAME=subumbra.example.com
```

### Optional naming overrides for auto-provision

```bash
CF_TUNNEL_NAME=subumbra-proxy-tunnel
CF_ACCESS_APP_NAME=subumbra-proxy-worker-access
CF_SERVICE_TOKEN_NAME=subumbra-proxy-service-token
```

### BYOC runtime secrets

```bash
TUNNEL_TOKEN=...
CF_ACCESS_CLIENT_ID=...
CF_ACCESS_CLIENT_SECRET=...
```

## BYOC path

If you already created the Tunnel or CF Access service token yourself, provide the runtime secrets during bootstrap:

- interactive wizard: enter them when prompted
- automation: add them to `.env.bootstrap`

Bootstrap writes these values into `.env` and does not try to recreate them.

Day-2 rotation remains:

```bash
./bootstrap.sh --update-tunnel
./bootstrap.sh --update-access
```

## Auto-provision path

If `TUNNEL_TOKEN` is absent and `CF_TUNNEL_HOSTNAME` is present, bootstrap may create:

1. a Cloudflare Tunnel
2. a DNS CNAME pointing `CF_TUNNEL_HOSTNAME` at `<tunnel_id>.cfargotunnel.com`

If `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` are absent, bootstrap may create:

1. a CF Access app protecting the Worker hostname
2. a `service_auth` policy
3. a CF Access service token

Bootstrap immediately writes:

- `TUNNEL_TOKEN`
- `CF_ACCESS_CLIENT_ID`
- `CF_ACCESS_CLIENT_SECRET`

into `.env`, and writes non-secret resource IDs into `data/cf-resources.json`.

## Adopt-existing limits

Cloudflare does not re-display Tunnel tokens or Access service-token secrets after creation.

That means adopt-existing is asymmetric:

- existing **IDs** can be reused when Subumbra already has them recorded in `data/cf-resources.json`
- existing **Tunnel token** still requires BYOC `TUNNEL_TOKEN`
- existing **Access service-token secret** still requires BYOC `CF_ACCESS_CLIENT_SECRET`

If the manifest says a Tunnel or service token exists but the corresponding runtime secret is missing from `.env`, bootstrap fails closed and tells you to either:

- provide the secret manually, or
- run `./bootstrap.sh --nuke-cloudflare` and recreate the resources cleanly

## Day-2 teardown

To delete Cloudflare-managed Tunnel / DNS / Access resources created under the `r73` contract:

```bash
./bootstrap.sh --nuke-cloudflare
```

This command:

- stops `cloudflared`
- deletes the tracked Tunnel, DNS record, Access policy, Access app, and service token
- clears `TUNNEL_TOKEN`, `CF_ACCESS_CLIENT_ID`, and `CF_ACCESS_CLIENT_SECRET` from `.env`
- removes `data/cf-resources.json`

It uses only the resource IDs previously recorded in `data/cf-resources.json`; it is not a broad discovery / reconcile tool.

## Verification expectations

- direct Worker `/auth-ping` without Access headers should return `403` when Access auto-provision is enabled
- direct Worker `/auth-ping` with generated Access service-token headers should return `200`
- `docker compose --profile tunnel up -d cloudflared` should show `Registered tunnel connection`
- `data/cf-resources.json` must contain only non-secret IDs
- `.env` must contain the generated runtime secrets

## Worker versus UI protection

- **Worker protected by CF Access:** the proxy uses `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` when calling the Worker
- **UI exposed by Tunnel:** `cloudflared` uses `TUNNEL_TOKEN` for the public hostname path
- you may use one or both

## CRITICAL-3 note

CF Access headers are trusted edge inputs for the Worker. Subumbra does not rely on those headers being absent in downstream Worker execution. Keep downstream integrations independent of CF Access header presence.
