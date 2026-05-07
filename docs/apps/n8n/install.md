# N8N Install

*Canonical n8n app-owned Subumbra integration.*

n8n is not part of the core `/opt/subumbra` compose stack. The supported model
is:

- Subumbra core runs in `/opt/subumbra`
- n8n runs in its own install, for example `/opt/n8n`
- n8n talks to `subumbra-proxy` over the secure transparent path

## Host Vs Docker-Internal Ports

Use the host-published port only for operator checks from the VPS host:

- host health check: `http://127.0.0.1:10199/health`

Use the Docker-internal service address from app containers on `subumbra-net`:

- Anthropic AI-node base URL example:
  `http://subumbra-proxy:8090/t/anthropic_n8n_1`
- OpenAI AI-node base URL example:
  `http://subumbra-proxy:8090/t/openai_n8n_1/v1`

Do not point n8n containers at `127.0.0.1:10199`.

## Secure Contract

n8n uses app-owned adapter tokens:

- the credential secret is the n8n adapter token, for example
  `SUBUMBRA_TOKEN_N8N`
- the Subumbra `key_id` lives in the base URL path
- `SUBUMBRA_TOKEN_PROXY` is compatibility/simple mode only, not the normal n8n
  app credential

## Prerequisites

Before pointing n8n at Subumbra, confirm:

1. the Subumbra core stack is already running in `/opt/subumbra`
2. `subumbra-proxy` reports healthy Worker auth
3. the n8n adapter token is available to the n8n container
4. n8n is attached to `subumbra-net`

```bash
cd /opt/subumbra
docker compose ps
curl -sS http://127.0.0.1:10199/health
```

Healthy proxy output should include:

```json
{"status":"ok","worker_auth":"ok"}
```

## AI-Node Configuration

For provider-native AI nodes, set the provider credential to the n8n adapter
token and use the Subumbra base URL for the chosen key:

- Anthropic:
  - base URL: `http://subumbra-proxy:8090/t/anthropic_n8n_1`
  - API key: `${SUBUMBRA_TOKEN_N8N}`
- OpenAI-compatible:
  - base URL: `http://subumbra-proxy:8090/t/openai_n8n_1/v1`
  - API key: `${SUBUMBRA_TOKEN_N8N}`

The key rule is always the same:

1. credential secret = adapter token
2. target `key_id` = first path segment after `/t/`

## HTTP Request Node Configuration

For direct workflow-node calls, use the same contract:

```text
POST http://subumbra-proxy:8090/t/openai_n8n_1/v1/chat/completions
Authorization: Bearer ${SUBUMBRA_TOKEN_N8N}
Content-Type: application/json
```

## Functional Checks

### Positive proof

A successful n8n request should produce proxy logs like:

```text
request adapter=n8n key_id=openai_n8n_1 method=POST
complete adapter=n8n key_id=openai_n8n_1 status=200
```

### Fail-closed check

An invalid adapter token must fail closed:

```bash
curl -sS -i \
  -H 'Authorization: Bearer definitely_not_allowed' \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"test"}]}' \
  http://127.0.0.1:10199/t/openai_n8n_1/v1/chat/completions
```

Expected result: `401`.

## Operator Checklist

1. Confirm `subumbra-proxy` health is `worker_auth":"ok"`.
2. Use `SUBUMBRA_TOKEN_N8N` as the n8n credential secret.
3. Put the target `key_id` in the `/t/<key_id>/...` path.
4. Keep `SUBUMBRA_TOKEN_PROXY` reserved for explicit compatibility/simple mode.
5. Confirm the live request path in proxy logs.
