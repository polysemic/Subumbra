# Subumbra Provider Catalog

*Round 26 operator reference for the explicit sidecar.*

## Sidecar Request Contract

Every sidecar request uses the same five fields:

- `key_id`
- `target_url`
- `method`
- `headers`
- `body`

Callers must **not** include the provider authorization header in `headers`.
The Worker/Durable Object injects provider auth from the subumbra record.
`key_id` must exactly match the key ID you entered during bootstrap for that
provider record.

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
curl -s -X POST http://localhost:10199/v1/request \
  -H "Content-Type: application/json" \
  -d '{"key_id":"<your_anthropic_key_id>","target_url":"https://api.anthropic.com/v1/messages","method":"POST","headers":{"content-type":"application/json","anthropic-version":"2023-06-01"},"body":{"model":"claude-3-5-haiku-latest","max_tokens":16,"messages":[{"role":"user","content":"Say test"}]}}'
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
curl -s -X POST http://localhost:10199/v1/request \
  -H "Content-Type: application/json" \
  -d '{"key_id":"<your_openai_key_id>","target_url":"https://api.openai.com/v1/chat/completions","method":"POST","headers":{"content-type":"application/json"},"body":{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}}'
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
curl -s -X POST http://localhost:10199/v1/request \
  -H "Content-Type: application/json" \
  -d '{"key_id":"<your_groq_key_id>","target_url":"https://api.groq.com/openai/v1/chat/completions","method":"POST","headers":{"content-type":"application/json"},"body":{"model":"llama-3.1-8b-instant","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}}'
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
curl -s -X POST http://localhost:10199/v1/request \
  -H "Content-Type: application/json" \
  -d '{"key_id":"<your_deepseek_key_id>","target_url":"https://api.deepseek.com/v1/chat/completions","method":"POST","headers":{"content-type":"application/json"},"body":{"model":"deepseek-chat","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}}'
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
curl -s -X POST http://localhost:10199/v1/request \
  -H "Content-Type: application/json" \
  -d '{"key_id":"<your_github_key_id>","target_url":"https://api.github.com/user","method":"GET","headers":{"accept":"application/vnd.github+json","x-github-api-version":"2022-11-28","user-agent":"subumbra-proxy/1.0"},"body":null}'
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
curl -s -X POST http://localhost:10199/v1/request \
  -H "Content-Type: application/json" \
  -d '{"key_id":"<your_slack_key_id>","target_url":"https://slack.com/api/auth.test","method":"POST","headers":{"content-type":"application/json"},"body":{}}'
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
curl -s -X POST http://localhost:10199/v1/request \
  -H "Content-Type: application/json" \
  -d '{"key_id":"<your_sendgrid_key_id>","target_url":"https://api.sendgrid.com/v3/scopes","method":"GET","headers":{"content-type":"application/json"},"body":null}'
```
