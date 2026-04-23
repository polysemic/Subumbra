# AppName — Install

Replace this file with the clean/fresh install path for the app.

## Scope

State:

- what this install path proves
- what is deferred
- whether takeover/migration is covered separately

## Prerequisites

Include the standard Subumbra readiness checks:

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

## Supported Env Shape

Document the approved operator-facing config shape and point to any extracted
template under `templates/`.

## Cut-Over Steps

Provide the exact install/update/recreate steps needed for the app.

## Operator Notes

List only app-specific caveats, limits, and governance notes.

## Persistence and Purge

If the app persists config to SQLite, a named volume, or another local data
store, `docker compose up -d --force-recreate` recreates the container but does
not purge persisted app state.

A corrected config file may not take effect if the app continues to treat its
persisted database or data directory as authoritative.

If behavior remains broken after correcting the config:

1. Stop the container
2. Remove the container
3. Remove the app's data directory or named volume
4. Restart with the corrected config

## Fail-Closed Check

Document the expected fail-closed behavior for invalid or unscoped keys.

## Operator Checklist

End with a short checklist operators can follow without rereading the whole doc.
