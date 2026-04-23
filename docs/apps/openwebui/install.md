# OpenWebUI Install

*Canonical OpenWebUI app-owned Subumbra integration.*

OpenWebUI is not part of the core `/opt/subumbra` compose stack. The supported
model is:

- Subumbra core runs in `/opt/subumbra`
- OpenWebUI runs in its own install, for example `/opt/open-webui`
- OpenWebUI talks to `subumbra-proxy` over the OpenAI-compatible transparent path

## Supported Production Authority

The supported durable production authority is:

- env-defined OpenWebUI provider configuration
- `ENABLE_PERSISTENT_CONFIG=False`
- `webui.db` cleaned of legacy direct-provider OpenAI connection state

UI-managed provider edits are technically viable, but they are not the
supported durable production authority for this round. Treat them as transient
testing/admin actions, not the long-term source of truth.

## Prerequisites

Before pointing OpenWebUI at Subumbra, confirm:

1. The Subumbra core stack is already running in `/opt/subumbra`
2. `subumbra-proxy` reports healthy Worker auth
3. The required `key_id` is already included in `PROXY_ALLOWED_KEYS`
4. OpenWebUI is attached to `subumbra-net`

```bash
cd /opt/subumbra
docker compose ps
curl -sS http://127.0.0.1:10199/health
grep '^PROXY_ALLOWED_KEYS=' .env
```

Healthy proxy output should include:

```json
{"status":"ok","worker_auth":"ok"}
```

## Important Path Note

OpenWebUI uses the OpenAI-compatible `/models` and `/chat/completions` routes
directly. In live proof, that means the supported OpenWebUI base is:

```text
http://subumbra-proxy:8090/t/v1
```

Do not use bare `/t` for the OpenWebUI OpenAI-compatible path. Bare `/t` causes
OpenWebUI model discovery to hit the wrong upstream route.

## Path A — Env-Defined OpenWebUI -> Subumbra

Use this as the supported production setup in `/opt/open-webui/.env`:

```dotenv
OPENAI_API_BASE_URL=http://subumbra-proxy:8090/t/v1
OPENAI_API_KEY=openai_prod
ENABLE_PERSISTENT_CONFIG=False
WEBUI_AUTH=false
WEBUI_SECRET_KEY=<random-long-value>
```

Rules:

- `OPENAI_API_KEY` is the plain `key_id`
- do **not** use `subumbra:<key_id>`
- `ENABLE_PERSISTENT_CONFIG=False` is required
- restart OpenWebUI after changing `.env`

```bash
cd /opt/open-webui
docker compose up -d
```

## Path B — UI / Admin Connection Behavior

OpenWebUI’s admin connection surface can accept:

- base URL: `http://subumbra-proxy:8090/t/v1`
- API key: plain `key_id` such as `openai_prod`

That is useful for testing and diagnostics, but it is not the supported durable
production authority. The supported production source of truth remains the env
file plus `ENABLE_PERSISTENT_CONFIG=False`.

If you use the admin UI or admin API for a temporary test:

1. point it at `http://subumbra-proxy:8090/t/v1`
2. use a plain `key_id`
3. finish the test
4. restart OpenWebUI so the env-defined production config is re-applied

## Path C — OpenWebUI -> LiteLLM -> Subumbra

For the aggregator path, point OpenWebUI at standalone LiteLLM:

```text
OPENAI_API_BASE_URL=http://litellm:4000/v1
OPENAI_API_KEY=<LITELLM_MASTER_KEY>
```

This path is in-scope for Round 43 because:

- OpenWebUI still speaks OpenAI-compatible requests
- LiteLLM remains app-owned outside `/opt/subumbra`
- Subumbra proof remains visible in `subumbra-proxy` logs

## Rotation Proof

The supported rotation proof is zero-restart:

```bash
cd /opt/subumbra
docker compose --profile bootstrap run --rm -T bootstrap --rotate openai_prod
```

Then send a fresh OpenWebUI request and confirm in `subumbra-proxy` logs that:

- `key_id=openai_prod`
- the request still succeeds
- no containers were restarted

Do **not** add `--force-recreate` to the rotation proof. Per-key rotation does
not require a service restart.

## Functional Checks

### Path A model discovery

```bash
curl -sS http://127.0.0.1:10199/health
```

Then from OpenWebUI, load the models list and confirm proxy logs show:

```text
key_id=openai_prod method=GET target_url=https://api.openai.com/v1/models
```

### Path C LiteLLM check

With OpenWebUI pointed at `http://litellm:4000/v1`, confirm proxy logs show the
underlying provider route, for example:

```text
key_id=anthropic_prod method=POST target_url=https://api.anthropic.com/v1/messages
```

### Fail-closed check

An unscoped key ID must fail closed:

```bash
curl -sS -i \
  -H 'Authorization: Bearer definitely_not_allowed' \
  http://127.0.0.1:10199/t/v1/models
```

Expected result: non-200 failure from the proxy path.

## Operator Checklist

1. Put the OpenWebUI key IDs you want to use into `PROXY_ALLOWED_KEYS` during bootstrap.
2. Confirm `subumbra-proxy` health is `worker_auth":"ok"`.
3. Set `OPENAI_API_BASE_URL=http://subumbra-proxy:8090/t/v1`.
4. Set `OPENAI_API_KEY=<plain key_id>`.
5. Set `ENABLE_PERSISTENT_CONFIG=False`.
6. Clean legacy direct-provider DB state once.
7. Restart OpenWebUI.
8. Confirm the live request path in proxy logs.
