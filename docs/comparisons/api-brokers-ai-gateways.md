# API Brokers And AI Gateways

This page is not a ranking. AI gateways tend to focus on routing, model choice, analytics, cost controls, caching, and guardrails. Subumbra focuses on custody and policy enforcement around provider keys; it currently has much less gateway product surface.

## Visual Matrix

| Capability | Subumbra | LiteLLM Proxy | Portkey | Helicone | Cloudflare AI Gateway | OpenRouter | Kong/Tyk/Envoy-style gateways |
|------------|----------|---------------|---------|----------|-----------------------|------------|-------------------------------|
| OpenAI-compatible or proxy-style drop-in routing | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial |
| Provider routing/fallback/load balancing | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial |
| Spend tracking / token analytics | ✗ No | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial | ✗ No |
| Prompt/request observability | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial | ◑ Partial |
| App-facing virtual/API keys | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Broker stores or receives provider keys | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial |
| Local self-controlled proxy option | ✓ Yes | ✓ Yes | ◑ Partial | ◑ Partial | ✗ No | ✗ No | ✓ Yes |
| Adapter-token to key binding | ✓ Yes | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial |
| Method allowlist | ✓ Yes | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ✓ Yes |
| Path-prefix allowlist | ✓ Yes | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ✓ Yes |
| Deny path-prefix list | ✓ Yes | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ✓ Yes |
| Content-Type allowlist | ✓ Yes | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ✓ Yes |
| Body-size cap | ✓ Yes | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ✓ Yes |
| Request-header allowlist / stripping | ✓ Yes | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ✓ Yes |
| Response-header allowlist / stripping | ✓ Yes | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ✓ Yes |
| Buffered response deny-pattern scanning | ✓ Yes | ? Needs verification | ◑ Partial | ? Needs verification | ◑ Partial | ? Needs verification | ◑ Partial |
| Per-adapter RPM / velocity | ✓ Yes | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial | ✓ Yes |
| Per-key RPM / velocity | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial | ◑ Partial | ✓ Yes |
| Circuit breaker | ✓ Yes | ◑ Partial | ✓ Yes | ◑ Partial | ✓ Yes | ◑ Partial | ✓ Yes |
| Janus approval for selected HTTP operations | ✓ Yes | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification |

## Reality Notes

- Subumbra does not currently offer mature multi-provider model routing, automatic fallbacks, cost dashboards, or token-spend analytics.
- Rows for response scanning and header stripping are intentionally conservative. Portkey documents request and response guardrails; Cloudflare AI Gateway documents DLP as a feature area. That is not the same as Subumbra's exact buffered response deny-pattern model. [src:portkey-docs] [src:cloudflare-ai-gateway]
- LiteLLM documents virtual keys, budgets, and rate limits; Portkey, Helicone, Cloudflare AI Gateway, and OpenRouter document routing or gateway features. [src:litellm-docs] [src:portkey-docs] [src:helicone-docs] [src:cloudflare-ai-gateway] [src:openrouter-docs]
- The Subumbra policy rows come from `subumbra.example.yaml` and Worker enforcement paths for method, path, content type, body size, headers, velocity, circuit breaker, and response denial. [src:subumbra-manifest] [src:subumbra-worker]

## Where Others Are Stronger

- AI gateways generally provide the product features LLM teams expect first: model catalogs, fallback routing, cache controls, cost dashboards, token analytics, request logs, and provider abstraction.
- Some gateway products offer hosted operation and simple onboarding, where Subumbra currently expects an operator-owned deployment.
- General API gateways have mature request routing, ingress policy, auth plugin ecosystems, and enterprise fleet operations.

## Where Subumbra Is Different

- Subumbra's default question is not "which model should this request use?" It is "should this adapter be able to use this specific key for this specific method/path/body/header shape right now?"
- The adapter token is not the provider key. If an app config leaks, the leaked value is still constrained by adapter scope, key binding, session state, policy, and the Worker boundary. [src:subumbra-proxy] [src:subumbra-worker]
- Janus approval can hold selected HTTP operations behind an operator approval step before the proxy resubmits the request. [src:subumbra-janus]

## Current Subumbra Gaps

- No model catalog or provider fallback planner.
- No mature prompt analytics or token-spend dashboard.
- No hosted gateway control plane.
- No broad gateway plugin marketplace.
- No polished per-tenant governance UI yet.

