#!/usr/bin/env bash
# post-bootstrap.sh — finalize bootstrap: copy runtime tokens into .env, shred .env.bootstrap
set -euo pipefail

ENV_FILE=".env"
BOOTSTRAP_FILE=".env.bootstrap"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found. Create it first: cp .env.example .env" >&2
    exit 1
fi

WIZARD_MODE=false
if [[ ! -f "$BOOTSTRAP_FILE" ]]; then
    echo "No .env.bootstrap found — wizard path, nothing to shred."
    WIZARD_MODE=true
fi

echo "Reading runtime tokens from Subumbra volume..."
RUNTIME=$(SUBUMBRA_ADAPTER_REGISTRY=x SUBUMBRA_TOKEN_LITELLM=x FORGE_TOKEN_LITELLM=x SUBUMBRA_TOKEN_PROXY=x SUBUMBRA_TOKEN_UI=x SUBUMBRA_TOKEN_PROBE=x SUBUMBRA_HMAC_KEY=x docker compose run --rm -u 0 -T subumbra-keys cat /app/data/runtime.env 2>/dev/null) || {
    echo "ERROR: Could not read /app/data/runtime.env from the subumbra-keys container." >&2
    echo "  Make sure bootstrap completed: docker compose --profile bootstrap run --rm bootstrap" >&2
    exit 1
}

_get() { printf '%s\n' "$RUNTIME" | grep "^${1}=" | cut -d= -f2- || true; }

SUBUMBRA_ADAPTER_REGISTRY=$(_get SUBUMBRA_ADAPTER_REGISTRY)
SUBUMBRA_TOKEN_LITELLM=$(_get SUBUMBRA_TOKEN_LITELLM)
if [[ -z "$SUBUMBRA_TOKEN_LITELLM" ]]; then
    SUBUMBRA_TOKEN_LITELLM=$(_get FORGE_TOKEN_LITELLM)
fi
SUBUMBRA_TOKEN_PROXY=$(_get SUBUMBRA_TOKEN_PROXY)
SUBUMBRA_TOKEN_UI=$(_get SUBUMBRA_TOKEN_UI)
SUBUMBRA_TOKEN_PROBE=$(_get SUBUMBRA_TOKEN_PROBE)
SUBUMBRA_HMAC_KEY=$(_get SUBUMBRA_HMAC_KEY)
CF_WORKER_URL=$(_get CF_WORKER_URL)
LITELLM_ALLOWED_KEYS=$(_get LITELLM_ALLOWED_KEYS)
PROXY_ALLOWED_KEYS=$(_get PROXY_ALLOWED_KEYS)
PROBE_ALLOWED_KEYS=$(_get PROBE_ALLOWED_KEYS)
UI_ALLOWED_KEYS=$(_get UI_ALLOWED_KEYS)

if [[ -z "$SUBUMBRA_ADAPTER_REGISTRY" || -z "$SUBUMBRA_TOKEN_LITELLM" || -z "$SUBUMBRA_TOKEN_PROXY" || -z "$SUBUMBRA_TOKEN_UI" || -z "$SUBUMBRA_TOKEN_PROBE" || -z "$SUBUMBRA_HMAC_KEY" || -z "$CF_WORKER_URL" ]]; then
    echo "ERROR: runtime.env is missing one or more required values." >&2
    exit 1
fi

echo "  SUBUMBRA_ADAPTER_REGISTRY : present"
echo "  SUBUMBRA_TOKEN_LITELLM    : ${SUBUMBRA_TOKEN_LITELLM:0:8}..."
echo "  SUBUMBRA_TOKEN_PROXY      : ${SUBUMBRA_TOKEN_PROXY:0:8}..."
echo "  SUBUMBRA_TOKEN_UI         : ${SUBUMBRA_TOKEN_UI:0:8}..."
echo "  SUBUMBRA_TOKEN_PROBE      : ${SUBUMBRA_TOKEN_PROBE:0:8}..."
echo "  SUBUMBRA_HMAC_KEY         : ${SUBUMBRA_HMAC_KEY:0:8}..."
echo "  CF_WORKER_URL             : $CF_WORKER_URL"

update_env() {
    local key="$1" value="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

echo ""
echo "Writing to $ENV_FILE..."
update_env "SUBUMBRA_ADAPTER_REGISTRY" "$SUBUMBRA_ADAPTER_REGISTRY"
update_env "SUBUMBRA_TOKEN_LITELLM"    "$SUBUMBRA_TOKEN_LITELLM"
update_env "SUBUMBRA_TOKEN_PROXY"      "$SUBUMBRA_TOKEN_PROXY"
update_env "SUBUMBRA_TOKEN_UI"         "$SUBUMBRA_TOKEN_UI"
update_env "SUBUMBRA_TOKEN_PROBE"      "$SUBUMBRA_TOKEN_PROBE"
update_env "SUBUMBRA_HMAC_KEY"         "$SUBUMBRA_HMAC_KEY"
update_env "CF_WORKER_URL"             "$CF_WORKER_URL"
update_env "LITELLM_ALLOWED_KEYS"      "$LITELLM_ALLOWED_KEYS"
update_env "PROXY_ALLOWED_KEYS"        "$PROXY_ALLOWED_KEYS"
update_env "PROBE_ALLOWED_KEYS"        "$PROBE_ALLOWED_KEYS"
update_env "UI_ALLOWED_KEYS"           "$UI_ALLOWED_KEYS"

VERIFY_FAILED=0
for key in SUBUMBRA_ADAPTER_REGISTRY SUBUMBRA_TOKEN_LITELLM SUBUMBRA_TOKEN_PROXY SUBUMBRA_TOKEN_UI SUBUMBRA_TOKEN_PROBE SUBUMBRA_HMAC_KEY CF_WORKER_URL; do
    if ! grep -q "^${key}=" "$ENV_FILE"; then
        echo "ERROR: Failed to write ${key} to $ENV_FILE" >&2
        VERIFY_FAILED=1
    fi
done
[[ "$VERIFY_FAILED" -ne 0 ]] && exit 1
echo "  Verified: all required values present in $ENV_FILE."

echo ""
echo "Checking for token drift in running containers..."
DRIFT=false
for svc in litellm subumbra-ui subumbra-proxy subumbra-probe; do
    if docker compose ps --status running "$svc" 2>/dev/null | grep -q "$svc"; then
        case "$svc" in
            litellm)      token_val="$SUBUMBRA_TOKEN_LITELLM" ;;
            subumbra-ui)  token_val="$SUBUMBRA_TOKEN_UI" ;;
            subumbra-proxy) token_val="$SUBUMBRA_TOKEN_PROXY" ;;
            subumbra-probe) token_val="$SUBUMBRA_TOKEN_PROBE" ;;
        esac
        running_val="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$svc" 2>/dev/null | grep "^SUBUMBRA_ACCESS_TOKEN=" | cut -d= -f2- || true)"
        if [[ -n "$running_val" && "$running_val" != "$token_val" ]]; then
            echo "  WARNING: $svc has stale token. Run: docker compose up -d --force-recreate" >&2
            DRIFT=true
        fi
    fi
done
[[ "$DRIFT" == "false" ]] && echo "  No drift detected."

if [[ "$WIZARD_MODE" == "false" ]]; then
    echo ""
    echo "Shredding $BOOTSTRAP_FILE..."
    if command -v shred &>/dev/null; then
        shred -u "$BOOTSTRAP_FILE"
        echo "  $BOOTSTRAP_FILE shredded."
    else
        python3 -c "
import os,sys; p=sys.argv[1]; s=os.path.getsize(p)
f=open(p,'r+b'); f.write(b'\x00'*s); f.flush(); os.fsync(f.fileno()); f.close(); os.remove(p)
" "$BOOTSTRAP_FILE"
        echo "  $BOOTSTRAP_FILE overwritten and deleted."
    fi
else
    echo "Wizard path — no .env.bootstrap to shred."
fi

echo ""
echo "Bootstrap complete. Next step:"
echo "  docker compose up -d --force-recreate"
