# Subumbra Testing Guide

*How to verify a running Subumbra deployment — core health, sidecar flow,
standalone LiteLLM, and council proof capture.*

Assumes the core stack is already running. See
[docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md).

## 1. Environment Setup

```bash
export CF_WORKER_URL="$(sed -n 's/^CF_WORKER_URL=//p' .env)"
```

> Do not `source .env` — it contains JSON in `SUBUMBRA_ADAPTER_REGISTRY`.

## 2. Core Health Checks

```bash
docker compose ps
curl -sS "$CF_WORKER_URL/health"
curl -sS http://127.0.0.1:10199/health
curl -sS http://127.0.0.1:6563/api/status
```

Healthy core means:

- Worker `/health` returns `{"status":"ok",...}`
- proxy `/health` returns `{"status":"ok","worker_auth":"ok"}`
- UI status is reachable

## 3. Sidecar (`subumbra-proxy`) Tests

### Transparent route test

```bash
curl -sS -w "\nHTTP %{http_code}\n" \
  http://127.0.0.1:10199/t/user \
  -H "Authorization: Bearer github_prod" \
  -H "Accept: application/json"
```

### Explicit `/v1/request` test

```bash
PROXY_TOKEN="$(sed -n 's/^SUBUMBRA_TOKEN_PROXY=//p' .env)"

curl -sS http://127.0.0.1:10199/v1/request \
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

## 4. Standalone LiteLLM Test

Standalone LiteLLM lives outside `/opt/subumbra`. See
[docs/apps/litellm/install.md](/home/eric/git/Subumbra/docs/apps/litellm/install.md)
for setup.

Once it is running under `/opt/litellm`, a real request through that app-owned
path is the supported proof:

```bash
export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' /opt/litellm/.env)"

curl http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4",
    "messages": [{"role": "user", "content": "say hi in 3 words"}],
    "max_tokens": 20
  }'
```

## 5. Security Contract Tests

### Wrong token rejection

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" \
  http://127.0.0.1:10199/v1/request \
  -H "Authorization: Bearer badtoken" \
  -H "Content-Type: application/json" \
  -d '{"key_id":"anthropic_prod","target_url":"https://api.anthropic.com","method":"POST","headers":{},"body":{}}'
```

Expected: `401` or `403`.

### Worker-auth visibility

If proxy `/health` reports `worker_auth: "stale"` or `worker_auth: "unreachable"`,
the operator should treat that as a runtime-auth or reachability problem before
debugging provider credentials.

## 6. Audit Log Queries

```bash
docker exec subumbra-keys python3 -c "
import sqlite3
db = sqlite3.connect('/app/audit/audit.db')

print('=== Verdict breakdown ===')
for r in db.execute('SELECT verdict, reason_code, COUNT(*) FROM audit_events GROUP BY verdict, reason_code ORDER BY 3 DESC').fetchall():
    print(r)
"
```

## 7. Adapter-Probe

```bash
docker compose run --rm subumbra-probe python probe.py
docker compose run --rm subumbra-probe python probe.py --key anthropic_prod
```

## 8. Council Verification Harness

Preferred fresh-state proof:

```bash
./scripts/council/clean-run.sh --round <round-dir-name> --agent <llm>
```

Fallback:

```bash
./scripts/council/reset.sh
AGENT=<llm> ./scripts/council/verify.sh <round-dir-name>
```
