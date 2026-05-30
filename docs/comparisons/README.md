# Subumbra Comparison Atlas

This atlas is not a ranking. Mature products solve many problems Subumbra does not solve yet, and several tools listed here may be complementary rather than alternatives. The goal is a truthful value-proposition map: what each category is designed to do, where Subumbra is different, and where Subumbra is still alpha.

Start with [Subumbra alpha limitations](subumbra-alpha-limitations.md) before reading the feature tables. In normal runtime, apps receive adapter tokens and proxy routes, not provider plaintext; decrypted provider keys exist only inside the Cloudflare Durable Object during the proxied request. [src:subumbra-claude]

## Visual Matrix

| Subumbra capability | Comparison page |
|---------------------|-----------------|
| Split-trust custody, no provider plaintext in normal app runtime, AAD binding, offline rotation | [Secret vaults](secret-vaults.md) |
| Adapter tokens, local proxy mediation, policy firewall controls, RPM/velocity | [API brokers and AI gateways](api-brokers-ai-gateways.md) |
| SSH sign-only custody, host binding, per-sign Janus approval | [SSH agent access](ssh-agent-access.md) |
| Agent/MCP adjacent credential protection and runtime policy projects | [Agent/MCP security](agent-mcp-security.md) |
| Malware, CI leak, social engineering, malicious PR, and control-plane failure outcomes | [Threat model and failure modes](threat-model-failure-modes.md) |
| Subumbra alpha gaps and current limitations | [Subumbra alpha limitations](subumbra-alpha-limitations.md) |

## How To Read Cells

| Cell | Meaning |
|------|---------|
| `✓ Yes` | Supported in the current documented normal workflow |
| `✗ No` | Not present in the current documented normal workflow |
| `◑ Partial` | Partial, alpha, tier-specific, narrower, or caveated support |
| `— N/A` | Category mismatch |
| `⊙ Planned` | Explicitly planned but not current |
| `? Needs verification` | Source coverage is not sufficient yet |

## Reality Notes

- The matrix layer is meant for quick scanning; the sections underneath each table are the actual claim boundary.
- External products change quickly. Refresh [source notes](source-notes.md) before using these docs for public release.
- A `✓ Yes` for Subumbra should map back to repo evidence, usually `CLAUDE.md`, `subumbra.example.yaml`, `subumbra-proxy/app.py`, `worker/src/worker.js`, or bootstrap modules.
- A `? Needs verification` row is deliberately unfinished, not a quiet criticism of another project.

## Where Others Are Stronger

Many established vaults, gateways, and access platforms have hosted support, SSO/RBAC, audit export, compliance packages, broad integrations, cost dashboards, and large deployment communities. Subumbra should say that plainly.

## Where Subumbra Is Different

Subumbra combines API-key custody, adapter-token mediation, policy enforcement, SSH signing, session lockdown, and Janus approval in one operator-controlled stack. Its core workflow aims to avoid placing provider plaintext in the app server's normal config path. [src:subumbra-claude]

## Current Subumbra Gaps

The shortest version: Subumbra is young, self-operated, Cloudflare-dependent today, and missing enterprise administration depth. The longer version lives in [Subumbra alpha limitations](subumbra-alpha-limitations.md).

