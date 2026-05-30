# Comparison Source Notes

## Reviewed On

Reviewed on 2026-05-30. External offerings change frequently; refresh these notes and the matrices before public release.

## Subumbra Source Evidence

- `[src:subumbra-claude]` - `CLAUDE.md` documents the product purpose, adapter-token app path, Cloudflare Worker/Durable Object decrypt boundary, V3 envelope shape, AAD binding, read-only UI rationale, and offline rotation path.
- `[src:subumbra-manifest]` - `subumbra.example.yaml` documents policy fields for allowed adapters, methods, path prefixes, content types, body-size caps, request headers, deny path prefixes, response header allowlists, response deny patterns, velocity, SSH key entries, host restrictions, and approval policy.
- `[src:subumbra-proxy]` - `subumbra-proxy/app.py` validates adapter tokens through `SUBUMBRA_ADAPTER_REGISTRY`, extracts `key_id` from `/t/<key_id>/...`, fetches the encrypted record, strips/filters headers, and sends canonical Worker `/proxy` payloads.
- `[src:subumbra-worker]` - `worker/src/worker.js` maps policy schema fields, strips hop-by-hop headers, enforces content type/body-size/header policy, tracks velocity and circuit breakers, uses the live registry `policy_hash`, and scans buffered responses when deny patterns are configured.
- `[src:subumbra-rotation]` - `bootstrap/subumbra_keys.py` implements `./bootstrap.sh --rotate` for existing V3 records using the existing RSA public key and no Cloudflare interaction.
- `[src:subumbra-session]` - `bootstrap/subumbra_session.py` rejects overlapping sessions and reconciles `active_adapter:<adapter_id>` gates.
- `[src:subumbra-ssh]` - `worker/src/worker.js` stores SSH private material in Durable Object SQLite, performs Ed25519 signing in custody, and supports host-bound sign requests.
- `[src:subumbra-ssh-quota]` - `worker/src/worker.js` enforces SSH per-session sign quotas in Durable Object state.
- `[src:subumbra-janus]` - `worker/src/worker.js` currently names the Durable Object class `SubumbraGate`; public docs use Janus while code-name alignment remains deferred in `council/r91-doc-updaes/deferred.md`.

## External Product Sources

- `[src:vault-docs]` - HashiCorp Vault product page and developer docs, retrieved 2026-05-30. Claim areas: identity-based secret management, dynamic database credentials, rotation, SSH secrets engine, audit/enterprise maturity. https://www.hashicorp.com/en/products/vault and https://developer.hashicorp.com/vault/docs/secrets/databases and https://developer.hashicorp.com/vault/docs/secrets/ssh
- `[src:akeyless-docs]` - Akeyless docs, retrieved 2026-05-30. Claim areas: static/dynamic/rotated secrets, SSH certificate issuance, secure remote access categories. https://docs.akeyless.io/docs/what-is-akeyless
- `[src:infisical-docs]` - Infisical docs, retrieved 2026-05-30. Claim areas: open source secrets platform, secret scanning, dynamic credentials, SSH certificate access, audit. https://infisical.com/docs/documentation/getting-started/introduction
- `[src:doppler-docs]` - Doppler docs, retrieved 2026-05-30. Claim areas: rotated secrets, integrations, access controls, activity/access logs, team/enterprise features. https://docs.doppler.com/docs/secrets-rotation
- `[src:1password-connect]` - 1Password Connect developer docs, retrieved 2026-05-30. Claim areas: app/service access to 1Password vault items through Connect. https://www.1password.dev/connect
- `[src:1password-ssh-agent]` - 1Password SSH agent docs, retrieved 2026-05-30. Claim areas: private key stays in 1Password app, SSH clients authenticate through agent, authorization requirement. https://www.1password.dev/ssh/agent
- `[src:aws-secrets-docs]` - AWS Secrets Manager user guide, retrieved 2026-05-30. Claim areas: secrets storage and rotation workflows. https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html
- `[src:gcp-secret-manager]` - Google Cloud Secret Manager overview, retrieved 2026-05-30. Claim areas: secret storage, IAM, auditing, replication, rotation schedules. https://docs.cloud.google.com/secret-manager/docs/overview
- `[src:azure-key-vault]` - Azure Key Vault overview, retrieved 2026-05-30. Claim areas: secrets, keys, certificates, Entra ID auth, logging, HSM-backed tiers. https://learn.microsoft.com/en-us/azure/key-vault/general/overview
- `[src:litellm-docs]` - LiteLLM proxy docs, retrieved 2026-05-30. Claim areas: virtual keys, budgets, rate limits, AI gateway/proxy surface. https://docs.litellm.ai/docs/proxy/virtual_keys and https://docs.litellm.ai/docs/proxy/users
- `[src:portkey-docs]` - Portkey docs, retrieved 2026-05-30. Claim areas: universal API, routing, cache, fallbacks, circuit breaker, rate limits, guardrails. https://portkey.ai/docs/product/ai-gateway and https://portkey.ai/docs/product/guardrails
- `[src:helicone-docs]` - Helicone docs, retrieved 2026-05-30. Claim areas: AI gateway, model access, automatic logging, observability, fallbacks, credits/BYOK. https://docs.helicone.ai/getting-started/quick-start
- `[src:cloudflare-ai-gateway]` - Cloudflare AI Gateway docs, retrieved 2026-05-30. Claim areas: analytics, logging, caching, rate limiting, retries, model fallback, DLP feature area. https://developers.cloudflare.com/ai-gateway/
- `[src:openrouter-docs]` - OpenRouter docs, retrieved 2026-05-30. Claim areas: unified API, provider selection, fallbacks, model routing. https://openrouter.ai/docs/quickstart and https://openrouter.ai/docs/guides/routing/provider-selection
- `[src:teleport-docs]` - Teleport official docs, retrieved 2026-05-30. Claim areas: certificate-based access, server access, session recording, broad access platform. https://goteleport.com/docs/
- `[src:boundary-docs]` - HashiCorp Boundary docs, retrieved 2026-05-30. Claim areas: least-privilege secure access to applications and machines, targets, RBAC, secure sessions. https://developer.hashicorp.com/boundary/docs
- `[src:github-deploy-keys]` - GitHub deploy key docs, retrieved 2026-05-30. Claim areas: deployment SSH key management options. https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys
- `[src:agentsecrets-github]` - AgentSecrets primary GitHub repo, retrieved 2026-05-30. Claim areas: MCP credential broker concept. https://github.com/The-17/agentsecrets
- `[src:peta-core]` - Peta Core primary project / site, retrieved 2026-05-30. Claim areas: managed MCP runtime and zero-trust gateway. https://github.com/dunialabs/peta-core and https://peta.io/
- `[src:nono-github]` - nono primary GitHub repo, retrieved 2026-05-30. Claim areas: capability-based agent sandbox and fine-grained policies. https://github.com/always-further/nono
- `[src:faramesh-github]` - Faramesh primary GitHub repo, retrieved 2026-05-30. Claim areas: agent execution control, policy, approval, evidence, credential broker. https://github.com/faramesh/faramesh-core
- `[src:mcpproxy-github]` - MCPProxy primary GitHub repo, retrieved 2026-05-30. Claim areas: MCP proxy, local keyring storage, OAuth, tool limits. https://github.com/smart-mcp-proxy/mcpproxy-go
- `[src:varlock-github]` - varlock primary GitHub repo, retrieved 2026-05-30. Claim areas need deeper review. https://github.com/dmno-dev/varlock
- `[src:trailofbits-mcp-context]` - Trail of Bits MCP Context Protector repo, retrieved 2026-05-30. Claim areas: MCP wrapper, configuration pinning, guardrail scanning, quarantine. https://github.com/trailofbits/mcp-context-protector
- `[src:pipelock-github]` - Pipelock primary GitHub repo, retrieved 2026-05-30. Claim areas: agent firewall, egress/MCP/DLP/SSRF and receipts. https://github.com/luckyPipewrench/pipelock
- `[src:google-mcp-security]` - Google MCP Security repo, retrieved 2026-05-30. Claim areas: Google security product MCP servers and authentication requirements. https://github.com/google/mcp-security
- `[src:snyk-agent-scan]` - Snyk Agent Scan repo, retrieved 2026-05-30. Claim areas: agent/MCP/skill scanning, prompt-injection and vulnerable configuration scanning. https://github.com/snyk/agent-scan
- `[src:agentshield-github]` - AgentShield repo, retrieved 2026-05-30. Claim areas: agent configuration, MCP risk, permission, hook, and prompt-injection auditing. https://github.com/affaan-m/agentshield

## Needs Verification

- Exact internals for proprietary hosted products should not be inferred beyond official docs.
- Rows marked `? Needs verification` require product-specific source review before public release.
- Mention-only candidates not yet tabled: joelhooks agent-secrets, nopeek, LEASH, keiko, never-leak-protocol, Casbin Gateway, Spring MCP Security, and MCP-Dandan.
- Several tools may have features that resemble Subumbra controls under different names; keep `◑ Partial` until the exact behavior is sourced.
