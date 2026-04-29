# AnythingLLM — Provider Switching Guide

*Tested in Round 43-6. Secure transparent contract updated in Round 43-6-4-1.*

---

## 1. Direct → Subumbra (OpenAI-compatible, single provider)

**Navigation:** Workspace settings → LLM Preference → Generic OpenAI

- **Base Path:** `http://subumbra-proxy:8090/t/openai_prod/v1`
- **API Key:** `${SUBUMBRA_TOKEN_ANYTHINGLLM}`
- **Model:** the exact model name (e.g. `gpt-4o-mini`)
- **Max Tokens:** as desired

This path now uses:

- adapter token in the credential field
- target Subumbra `key_id` in the base path

---

## 2. Named Providers — Blocked by App Design

AnythingLLM's named provider integrations still hardcode their official API
endpoints. The Generic OpenAI path remains the direct path that allows Subumbra
routing.

---

## 3. Via LiteLLM → Subumbra

**Navigation:** Settings → LLM Preference → LiteLLM

- **Base Path:** `http://litellm:4000`

This remains the recommended path for AnythingLLM users who need multi-provider
access behind one app credential surface.

---

## App-Specific Notes

- The base path applies to both chat and embedding endpoints for the Generic
  OpenAI provider.
- The credential is now the adapter token, not a plain key ID.
- For multi-provider access, LiteLLM remains the supported aggregator path.
