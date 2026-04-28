# OpenWebUI — Provider Switching Guide

*Tested in Round 43-6. Secure transparent contract updated in Round 43-6-4-1.*

---

## 1. Direct → Subumbra (OpenAI-compatible providers)

**Navigation:** Settings → Admin Panel → Connections → OpenAI API

- **API Key:** `${SUBUMBRA_TOKEN_OPENWEBUI}`
- **Connection type:** OpenAI

Set the base URL so the target `key_id` is embedded in the path:

| Provider | Base URL |
|---|---|
| openai | `http://subumbra-proxy:8090/t/openai_prod/v1` |
| groq | `http://subumbra-proxy:8090/t/groq_prod/openai/v1` |
| deepseek | `http://subumbra-proxy:8090/t/deepseek_prod/v1` |
| cerebras | `http://subumbra-proxy:8090/t/cerebras_prod/v1` |
| mistral | `http://subumbra-proxy:8090/t/mistral_prod/v1` |
| openrouter | `http://subumbra-proxy:8090/t/openrouter_prod/api/v1` |
| together | `http://subumbra-proxy:8090/t/together_prod/v1` |
| xai | `http://subumbra-proxy:8090/t/xai_prod/v1` |

Click the arrows icon to fetch the model list. Models are fetched through the
secure transparent route using the path-carried `key_id`.

---

## 2. Direct → Subumbra (Anthropic)

Anthropic still requires the Local connector so OpenWebUI sends Anthropic-format
requests.

**Navigation:** Settings → Admin Panel → Connections → Add connection

- **Connection type:** Local
- **Base URL:** `http://subumbra-proxy:8090/t/anthropic_prod/v1`
- **API Key:** `${SUBUMBRA_TOKEN_OPENWEBUI}`
- **Custom headers (JSON):**
  ```json
  {
    "anthropic-version": "2023-06-01"
  }
  ```

---

## 3. Via LiteLLM → Subumbra

- **API Base URL:** `http://litellm:4000/v1`
- **API Key:** any non-empty value accepted by LiteLLM
- **Connection type:** OpenAI

---

## 4. Via Bifrost → Subumbra

- **API Base URL:** `http://bifrost:8080/v1`
- **API Key:** Bifrost's own API key
- **Connection type:** OpenAI

---

## App-Specific Notes

- `ENABLE_PERSISTENT_CONFIG=False` must be set in OpenWebUI's `.env` for
  env-defined config to remain authoritative.
- The app credential is now the adapter token. Do not use plain key IDs in the
  API key field.
- The target `key_id` always lives in the proxy path.
