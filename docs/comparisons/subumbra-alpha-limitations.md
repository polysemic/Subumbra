# Subumbra Alpha Limitations

This page is not a ranking. It is the control panel for honesty: Subumbra has a sharp value proposition, but it is a young self-operated project with real operational and product gaps. Many of those gaps are actively planned; the ones that are not are called out clearly.

## Visual Matrix

| Area | Current Subumbra state | Mature-platform contrast | Status |
|------|------------------------|--------------------------|--------|
| Enterprise RBAC/SSO | Basic scoped tokens and UI auth exist, but not mature org admin | Vault/Akeyless/Infisical/Doppler/1Password have deeper team controls | ⊙ Planned |
| Hosted SaaS convenience | Operator deploys and runs the stack | Many products offer hosted onboarding | ⊙ Planned |
| Cloudflare dependency | Cloudflare Worker and Durable Object custody are required today — making CF optional is future architecture work | Some products are fully self-hostable or provider-native | ✓ Yes |
| Non-Cloudflare authority backend | No alternative custody/proxy backend today | Some platforms support pluggable authority backends | ⊙ Planned |
| Integration ecosystem | Templates and examples exist, but ecosystem is small | Mature tools have broad SDKs/plugins/docs | ◑ Partial |
| Support organization | Community/operator-led | Mature vendors have support paths | ✗ No |
| Cost analytics/spend dashboard | Not currently a product surface | AI gateways commonly show usage and cost analytics | ⊙ Planned |
| Dynamic database/cloud credentials | Not currently implemented | Vault/Akeyless/Infisical/Doppler document dynamic or rotated secret workflows | ⊙ Planned |
| UI write operations | UI is read-only until hardened management API exists | Mature consoles support lifecycle operations | ⊙ Planned |
| MCP / agent native integration | No MCP server integration yet; adapter-token-based MCP custody is planned | MCP ecosystem tools have native credential integrations | ⊙ Planned |
| Multi-tenancy / multi-user isolation | Single-operator model today; multi-user primitives via named sessions | Mature platforms support multiple isolated tenants | ⊙ Planned |
| Host malware during active sessions | Host malware remains meaningful during active sessions | Mature platforms also depend on endpoint and IAM controls | ◑ Partial |
| CSP/Janus naming/hardening follow-ups | Known cleanup remains outside this round | Mature products usually have longer hardening cycles | ⊙ Planned |

## Reality Notes

- **Cloudflare dependency is confirmed (✓ Yes):** Subumbra uses Cloudflare Worker and Durable Object custody in the normal decrypt/proxy path. This is a deliberate split-trust design choice, not an oversight. Making Cloudflare optional is explicitly tracked as a future architecture arc (ROADMAP: "Endpoint / authority modularization — long-term direction: make Cloudflare one supported authority/exposure backend among several"). [src:subumbra-claude]
- Host malware remains meaningful during active sessions. Subumbra narrows what a stolen token can do; it does not make a compromised workstation safe.
- The UI is intentionally read-only for key lifecycle until a hardened management API exists, because a browser UI that relays plaintext would become a second plaintext authority. [src:subumbra-claude]
- CSP cleanup and the public Janus/code-name drift are tracked for future work, not this docs round.
- Multi-tenancy has extensive design notes (per-tenant vault instances, user-scoped encryption, owner-scoped audit filtering) but is a separate architecture arc from the current single-operator model.

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

## Current Cloudflare dependency

Subumbra currently places the decrypt/proxy authority in Cloudflare Worker plus Durable Object storage. That is a deliberate split-trust design today, but it is also a confirmed platform dependency. Making Cloudflare optional is tracked as a long-term architecture goal — a separate arc from the current core hardening rounds — not a current fact.

## UI/Docs Maturity

The UI is improving quickly but remains read-focused. Write operations (rotation, provisioning, adapter lifecycle) are planned behind a hardened management API. The comparison atlas itself should be refreshed as docs, competitors, and Subumbra features change.

## Team/Admin Maturity

Subumbra has adapter scoping, session controls, and Basic Auth/Cloudflare Access UI modes, but not full organization management. Multi-user primitives (named sessions with scoped keys and TTLs) cover many team-access use cases today. Full RBAC/SSO and multi-tenancy are planned for a future round.

## Integration Surface Gaps

Subumbra currently has a small set of examples and templates compared with mature vaults and AI gateways. An app validation queue (AnythingLLM, LibreChat, Dify, and others) is tracked in ROADMAP and needs proof docs before broad claims are defensible.

## Security Limitations That Remain True

- Active session abuse is still possible from a compromised host.
- Cloudflare deploy/control-plane authority is important.
- Browser compromise can matter for UI and Janus approval flows.
- Subumbra does not replace code review, IAM, branch protection, CI policy, endpoint security, or provider-side monitoring.

## Roadmap-Oriented Gaps

- **Planned:** Optional non-Cloudflare authority backend (long-term architecture arc).
- **Planned:** Hardened management API and safe UI writes.
- **Planned:** Enterprise RBAC/SSO and multi-tenancy.
- **Planned:** Hosted SaaS deployment option.
- **Planned:** Cost analytics and spend dashboard (Cloudflare Analytics Engine / log-tail integration).
- **Planned:** Dynamic credential engine for databases and cloud services.
- **Planned:** MCP / agent native integration via scoped adapter tokens.
- **Planned:** CSP cleanup after inline code is removed.
- **Planned:** Public Janus naming alignment across code and docs.
- **Planned:** More app integrations (AnythingLLM, LibreChat, Dify, and others).
- Canary-token or deception telemetry, if later scoped and designed safely.
