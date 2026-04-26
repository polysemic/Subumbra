# AnythingLLM

AnythingLLM is a proven app-owned Subumbra integration.

Current proven scope:

- clean install through the OpenAI-compatible Generic OpenAI path
- existing-instance takeover for the OpenAI-compatible Generic OpenAI path
- full multi-provider access via the LiteLLM aggregator path

Start here:

- [install.md](./install.md)
- [takeover.md](./takeover.md)
- [switching.md](./switching.md) — provider path options and limitations (R43-6)

Current scope note:

- the direct Subumbra path is limited to Generic OpenAI (single model, no model chooser)
- named providers (Anthropic, Groq, etc.) hardcode official endpoints — no base URL
  override; PR #5295 rejected by maintainer
- full multi-provider access with model chooser requires the LiteLLM aggregator path
