# Subumbra Testing Guide

*How to verify a running Subumbra deployment — health checks, functional tests,
audit log queries, and council harness usage.*

Assumes a stack is already running. See
[`docs/subumbra-install.md`](./subumbra-install.md) for setup.

---

## 1. Environment Setup

Export the two values you will need most:

```bash
export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' .env)"
export CF_WORKER_URL="$(sed -n 's/^CF_WORKER_URL=//p' .env)"
```

> **Do not `source .env`** — mangles `SUBUMBRA_ADAPTER_REGISTRY` JSON and crashes
> `subumbra-keys` on next compose start.

---

## 2. Service Health Checks

```bash
# Container status
docker compose ps

# subumbra-keys health (internal — must run from inside another container)
docker exec litellm python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://subumbra-keys:9090/health').read().decode())"

# Cloudflare Worker health
curl -sS "$CF_WORKER_URL/health"

# Sidecar health
curl -sS http://127.0.0.1:8090/health

# LiteLLM health
curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://127.0.0.1:4000/health

# UI status
curl -sS http://127.0.0.1:8080/api/status
```

All five healthy = stack is ready for functional testing.

---

## 3. End-to-End LiteLLM Test

Send a real request through the full decrypt/proxy chain:

```bash
curl http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4",
    "messages": [{"role": "user", "content": "say hi in 3 words"}],
    "max_tokens": 20
  }'
```

A streaming response from the provider confirms: subumbra record fetch → nonce
validation → CF Worker decrypt → Durable Object upstream call → response.

---

## 4. Sidecar (`subumbra-proxy`) Tests

### Direct `/v1/request` call

```bash
# Get the proxy token from .env
PROXY_TOKEN="$(sed -n 's/^SUBUMBRA_TOKEN_PROXY=//p' .env)"

curl -sS http://127.0.0.1:8090/v1/request \
  -H "Authorization: Bearer $PROXY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "key_id": "anthropic_prod",
    "target_url": "https://api.anthropic.com/v1/messages",
    "method": "POST",
    "headers": {"anthropic-version": "2023-06-01"},
    "body": {
      "model": "claude-haiku-4-5-20251001",
      "max_tokens": 10,
      "messages": [{"role": "user", "content": "hi"}]
    }
  }'
```

### Transparent route test (GitHub example)

```bash
curl -sS -w "\nHTTP %{http_code}\n" \
  http://127.0.0.1:8090/t/user \
  -H "Authorization: Bearer github_prod" \
  -H "Accept: application/json"
```

---

## 5. Security Contract Tests

### Nonce replay rejection

Run the same request twice with the same nonce — the second must return 401:

```bash
PROXY_TOKEN="$(sed -n 's/^SUBUMBRA_TOKEN_PROXY=//p' .env)"
HMAC_KEY="$(sed -n 's/^SUBUMBRA_HMAC_KEY=//p' .env)"
NONCE=$(python3 -c "import secrets; print(secrets.token_hex(16))")
TS=$(date -u +%s)
SIG=$(python3 -c "
import hmac, hashlib, sys
key_id, ts, nonce, hmac_key = sys.argv[1:]
print(hmac.new(hmac_key.encode(), f'{key_id}:{ts}:{nonce}'.encode(), hashlib.sha256).hexdigest())
" anthropic_prod "$TS" "$NONCE" "$HMAC_KEY")

# First call — expect 200
curl -sS -o /dev/null -w "First:  HTTP %{http_code}\n" \
  http://subumbra-keys:9090/keys/anthropic_prod 2>/dev/null || \
  docker exec litellm curl -sS -o /dev/null -w "First:  HTTP %{http_code}\n" \
  -H "X-Subumbra-Token: $PROXY_TOKEN" \
  -H "X-Subumbra-Nonce: $NONCE" \
  -H "X-Subumbra-Timestamp: $TS" \
  -H "X-Subumbra-Signature: $SIG" \
  http://subumbra-keys:9090/keys/anthropic_prod

# Repeat with same nonce — expect 401 nonce_reused
```

### Wrong token rejection

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" \
  http://127.0.0.1:8090/v1/request \
  -H "Authorization: Bearer badtoken" \
  -H "Content-Type: application/json" \
  -d '{"key_id":"anthropic_prod","target_url":"https://api.anthropic.com","method":"POST","headers":{},"body":{}}'
```

Expected: `401` or `403`.

---

## 6. Audit Log Queries

```bash
docker exec subumbra-keys python3 -c "
import sqlite3
db = sqlite3.connect('/app/audit/audit.db')

print('=== Verdict breakdown ===')
for r in db.execute('SELECT verdict, reason_code, COUNT(*) FROM audit_events GROUP BY verdict, reason_code ORDER BY 3 DESC').fetchall():
    print(r)

print()
print('=== Recent events (last 10) ===')
for r in db.execute('SELECT timestamp, adapter_id, key_id, verdict, reason_code FROM audit_events ORDER BY id DESC LIMIT 10').fetchall():
    print(r)

print()
print('Nonce table count:', db.execute('SELECT COUNT(*) FROM subumbra_nonces').fetchone()[0])
"
```

A healthy deployment shows:

- `allow / allowed` events for normal fetches
- `deny / nonce_reused` for replayed requests
- `deny / nonce_missing` for requests without nonces
- `deny / key_scope_denied` for out-of-scope key_id requests

---

## 7. Adapter-Probe

The subumbra-probe container runs functional checks against the CF Worker for
configured key_ids.

```bash
# Run probe against all PROBE_ALLOWED_KEYS
docker compose run --rm subumbra-probe python probe.py

# Or specify a key explicitly
docker compose run --rm subumbra-probe python probe.py --key anthropic_prod
```

Expected output: `PASS provider <name>: HTTP 200` for each key tested.

If you see `403 key_scope_denied`, the key_id is not in the probe's allowed
scope — check `PROBE_ALLOWED_KEYS` in `.env`.

---

## 8. Council Verification Harness

### Preferred: clean-run (fresh-state proof capture)

```bash
./scripts/council/clean-run.sh --round round-40-broader-decoupling-security-hardening --agent claude
```

This runs bootstrap + verify in an isolated workspace. Artifacts land in:

```text
council/clean-run-harness/runs/<run-id>/
```

### Fallback: direct reset + verify

```bash
./scripts/council/reset.sh
AGENT=claude ./scripts/council/verify.sh round-40-broader-decoupling-security-hardening
```

Use `reset.sh --build <services>` when image-built service code changed.

### Evidence taxonomy

| Label | Meaning |
|-------|---------|
| `PROOF` | Harness-generated run artifacts — official PASS evidence |
| `DIAG` | Logs, manual curl — diagnostic only |
| Manual | Checks the approved plan explicitly requires beyond the harness |

Cite artifact paths rather than pasting output:

```text
See council/round-40-broader-decoupling-security-hardening/runs/claude-20260411T163601/summary.txt
```

---

## 9. Reporting Template

When reporting VPS test results, always include:

```text
Branch: <branch-name>
Commit: <short-sha>
VPS path: /opt/subumbra
Commands run:
  - <command>
  - <command>
Result: PASS / FAIL
Findings:
  - <finding> → code fix / doc fix / no change needed
```
