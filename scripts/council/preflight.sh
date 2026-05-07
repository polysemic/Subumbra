#!/usr/bin/env bash
set -euo pipefail

PREFLIGHT_TIMEOUT_SECONDS="${PREFLIGHT_TIMEOUT_SECONDS:-60}"
PREFLIGHT_INTERVAL_SECONDS="${PREFLIGHT_INTERVAL_SECONDS:-1}"

deadline=$(( $(date +%s) + PREFLIGHT_TIMEOUT_SECONDS ))

poll_docker_health() {
    local name="$1"
    local status=""
    while :; do
        if status="$(docker inspect --format '{{.State.Health.Status}}' "$name" 2>/dev/null)"; then
            if [[ "$status" == "healthy" ]]; then
                printf '[OK] %s docker-health=healthy\n' "$name"
                return 0
            fi
        else
            status="unavailable"
        fi

        if (( $(date +%s) >= deadline )); then
            printf '[FAIL] %s docker-health=%s\n' "$name" "$status"
            return 1
        fi
        sleep "$PREFLIGHT_INTERVAL_SECONDS"
    done
}

poll_litellm() {
    local headers body status version
    headers="$(mktemp)"
    body="$(mktemp)"
    trap 'rm -f "$headers" "$body"' RETURN

    while :; do
        if curl -sS -D "$headers" -o "$body" http://127.0.0.1:4000/health/readiness >/dev/null 2>&1; then
            status="$(awk 'toupper($1) ~ /^HTTP\// {code=$2} END {print code}' "$headers")"
            if [[ "$status" == "200" ]]; then
                version="$(
                    python3 - "$body" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], "r", encoding="utf-8"))
    print(data.get("litellm_version", "unknown"))
except Exception:
    print("unknown")
PY
                )"
                printf '[OK] litellm http=200 version=%s\n' "$version"
                return 0
            fi
        else
            status="curl-error"
        fi

        if (( $(date +%s) >= deadline )); then
            if [[ "$status" == "curl-error" ]]; then
                printf '[FAIL] litellm http=unreachable version=unknown\n'
            else
                printf '[FAIL] litellm http=%s version=unknown\n' "${status:-unknown}"
            fi
            return 1
        fi
        sleep "$PREFLIGHT_INTERVAL_SECONDS"
    done
}

bundled_litellm_present() {
    docker compose config --services 2>/dev/null | grep -Fxq "litellm"
}

poll_ui() {
    local headers body status subumbra_keys_error
    headers="$(mktemp)"
    body="$(mktemp)"
    trap 'rm -f "$headers" "$body"' RETURN

    while :; do
        if curl -sS -D "$headers" -o "$body" http://127.0.0.1:6563/api/status >/dev/null 2>&1; then
            status="$(awk 'toupper($1) ~ /^HTTP\// {code=$2} END {print code}' "$headers")"
            if [[ "$status" =~ ^2[0-9][0-9]$ ]]; then
                subumbra_keys_error="$(
                    python3 - "$body" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], "r", encoding="utf-8"))
    value = data.get("subumbra_keys_error")
    print("" if value is None else str(value))
except Exception:
    print("__PARSE_ERROR__")
PY
                )"
                if [[ "$subumbra_keys_error" == "" ]]; then
                    printf '[OK] subumbra-ui http=%s subumbra_keys_error=null\n' "$status"
                else
                    printf '[WARN] subumbra-ui http=%s subumbra_keys_error="%s"\n' "$status" "$subumbra_keys_error"
                fi
                return 0
            fi
        else
            status="curl-error"
        fi

        if (( $(date +%s) >= deadline )); then
            if [[ "$status" == "curl-error" ]]; then
                printf '[FAIL] subumbra-ui http=unreachable subumbra_keys_error=unknown\n'
            else
                printf '[FAIL] subumbra-ui http=%s subumbra_keys_error=unknown\n' "${status:-unknown}"
            fi
            return 1
        fi
        sleep "$PREFLIGHT_INTERVAL_SECONDS"
    done
}

fail=0
poll_docker_health "${SUBUMBRA_KEYS_CONTAINER:-subumbra-keys}" || fail=1
poll_docker_health "${SUBUMBRA_PROXY_CONTAINER:-subumbra-proxy}" || fail=1
if bundled_litellm_present; then
    poll_litellm || fail=1
else
    printf '[OK] litellm bundled-service=absent\n'
fi
poll_ui || fail=1

exit "$fail"
