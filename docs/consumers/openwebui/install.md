# OpenWebUI Install

*Canonical OpenWebUI app-owned Subumbra integration.*

OpenWebUI is not part of the core `/opt/subumbra` compose stack. The supported
model is:

- Subumbra core runs in `/opt/subumbra`
- OpenWebUI runs in its own install, for example `/opt/open-webui`
- OpenWebUI talks to `subumbra-proxy` over the secure transparent path

## Host Vs Docker-Internal Ports

Use the host-published port only for operator checks from the VPS host:

- host health check: `http://127.0.0.1:10199/health`

Use the Docker-internal service address from app containers on `subumbra-net`.

Do not set `OPENAI_API_BASE_URL` to `127.0.0.1:10199` inside the OpenWebUI
container.

## Supported Production Authority

The supported durable production authority is:

- env-defined OpenWebUI provider configuration
- `ENABLE_PERSISTENT_CONFIG=False`
- `webui.db` cleaned of legacy direct-provider connection state

## Secure Path Contract

OpenWebUI now uses:

- `OPENAI_API_KEY` = the OpenWebUI consumer token
- `OPENAI_API_BASE_URL` = `http://subumbra-proxy:8090/t/<key_id>/v1`

Example:

```dotenv
OPENAI_API_BASE_URL=http://subumbra-proxy:8090/t/openai_prod/v1
OPENAI_API_KEY=${SUBUMBRA_TOKEN_OPENWEBUI}
ENABLE_PERSISTENT_CONFIG=False
WEBUI_AUTH=false
WEBUI_SECRET_KEY=<random-long-value>
```

Rules:

- the app credential is the consumer token, not a plain key ID
- the Subumbra `key_id` lives in the URL path
- `ENABLE_PERSISTENT_CONFIG=False` is required

## Core Dependency

Before pointing OpenWebUI at Subumbra, confirm:

1. the Subumbra core stack is already running in `/opt/subumbra`
2. `subumbra-proxy` reports healthy Worker auth
3. the OpenWebUI consumer token is available to the OpenWebUI container

```bash
cd /opt/subumbra
docker compose ps
curl -sS http://127.0.0.1:10199/health
```

Healthy proxy output should include:

```json
{"status":"ok","worker_auth":"ok"}
```

## OpenAI-Compatible Direct Path

For the OpenAI-compatible path, use:

```text
http://subumbra-proxy:8090/t/openai_prod/v1
```

OpenWebUI then appends `/models` and `/chat/completions` to that base URL.

## Anthropic Direct Path

For Anthropic through the Local connector, use:

- **Base URL:** `http://subumbra-proxy:8090/t/anthropic_prod/v1`
- **API Key:** `${SUBUMBRA_TOKEN_OPENWEBUI}`
- **Custom headers:** `{"anthropic-version":"2023-06-01"}`

This keeps the secure split intact:

- consumer token in credential
- `anthropic_prod` in the path

## Via LiteLLM

For the aggregator path, point OpenWebUI at standalone LiteLLM:

```text
OPENAI_API_BASE_URL=http://litellm:4000/v1
OPENAI_API_KEY=<LITELLM_MASTER_KEY>
```

## Functional Checks

### Model discovery

From OpenWebUI, load the models list and confirm proxy logs show the chosen
path-carried `key_id`, for example:

```text
key_id=openai_prod method=GET target_url=https://api.openai.com/v1/models
```

### Chat proof

A successful OpenWebUI chat should produce a matching secure-path proxy log,
for example:

```text
key_id=openai_prod method=POST target_url=https://api.openai.com/v1/chat/completions
```

### Fail-closed check

An invalid consumer token must fail closed:

```bash
curl -sS -i \
  -H 'Authorization: Bearer definitely_not_allowed' \
  http://127.0.0.1:10199/t/openai_prod/v1/models
```

Expected result: `401`.

## Operator Checklist

1. Confirm `subumbra-proxy` health is `worker_auth":"ok"`.
2. Set `OPENAI_API_BASE_URL=http://subumbra-proxy:8090/t/<key_id>/v1`.
3. Set `OPENAI_API_KEY` to the OpenWebUI consumer token.
4. Set `ENABLE_PERSISTENT_CONFIG=False`.
5. Clean legacy direct-provider DB state once.
6. Restart OpenWebUI.
7. Confirm the live request path in proxy logs.
