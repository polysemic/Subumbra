# Standalone LiteLLM Guide

*Canonical example of an app-owned Subumbra integration.*

LiteLLM is no longer part of the core `/opt/subumbra` compose stack. The
supported model is:

- Subumbra core runs in `/opt/subumbra`
- LiteLLM runs in its own install, for example `/opt/litellm`
- LiteLLM talks to `subumbra-proxy` over `http://subumbra-proxy:8090/t`

## Important Identity Note

The current universal `/t` path uses a shared `subumbra-proxy` identity at the
Worker boundary. LiteLLM does **not** get a distinct Worker identity in this
round. Requests are authorized through the `subumbra-proxy` scope.

## Required LiteLLM Model Shape

Each model entry should use the transparent sidecar contract:

```yaml
model_list:
  - model_name: claude-sonnet-4
    litellm_params:
      model: anthropic/claude-sonnet-4
      api_base: http://subumbra-proxy:8090/t
      api_key: anthropic_prod

  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_base: http://subumbra-proxy:8090/t/v1
      api_key: openai_prod

  - model_name: llama-3.1-8b
    litellm_params:
      model: groq/llama-3.1-8b
      api_base: http://subumbra-proxy:8090/t/openai/v1
      api_key: groq_prod

  - model_name: openrouter-claude
    litellm_params:
      model: openrouter/anthropic/claude-sonnet-4
      api_base: http://subumbra-proxy:8090/t/api/v1
      api_key: openrouter_prod

  - model_name: cerebras-llama-3
    litellm_params:
      model: cerebras/llama3.1-8b
      api_base: http://subumbra-proxy:8090/t/v1
      api_key: cerebras_prod
```

Rules:

- `api_base` points to `subumbra-proxy:8090/t`. **Note:** LiteLLM provider aliases natively require specific completion paths, meaning you must append the correct suffix so the proxy directs the external request properly:
  - **OpenAI, Cerebras, X.ai (Grok)**: `/t/v1`
  - **Groq**: `/t/openai/v1`
  - **OpenRouter**: `/t/api/v1`
  - **Anthropic**: `/t` (or omit for native SDK headers)
- `api_key` is the plain `key_id`
- do **not** use `subumbra:<key_id>`
- the `key_id` must be included in `PROXY_ALLOWED_KEYS`

## Core Dependency

The Subumbra core stack must already be running in `/opt/subumbra`:

```bash
cd /opt/subumbra
docker compose up -d --force-recreate
curl -sS http://127.0.0.1:8090/health
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

1. Put the provider key IDs used by LiteLLM into `PROXY_ALLOWED_KEYS` during bootstrap.
2. Run `./post-bootstrap.sh` in `/opt/subumbra`.
3. Recreate the core stack so `subumbra-proxy` picks up the new runtime values.
4. Configure LiteLLM models with `api_base` pointing to the proxy (e.g. `http://subumbra-proxy:8090/t/v1` for OpenAI).
5. Use plain key IDs in LiteLLM config.

Round 41.7’s callback-era standalone LiteLLM flow is superseded by this
transparent sidecar contract.
