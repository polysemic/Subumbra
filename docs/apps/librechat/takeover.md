# LibreChat Takeover

*Canonical takeover path for moving an existing LibreChat install onto Subumbra.*

## Scope

This guide covers takeover of an existing standard single-operator LibreChat
install that is not already on the supported Subumbra path.

This path proves:

- existing operator login continuity
- existing conversation continuity
- one custom LibreChat endpoint routed through Subumbra
- fail-closed behavior for an invalid or unscoped Subumbra key ID

This path does not cover plugins, agents, MCP, tool store, OAuth, SSO, or
multi-endpoint LibreChat setups.

## Before-State Assumptions

Before takeover:

1. LibreChat is already installed in its own directory, for example
   `/opt/librechat`.
2. The LibreChat stack is already running.
3. The operator can log in with an existing account.
4. At least one conversation exists for that account.
5. The install is not already using the supported Subumbra path.
6. The install uses the standard upstream compose shape expected by the proven
   override template.

The before-state is operator-established. The verifier asserts it, but it does
not register users or seed MongoDB.

## Required Preflight Checks

Confirm Subumbra is ready:

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

Confirm the chosen LibreChat key ID appears in `PROXY_ALLOWED_KEYS`.

Confirm the LibreChat before-state is not already on Subumbra:

```bash
cd /opt/librechat
grep '^SUBUMBRA_KEY_ID=' .env || true
grep -R 'subumbra-proxy' librechat.yaml 2>/dev/null || true
grep -R 'subumbra-net' docker-compose.override.yml 2>/dev/null || true
```

For a valid takeover before-state, these checks should not show active Subumbra
configuration.

## Exact Cut-Over Steps

1. Remove or comment out uncommented direct-provider secret lines in `.env`.

   Examples:

   ```text
   OPENAI_API_KEY=
   ANTHROPIC_API_KEY=
   GOOGLE_KEY=
   ASSISTANTS_API_KEY=
   AZURE_API_KEY=
   ```

2. Add the Subumbra key ID:

   ```text
   SUBUMBRA_KEY_ID=<plain-key-id>
   ```

3. Copy the takeover `librechat.yaml` into the LibreChat root:

   ```bash
   cp /path/to/templates/librechat.yaml /opt/librechat/librechat.yaml
   ```

4. Copy the takeover compose override into the LibreChat root:

   ```bash
   cp /path/to/templates/docker-compose.override.yml /opt/librechat/docker-compose.override.yml
   ```

5. Recreate the stack:

   ```bash
   cd /opt/librechat
   docker compose up -d --force-recreate
   ```

The supported endpoint shape is:

```yaml
name: "Subumbra"
apiKey: "${SUBUMBRA_KEY_ID}"
baseURL: "http://subumbra-proxy:8090/t/v1"
models:
  fetch: true
```

Keep the endpoint name exactly `Subumbra`. The verifier and LibreChat chat API
path are endpoint-name-sensitive.

## Continuity Verification

Before takeover, log in as the operator and count the first page of
conversations:

```text
GET /api/convos?limit=25
```

After takeover, run the same authenticated request and confirm:

```text
after_count >= before_count
```

The verifier uses `conversations.length` from the response body. It does not dump
MongoDB data.

## Fail-Closed Verification

Fail closed means:

- LibreChat attempts a normal routed chat
- `subumbra-proxy` cannot resolve or use the configured key ID
- the caller-facing chat attempt returns a non-200 failure, or LibreChat returns
  a stream ID before surfacing the upstream failure
- proxy logs show the invalid key was denied

The verified target expectation for the direct proxy failure path is `502`.

## Operator Notes

- LibreChat custom endpoints are configured in `librechat.yaml`, not in the chat
  UI.
- Direct-provider secrets left in `.env` can keep built-in provider routes
  available and bypass Subumbra.
- User-level saved keys are inert for the fixed Subumbra endpoint because
  `apiKey: "${SUBUMBRA_KEY_ID}"` does not use `user_provided`.
- If an operator has used LibreChat's admin config override system, DB overrides
  may supersede YAML by endpoint name. This round does not prove automatic
  cleanup for that case; treat it as a cleanup-required edge case outside the
  default proof path.
- Default cache behavior is unaffected by Redis. If an operator has explicitly
  changed cache namespace behavior away from the default, stale config cache may
  require manual cache or service intervention.
- Existing conversations that referenced older endpoint names may show expected
  unavailable-endpoint UX after replacement.

## Known Limitations

- This guide proves the standard single-operator existing-install path only.
- Admin-config-override installs are outside the default proof path.
- Non-default cache namespace customization is outside the default proof path.
- The override template assumes the standard upstream LibreChat compose services:
  `api`, `mongodb`, `meilisearch`, `vectordb`, and `rag_api`.
- This guide does not claim that every historical or customized LibreChat
  deployment shape can be auto-migrated.

## Operator Checklist

1. Confirm Subumbra proxy health is `{"status":"ok","worker_auth":"ok"}`.
2. Confirm the chosen key ID appears in `PROXY_ALLOWED_KEYS`.
3. Confirm the LibreChat before-state is not already on Subumbra.
4. Confirm operator login works and at least one conversation exists.
5. Remove direct-provider secrets from LibreChat `.env`.
6. Set `SUBUMBRA_KEY_ID=<plain-key-id>`.
7. Copy `librechat.yaml` and `docker-compose.override.yml`.
8. Run `docker compose up -d --force-recreate`.
9. Confirm the `Subumbra` endpoint appears.
10. Confirm routed chat succeeds and proxy logs show the Subumbra route.
