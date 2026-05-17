# Cloudflare Tunnel and CF Access — Operator Guide

## Decision tree

- Use **Cloudflare Tunnel** to expose Subumbra services without opening ports on
  your VPS.
- Use **CF Access** to add identity-gated authentication in front of those
  services.
- Both are optional. Subumbra runs without them; the Worker decrypt path is
  unchanged.
- If CF Access protects the Subumbra Worker, read the CRITICAL-3 note at the
  end of this document.

## Bring-your-own-credentials (BYOC) — the standard path

### Prerequisites

- A Cloudflare account with a Tunnel already created in the dashboard:
  `Zero Trust -> Networks -> Tunnels`
- A `TUNNEL_TOKEN` copied from that Tunnel's dashboard page
- Optionally: a CF Access application protecting either the UI hostname or the
  Worker hostname, plus a service token for machine-to-machine auth
- The corresponding `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET`

### First install: providing credentials during bootstrap

The interactive bootstrap wizard prompts for optional Tunnel and Access
credentials right after the required Cloudflare deploy authority step.

To supply them non-interactively, add the optional lines to `.env.bootstrap`:

```bash
TUNNEL_TOKEN=<your-token>
CF_ACCESS_CLIENT_ID=<your-client-id>
CF_ACCESS_CLIENT_SECRET=<your-client-secret>
```

Bootstrap writes these values to `.env` and shreds `.env.bootstrap` on success.
The bootstrap-only `CF_API_TOKEN` / `CF_ACCOUNT_ID` values are still not
retained in `.env`.

### Day-2: rotating credentials

Update the Tunnel token:

```bash
./bootstrap.sh --update-tunnel
```

Update the CF Access service token:

```bash
./bootstrap.sh --update-access
```

Both commands prompt securely and do not print credential values.

### Verification steps

1. Run `docker compose ps` and confirm the `cloudflared` container is `Up` if
   you enabled the Tunnel profile.
2. Curl the UI through the public Tunnel hostname.
   Expect either:
   - HTTP `200` from the UI when Access is not enabled for that hostname, or
   - the CF Access login flow when Access is enabled and you are unauthenticated.
3. Curl the Worker hostname without a valid service token header. If CF Access
   protects the Worker, expect HTTP `401`.
4. Repeat with valid CF Access service token headers. The request should pass
   through normally.

## Worker versus UI protection

- **Worker protected by CF Access:** put the Access application in front of the
  Worker hostname and configure the Subumbra proxy runtime with
  `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` so service-token headers are
  sent upstream.
- **UI protected by Tunnel + Access:** route the UI through Cloudflare Tunnel and
  leave `UI_USERNAME` / `UI_PASSWORD` unset so CF Access owns authentication at
  the edge.
- You may use one or both. They are separate configuration paths.

## CRITICAL-3: CF Access header strip at the Worker edge

CF Access injects headers at the Cloudflare network edge. Subumbra strips
`X-Subumbra-*` headers before upstream calls, but it does not strip CF Access
headers before Worker-to-provider calls.

This is an accepted architectural constraint. The Worker executes inside the
Cloudflare network where those headers are trusted edge inputs. Do not add
downstream code that depends on CF Access headers being absent from
Worker-to-provider traffic.

## Auto-provisioning (planned for r73)

API-driven Tunnel and Access provisioning is planned for
`r73-cloudflare-autoprovision`. That future round is expected to cover:

- creating Tunnels and DNS routes from the bootstrap flow
- creating Access applications, policies, and service tokens from bootstrap
- idempotent state tracking and teardown semantics

This document covers the current BYOC path only.
