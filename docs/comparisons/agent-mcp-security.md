# Agent And MCP Security

This page covers emerging adjacent projects. It is not a mature enterprise parity table, and inclusion is not endorsement. The agent/MCP security ecosystem is moving quickly; several tools below may be complementary to Subumbra rather than alternatives.

## Visual Matrix

| Project | Category | What it appears to protect | Credential storage vs brokering | Agent receives plaintext in normal workflow | Human approval / policy enforcement | Audit surface | Local-first vs hosted/control-plane model | Maturity signal | Relationship to Subumbra | Source |
|---------|----------|----------------------------|----------------------------------|---------------------------------------------|-------------------------------------|---------------|-------------------------------------------|-----------------|--------------------------|--------|
| AgentSecrets | MCP credential broker | Agent API calls without exposing raw keys to context | Brokering | ? Needs verification | ? Needs verification | ? Needs verification | Local-first appears likely | Early public project | Similar credential-boundary idea | [src:agentsecrets-github] |
| Peta Core | MCP runtime / gateway | MCP runtime and zero-trust gateway | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | Hosted/control-plane appears likely | Emerging | Adjacent MCP control plane | [src:peta-core] |
| nono | Agent sandbox | Capability-based sandbox and policy controls | Brokering/isolation | ? Needs verification | ✓ Yes | ? Needs verification | Local/runtime oriented | Active OSS | Complementary runtime containment | [src:nono-github] |
| Faramesh | Agent governance | Tool-call policy, approvals, evidence | Credential broker plus policy | ? Needs verification | ✓ Yes | ✓ Yes | Local plus optional integrations | Active OSS | Complementary policy/evidence layer | [src:faramesh-github] |
| MCPProxy | MCP proxy | Tool discovery, proxying, keyring secrets | Storage plus proxying | ◑ Partial | ◑ Partial | ◑ Partial | Local-first | Active OSS | Neighbor for MCP secret handling | [src:mcpproxy-github] |
| varlock | Secret/config tooling | Environment and secret handling | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | Needs review | Mention-only until sourced | [src:varlock-github] |
| Trail of Bits MCP Context Protector | MCP wrapper | Config pinning, prompt/tool response guardrails | Not primary focus | ? Needs verification | ✓ Yes | ◑ Partial | Local-first | Active OSS | Complementary MCP hardening | [src:trailofbits-mcp-context] |
| Pipelock | Agent firewall | Egress, MCP, DLP, SSRF, prompt-injection defense | Not primary focus | ? Needs verification | ✓ Yes | ✓ Yes | Local/sidecar | Active OSS | Complementary agent firewall | [src:pipelock-github] |
| Google MCP security | Security-product MCP servers | SecOps, SOAR, GTI, SCC access through MCP | Uses Google auth/env | ? Needs verification | ◑ Partial | ◑ Partial | Google-managed plus local packages | Large vendor repo | Adjacent security data plane | [src:google-mcp-security] |
| Snyk Agent Scan | Scanner | Agent/MCP/skill risks and prompt injection | Not a broker | — N/A | ◑ Partial | ✓ Yes | Local scanner | Active OSS | Complementary preflight scanner | [src:snyk-agent-scan] |
| AgentShield | Scanner / auditor | Agent configs, MCP risks, permissions | Not a broker | — N/A | ◑ Partial | ✓ Yes | CLI / CI / app | Active OSS | Complementary scanner | [src:agentshield-github] |

## Reality Notes

- This ecosystem is young and moving quickly.
- Inclusion is not endorsement and omission is not criticism.
- Several projects may be complementary to Subumbra rather than competitors.
- AgentSecrets, Peta Core, varlock, and several mention-only candidates need deeper source review before stronger cells should be used. [src:needs-verification]
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

