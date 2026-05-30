# Agent And MCP Security

This page covers emerging adjacent projects. It is not a mature enterprise parity table, and inclusion is not endorsement. The agent/MCP security ecosystem is moving quickly; several tools below may be complementary to Subumbra rather than alternatives.

## Visual Matrix

| Project | Category | What it appears to protect | Credential storage vs brokering | Agent receives plaintext in normal workflow | Human approval / policy enforcement | Audit surface | Local-first vs hosted/control-plane model | Maturity signal | Relationship to Subumbra | Source |
|---------|----------|----------------------------|----------------------------------|---------------------------------------------|-------------------------------------|---------------|-------------------------------------------|-----------------|--------------------------|--------|
| AgentSecrets | MCP credential broker | Agent API calls without exposing raw keys to context | Brokering | ✗ No | ✗ No | ✓ Yes | Local-first | Early public project (v2.1.0) | Similar credential-boundary idea | [src:agentsecrets-github] |
| Peta Core | MCP runtime / gateway | MCP runtime and zero-trust gateway | Brokering | ✗ No | ✓ Yes | ✓ Yes | Self-hosted (requires PostgreSQL) | Emerging (v1.2.1) | Adjacent MCP control plane with Janus-style approval | [src:peta-core] |
| nono | Agent sandbox | Capability-based sandbox and policy controls | Brokering/isolation | ◑ Partial | ✓ Yes | ✓ Yes | Local-first | Active OSS (Rust) | Complementary kernel-level sandbox | [src:nono-github] |
| Faramesh | Agent governance | Tool-call policy, approvals, evidence | Credential broker plus policy | ◑ Partial | ✓ Yes | ✓ Yes | Local daemon | Active OSS (Go, pre-1.0) | Complementary policy/evidence layer | [src:faramesh-github] |
| MCPProxy | MCP proxy | Tool discovery, proxying, keyring secrets | Storage plus proxying | ◑ Partial | ◑ Partial | ◑ Partial | Local-first | Active OSS | Neighbor for MCP secret handling | [src:mcpproxy-github] |
| varlock | Secret/config tooling | .env schema exposure without secret values | Schema-only | ✗ No | ✗ No | ✗ No | Local-first | Active project (v1.4.0) | Dev-facing config tooling; complementary for agent schema isolation | [src:varlock-github] |
| Trail of Bits MCP Context Protector | MCP wrapper | Config pinning, prompt/tool response guardrails | Not primary focus | — N/A | ✓ Yes | ◑ Partial | Local-first | Active OSS (Python) | Complementary MCP hardening | [src:trailofbits-mcp-context] |
| Pipelock | Agent firewall | Egress, MCP, DLP, SSRF, prompt-injection defense | Not primary focus | ◑ Partial | ✓ Yes | ✓ Yes | Local/sidecar | Active OSS (Go, CNCF) | Complementary agent firewall | [src:pipelock-github] |
| Google MCP security | Security-product MCP servers | SecOps, SOAR, GTI, SCC access through MCP | Uses Google Cloud IAM | ◑ Partial | ✗ No | ◑ Partial | Google-managed plus local packages | Large vendor repo | Security data plane, not a control plane | [src:google-mcp-security] |
| Snyk Agent Scan | Scanner | Agent/MCP/skill risks and prompt injection | Not a broker | — N/A | ◑ Partial | ✓ Yes | Local scanner | Active OSS | Complementary preflight scanner | [src:snyk-agent-scan] |
| AgentShield | Scanner / auditor | Agent configs, MCP risks, permissions | Not a broker | — N/A | ◑ Partial | ✓ Yes | CLI / CI / app | Active OSS | Complementary scanner | [src:agentshield-github] |

## Reality Notes

- This ecosystem is young and moving quickly.
- Inclusion is not endorsement and omission is not criticism.
- Several projects may be complementary to Subumbra rather than competitors.
- AgentSecrets and Peta Core are the closest conceptual neighbors for credential brokering: both keep provider credentials server-side and expose a token to the agent. Peta Core additionally implements a durable human-approval queue that is architecturally similar to Janus. [src:agentsecrets-github] [src:peta-core]
- Pipelock (CNCF Landscape, Go) is the strongest complement for egress firewall, DLP, and response scanning; it does not do credential custody. Faramesh covers tool-call policy and tamper-evident audit without custody. Both pair well with Subumbra rather than replacing it.
- varlock is developer-facing config tooling (schema-only .env management with leak scanning) — useful as a pre-runtime complement but not a runtime policy or custody system.
- nono (Rust, kernel-level Landlock/Seatbelt sandbox) is a containment layer for agent processes; it sits below the credential layer.
- Google MCP Security is a security data plane (access to Chronicle, SOAR, GTI, SCC) rather than a security control plane — not comparable in the credential-custody or policy-enforcement sense.
- Subumbra currently protects provider-key usage through adapter tokens, session state, policy firewalling, and split custody. It is not yet a general MCP sandbox or agent runtime. [src:subumbra-claude]

## Where Others Are Stronger

- Agent firewall and MCP-wrapper projects may cover tool-call scanning, server-configuration pinning, DLP, SSRF, sandboxing, and prompt-injection detection more directly than Subumbra.
- Scanner projects can inventory risky agent/MCP/skill configurations before runtime; Subumbra does not currently scan a workstation or repo for agent tooling risks.
- Several projects are built around agent workflow governance first, while Subumbra is currently a secret broker with emerging adjacent fit.

## Where Subumbra Is Different

- Subumbra's core boundary is credential custody and policy-bound proxying, not just observing agent prompts or tool calls.
- The app-facing token is intentionally not the provider key, and provider plaintext is not present in the normal app config path. [src:subumbra-proxy] [src:subumbra-worker]
- Subumbra brings API and SSH custody into the same session/Janus/audit model, which may pair well with agent/MCP containment layers.

## Current Subumbra Gaps

- No full MCP proxy or MCP server marketplace.
- No agent sandbox, DLP engine, or filesystem/network containment layer.
- No workstation scanner for MCP configs or agent skills.
- No formal integrations with the listed projects yet.
- Many adjacent-project rows need refresh before public launch.

