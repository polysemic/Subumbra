#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# test-local.sh — smoke-test the CF Worker against a local wrangler dev server
#
# Prerequisites:
#   1. Copy worker/.dev.vars.example to worker/.dev.vars and fill in your local test secrets:
#        WORKER_PRIVATE_KEY=<base64 RSA-4096 PKCS#8 DER test key>
#        WORKER_KEY_FINGERPRINT=sha256:<hex fingerprint of test key>
#        SUBUMBRA_ACCESS_TOKEN=<any hex string, must match what you send>
#        SUBUMBRA_HMAC_KEY=<any hex string>
#   2. Start the dev server in a separate terminal:
#        cd worker && npx wrangler dev --local
#   3. Run this script: bash test-local.sh
#
# The dev server binds to http://localhost:8787 by default.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

BASE_URL="${WORKER_URL:-http://localhost:8787}"
DEV_VARS_FILE="$(dirname "$0")/.dev.vars"

# ── Read SUBUMBRA_ACCESS_TOKEN from .dev.vars ────────────────────────────────────
if [[ -f "$DEV_VARS_FILE" ]]; then
  VALID_TOKEN="$(grep '^SUBUMBRA_ACCESS_TOKEN=' "$DEV_VARS_FILE" | cut -d= -f2- | tr -d '[:space:]')"
else
  echo "WARNING: $DEV_VARS_FILE not found."
  echo "         Copy worker/.dev.vars.example and set SUBUMBRA_ACCESS_TOKEN=<value> and WORKER_PRIVATE_KEY=<value>"
  echo "         Using placeholder token — tests 1 and 2 will still pass; 3 and 4 may not."
  echo ""
  VALID_TOKEN="test-token-placeholder"
fi

# A syntactically valid base64 blob of the right minimum size:
# format is nonce[12] || ciphertext[n] || tag[16] — minimum 28 bytes base64-encoded.
# This is deliberately wrong crypto so decryption fails (expected for test 4).
DUMMY_CIPHERTEXT="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # 28 zero-bytes in base64

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
# Test 3 — Valid token, missing ciphertext field → 400
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "── Test 3: Valid token, missing ciphertext ───────────────────────────────"
echo "   Expect: HTTP 400 (ciphertext field absent — input validation should fire)"

BODY=$(curl -s -X POST "$BASE_URL/proxy" \
  -H "Content-Type: application/json" \
  -H "X-Subumbra-Token: $VALID_TOKEN" \
  -d '{"provider":"anthropic","target_url":"https://api.anthropic.com/v1/messages","enc_version":2}')
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/proxy" \
  -H "Content-Type: application/json" \
  -H "X-Subumbra-Token: $VALID_TOKEN" \
  -d '{"provider":"anthropic","target_url":"https://api.anthropic.com/v1/messages","enc_version":2}')

check "Valid token, missing ciphertext → 400" "400" "$STATUS" "$BODY"

# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Valid token, valid ciphertext format → decryption failure
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "── Test 4: Valid token, plausible ciphertext ────────────────────────────"
echo "   Expect: HTTP 500 (passes all validation; decryption fails on bad ciphertext)"
echo "   OR:     HTTP 503 if WORKER_PRIVATE_KEY is not set in .dev.vars"

BODY=$(curl -s -X POST "$BASE_URL/proxy" \
  -H "Content-Type: application/json" \
  -H "X-Subumbra-Token: $VALID_TOKEN" \
  -d "{
    \"ciphertext\": \"$DUMMY_CIPHERTEXT\",
    \"wrapped_dek\": \"$DUMMY_CIPHERTEXT\",
    \"enc_version\": 2,
    \"provider\":   \"anthropic\",
    \"target_url\": \"https://api.anthropic.com/v1/messages\",
    \"method\":     \"POST\",
    \"headers\":    {\"content-type\": \"application/json\"},
    \"body\":       {\"model\": \"claude-opus-4-5\", \"max_tokens\": 1, \"messages\": [{\"role\": \"user\", \"content\": \"hi\"}]}
  }")
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/proxy" \
  -H "Content-Type: application/json" \
  -H "X-Subumbra-Token: $VALID_TOKEN" \
  -d "{
    \"ciphertext\": \"$DUMMY_CIPHERTEXT\",
    \"wrapped_dek\": \"$DUMMY_CIPHERTEXT\",
    \"enc_version\": 2,
    \"provider\":   \"anthropic\",
    \"target_url\": \"https://api.anthropic.com/v1/messages\",
    \"method\":     \"POST\",
    \"headers\":    {\"content-type\": \"application/json\"},
    \"body\":       {\"model\": \"claude-opus-4-5\", \"max_tokens\": 1, \"messages\": [{\"role\": \"user\", \"content\": \"hi\"}]}
  }")

# Accept 500 (decrypt failed) or 503 (secrets not configured) — both mean
# the request passed all auth and validation checks and reached the crypto step.
if [[ "$STATUS" == "500" || "$STATUS" == "503" ]]; then
  echo "  PASS  Valid token + ciphertext format → HTTP $STATUS (reached crypto layer)"
  echo "        body: $BODY"
  PASS=$((PASS + 1))
else
  echo "  FAIL  Valid token + ciphertext format — expected 500 or 503, got HTTP $STATUS"
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
