# Subumbra Alpha Limitations

This page is not a ranking. It is the control panel for honesty: Subumbra has a sharp value proposition, but it is a young self-operated project with real operational and product gaps.

## Visual Matrix

| Area | Current Subumbra state | Mature-platform contrast | Status |
|------|------------------------|--------------------------|--------|
| Enterprise RBAC/SSO | Basic scoped tokens and UI auth exist, but not mature org admin | Vault/Akeyless/Infisical/Doppler/1Password have deeper team controls | ◑ Partial |
| Hosted SaaS convenience | Operator deploys and runs the stack | Many products offer hosted onboarding | ✗ No |
| Cloudflare dependency | Cloudflare Worker/Durable Object is required today | Some products are fully self-hostable or provider-native | ◑ Partial |
| Integration ecosystem | Templates and examples exist, but ecosystem is small | Mature tools have broad SDKs/plugins/docs | ◑ Partial |
| Support organization | Community/operator-led | Mature vendors have support paths | ✗ No |
| Cost analytics/spend dashboard | Not currently a product surface | AI gateways commonly show usage and cost analytics | ✗ No |
| Dynamic database/cloud credentials | Not currently implemented | Vault/Akeyless/Infisical/Doppler document dynamic or rotated secret workflows | ✗ No |
| UI write operations | UI is read-only until hardened management API exists | Mature consoles support lifecycle operations | ✗ No |
| Host malware during active sessions | Host malware remains meaningful during active sessions | Mature platforms also depend on endpoint and IAM controls | ◑ Partial |
| CSP/Janus naming/hardening follow-ups | Known cleanup remains outside this round | Mature products usually have longer hardening cycles | ⊙ Planned |

## Reality Notes

- Current Cloudflare dependency is real: Subumbra uses Cloudflare Worker and Durable Object custody in the normal decrypt/proxy path. [src:subumbra-claude]
- Host malware remains meaningful during active sessions. Subumbra narrows what a stolen token can do; it does not make a compromised workstation safe.
- The UI is intentionally read-only for key lifecycle until a hardened management API exists, because a browser UI that relays plaintext would become a second plaintext authority. [src:subumbra-claude]
- CSP cleanup and the public Janus/code-name drift are tracked for future work, not this docs round.

## What Mature Platforms Do Stronger Today

- Enterprise identity: SSO, SCIM, RBAC, delegated administration, break-glass workflows, and audit export.
- Hosted operations: managed uptime, upgrades, support, compliance documents, and billing flows.
- Integrations: SDKs, CI/CD integrations, Kubernetes operators, cloud-native connectors, and polished onboarding.
- Analytics: cost, usage, spend caps, dashboarding, alerting, and reporting.

## Current Subumbra Operational Costs

- You operate the stack yourself.
- You need Cloudflare credentials and Worker/Durable Object state today.
- You need to understand adapter names, key IDs, sessions, policies, and manifests.
- You need to treat `subumbra.yaml`, `.env.bootstrap`, and local runtime state carefully.

## Current Cloudflare Dependency

Subumbra currently places the decrypt/proxy authority in Cloudflare Worker plus Durable Object storage. That is a deliberate split-trust design today, but it is also a platform dependency. Making Cloudflare optional is future architecture work, not a current fact.

## UI/Docs Maturity

The UI is improving quickly but remains read-focused. The comparison atlas itself should be refreshed as docs, competitors, and Subumbra features change.

## Team/Admin Maturity

Subumbra has adapter scoping, session controls, and Basic Auth/Cloudflare Access UI modes, but not full organization management.

## Integration Surface Gaps

Subumbra currently has a small set of examples and templates compared with mature vaults and AI gateways. It needs more validated app recipes before broad claims are defensible.

## Security Limitations That Remain True

- Active session abuse is still possible from a compromised host.
- Cloudflare deploy/control-plane authority is important.
- Browser compromise can matter for UI and Janus approval flows.
- Subumbra does not replace code review, IAM, branch protection, CI policy, endpoint security, or provider-side monitoring.

## Roadmap-Oriented Gaps

- Optional non-Cloudflare authority backend.
- Hardened management API and safe UI writes.
- CSP cleanup after inline code is removed.
- Public Janus naming alignment across code and docs.
- More app and MCP integrations.
- Canary-token or deception telemetry, if later scoped and designed safely.

