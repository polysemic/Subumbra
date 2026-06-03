# LibreChat — Provider Switching Guide

*Tested in Round 43-6. Secure transparent contract updated in Round 43-6-4-1.*

---

## 1. Direct → Subumbra

LibreChat provider configuration lives in `librechat.yaml`. The secure pattern
is:

- one shared LibreChat consumer token in the credential field
- one path-carried Subumbra `key_id` per endpoint

### Anthropic (native endpoint type)

In LibreChat `.env`:

```dotenv
ANTHROPIC_API_KEY=${SUBUMBRA_TOKEN_LIBRECHAT}
ANTHROPIC_REVERSE_PROXY=http://subumbra-proxy:8090/t/anthropic_prod
```

In `librechat.yaml`:

```yaml
endpoints:
  anthropic:
    apiKey: "${SUBUMBRA_TOKEN_LIBRECHAT}"
```

### OpenAI-compatible providers (custom endpoints)

All other providers go under `endpoints.custom:` with:

- `apiKey: "${SUBUMBRA_TOKEN_LIBRECHAT}"`
- provider-specific base URL carrying the `key_id`

| Provider | Base URL |
|---|---|
| OpenAI | `http://subumbra-proxy:8090/t/openai_prod/v1` |
| Groq | `http://subumbra-proxy:8090/t/groq_prod/openai/v1` |
| DeepSeek | `http://subumbra-proxy:8090/t/deepseek_prod/v1` |
| Cerebras | `http://subumbra-proxy:8090/t/cerebras_prod/v1` |
| Mistral | `http://subumbra-proxy:8090/t/mistral_prod/v1` |
| OpenRouter | `http://subumbra-proxy:8090/t/openrouter_prod/api/v1` |
| Together | `http://subumbra-proxy:8090/t/together_prod` |
| xAI | `http://subumbra-proxy:8090/t/xai_prod/v1` |

---

## 2. Via LiteLLM → Subumbra

Use a custom endpoint in `librechat.yaml` pointing at LiteLLM:

- `apiKey`: any value LiteLLM expects
- `baseURL`: `http://litellm:4000/v1`

---

## 3. Via Bifrost → Subumbra

Use a custom endpoint in `librechat.yaml` pointing at Bifrost:

- `apiKey`: Bifrost's own API key
- `baseURL`: `http://bifrost:8080/v1`

---

## App-Specific Notes

- `librechat.yaml` is file-authoritative.
- The credential is now the LibreChat consumer token, not a plain key ID.
- The target provider key choice happens in the base URL path or reverse proxy
  path.
