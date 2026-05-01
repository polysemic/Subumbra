#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# test-local.sh — fail-closed smoke-test the CF Worker against a local wrangler dev server
#
# Prerequisites:
#   1. Copy worker/.dev.vars.example to worker/.dev.vars and fill in the local test values:
#        SUBUMBRA_ADAPTER_TOKENS=["<adapter token to accept>"]
#        SUBUMBRA_SETUP_TOKEN=<setup token for /setup/keygen smoke>
#        SUBUMBRA_HMAC_KEY=<any base64 value>
#   2. Start the dev server in a separate terminal:
#        cd worker && npx wrangler dev --local
#   3. Run this script: bash test-local.sh
#
# This script checks health and fail-closed auth surfaces only. It does not
# provision a local KV namespace or vault custody state.
# The dev server binds to http://localhost:8787 by default.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

BASE_URL="${WORKER_URL:-http://localhost:8787}"
DEV_VARS_FILE="$(dirname "$0")/.dev.vars"

# ── Read local test tokens from .dev.vars ────────────────────────────────────────
if [[ -f "$DEV_VARS_FILE" ]]; then
  VALID_TOKEN="$(grep '^SUBUMBRA_ADAPTER_TOKENS=' "$DEV_VARS_FILE" | sed -E 's/^SUBUMBRA_ADAPTER_TOKENS=\[\"([^\"]+)\"\].*/\1/' | tr -d '[:space:]')"
  SETUP_TOKEN="$(grep '^SUBUMBRA_SETUP_TOKEN=' "$DEV_VARS_FILE" | cut -d= -f2- | tr -d '[:space:]')"
else
  echo "WARNING: $DEV_VARS_FILE not found."
  echo "         Copy worker/.dev.vars.example and set SUBUMBRA_ADAPTER_TOKENS=[\"<token>\"]"
  echo "         and SUBUMBRA_SETUP_TOKEN=<value>."
  echo "         Using placeholder tokens — unauthorized checks still pass; success checks may not."
  echo ""
  VALID_TOKEN="test-token-placeholder"
  SETUP_TOKEN="test-setup-token-placeholder"
fi

PASS=0
FAIL=0

# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# Health check — confirm the server is up before running auth tests
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "── Health check ─────────────────────────────────────────────────────────"
HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health")
if [[ "$HEALTH_STATUS" != "200" ]]; then
  echo "  ERROR: /health returned HTTP $HEALTH_STATUS"
  echo "  Is wrangler dev running?  cd worker && npx wrangler dev --local"
  exit 1
fi
echo "  OK — server is up at $BASE_URL"

# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Missing X-Subumbra-Token header → 401
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Wrong X-Subumbra-Token → 401
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Valid token, /auth-ping → 200
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "── Test 3: Valid token on /auth-ping ─────────────────────────────────────"
echo "   Expect: HTTP 200 (adapter token accepted on the current auth surface)"

BODY=$(curl -s -X GET "$BASE_URL/auth-ping" \
  -H "X-Subumbra-Token: $VALID_TOKEN")
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$BASE_URL/auth-ping" \
  -H "X-Subumbra-Token: $VALID_TOKEN")

check "Valid token on /auth-ping → 200" "200" "$STATUS" "$BODY"

# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Missing setup bearer on /setup/keygen → 401
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "── Test 4: Missing setup bearer on /setup/keygen ────────────────────────"
echo "   Expect: HTTP 401 (setup endpoint must fail closed without bearer auth)"

BODY=$(curl -s -X POST "$BASE_URL/setup/keygen")
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/setup/keygen")

check "Missing setup bearer → 401" "401" "$STATUS" "$BODY"

# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Valid setup bearer, one-shot setup behavior
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "── Test 5: Valid setup bearer on /setup/keygen ──────────────────────────"
echo "   Expect: HTTP 200 on first initialization, or HTTP 409 if already initialized"

BODY=$(curl -s -X POST "$BASE_URL/setup/keygen" \
  -H "Authorization: Bearer $SETUP_TOKEN")
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/setup/keygen" \
  -H "Authorization: Bearer $SETUP_TOKEN")

if [[ "$STATUS" == "200" || "$STATUS" == "409" ]]; then
  echo "  PASS  Valid setup bearer → HTTP $STATUS"
  echo "        body: $BODY"
  PASS=$((PASS + 1))
else
  echo "  FAIL  Valid setup bearer — expected HTTP 200 or 409, got HTTP $STATUS"
  echo "        body: $BODY"
  FAIL=$((FAIL + 1))
fi

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "─────────────────────────────────────────────────────────────────────────"
echo "  Results: $PASS passed, $FAIL failed"
echo "─────────────────────────────────────────────────────────────────────────"
echo ""

[[ $FAIL -eq 0 ]]
