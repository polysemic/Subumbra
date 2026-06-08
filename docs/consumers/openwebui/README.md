# OpenWebUI

OpenWebUI is a proven app-owned Subumbra integration.

Start here:

- [install.md](./install.md)
- [takeover.md](./takeover.md)
- [switching.md](./switching.md) — multi-provider switching guide (R43-6)

Current scope note:

- env-defined authority remains the durable production path
- the admin/UI path is useful for testing but not the supported durable source
  of truth
- the optional LiteLLM aggregator path remains documented in the install guide

Tested providers (R43-6): openai, anthropic, groq, deepseek, cerebras, mistral,
openrouter, together, xai. Gemini deferred (path mismatch).
