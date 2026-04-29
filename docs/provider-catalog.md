# Subumbra Provider Catalog

*Round 26 operator reference for the explicit sidecar.*

## Sidecar Request Contract

Every app-facing sidecar request now uses the secure transparent contract:

- send the adapter token in `Authorization` or `X-API-Key`
- put the requested `key_id` in the first path segment after `/t/`
- append the provider-specific upstream path after that

Callers must **not** include the provider authorization header. The
Worker/Durable Object injects provider auth from the subumbra record.

## Providers

### anthropic

- `provider_id`: `anthropic`
- `target_url` example: `https://api.anthropic.com/v1/messages`
- required caller headers:
  - `content-type: application/json`
  - `anthropic-version: 2023-06-01`
- `body` example:

```json
{
  "model": "claude-3-5-haiku-latest",
  "max_tokens": 16,
  "messages": [{"role": "user", "content": "Say test"}]
}
```

```bash
curl -s -X POST http://localhost:10199/t/<your_anthropic_key_id>/v1/messages \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-3-5-haiku-latest","max_tokens":16,"messages":[{"role":"user","content":"Say test"}]}'
```

### openai

- `provider_id`: `openai`
- `target_url` example: `https://api.openai.com/v1/chat/completions`
- required caller headers:
  - `content-type: application/json`
- `body` example:

```json
{
  "model": "gpt-4o-mini",
  "messages": [{"role": "user", "content": "Say test"}],
  "max_tokens": 16
}
```

```bash
curl -s -X POST http://localhost:10199/t/<your_openai_key_id>/v1/chat/completions \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}'
```

### groq

- `provider_id`: `groq`
- `target_url` example: `https://api.groq.com/openai/v1/chat/completions`
- required caller headers:
  - `content-type: application/json`
- `body` example:

```json
{
  "model": "llama-3.1-8b-instant",
  "messages": [{"role": "user", "content": "Say test"}],
  "max_tokens": 16
}
```

```bash
curl -s -X POST http://localhost:10199/t/<your_groq_key_id>/openai/v1/chat/completions \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama-3.1-8b-instant","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}'
```

### deepseek

- `provider_id`: `deepseek`
- `target_url` example: `https://api.deepseek.com/v1/chat/completions`
- required caller headers:
  - `content-type: application/json`
- `body` example:

```json
{
  "model": "deepseek-chat",
  "messages": [{"role": "user", "content": "Say test"}],
  "max_tokens": 16
}
```

```bash
curl -s -X POST http://localhost:10199/t/<your_deepseek_key_id>/v1/chat/completions \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}'
```

### github

- `provider_id`: `github`
- `target_url` example: `https://api.github.com/user`
- required caller headers:
  - `accept: application/vnd.github+json`
  - `x-github-api-version: 2022-11-28`
  - `user-agent: <your-app-name>` — GitHub rejects requests with no User-Agent with HTTP 403
- `body` example:

```json
null
```

```bash
curl -s http://localhost:10199/t/<your_github_key_id>/user \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "User-Agent: subumbra-proxy/1.0"
```

### slack

- `provider_id`: `slack`
- `target_url` example: `https://slack.com/api/auth.test`
- required caller headers:
  - `content-type: application/json`
- `body` example:

```json
{}
```

```bash
curl -s -X POST http://localhost:10199/t/<your_slack_key_id>/api/auth.test \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### sendgrid

- `provider_id`: `sendgrid`
- `target_url` example: `https://api.sendgrid.com/v3/scopes`
- required caller headers:
  - `content-type: application/json`
- `body` example:

```json
null
```

```bash
curl -s http://localhost:10199/t/<your_sendgrid_key_id>/v3/scopes \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json"
```
