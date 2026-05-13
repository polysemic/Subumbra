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

- **Routing and auth** for each key come from operator-authored **`subumbra.json`**
  (inline `policy` or signed **`template`** expansion; see `policy.target.host`,
  `policy.auth`, allowlists).
- **Manifest templates:** the repo ships [`subumbra.minimal.json`](../subumbra.minimal.json)
  (smallest valid file: one OpenAI key via `template` only) and
  [`subumbra.example.json`](../subumbra.example.json) (every catalog provider +
  one inline “gold” policy). Operators copy to gitignored `subumbra.json` before
  bootstrap. Normative validation lives in `bootstrap/subumbra-bootstrap.py`.
- **Encrypted blobs** live in `subumbra-keys`; **decrypt authority** is split with the Cloudflare Worker + vault Durable Object (see `README.md` Key Properties and `docs/operator-guide.md`).
- **Policy-Bound Encryption (AAD)**: Every provider secret is encrypted using its specific policy as **Associated Authenticated Data (AAD)**. This creates a cryptographic "seal" between the key and its rules (like `max_body_bytes` or `path_prefixes`). If the policy in Cloudflare KV is tampered with, the Worker will fail to decrypt the key, preventing unauthorized use of the secret under a modified policy.

## Related

- [README.md](../README.md) — quick start and links
- [docs/subumbra-install.md](subumbra-install.md) — install and bootstrap
- [docs/adapter-contract.md](adapter-contract.md) — `/proxy` contract
