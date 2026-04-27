# LibreChat — Provider Switching Guide

*Tested in Round 43-6 (2026-04-25/26). All providers configured via `librechat.yaml`.*

---

## 1. Direct → Subumbra

LibreChat provider configuration lives entirely in `librechat.yaml` — there is no
UI-based provider configuration. Changes require a service restart.

### Anthropic (native endpoint type)

The native LibreChat Anthropic endpoint sends proper Anthropic-format requests. It requires
two `.env` entries and a yaml block under `endpoints.anthropic:` (not `endpoints.custom:`):

**In LibreChat `.env`:**
```
ANTHROPIC_API_KEY=anthropic_prod
ANTHROPIC_REVERSE_PROXY=http://subumbra-proxy:8090/t
```

**In `librechat.yaml`:**
```yaml
endpoints:
  anthropic:
    apiKey: "anthropic_prod"
    models:
      - "claude-sonnet-4-6"
      - "claude-opus-4-5"
      - "claude-haiku-4-5-20251001"
    titleConvo: true
    titleModel: "claude-haiku-4-5-20251001"
```

Note: the `models:` block under `anthropic:` takes a flat array — not the `fetch`/`default`
object form used by custom endpoints.

Note: background model refresh requests (`GET /t/models`) return 404 — this is expected
noise. The sidecar transparent path does not serve a `/models` endpoint; actual chat
requests use the native Anthropic format through `ANTHROPIC_REVERSE_PROXY` and succeed.

### OpenAI-compatible providers (custom endpoints)

All other providers go under `endpoints.custom:` in `librechat.yaml`.

**Base URL patterns:**

| Provider | Base URL | Notes |
|---|---|---|
| OpenAI | `http://subumbra-proxy:8090/t/v1` | |
| Groq | `http://subumbra-proxy:8090/t/openai/v1` | Groq uses `/openai/v1` prefix |
| DeepSeek | `http://subumbra-proxy:8090/t/v1` | |
| Cerebras | `http://subumbra-proxy:8090/t/v1` | |
| Mistral | `http://subumbra-proxy:8090/t/v1` | requires `dropParams` (see below) |
| OpenRouter | `http://subumbra-proxy:8090/t/api/v1` | OpenRouter uses `/api/v1` prefix |
| Together | `http://subumbra-proxy:8090/t` | bare; `fetch: false` required |
| xAI | `http://subumbra-proxy:8090/t/v1` | |

**Mistral — `dropParams` required:**
```yaml
dropParams:
  - "user"
  - "frequency_penalty"
  - "presence_penalty"
  - "parallel_tool_calls"
  - "stop"
```
Without this, Mistral returns 422 Unprocessable Entity (rejects OpenAI-specific fields).

**Together — `fetch: false` required:**
```yaml
models:
  fetch: false
  default:
    - "meta-llama/Llama-3.3-70B-Instruct-Turbo"
```
Together's `/models` endpoint returns a raw array, not OpenAI `{data:[]}` format.
LibreChat's model list parser fails with `Cannot read properties of undefined (reading 'map')`.

**Apply changes:**
```bash
cd /opt/librechat
cp librechat.yaml /path/to/librechat/  # if editing locally
docker compose up -d --force-recreate LibreChat
```

---

## 2. Via LiteLLM → Subumbra

Add a custom endpoint in `librechat.yaml` pointing at LiteLLM:

```yaml
custom:
  - name: "LiteLLM"
    apiKey: "litellm"
    baseURL: "http://litellm:4000/v1"
    models:
      fetch: true
      default:
        - "gpt-4o"
```

Model list shows LiteLLM `config.yaml` `model_name` aliases, not Subumbra key_ids.
All providers in the LiteLLM config are accessible.

---

## 3. Via Bifrost → Subumbra

Add a custom endpoint in `librechat.yaml` pointing at Bifrost:

```yaml
custom:
  - name: "Bifrost"
    apiKey: "<bifrost-api-key>"
    baseURL: "http://bifrost:8080/v1"
    models:
      fetch: true
      default:
        - "gpt-4o-mini"
```

---

## 4. Tested combinations

See [provider-matrix.md](../../provider-matrix.md) LibreChat column.

All 9 providers confirmed working in R43-6:
anthropic (native endpoint), openai, groq, deepseek, cerebras, mistral★, openrouter,
together✦, xai.

---

## App-Specific Notes

- `librechat.yaml` is file-authoritative. Changes require `docker compose up -d --force-recreate LibreChat` to take effect. `docker compose restart` alone does not reload the config.
- LibreChat must be on the `subumbra_internal` Docker network to resolve `subumbra-proxy` by hostname. Add a `docker-compose.override.yml` to join the network if LibreChat runs in a separate compose project.
- Direct provider API keys (e.g., `OPENAI_API_KEY=sk-real-key`) in `.env` take precedence over the yaml config for the named endpoint type. Remove real keys from `.env` before testing the Subumbra path.
