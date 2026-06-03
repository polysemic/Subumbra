# LibreChat Install

*Canonical direct LibreChat app-owned Subumbra integration.*

LibreChat is not part of the core `/opt/subumbra` compose stack. The supported
model is:

- Subumbra core runs in `/opt/subumbra`
- LibreChat runs in its own install, for example `/opt/librechat`
- LibreChat talks to `subumbra-proxy` over the secure transparent path

## Host Vs Docker-Internal Ports

Use the host-published port only for operator checks from the VPS host:

- Subumbra proxy health: `http://127.0.0.1:10199/health`
- LibreChat UI/API: `http://127.0.0.1:3080`

Use the Docker-internal service address from app containers on `subumbra-net`.

## Scope

This install path proves:

- direct Docker install of LibreChat
- one or more LibreChat endpoints routed through Subumbra
- normal LibreChat user login plus authenticated session chat proof

## Prerequisites

Standard Subumbra readiness:

```bash
cd /opt/subumbra
docker compose ps
curl -sS http://127.0.0.1:10199/health
```

Expected proxy health:

```json
{"status":"ok","worker_auth":"ok"}
```

The LibreChat consumer token must already be available to the LibreChat
container.

## Supported Config Shape

LibreChat custom endpoints use three files:

- `.env`
- `librechat.yaml`
- `docker-compose.override.yml`

For the secure Subumbra path:

- the consumer token is the credential
- the target `key_id` lives in the path
- no real provider secret belongs in LibreChat config

Use the staged templates in `templates/`:

- [templates/librechat.env](./templates/librechat.env)
- [templates/librechat.yaml](./templates/librechat.yaml)
- [templates/docker-compose.override.yml](./templates/docker-compose.override.yml)

Important:

- do **not** use `apiKey: "user_provided"`
- do **not** put a real provider key in LibreChat for the Subumbra endpoint
- keep the endpoint base URL on the Docker-internal proxy path

## Secure Path Patterns

Examples from the promoted template:

- OpenAI custom endpoint:
  - `apiKey: "${SUBUMBRA_TOKEN_LIBRECHAT}"`
  - `baseURL: "http://subumbra-proxy:8090/t/openai_prod/v1"`
- Groq custom endpoint:
  - `baseURL: "http://subumbra-proxy:8090/t/groq_prod/openai/v1"`
- OpenRouter custom endpoint:
  - `baseURL: "http://subumbra-proxy:8090/t/openrouter_prod/api/v1"`
- Anthropic native endpoint:
  - `ANTHROPIC_API_KEY=${SUBUMBRA_TOKEN_LIBRECHAT}`
  - `ANTHROPIC_REVERSE_PROXY=http://subumbra-proxy:8090/t/anthropic_prod`

## Cut-Over Steps

1. Install LibreChat in its own directory.
2. Append the staged env excerpt to `.env`.
3. Copy `librechat.yaml` and `docker-compose.override.yml` into the LibreChat root.
4. Start LibreChat with Docker Compose.
5. Register the first account.
6. Export `LIBRECHAT_EMAIL` and `LIBRECHAT_PASSWORD` before verification.

## Operator Notes

- LibreChat custom endpoints are configured in `librechat.yaml`, not in the chat UI.
- Remove or comment out real direct-provider secrets from `.env` when adopting
  the Subumbra path.
- The active credential is the LibreChat consumer token. The target provider key
  selection happens in the URL path.

## Fail-Closed Check

Fail closed means:

- LibreChat attempts a normal routed chat
- `subumbra-proxy` cannot resolve the configured consumer token or cannot use the
  path-carried `key_id`
- the caller-facing chat attempt returns a non-200 failure

## Operator Checklist

1. Confirm `http://127.0.0.1:10199/health` returns `worker_auth":"ok"`.
2. Confirm the LibreChat consumer token is available to the container.
3. Keep the active endpoint `baseURL` on `http://subumbra-proxy:8090/t/<key_id>/...`.
4. Keep `apiKey` on the shared LibreChat consumer token.
5. Register the first LibreChat user through the UI.
6. Export `LIBRECHAT_EMAIL` and `LIBRECHAT_PASSWORD` before verification.
7. Confirm the routed chat proof in `subumbra-proxy` logs.
