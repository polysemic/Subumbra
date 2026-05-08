#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# test-local.sh — fail-closed smoke-test the CF Worker against a local wrangler dev server
#
# Prerequisites:
#   1. Copy worker/.dev.vars.example to worker/.dev.vars and fill in the local test values:
#        SUBUMBRA_ADAPTER_TOKENS=[{"id":"local-test","token":"<adapter token to accept>"}]
#        SUBUMBRA_SETUP_TOKEN=<setup token for /setup/keygen smoke>
#        SUBUMBRA_HMAC_KEY=<any base64 value>
#   2. Start the dev server in a separate terminal:
#        cd worker && npx wrangler dev --local
#   3. Run this script:
#        TEST_MODE=normal bash test-local.sh
#        TEST_MODE=no-vault bash test-local.sh
#
# This script checks health and fail-closed auth surfaces only. It does not
# provision a local KV namespace or vault custody state. On deployed stacks,
# setup bearer checks may return 403 after bootstrap because the setup token is
# transient and removed once initialization completes.
# The dev server binds to http://localhost:8787 by default.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

BASE_URL="${WORKER_URL:-http://localhost:8787}"
DEV_VARS_FILE="$(dirname "$0")/.dev.vars"
TEST_MODE="${TEST_MODE:-normal}"

if [[ -f "$DEV_VARS_FILE" ]]; then
  VALID_TOKEN="$(python3 - "$DEV_VARS_FILE" <<'PY'
import json, sys
for line in open(sys.argv[1], encoding="utf-8"):
    if not line.startswith("SUBUMBRA_ADAPTER_TOKENS="):
        continue
    payload = line.split("=", 1)[1].strip()
    entries = json.loads(payload)
    token = entries[0]["token"] if entries else ""
    print(token, end="")
    break
PY
)"
  SETUP_TOKEN="$(grep '^SUBUMBRA_SETUP_TOKEN=' "$DEV_VARS_FILE" | cut -d= -f2- | tr -d '[:space:]')"
else
  echo "WARNING: $DEV_VARS_FILE not found."
  echo '         Copy worker/.dev.vars.example and set SUBUMBRA_ADAPTER_TOKENS=[{"id":"local-test","token":"<token>"}]'
  echo "         and SUBUMBRA_SETUP_TOKEN=<value>."
  echo "         Using placeholder tokens — unauthorized checks still pass; success checks may not."
  echo ""
  VALID_TOKEN="test-token-placeholder"
  SETUP_TOKEN="test-setup-token-placeholder"
fi

PASS=0
FAIL=0

check() {
  local label="$1"
  local expected_status="$2"
  local actual_status="$3"
  local body="$4"

  if [[ "$actual_status" == "$expected_status" ]]; then
    echo "  PASS  $label (HTTP $actual_status)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label — expected HTTP $expected_status, got HTTP $actual_status"
    echo "        body: $body"
    FAIL=$((FAIL + 1))
  fi
}

check_exact_body() {
  local label="$1"
  local expected_body="$2"
  local actual_body="$3"

  if [[ "$actual_body" == "$expected_body" ]]; then
    echo "  PASS  $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label — expected body: $expected_body"
    echo "        actual body: $actual_body"
    FAIL=$((FAIL + 1))
  fi
}

check_absent_fragment() {
  local label="$1"
  local fragment="$2"
  local body="$3"

  if grep -Fqi -- "$fragment" <<<"$body"; then
    echo "  FAIL  $label — body contains forbidden fragment: $fragment"
    echo "        body: $body"
    FAIL=$((FAIL + 1))
  else
    echo "  PASS  $label"
    PASS=$((PASS + 1))
  fi
}

echo "TEST_MODE=$TEST_MODE"

echo ""
echo "── Health check ─────────────────────────────────────────────────────────"
HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health")
if [[ "$HEALTH_STATUS" != "200" ]]; then
  echo "  ERROR: /health returned HTTP $HEALTH_STATUS"
  echo "  Is wrangler dev running?  cd worker && npx wrangler dev --local"
  exit 1
fi
echo "  OK — server is up at $BASE_URL"
HEALTH_BODY="$(curl -sS "$BASE_URL/health")"
check_exact_body "Health body is minimal" '{"status":"ok"}' "$HEALTH_BODY"

if [[ "$TEST_MODE" == "normal" ]]; then
  echo ""
  echo "── Test 1: Missing X-Subumbra-Token header ─────────────────────────────────"
  echo "   Expect: HTTP 401 (no token supplied — should be rejected immediately)"

  BODY=$(curl -s -X POST "$BASE_URL/proxy" \
    -H "Content-Type: application/json" \
    -d '{"ciphertext":"abc","provider":"anthropic","target_url":"https://api.anthropic.com/v1/messages"}')
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/proxy" \
    -H "Content-Type: application/json" \
    -d '{"ciphertext":"abc","provider":"anthropic","target_url":"https://api.anthropic.com/v1/messages"}')

  check "Missing X-Subumbra-Token → 401" "401" "$STATUS" "$BODY"

  echo ""
  echo "── Test 2: Wrong X-Subumbra-Token ──────────────────────────────────────────"
  echo "   Expect: HTTP 401 (token mismatch — timing-safe comparison must reject)"

  BODY=$(curl -s -X POST "$BASE_URL/proxy" \
    -H "Content-Type: application/json" \
    -H "X-Subumbra-Token: definitely-wrong-token" \
    -d '{"ciphertext":"abc","provider":"anthropic","target_url":"https://api.anthropic.com/v1/messages"}')
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/proxy" \
    -H "Content-Type: application/json" \
    -H "X-Subumbra-Token: definitely-wrong-token" \
    -d '{"ciphertext":"abc","provider":"anthropic","target_url":"https://api.anthropic.com/v1/messages"}')

  check "Wrong X-Subumbra-Token → 401" "401" "$STATUS" "$BODY"

  echo ""
  echo "── Test 3: Valid token on /auth-ping ─────────────────────────────────────"
  echo "   Expect: HTTP 200 (adapter token accepted on the current auth surface)"

  BODY=$(curl -s -X GET "$BASE_URL/auth-ping" \
    -H "X-Subumbra-Token: $VALID_TOKEN")
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$BASE_URL/auth-ping" \
    -H "X-Subumbra-Token: $VALID_TOKEN")

  check "Valid token on /auth-ping → 200" "200" "$STATUS" "$BODY"

  echo ""
  echo "── Test 4: Missing setup bearer on /setup/keygen ────────────────────────"
  echo "   Expect: HTTP 401 (setup endpoint must fail closed without bearer auth)"

  BODY=$(curl -s -X POST "$BASE_URL/setup/keygen")
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/setup/keygen")

  check "Missing setup bearer → 401" "401" "$STATUS" "$BODY"

  echo ""
  echo "── Test 5: Valid setup bearer on /setup/keygen ──────────────────────────"
  echo "   Expect: HTTP 200 on first initialization, HTTP 409 if already initialized,"
  echo "           or HTTP 403 if the deployed stack has already removed the transient setup token"

  BODY=$(curl -s -X POST "$BASE_URL/setup/keygen" \
    -H "Authorization: Bearer $SETUP_TOKEN")
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/setup/keygen" \
    -H "Authorization: Bearer $SETUP_TOKEN")

  if [[ "$STATUS" == "200" || "$STATUS" == "409" || "$STATUS" == "403" ]]; then
    echo "  PASS  Valid setup bearer → HTTP $STATUS"
    echo "        body: $BODY"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  Valid setup bearer — expected HTTP 200, 409, or 403, got HTTP $STATUS"
    echo "        body: $BODY"
    FAIL=$((FAIL + 1))
  fi

  echo ""
  echo "── Test 6: Second valid setup bearer on /setup/keygen ───────────────────"
  echo "   Expect: HTTP 409 if setup token remains active, or HTTP 403 if the"
  echo "           deployed stack has already removed the transient setup token"

  BODY=$(curl -s -X POST "$BASE_URL/setup/keygen" \
    -H "Authorization: Bearer $SETUP_TOKEN")
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/setup/keygen" \
    -H "Authorization: Bearer $SETUP_TOKEN")

  if [[ "$STATUS" == "409" || "$STATUS" == "403" ]]; then
    echo "  PASS  Second valid setup bearer → HTTP $STATUS"
    echo "        body: $BODY"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  Second valid setup bearer — expected HTTP 409 or 403, got HTTP $STATUS"
    echo "        body: $BODY"
    FAIL=$((FAIL + 1))
  fi
elif [[ "$TEST_MODE" == "no-vault" ]]; then
  echo ""
  echo "── Test 1: Valid setup bearer on /setup/keygen without vault binding ────"
  echo "   Expect: HTTP 503 with exact structured JSON body"

  BODY=$(curl -s -X POST "$BASE_URL/setup/keygen" \
    -H "Authorization: Bearer $SETUP_TOKEN")
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/setup/keygen" \
    -H "Authorization: Bearer $SETUP_TOKEN")

  check "No-vault setup bearer → 503" "503" "$STATUS" "$BODY"
  check_exact_body "No-vault setup bearer body is exact" '{"error":"vault unavailable"}' "$BODY"

  for fragment in "TypeError" "Error:" "stack" "cf-ray" "pub_key_fp" "private_key" "Authorization"; do
    check_absent_fragment "No-vault body omits $fragment" "$fragment" "$BODY"
  done
else
  echo "ERROR: unsupported TEST_MODE=$TEST_MODE"
  exit 1
fi

echo ""
echo "─────────────────────────────────────────────────────────────────────────"
echo "  Results: $PASS passed, $FAIL failed"
echo "─────────────────────────────────────────────────────────────────────────"
echo ""

[[ $FAIL -eq 0 ]]
