# LibreChat Install

*Canonical direct LibreChat app-owned Subumbra integration.*

LibreChat is not part of the core `/opt/subumbra` compose stack. The supported
model is:

- Subumbra core runs in `/opt/subumbra`
- LibreChat runs in its own install, for example `/opt/librechat`
- LibreChat talks to `subumbra-proxy` over the OpenAI-compatible transparent
  path

## Host Vs Docker-Internal Ports

Use the host-published port only for operator checks from the VPS host:

- Subumbra proxy health: `http://127.0.0.1:10199/health`
- LibreChat UI/API: `http://127.0.0.1:3080`

Use the Docker-internal service address from app containers on `subumbra-net`:

- LibreChat custom endpoint base URL:
  `http://subumbra-proxy:8090/t/v1`

Do not replace `subumbra-proxy:8090` with `127.0.0.1:10199` inside the
LibreChat container config.

## Scope

This install path proves:

- direct Docker install of LibreChat
- one custom LibreChat endpoint routed through Subumbra
- normal LibreChat user login plus authenticated session chat proof
- fail-closed behavior for an invalid or unscoped Subumbra key ID

Deferred:

- takeover of an existing LibreChat install
- agents API, plugins, MCP, tool store, OAuth, SSO
- multi-endpoint LibreChat setups

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

The Subumbra key ID you plan to use for LibreChat must already appear in
`PROXY_ALLOWED_KEYS`.

## Supported Config Shape

LibreChat custom endpoints use three files:

- `.env`
- `librechat.yaml`
- `docker-compose.override.yml`

For the Subumbra path:

- `SUBUMBRA_KEY_ID` is the plain Subumbra key ID, not a real provider secret
- `librechat.yaml` defines exactly one custom endpoint
- `docker-compose.override.yml` mounts `librechat.yaml`, attaches the LibreChat
  services to `subumbra-net`, and preserves explicit service names on that
  network so internal DNS stays stable
- `models.fetch: true` lets LibreChat request the available model list through
  the Subumbra OpenAI-compatible path, while `default` remains as a fallback

Use the staged templates in `templates/`:

- [templates/librechat.env](./templates/librechat.env)
- [templates/librechat.yaml](./templates/librechat.yaml)
- [templates/docker-compose.override.yml](./templates/docker-compose.override.yml)

Important:

- do **not** use `apiKey: "user_provided"`
- do **not** put a real OpenAI key in LibreChat for the Subumbra endpoint
- keep the endpoint base URL on the Docker-internal proxy path
- after changing `librechat.yaml`, recreate the LibreChat `api` service so the
  mounted config is re-read on startup

## Cut-Over Steps

1. Install LibreChat in its own directory:

   ```bash
   git clone https://github.com/danny-avila/LibreChat.git /opt/librechat
   cd /opt/librechat
   cp .env.example .env
   ```

2. Append the staged env excerpt:

   ```bash
   cat /path/to/templates/librechat.env >> /opt/librechat/.env
   ```

   Then edit:

   - `SUBUMBRA_KEY_ID=<plain-key-id>`
   - set `UID` / `GID` if your host requires explicit values

3. Copy the staged LibreChat config files into the LibreChat root:

   ```bash
   cp /path/to/templates/librechat.yaml /opt/librechat/librechat.yaml
   cp /path/to/templates/docker-compose.override.yml /opt/librechat/docker-compose.override.yml
   ```

4. Start LibreChat:

   ```bash
   cd /opt/librechat
   docker compose up -d
   ```

5. Verify the containers started and the config mounted cleanly:

   ```bash
   docker compose ps
   docker compose logs api
   curl -sS http://127.0.0.1:3080/api/config
   ```

   If you change `librechat.yaml` later, reload the config with:

   ```bash
   cd /opt/librechat
   docker compose up -d --force-recreate api
   ```

6. Open LibreChat in the browser and register the first account.

7. Before running the verifier, export the LibreChat login used for that
   account:

   ```bash
   export LIBRECHAT_EMAIL='<librechat-login-email>'
   export LIBRECHAT_PASSWORD='<librechat-login-password>'
   ```

## Operator Notes

- LibreChat custom endpoints are configured in `librechat.yaml`, not in the
  chat UI.
- LibreChat custom-endpoint chat uses the normal authenticated user session.
  It does **not** use the Agents API or LibreChat API keys.
- The first registered LibreChat account becomes the admin account.
- If `librechat.yaml` is invalid, LibreChat fails fast on startup. Use
  `docker compose logs api` first.

## Fail-Closed Check

Fail closed means:

- LibreChat attempts a normal routed chat
- `subumbra-proxy` cannot resolve or use the configured key ID
- the caller-facing chat attempt returns a non-200 failure
- the verified target expectation for this path is `502`

## Operator Checklist

1. Confirm `http://127.0.0.1:10199/health` returns `worker_auth":"ok"`.
2. Confirm the chosen `SUBUMBRA_KEY_ID` appears in `PROXY_ALLOWED_KEYS`.
3. Set `SUBUMBRA_KEY_ID=<plain-key-id>` in LibreChat `.env`.
4. Keep `baseURL: http://subumbra-proxy:8090/t/v1` in `librechat.yaml`.
5. Confirm `apiKey: "${SUBUMBRA_KEY_ID}"`, `models.fetch: true`, and no
   `user_provided`.
6. Register the first LibreChat user through the UI.
7. Export `LIBRECHAT_EMAIL` and `LIBRECHAT_PASSWORD` before verification.
8. Confirm the routed chat proof in `subumbra-proxy` logs.
