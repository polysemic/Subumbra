# API Brokers And AI Gateways

This page is not a ranking. AI gateways tend to focus on routing, model choice, analytics, cost controls, caching, and guardrails. Subumbra focuses on custody and policy enforcement around provider keys; it currently has much less gateway product surface. Subumbra is designed to sit beside gateways like LiteLLM rather than replace them — see the complementary use section below.

## Visual Matrix

| Capability | Subumbra | LiteLLM Proxy | Portkey | Helicone | Cloudflare AI Gateway | OpenRouter | Kong/Tyk/Envoy-style gateways |
|------------|----------|---------------|---------|----------|-----------------------|------------|-------------------------------|
| OpenAI-compatible or proxy-style drop-in routing | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial |
| Provider routing/fallback/load balancing | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial |
| Spend tracking / token analytics | ⊙ Planned | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial | ✗ No |
| Prompt/request observability | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial | ◑ Partial |
| App-facing virtual/API keys | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| App/adapter config holds proxy token, not provider key | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No |
| Provider key held in split-custody encrypted envelope | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No |
| Broker stores or receives provider keys | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial |
| Local self-controlled proxy option | ✓ Yes | ✓ Yes | ◑ Partial | ◑ Partial | ✗ No | ✗ No | ✓ Yes |
| Adapter-token to key binding | ✓ Yes | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial |
| Method allowlist | ✓ Yes | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✓ Yes |
| Path-prefix allowlist | ✓ Yes | ◑ Partial | ◑ Partial | ✗ No | ✗ No | ✗ No | ✓ Yes |
| Deny path-prefix list | ⊙ Planned | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✓ Yes |
| Content-Type allowlist | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✓ Yes |
| Body-size cap | ✓ Yes | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✓ Yes |
| Request-header allowlist / stripping | ✓ Yes | ◑ Partial | ◑ Partial | ✗ No | ✗ No | ✗ No | ✓ Yes |
| Response-header allowlist / stripping | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✓ Yes |
| Buffered response deny-pattern scanning | ✓ Yes | ✗ No | ✗ No | ✗ No | ✓ Yes | ✗ No | ◑ Partial |
| Per-adapter RPM / velocity | ✓ Yes | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial | ✓ Yes |
| Per-key RPM / velocity | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial | ◑ Partial | ✓ Yes |
| Circuit breaker | ✓ Yes | ◑ Partial | ✓ Yes | ◑ Partial | ✓ Yes | ◑ Partial | ✓ Yes |
| Janus approval for selected HTTP operations | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No |

## Reality Notes

- Subumbra does not currently offer mature multi-provider model routing, automatic fallbacks, cost dashboards, or token-spend analytics. Spend tracking is planned (Cloudflare Analytics Engine / log-tail integration, target Q3 2026).
- For the response scanning row: Cloudflare AI Gateway DLP buffers streamed responses before scanning them for sensitive patterns — the ✓ Yes reflects actual buffered scanning behavior. Portkey and Helicone guardrails operate at the LLM input/output validation layer (schema, regex, injection detection) rather than buffered HTTP response body scanning — hence ✗ No. [src:portkey-docs] [src:cloudflare-ai-gateway]
- Request-side enforcement in Subumbra is structural (method, path, content-type, body-size, header allowlists) — not request body content scanning. Deny-pattern scanning applies to buffered provider responses only.
- LiteLLM documents method allowlists for pass-through endpoints and `max_request_size_mb` body-size caps, but does not support content-type allowlists, deny path lists, or response-header filtering. [src:litellm-docs]
- LiteLLM documents virtual keys, budgets, and rate limits; Portkey, Helicone, Cloudflare AI Gateway, and OpenRouter document routing or gateway features. [src:litellm-docs] [src:portkey-docs] [src:helicone-docs] [src:cloudflare-ai-gateway] [src:openrouter-docs]
- The Subumbra policy rows come from `manifest.example.yaml` and Worker enforcement paths for method, path, content type, body size, headers, velocity, circuit breaker, and response denial. Deny path-prefix list is declared in the manifest schema but not yet parsed or enforced by the Worker; it is marked ⊙ Planned. [src:subumbra-manifest] [src:subumbra-worker]
- The two new core-value rows reflect Subumbra's primary design constraint: the adapter never receives the provider key, and the provider key is never stored in plaintext on any system the operator controls. [src:subumbra-claude] [src:subumbra-worker]

## How Subumbra Complements LiteLLM And Similar Gateways

Subumbra is not a replacement for LiteLLM, Portkey, or other AI gateways — it is a credential custody layer that can sit in front of or beside them. The two can work together without disturbing each other's functionality.

**Transparent integration pattern:**
- LiteLLM (or any gateway) is configured to use a consumer token as its `api_key` and a Subumbra proxy URL as its `api_base`.
- LiteLLM retains all of its features: model routing, fallback, virtual keys, spend tracking, prompt observability, caching, and rate limits.
- Subumbra intercepts the outbound provider call, decrypts the real API key inside the Cloudflare Durable Object, injects auth, and forwards the request — then streams the response back transparently.
- The provider key never exists in LiteLLM's config, environment, or logs. If a LiteLLM instance is compromised or its config is leaked, the attacker has a consumer token and a proxy URL — not a provider key.

**What Subumbra adds without disrupting the gateway:**
- The provider key is held in a split-custody encrypted envelope: ciphertext and wrapped DEK on the operator's host, RSA private key inside the Cloudflare Durable Object. Neither side can decrypt alone.
- Consumer tokens are scoped to specific keys and methods, so a leaked LiteLLM token cannot be used against a different key or a different provider.
- Velocity limits and circuit breakers apply at the Subumbra Worker layer, complementing whatever rate limits LiteLLM itself enforces.
- Janus approval can gate specific high-risk operations even when LiteLLM passes them through.

Latency impact is minimal: the proxy adds one additional local hop (subumbra-proxy on the same host or Docker network), and the Cloudflare Worker decrypt path is typically sub-100ms round-trip. LiteLLM's streaming and response handling are preserved end-to-end.

## Where Others Are Stronger

- AI gateways generally provide the product features LLM teams expect first: model catalogs, fallback routing, cache controls, cost dashboards, token analytics, request logs, and provider abstraction.
- Some gateway products offer hosted operation and simple onboarding, where Subumbra currently expects an operator-owned deployment.
- General API gateways have mature request routing, ingress policy, auth plugin ecosystems, and enterprise fleet operations.

## Where Subumbra Is Different

- Subumbra's default question is not "which model should this request use?" It is "should this adapter be able to use this specific key for this specific method/path/body/header shape right now?"
- The consumer token is not the provider key. If an app config leaks, the leaked value is still constrained by adapter scope, key binding, session state, policy, and the Worker boundary. [src:subumbra-proxy] [src:subumbra-worker]
- The provider key is stored in a split-custody encrypted envelope — the host side holds ciphertext and a wrapped data-encryption key, while the Cloudflare Durable Object holds the RSA private key. Neither side can reconstruct the plaintext key alone. [src:subumbra-claude] [src:subumbra-worker]
- Janus approval can hold selected HTTP operations behind an operator approval step before the proxy resubmits the request. [src:subumbra-janus]

## Current Subumbra Gaps

- No model catalog or provider fallback planner.
- No mature prompt analytics or token-spend dashboard (planned, Q3 2026).
- No hosted gateway control plane.
- No broad gateway plugin marketplace.
- No polished per-tenant governance UI yet.
