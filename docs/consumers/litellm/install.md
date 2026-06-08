# LiteLLM Install

*Canonical example of an app-owned Subumbra integration.*

LiteLLM is no longer part of the core `/opt/subumbra` compose stack. The
supported model is:

- Subumbra core runs in `/opt/subumbra`
- LiteLLM runs in its own install, for example `/opt/litellm`
- LiteLLM talks to `subumbra-proxy` over `http://subumbra-proxy:8090/t`

## Host Vs Docker-Internal Ports

Use the host-published port only for operator checks from the VPS host:

- host health check: `http://127.0.0.1:10199/health`

Use the Docker-internal service address from app containers on `subumbra-net`:

- app-to-proxy base: `http://subumbra-proxy:8090/...`

Do not point LiteLLM at `127.0.0.1:10199` from inside the LiteLLM container.

## Secure Identity Contract

LiteLLM now uses the secure transparent contract:

- `api_key` is the LiteLLM consumer token, for example
  `SUBUMBRA_TOKEN_LITELLM`
- `key_id` moves into the `api_base` path
- LiteLLM no longer sends plain Subumbra key IDs as credentials
- `SUBUMBRA_TOKEN_PROXY` is compatibility/simple mode only, not the normal
  LiteLLM app credential

## Required LiteLLM Model Shape

Each model entry uses the same LiteLLM consumer token and its own path-carried
`key_id`.

```yaml
model_list:
  - model_name: claude-sonnet-4
    litellm_params:
      model: anthropic/claude-sonnet-4
      api_base: http://subumbra-proxy:8090/t/anthropic_prod
      api_key: ${SUBUMBRA_TOKEN_LITELLM}

  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_base: http://subumbra-proxy:8090/t/openai_prod/v1
      api_key: ${SUBUMBRA_TOKEN_LITELLM}

  - model_name: llama-3.1-8b
    litellm_params:
      model: groq/llama-3.1-8b
      api_base: http://subumbra-proxy:8090/t/groq_prod/openai/v1
      api_key: ${SUBUMBRA_TOKEN_LITELLM}

  - model_name: openrouter-claude
    litellm_params:
      model: openrouter/anthropic/claude-sonnet-4
      api_base: http://subumbra-proxy:8090/t/openrouter_prod/api/v1
      api_key: ${SUBUMBRA_TOKEN_LITELLM}

  - model_name: cerebras-llama-3
    litellm_params:
      model: cerebras/llama3.1-8b
      api_base: http://subumbra-proxy:8090/t/cerebras_prod/v1
      api_key: ${SUBUMBRA_TOKEN_LITELLM}
```

Rules:

- `api_key` is always the LiteLLM consumer token
- the first path segment after `/t/` is the requested Subumbra `key_id`
- provider-specific upstream suffixes still remain after the `key_id` segment:
  - **OpenAI, Cerebras, X.ai (Grok-compatible), DeepSeek, Mistral**:
    `/t/<key_id>/v1`
  - **Groq**: `/t/<key_id>/openai/v1`
  - **OpenRouter**: `/t/<key_id>/api/v1`
  - **Anthropic**: `/t/<key_id>`

## Core Dependency

The Subumbra core stack must already be running in `/opt/subumbra`:

```bash
cd /opt/subumbra
docker compose up -d --force-recreate
curl -sS http://127.0.0.1:10199/health
```

Healthy proxy output should include:

```json
{"status":"ok","worker_auth":"ok"}
```

## Example LiteLLM Health / Functional Test

Assuming LiteLLM is already installed in `/opt/litellm`:

```bash
export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' /opt/litellm/.env)"

curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  http://127.0.0.1:4000/health

curl http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4",
    "messages": [{"role": "user", "content": "say hi in 3 words"}],
    "max_tokens": 20
  }'
```

## Operator Checklist

1. Confirm `subumbra-proxy` health returns `worker_auth":"ok"`.
2. Confirm the LiteLLM consumer token is available to the LiteLLM container.
3. Configure LiteLLM models with `api_base` pointing to
   `http://subumbra-proxy:8090/t/<key_id>/...`.
4. Use the LiteLLM consumer token as `api_key` for every model entry.
5. Do not use plain key IDs as LiteLLM credentials.

Round 41.7’s callback-era standalone LiteLLM flow is superseded by this
secure transparent sidecar contract.
