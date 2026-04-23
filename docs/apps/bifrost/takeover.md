# Bifrost Migration to Subumbra Routing

This guide covers migrating an existing Bifrost instance that currently routes
requests directly to OpenAI to route through Subumbra instead. It applies when
your Bifrost instance has `config_store` enabled (SQLite-backed state).

If you are setting up Bifrost for the first time, see
[`install.md`](./install.md) instead.

---

## Background: Why config.json edits don't work

When `config_store.enabled: true` (the recommended configuration), Bifrost reads
its running configuration exclusively from a SQLite database (`config.db`).
On first boot, it seeds `config.db` from `config.json`. After that, `config.json`
is ignored — editing the file and restarting the container will **not** change
routing.

> **Important**: The `config.db` path in your config must point inside the
> mounted data volume (for example, `/app/data/config.db`). Using a relative
> path like `./config.db` places the database outside the volume and makes it
> ephemeral.

Two supported migration paths exist. Strategy A is preferred for most operators.

---

## Strategy A — Live API Update (Preferred)

No restart or data loss required. Changes take effect immediately.

The Bifrost admin API (`/api/providers`) is unauthenticated by default. If you
have explicitly enabled Bifrost admin auth via `governance.auth_config`, add
`-u "<username>:<password>"` to the `curl` command below and use the values
from your `BIFROST_ADMIN_USERNAME` / `BIFROST_ADMIN_PASSWORD` environment
variables.

**Prerequisites:**
- Bifrost container is running
- `BIFROST_OPENAI_KEY` is set to your Subumbra `key_id` in the container environment

**Step 1: Send the migration API call**

Replace `<bifrost-host-port>` with the host port your Bifrost container
exposes (for example, `8080`).

```bash
curl -s -X PUT http://127.0.0.1:<bifrost-host-port>/api/providers/openai \
  -H "Content-Type: application/json" \
  -d '{
    "id": "openai",
    "name": "OpenAI",
    "model_provider": "openai",
    "weight": 1,
    "keys": [
      {
        "name": "subumbra-key",
        "value": "env.BIFROST_OPENAI_KEY",
        "models": ["gpt-4o-mini"],
        "weight": 1,
        "enabled": true
      }
    ],
    "network_config": {
      "base_url": "http://subumbra-proxy:8090/t"
    },
    "concurrency_and_buffer_size": {
      "concurrency": 1000,
      "buffer_size": 5000
    }
  }'
```

> **Note on `base_url`**: The value must be `http://subumbra-proxy:8090/t` —
> the bare `/t` path with no `/v1` suffix. Bifrost appends the provider path
> itself. Bifrost does **not** support environment variable references in
> `base_url`.

**Step 2: Verify the change**

Send a test chat request. Check that `subumbra-proxy` logs show:
- `key_id=<your_key_id>`
- `target_url=https://api.openai.com/v1/chat/completions`
- `complete key_id=<your_key_id> status=200`

The migration takes effect immediately — no restart needed.

---

## Strategy B — Surgical Purge (File-Driven Alternative)

Use this if you prefer file-driven configuration or cannot use the API. Chat
history (`logs.db`) is preserved. The container must be stopped.

**Step 1: Stop the container**

```bash
docker compose stop bifrost   # or: docker stop bifrost
```

**Step 2: Delete only the config database**

```bash
# From your bifrost data directory (for example, ./bifrost-data on the host):
rm -f config.db config.db-shm config.db-wal
# Do NOT delete logs.db — it contains request history
```

**Step 3: Update config.json for Subumbra routing**

Replace your `config.json` with the Subumbra configuration. Ensure
`BIFROST_OPENAI_KEY` is set to your Subumbra `key_id` in the container
environment before starting.

```json
{
  "providers": {
    "openai": {
      "keys": [
        {
          "name": "subumbra-key",
          "value": "env.BIFROST_OPENAI_KEY",
          "models": ["gpt-4o-mini"],
          "weight": 1.0
        }
      ],
      "network_config": {
        "base_url": "http://subumbra-proxy:8090/t"
      }
    }
  },
  "config_store": {
    "enabled": true,
    "type": "sqlite",
    "config": {
      "path": "/app/data/config.db"
    }
  }
}
```

**Step 4: Start the container**

```bash
docker compose up -d bifrost
```

Bifrost re-seeds `config.db` from the new `config.json`. The `logs.db` file
(chat history) is untouched.

---

## Verifying the migration

After either strategy, send a test request and check proxy logs:

```bash
docker logs subumbra-proxy --tail 20
```

Look for lines like:

```text
key_id=<your_key_id> target_url=https://api.openai.com/v1/chat/completions ...
complete key_id=<your_key_id> status=200
```

If you see the old direct-routing target URL without `key_id=`, the migration
did not take effect. Verify that `BIFROST_OPENAI_KEY` is set correctly in the
container environment and retry.
