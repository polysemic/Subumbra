# Agent And MCP Security

This page covers Subumbra and emerging adjacent projects in the agent and MCP credential-security space. It is not a mature enterprise parity table, and inclusion is not endorsement. The agent/MCP security ecosystem is moving quickly; several tools below are complementary to Subumbra rather than alternatives.

Subumbra's planned direction for MCP and agent workflows is described in the sections below. The goal is to let local agents call providers through scoped adapter tokens — not provider plaintext — with the same session limits, policy firewalling, and Janus approval model used for all other Subumbra-brokered calls. This work is actively planned and not yet shipped.

## Visual Matrix

| Project | Category | What it appears to protect | Credential storage vs brokering | Agent receives plaintext in normal workflow | Human approval / policy enforcement | Audit surface | Local-first vs hosted/control-plane model | Maturity signal | Source |
|---------|----------|----------------------------|----------------------------------|---------------------------------------------|-------------------------------------|---------------|-------------------------------------------|-----------------|--------|
| AgentSecrets | MCP credential broker | Agent API calls without exposing raw keys to context | Brokering | ✗ No | ✗ No | ✓ Yes | Local-first | Early public project (v2.1.0) | [src:agentsecrets-github] |
| AgentShield | Scanner / auditor | Agent configs, MCP risks, permissions | Not a broker | — N/A | ◑ Partial | ✓ Yes | CLI / CI / app | Active OSS | [src:agentshield-github] |
| Faramesh | Agent governance | Tool-call policy, approvals, evidence | Credential broker plus policy | ◑ Partial | ✓ Yes | ✓ Yes | Local daemon | Active OSS (Go, pre-1.0) | [src:faramesh-github] |
| Google MCP security | Security-product MCP servers | SecOps, SOAR, GTI, SCC access through MCP | Uses Google Cloud IAM | ◑ Partial | ✗ No | ◑ Partial | Google-managed plus local packages | Large vendor repo | [src:google-mcp-security] |
| MCPProxy | MCP proxy | Tool discovery, proxying, keyring secrets | Storage plus proxying | ◑ Partial | ◑ Partial | ◑ Partial | Local-first | Active OSS | [src:mcpproxy-github] |
| nono | Agent sandbox | Capability-based sandbox and policy controls | Brokering/isolation | ◑ Partial | ✓ Yes | ✓ Yes | Local-first | Active OSS (Rust) | [src:nono-github] |
| Peta Core | MCP runtime / gateway | MCP runtime and zero-trust gateway | Brokering | ✗ No | ✓ Yes | ✓ Yes | Self-hosted (requires PostgreSQL) | Emerging (v1.2.1) | [src:peta-core] |
| Pipelock | Agent firewall | Egress, MCP, DLP, SSRF, prompt-injection defense | Not primary focus | ◑ Partial | ✓ Yes | ✓ Yes | Local/sidecar | Active OSS (Go, CNCF) | [src:pipelock-github] |
| Snyk Agent Scan | Scanner | Agent/MCP/skill risks and prompt injection | Not a broker | — N/A | ◑ Partial | ✓ Yes | Local scanner | Active OSS | [src:snyk-agent-scan] |
| Subumbra | Secret broker / MCP credential custody (planned) | Provider API keys and SSH keys from agent context; MCP tool-call custody planned | Split-custody storage + brokering (V3 encrypted envelope) | ✗ No | ✓ Yes | ◑ Partial | Self-hosted + Cloudflare DO | Alpha (v1.1.1-alpha) | [src:subumbra-claude] |
| Trail of Bits MCP Context Protector | MCP wrapper | Config pinning, prompt/tool response guardrails | Not primary focus | — N/A | ✓ Yes | ◑ Partial | Local-first | Active OSS (Python) | [src:trailofbits-mcp-context] |
| varlock | Secret/config tooling | .env schema exposure without secret values | Schema-only | ✗ No | ✗ No | ✗ No | Local-first | Active project (v1.4.0) | [src:varlock-github] |

## Reality Notes

- This ecosystem is young and moving quickly.
- Inclusion is not endorsement and omission is not criticism.
- Several projects are complementary to Subumbra rather than competitors.
- AgentSecrets and Peta Core are the closest conceptual neighbors for credential brokering: both keep provider credentials server-side and expose a token to the agent. Peta Core additionally implements a durable human-approval queue that is architecturally similar to Janus. [src:agentsecrets-github] [src:peta-core]
- Pipelock (CNCF Landscape, Go) is the strongest complement for egress firewall, DLP, and response scanning; it does not do credential custody. Faramesh covers tool-call policy and tamper-evident audit without custody. Both pair well with Subumbra rather than replacing it.
- varlock is developer-facing config tooling (schema-only .env management with leak scanning) — useful as a pre-runtime complement but not a runtime policy or custody system.
- nono (Rust, kernel-level Landlock/Seatbelt sandbox) is a containment layer for agent processes; it sits below the credential layer.
- Google MCP Security is a security data plane (access to Chronicle, SOAR, GTI, SCC) rather than a security control plane — not comparable in the credential-custody or policy-enforcement sense.
- Subumbra currently protects provider-key usage through adapter tokens, session state, policy firewalling, and split custody. Formal MCP server integration is planned but not yet shipped. [src:subumbra-claude]

## Planned Subumbra MCP Direction

The planned approach is to give MCP agents the same credential isolation that Subumbra already provides for LiteLLM, LibreChat, and other apps:

- **Adapter tokens, not provider keys.** An MCP server or local agent receives a scoped adapter token for its session. The provider key never appears in the agent's context, config, or memory.
- **Session-bounded access.** Agents will be assigned named sessions with TTLs, per-session key scope, and optional total-request quotas — matching the session model already in use for operator sessions.
- **Policy firewalling.** Method allowlists, path-prefix allowlists, content-type restrictions, body-size caps, and response deny-pattern scanning apply to MCP-originated calls through the same Worker enforcement path as all other proxied requests.
- **Janus approval.** Selected MCP tool calls can be held for operator approval before the proxy resubmits, using the same gate mechanism already implemented for HTTP operations.
- **Bootstrap template integration.** Agent adapter tokens will be declarable in `bootstrap/templates/adapters` alongside existing adapter definitions, allowing admins to provision agent credentials without touching provider secrets.

This work is tracked in the project scratchpad and is planned for a future round. The current Subumbra row in the matrix above reflects this planned state.

## Where Others Are Stronger

- Agent firewall and MCP-wrapper projects cover tool-call scanning, server-configuration pinning, DLP, SSRF, sandboxing, and prompt-injection detection more directly than Subumbra does today.
- Scanner projects can inventory risky agent/MCP/skill configurations before runtime; Subumbra does not currently scan a workstation or repo for agent tooling risks.
- Several projects are built around agent workflow governance first, while Subumbra is currently a secret broker with MCP integration actively planned.

## Where Subumbra Is Different

- Subumbra's core boundary is credential custody and policy-bound proxying, not just observing agent prompts or tool calls.
- The app-facing token is intentionally not the provider key, and provider plaintext is not present in the normal app config path — this design extends naturally to MCP agents. [src:subumbra-proxy] [src:subumbra-worker]
- Subumbra brings API and SSH custody into the same session/Janus/audit model, which pairs well with agent/MCP containment layers.

## Current Subumbra Gaps

- No MCP server integration yet; adapter-token-based MCP credential custody is planned.
- No agent sandbox, DLP engine, or filesystem/network containment layer.
- No workstation scanner for MCP configs or agent skills.
- No formal integrations with the listed projects yet.
- Many adjacent-project rows need refresh before public launch.
