# AnythingLLM Install

*Canonical clean-install AnythingLLM app-owned Subumbra integration.*

AnythingLLM is not part of the core `/opt/subumbra` compose stack. The
supported model is:

- Subumbra core runs in `/opt/subumbra`
- AnythingLLM runs in its own install, for example `/opt/anythingllm`
- AnythingLLM talks to `subumbra-proxy` over the secure OpenAI-compatible path

## Host Vs Docker-Internal Ports

Use the host-published port only for operator checks from the VPS host:

- host health check: `http://127.0.0.1:10199/health`

Use the Docker-internal service address from app containers on `subumbra-net`:

- AnythingLLM base URL example:
  `http://subumbra-proxy:8090/t/openai_prod/v1`

Do not set `GENERIC_OPEN_AI_BASE_PATH` or `EMBEDDING_BASE_PATH` to
`127.0.0.1:10199` inside the AnythingLLM container.

## Secure Contract

AnythingLLM now uses:

- `GENERIC_OPEN_AI_API_KEY` = the AnythingLLM consumer token
- `GENERIC_OPEN_AI_EMBEDDING_API_KEY` = the same consumer token
- `GENERIC_OPEN_AI_BASE_PATH` = `http://subumbra-proxy:8090/t/<key_id>/v1`
- `EMBEDDING_BASE_PATH` = `http://subumbra-proxy:8090/t/<key_id>/v1`

## Prerequisites

Before pointing AnythingLLM at Subumbra, confirm:

1. the Subumbra core stack is already running in `/opt/subumbra`
2. `subumbra-proxy` reports healthy Worker auth
3. the AnythingLLM consumer token is available to the container
4. AnythingLLM is attached to `subumbra-net`

```bash
cd /opt/subumbra
docker compose ps
curl -sS http://127.0.0.1:10199/health
```

Healthy proxy output should include:

```json
{"status":"ok","worker_auth":"ok"}
```

## Supported Compose / Env Configuration

Use the tracked templates:

- [`templates/docker-compose.yml`](./templates/docker-compose.yml)
- [`templates/env-install.env`](./templates/env-install.env)

The supported direct path example is:

```text
http://subumbra-proxy:8090/t/openai_prod/v1
```

Two important rules:

1. both AnythingLLM credential fields use the consumer token
2. the target Subumbra `key_id` lives in the base path

After editing the compose file, recreate the container so the new env loads:

```bash
cd /opt/anythingllm
docker compose up -d --force-recreate anythingllm
```

Expected env shape:

```text
EMBEDDING_BASE_PATH=http://subumbra-proxy:8090/t/openai_prod/v1
EMBEDDING_ENGINE=generic-openai
GENERIC_OPEN_AI_API_KEY=${SUBUMBRA_TOKEN_ANYTHINGLLM}
GENERIC_OPEN_AI_BASE_PATH=http://subumbra-proxy:8090/t/openai_prod/v1
GENERIC_OPEN_AI_EMBEDDING_API_KEY=${SUBUMBRA_TOKEN_ANYTHINGLLM}
```

## Functional Checks

### Chat proof

A successful AnythingLLM chat request should produce proxy logs like:

```text
request consumer=anythingllm key_id=openai_prod method=POST target_url=https://api.openai.com/v1/chat/completions
complete key_id=openai_prod status=200
```

### Embedding proof

A fresh document ingest should produce proxy logs like:

```text
request consumer=anythingllm key_id=openai_prod method=POST target_url=https://api.openai.com/v1/embeddings
complete key_id=openai_prod status=200
```

### Fail-closed check

An invalid consumer token must fail closed:

```bash
curl -sS -i \
  -H 'Authorization: Bearer definitely_not_allowed' \
  -H 'Content-Type: application/json' \
  -X POST \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"test"}]}' \
  http://127.0.0.1:10199/t/openai_prod/v1/chat/completions
```

Expected result: `401`.

## Operator Checklist

1. Confirm `subumbra-proxy` health is `worker_auth":"ok"`.
2. Set `GENERIC_OPEN_AI_BASE_PATH=http://subumbra-proxy:8090/t/<key_id>/v1`.
3. Set `GENERIC_OPEN_AI_API_KEY` to the AnythingLLM consumer token.
4. Set `GENERIC_OPEN_AI_EMBEDDING_API_KEY` to the same consumer token.
5. Set `EMBEDDING_ENGINE=generic-openai`.
6. Recreate AnythingLLM after env changes.
7. Confirm the live request paths in proxy logs.
