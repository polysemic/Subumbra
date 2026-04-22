# OpenWebUI Takeover

*Canonical takeover path for moving an existing direct-provider OpenWebUI install
onto Subumbra.*

OpenWebUI is not part of the core `/opt/subumbra` compose stack. This guide
covers the **existing-instance takeover** path. For the clean-install path, see
[install.md](./install.md).

## Scope

This guide covers migration of an existing OpenWebUI install onto the supported
Subumbra path.

It does not change the durable production authority rules:

- env-defined provider configuration remains authoritative
- `ENABLE_PERSISTENT_CONFIG=False` remains required

## DB Cleanup — Remove Legacy Direct-Provider State

If OpenWebUI was previously pointed directly at OpenAI, Anthropic, Groq,
OpenRouter, or LiteLLM, clean the persisted connection state in `webui.db`.

Tested live-schema cleanup command:

```bash
docker exec open-webui python - <<'PY'
import json
import sqlite3

conn = sqlite3.connect("/app/backend/data/webui.db")
cur = conn.cursor()
rowid, raw = cur.execute("select rowid, data from config limit 1").fetchone()
cfg = json.loads(raw)

cfg.setdefault("openai", {})
cfg["openai"]["api_base_urls"] = ["http://subumbra-proxy:8090/t/v1"]
cfg["openai"]["api_keys"] = ["openai_prod"]
cfg["openai"]["api_configs"] = {}

cur.execute("update config set data=? where rowid=?", (json.dumps(cfg), rowid))
conn.commit()
print("updated config row", rowid)
PY
```

After cleanup, restart OpenWebUI:

```bash
cd /opt/open-webui
docker compose restart open-webui
```

The goal is not to make the DB authoritative. The goal is to remove stale
direct-provider bypass state so future admins do not inherit silent drift.

## Migration From Raw-Key / Direct-Provider Setup

If you previously used:

- `OPENAI_API_KEY=sk-...`
- `OPENAI_API_BASE_URL=https://api.openai.com/v1`
- direct Anthropic/Groq/OpenRouter URLs in OpenWebUI

then migrate in this order:

1. update `/opt/open-webui/.env` to the supported proxy route
2. set `ENABLE_PERSISTENT_CONFIG=False`
3. run the DB cleanup command above
4. restart OpenWebUI
5. confirm Subumbra traffic in proxy logs
