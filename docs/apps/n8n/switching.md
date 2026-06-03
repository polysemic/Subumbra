# N8N — Provider Switching Guide

*Tested in Round 43-6. Secure transparent contract updated in Round 43-6-4-1.*

---

## 1. AI-Node Integration (recommended)

Uses n8n's native provider credential nodes. The secure pattern is:

- consumer token in the credential field
- target Subumbra `key_id` in the base URL path

### Anthropic AI-node

- **Credential type:** Anthropic API
- **API Key:** `${SUBUMBRA_TOKEN_N8N}`
- **Base URL / Custom API URL:** `http://subumbra-proxy:8090/t/anthropic_prod`

The Anthropic node appends `/v1/messages` after the path-carried `key_id`.

### OpenAI AI-node

- **Credential type:** OpenAI API
- **API Key:** `${SUBUMBRA_TOKEN_N8N}`
- **Base URL / Organization Base URL:** `http://subumbra-proxy:8090/t/openai_prod/v1`

The OpenAI node appends its endpoint after the path-carried `key_id`.

### Switching providers

Change the base URL so it carries the target `key_id`:

| Provider | Base URL | API Key |
|---|---|---|
| anthropic | `http://subumbra-proxy:8090/t/anthropic_prod` | `${SUBUMBRA_TOKEN_N8N}` |
| openai | `http://subumbra-proxy:8090/t/openai_prod/v1` | `${SUBUMBRA_TOKEN_N8N}` |
| groq | `http://subumbra-proxy:8090/t/groq_prod/openai/v1` | `${SUBUMBRA_TOKEN_N8N}` |
| deepseek | `http://subumbra-proxy:8090/t/deepseek_prod/v1` | `${SUBUMBRA_TOKEN_N8N}` |
| mistral | `http://subumbra-proxy:8090/t/mistral_prod/v1` | `${SUBUMBRA_TOKEN_N8N}` |
| openrouter | `http://subumbra-proxy:8090/t/openrouter_prod/api/v1` | `${SUBUMBRA_TOKEN_N8N}` |
| xai | `http://subumbra-proxy:8090/t/xai_prod/v1` | `${SUBUMBRA_TOKEN_N8N}` |
| cerebras | `http://subumbra-proxy:8090/t/cerebras_prod/v1` | `${SUBUMBRA_TOKEN_N8N}` |

---

## 2. Workflow-Node (API) Integration

Uses n8n's HTTP Request node to call the CF Worker `/proxy` endpoint directly.
This pattern does **not** go through `subumbra-proxy`.

---

## 3. Via LiteLLM

Use the OpenAI credential type with base URL pointing at LiteLLM:

- **API Key:** any non-empty value LiteLLM accepts
- **Base URL:** `http://litellm:4000/v1`

---

## App-Specific Notes

- The credential is now the n8n consumer token, not a plain key ID.
- The target provider key selection happens in the base URL path.
- AI-node and Workflow-node patterns remain distinct; this round only changes
  the transparent `/t` credential model.
