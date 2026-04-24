# Bifrost AI Gateway — Install

## Scope

This install path proves:

- Bifrost running as a Docker service on the `subumbra-net` network
- OpenAI requests routed through Subumbra transparent sidecar (`/t`)
- `config_store` enabled with SQLite for persistent configuration on the mounted
  data path
- Fail-closed behavior for unscoped key IDs

Deferred:

- multi-provider configs (additional providers beyond OpenAI)
- Bifrost UI behind Cloudflare Access

Migration from a running Bifrost instance is covered separately in
[takeover.md](./takeover.md).

## Host Vs Docker-Internal Ports

Use the host-published port only for operator checks from the VPS host:

- host health check: `http://127.0.0.1:10199/health`

Use the Docker-internal service address from app containers on `subumbra-net`:

- Bifrost `network_config.base_url`: `http://subumbra-proxy:8090/t`

Do not replace `subumbra-proxy:8090` with `127.0.0.1:10199` in the Bifrost
container config.

## Prerequisites

Standard Subumbra readiness:

```bash
cd /opt/subumbra
docker compose ps
curl -sS http://127.0.0.1:10199/health
grep '^PROXY_ALLOWED_KEYS=' .env
```

Expected proxy health:

```json
{"status":"ok","worker_auth":"ok"}
```

Verify the key ID you intend to use appears in `PROXY_ALLOWED_KEYS`. If not,
add it before proceeding:

```bash
# in /opt/subumbra/.env
PROXY_ALLOWED_KEYS=...,<key_id>
docker compose up -d subumbra-proxy
```

## Supported Env Shape

Bifrost reads its provider API key from the environment:

```
BIFROST_OPENAI_KEY=<key_id>
```

The value is the plain Subumbra key ID (not a real API key). Extract from
[templates/bifrost.env](./templates/bifrost.env).

## Cut-Over Steps

1. Create the Bifrost data directory:

   ```bash
   mkdir -p /opt/bifrost-data
   ```

2. Write the config file. Copy and edit the template:

   ```bash
   cp /path/to/templates/config-subumbra.json /opt/bifrost-data/config.json
   ```

   Replace `<key_id>` with the Subumbra key ID for the OpenAI provider (e.g.,
   `openai_prod`). The key value field must remain `env.BIFROST_OPENAI_KEY`.

   The config-store path inside the copied file must remain:

   ```json
   "/app/data/config.db"
   ```

3. Set the environment file (for use with `--env-file` or Docker Compose):

   ```bash
   cp /path/to/templates/bifrost.env /opt/bifrost.env
   # edit: BIFROST_OPENAI_KEY=<key_id>
   ```

4. Place `docker-compose.yml` and start:

   ```bash
   docker compose -f /path/to/docker-compose.yml up -d
   ```

5. Verify Bifrost UI is accessible:

   ```bash
   curl -sI http://127.0.0.1:8080/
   # expect: HTTP/1.1 200 OK, Content-Type: text/html
   ```

6. Test a routed request:

   ```bash
   curl -s http://127.0.0.1:8080/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "x-bf-provider: openai" \
     -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hello"}]}'
   # expect: HTTP 200, response with "X-Subumbra-Provider" in provider_response_headers
   ```

## Operator Notes

- **base_url must be bare `/t`**: Bifrost constructs its own full API path by
  appending `/v1/chat/completions` to `network_config.base_url`. Setting
  `base_url` to `/t/v1` causes Bifrost to send `/t/v1/v1/chat/completions`
  which resolves to an invalid upstream path (404 from OpenAI).

- **SQLite persistence**: Even without `config_store` enabled, Bifrost creates
  `config.db`, `logs.db`, and WAL files on first boot. See Persistence and
  Purge below.

- **config_store**: When `config_store.enabled: true` and a persisted
  `config.db` exists on the mounted data path (for example
  `/app/data/config.db`), Bifrost loads its running config from that DB, not
  from `config.json`. A corrected `config.json` will not take effect without a
  purge.

- **Do not use `./config.db`**: a relative path resolves under `/app` in the
  container and bypasses the mounted `/app/data` volume, making the DB
  ephemeral on container recreation.

- For existing-instance migration, use [takeover.md](./takeover.md).

## Persistence and Purge

If the app persists config to SQLite, a named volume, or another local data
store, `docker compose up -d --force-recreate` recreates the container but does
not purge persisted app state.

For Bifrost specifically, if a persisted `config.db` exists on the mounted data
path, a corrected `config.json` may not take effect until that DB is purged or
the provider is updated through the API.

If behavior remains broken after correcting the config:

1. Stop the container
2. Remove the container
3. Remove the app's data directory or named volume
4. Restart with the corrected config

For Bifrost specifically:

```bash
docker compose down
docker rm -f bifrost 2>/dev/null || true
rm -rf /opt/bifrost-data
mkdir /opt/bifrost-data
cp /path/to/templates/config-subumbra.json /opt/bifrost-data/config.json
# edit config.json with correct key_id
docker compose up -d
```

## Fail-Closed Check

Bifrost with an unscoped key ID returns HTTP 502 with a provider-style error
body. The failure chain:

1. Bifrost sends request to `http://subumbra-proxy:8090/t/v1/chat/completions`
2. `subumbra-proxy` requests key from `subumbra-keys` → `403 FORBIDDEN`
3. `subumbra-proxy` returns 502 to Bifrost
4. Bifrost returns 502 to the client

Test:

```bash
curl -s -o /dev/null -w "%{http_code}" \
  http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-bf-provider: openai" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"test"}]}'
# expect: 502
```

## Operator Checklist

- [ ] Subumbra proxy health returns `{"status":"ok","worker_auth":"ok"}`
- [ ] Key ID appears in `PROXY_ALLOWED_KEYS`
- [ ] `BIFROST_OPENAI_KEY` is set to the Subumbra key ID (not a real API key)
- [ ] `network_config.base_url` is bare `http://subumbra-proxy:8090/t` (no trailing `/v1`)
- [ ] `config_store.config.path` is `/app/data/config.db`
- [ ] Bifrost UI returns HTTP 200 at the configured host port
- [ ] Routed request returns HTTP 200 with `X-Subumbra-Provider` in response headers
- [ ] Unscoped key returns HTTP 502
