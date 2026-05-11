# Provider Example Requests

*Operator reference for common providers brokered through Subumbra.*

Subumbra does not ship a hardcoded provider catalog. Operators declare each
provider's routing and auth in `subumbra.json` (see `subumbra.example.json`).
The examples below show typical `curl` requests through `subumbra-proxy` once a
provider is declared and a key is bootstrapped. Replace `<your_*_key_id>` and
`<your_adapter_token>` with the values from your `.env` and manifest.

For the full adapter contract see `docs/adapter-contract.md`. For provider
template shortcuts see `docs/operator-guide.md`.

---

## anthropic

```bash
curl -s -X POST http://localhost:10199/t/<your_anthropic_key_id>/v1/messages \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-3-5-haiku-latest","max_tokens":16,"messages":[{"role":"user","content":"Say test"}]}'
```

## openai

```bash
curl -s -X POST http://localhost:10199/t/<your_openai_key_id>/v1/chat/completions \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}'
```

## groq

```bash
curl -s -X POST http://localhost:10199/t/<your_groq_key_id>/openai/v1/chat/completions \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama-3.1-8b-instant","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}'
```

## deepseek

```bash
curl -s -X POST http://localhost:10199/t/<your_deepseek_key_id>/v1/chat/completions \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}'
```

## github

GitHub requires a `User-Agent` header (HTTP 403 without it).

```bash
curl -s http://localhost:10199/t/<your_github_key_id>/user \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "User-Agent: subumbra-proxy/1.0"
```

## slack

```bash
curl -s -X POST http://localhost:10199/t/<your_slack_key_id>/api/auth.test \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## sendgrid

```bash
curl -s http://localhost:10199/t/<your_sendgrid_key_id>/v3/scopes \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json"
```
