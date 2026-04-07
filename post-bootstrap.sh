#!/usr/bin/env bash
# post-bootstrap.sh — finalize bootstrap: copy runtime tokens into .env, shred .env.bootstrap
#
# Security properties:
#   - This script never reads raw API keys. It only reads the scoped forge
#     runtime secrets already destined for .env plus CF_WORKER_URL.
#   - Those values are runtime tokens already destined for .env — no new
#     exposure.
#   - Values pass through bash variables (RAM only, never written to a temp file).
#   - .env.bootstrap is shredded only after all required values are verified in .env.
#
# Usage:
#   ./post-bootstrap.sh
#
# Prerequisites:
#   - Bootstrap has completed successfully
#   - forge-keys container is running  (docker compose ps)
#   - .env exists (copy from .env.example if not)
#   - .env.bootstrap exists only if you used automation/CI mode (wizard path has no file to shred)

set -euo pipefail

ENV_FILE=".env"
BOOTSTRAP_FILE=".env.bootstrap"

# ── Preflight checks ──────────────────────────────────────────────────────────

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found." >&2
    echo "  Create it first: cp .env.example .env" >&2
    exit 1
fi

# ── Check bootstrap mode ─────────────────────────────────────────────────────
# If .env.bootstrap is absent, the interactive wizard was used — nothing to shred.
# If it exists, we shred it after copying tokens (automation/CI path).
WIZARD_MODE=false
if [[ ! -f "$BOOTSTRAP_FILE" ]]; then
    echo "No .env.bootstrap found — wizard path, nothing to shred."
    WIZARD_MODE=true
fi

# ── Read runtime tokens from the forge-keys volume ───────────────────────────

echo "Reading runtime tokens from forge volume..."
RUNTIME=$(docker compose run --rm -u 0 -T forge-keys cat /app/data/runtime.env 2>/dev/null) || {
    echo "ERROR: Could not read /app/data/runtime.env from the forge-keys container." >&2
    echo "  Make sure forge-keys is running: docker compose ps" >&2
    echo "  If bootstrap failed mid-run, re-run:" >&2
    echo "    Interactive: docker compose --profile bootstrap run --rm -it bootstrap" >&2
    echo "    Automation:  docker compose --profile bootstrap run --rm bootstrap" >&2
    exit 1
}

# ── Extract runtime values needed by Docker services ─────────────────────────
# WORKER_KEY_FINGERPRINT is intentionally NOT extracted here. It is pushed
# directly to CF Secrets by bootstrap and is not consumed by any Docker service.
# Its presence in runtime.env is for audit/diagnostic purposes only.

FORGE_ADAPTER_REGISTRY=$(printf '%s\n' "$RUNTIME" | grep '^FORGE_ADAPTER_REGISTRY=' | cut -d= -f2-)
FORGE_TOKEN_LITELLM=$(printf '%s\n' "$RUNTIME" | grep '^FORGE_TOKEN_LITELLM=' | cut -d= -f2-)
FORGE_TOKEN_PROXY=$(printf '%s\n' "$RUNTIME" | grep '^FORGE_TOKEN_PROXY=' | cut -d= -f2-)
FORGE_TOKEN_UI=$(printf '%s\n' "$RUNTIME" | grep '^FORGE_TOKEN_UI=' | cut -d= -f2-)
FORGE_TOKEN_PROBE=$(printf '%s\n' "$RUNTIME" | grep '^FORGE_TOKEN_PROBE=' | cut -d= -f2-)
FORGE_HMAC_KEY=$(printf '%s\n' "$RUNTIME" | grep '^FORGE_HMAC_KEY=' | cut -d= -f2-)
CF_WORKER_URL=$(printf '%s\n' "$RUNTIME" | grep '^CF_WORKER_URL=' | cut -d= -f2-)

if [[ -z "$FORGE_ADAPTER_REGISTRY" || -z "$FORGE_TOKEN_LITELLM" || -z "$FORGE_TOKEN_PROXY" || -z "$FORGE_TOKEN_UI" || -z "$FORGE_TOKEN_PROBE" || -z "$FORGE_HMAC_KEY" || -z "$CF_WORKER_URL" ]]; then
    echo "ERROR: runtime.env is missing one or more required values." >&2
    exit 1
fi

echo "  FORGE_ADAPTER_REGISTRY : present"
echo "  FORGE_TOKEN_LITELLM   : ${FORGE_TOKEN_LITELLM:0:8}... (truncated for display)"
echo "  FORGE_TOKEN_PROXY     : ${FORGE_TOKEN_PROXY:0:8}... (truncated for display)"
echo "  FORGE_TOKEN_UI        : ${FORGE_TOKEN_UI:0:8}... (truncated for display)"
echo "  FORGE_TOKEN_PROBE     : ${FORGE_TOKEN_PROBE:0:8}... (truncated for display)"
echo "  FORGE_HMAC_KEY        : ${FORGE_HMAC_KEY:0:8}... (truncated for display)"
echo "  CF_WORKER_URL         : $CF_WORKER_URL"

# ── Write into .env (replace existing lines, append if missing) ───────────────

update_env() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

echo ""
echo "Writing to $ENV_FILE..."
update_env "FORGE_ADAPTER_REGISTRY" "$FORGE_ADAPTER_REGISTRY"
update_env "FORGE_TOKEN_LITELLM" "$FORGE_TOKEN_LITELLM"
update_env "FORGE_TOKEN_PROXY" "$FORGE_TOKEN_PROXY"
update_env "FORGE_TOKEN_UI" "$FORGE_TOKEN_UI"
update_env "FORGE_TOKEN_PROBE" "$FORGE_TOKEN_PROBE"
update_env "FORGE_HMAC_KEY"     "$FORGE_HMAC_KEY"
update_env "CF_WORKER_URL"      "$CF_WORKER_URL"

# ── Verify all required values landed in .env ────────────────────────────────

VERIFY_FAILED=0
for key in FORGE_ADAPTER_REGISTRY FORGE_TOKEN_LITELLM FORGE_TOKEN_PROXY FORGE_TOKEN_UI FORGE_TOKEN_PROBE FORGE_HMAC_KEY CF_WORKER_URL; do
    if ! grep -q "^${key}=" "$ENV_FILE"; then
        echo "ERROR: Failed to write ${key} to $ENV_FILE" >&2
        VERIFY_FAILED=1
    fi
done

if [[ "$VERIFY_FAILED" -ne 0 ]]; then
    echo "Aborting — tokens were NOT written to $ENV_FILE. Fix the errors above and re-run." >&2
    exit 1
fi

echo "  Verified: all required values present in $ENV_FILE."

# ── Token-drift detection ─────────────────────────────────────────────────────
echo ""
echo "Checking for token drift in running containers..."
DRIFT=false
container_for_service() {
    case "$1" in
        litellm) echo "litellm" ;;
        forge-keys) echo "forge-keys" ;;
        ui) echo "keyvault-ui" ;;
        keyvault-proxy) echo "keyvault-proxy" ;;
        adapter-probe) echo "adapter-probe" ;;
        *)
            return 1
            ;;
    esac
}

expected_value_for_service() {
    case "$1" in
        forge-keys) printf '%s' "$FORGE_ADAPTER_REGISTRY" ;;
        litellm) printf '%s' "$FORGE_TOKEN_LITELLM" ;;
        ui) printf '%s' "$FORGE_TOKEN_UI" ;;
        keyvault-proxy) printf '%s' "$FORGE_TOKEN_PROXY" ;;
        adapter-probe) printf '%s' "$FORGE_TOKEN_PROBE" ;;
        *)
            return 1
            ;;
    esac
}

env_key_for_service() {
    case "$1" in
        forge-keys) printf '%s' "FORGE_ADAPTER_REGISTRY" ;;
        *) printf '%s' "FORGE_ACCESS_TOKEN" ;;
    esac
}

for svc in forge-keys litellm ui keyvault-proxy adapter-probe; do
    if docker compose ps --status running "$svc" 2>/dev/null | grep -q "$svc"; then
        container_name="$(container_for_service "$svc")"
        env_key="$(env_key_for_service "$svc")"
        expected_value="$(expected_value_for_service "$svc")"
        running_value="$(
            docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_name" 2>/dev/null \
            | grep "^${env_key}=" | cut -d= -f2- || true
        )"
        if [[ -n "$running_value" && "$running_value" != "$expected_value" ]]; then
            echo "  ⚠  $svc is running with a stale ${env_key}" >&2
            DRIFT=true
        fi
    fi
done

if [[ "$DRIFT" == "true" ]]; then
    echo "" >&2
    echo "════════════════════════════════════════════════════════════════════" >&2
    echo "  WARNING: Token drift detected." >&2
    echo "  Running containers hold stale forge runtime auth configuration." >&2
    echo "  The CF Worker will reject requests until services are recreated." >&2
    echo "" >&2
    echo "  Required action:" >&2
    echo "    docker compose up -d --force-recreate" >&2
    echo "" >&2
    echo "  Until you do this, API calls will fail with provider-shaped 401s" >&2
    echo "  (DeepseekException, OpenAIException, etc.) due to CF Worker" >&2
    echo "  rejection, and the UI dashboard will fail to load." >&2
    echo "════════════════════════════════════════════════════════════════════" >&2
else
    echo "  No drift detected (containers match .env token or are not running)."
fi

# ── Shred .env.bootstrap (only if it exists — automation/CI path) ─────────────
if [[ "$WIZARD_MODE" == "false" ]]; then
    echo ""
    echo "Shredding $BOOTSTRAP_FILE..."

    if command -v shred &>/dev/null; then
        shred -u "$BOOTSTRAP_FILE"
    elif command -v srm &>/dev/null; then
        srm -f "$BOOTSTRAP_FILE"
    else
        # Best-effort: overwrite with zeros then delete (no shred/srm available)
        python3 -c "
import os, sys
path = sys.argv[1]
size = os.path.getsize(path)
with open(path, 'r+b') as f:
    f.write(b'\x00' * size)
    f.flush()
    os.fsync(f.fileno())
os.remove(path)
" "$BOOTSTRAP_FILE"
    fi

    if [[ -f "$BOOTSTRAP_FILE" ]]; then
        echo "WARNING: shred command unavailable or failed — delete manually:" >&2
        echo "  Linux : shred -u $BOOTSTRAP_FILE" >&2
        echo "  macOS : rm -P $BOOTSTRAP_FILE  (or: srm -f $BOOTSTRAP_FILE)" >&2
    else
        echo "  $BOOTSTRAP_FILE shredded."
    fi
else
    echo ""
    echo "Wizard path — no .env.bootstrap to shred."
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "Bootstrap complete. Next step:"
echo "  docker compose up -d --force-recreate"
