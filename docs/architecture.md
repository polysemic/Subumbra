# Subumbra — Architecture Overview

This page holds the **runtime shape** and **compose stack** summary moved from the root `README.md` for a shorter first-run path there.

## Core runtime shape

```
App-owned integration (LiteLLM, LibreChat, n8n, etc.)
      ↓
subumbra-proxy  (/t)
      ↓
subumbra-keys   → encrypted record fetch only
      ↓
Cloudflare Worker + Durable Object
      ↓
provider API
```

The universal `/t` path is a **shared `subumbra-proxy` identity** at the Worker boundary. Per-app Worker identities are future work.

## Core stack

The default `/opt/subumbra` compose stack contains:

- `subumbra-keys`
- `subumbra-proxy`
- `subumbra-ui`
- `bootstrap` profile
- `subumbra-probe` profile
- optional `cloudflared` profile

Bundled LiteLLM is **not** part of the core stack (see `litellm/README.md` for the standalone example config).

## Authority

- **Routing and auth** for each key come from operator-authored **`subumbra.json`** (`policy.target.host`, `policy.auth`, allowlists).
- **Encrypted blobs** live in `subumbra-keys`; **decrypt authority** is split with the Cloudflare Worker + vault Durable Object (see `README.md` Key Properties and `docs/operator-guide.md`).

## Related

- [README.md](../README.md) — quick start and links
- [docs/subumbra-install.md](subumbra-install.md) — install and bootstrap
- [docs/adapter-contract.md](adapter-contract.md) — `/proxy` contract
