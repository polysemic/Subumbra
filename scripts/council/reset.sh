#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/council/reset.sh
  scripts/council/reset.sh --build subumbra-keys
  scripts/council/reset.sh --build subumbra-ui subumbra-proxy

Rebuild policy:
  Recreate-only is sufficient for bind-mounted changes:
    - litellm/custom_callbacks.py
    - litellm/config.yaml
    - worker/src/providers.json
  Use --build for image-built services when their source changes:
    - bootstrap/ (rebuilds image only; reset.sh does not start the bootstrap container)
    - subumbra-keys/
    - ui/
    - subumbra-proxy/
  Note: reset.sh applies DOCKER_BUILDKIT=0 for the explicit docker compose build step.
EOF
}

build_targets=()
if [[ $# -gt 0 ]]; then
    if [[ "$1" != "--build" ]]; then
        usage >&2
        exit 1
    fi
    shift
    if [[ $# -eq 0 ]]; then
        echo "ERROR: --build requires at least one service name" >&2
        usage >&2
        exit 1
    fi
    while [[ $# -gt 0 ]]; do
        case "$1" in
            bootstrap|subumbra-keys|subumbra-ui|subumbra-proxy)
                build_targets+=("$1")
                ;;
            *)
                echo "ERROR: unsupported --build target: $1" >&2
                usage >&2
                exit 1
                ;;
        esac
        shift
    done
fi

if [[ -f .env.bootstrap ]]; then
    :
elif [[ -f .env.bootstrap_bak ]]; then
    cp .env.bootstrap_bak .env.bootstrap
    echo "NOTICE: restored .env.bootstrap from .env.bootstrap_bak"
else
    echo "ERROR: fresh bootstrap required (.env.bootstrap and .env.bootstrap_bak both missing)" >&2
    exit 1
fi

if [[ ${#build_targets[@]} -gt 0 ]]; then
    DOCKER_BUILDKIT=0 docker compose build "${build_targets[@]}"
fi
docker compose up -d --force-recreate

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found for token drift check" >&2
    exit 1
fi

expected_registry="$(grep '^SUBUMBRA_ADAPTER_REGISTRY=' .env | cut -d= -f2- || true)"
expected_litellm_token="$(grep '^SUBUMBRA_TOKEN_LITELLM=' .env | cut -d= -f2- || true)"
expected_proxy_token="$(grep '^SUBUMBRA_TOKEN_PROXY=' .env | cut -d= -f2- || true)"
expected_ui_token="$(grep '^SUBUMBRA_TOKEN_UI=' .env | cut -d= -f2- || true)"
expected_probe_token="$(grep '^SUBUMBRA_TOKEN_PROBE=' .env | cut -d= -f2- || true)"

required_keys=(expected_registry expected_proxy_token expected_ui_token expected_probe_token)
if docker compose config --services 2>/dev/null | grep -qx 'litellm'; then
    required_keys+=(expected_litellm_token)
fi

for required_key in "${required_keys[@]}"; do
    if [[ -z "${!required_key}" ]]; then
        echo "ERROR: missing required Round 41.4 Subumbra value in .env (${required_key#expected_})" >&2
        exit 1
    fi
done

container_for_service() {
    case "$1" in
        litellm) echo "litellm" ;;
        subumbra-keys) echo "subumbra-keys" ;;
        subumbra-ui) echo "subumbra-ui" ;;
        subumbra-proxy) echo "subumbra-proxy" ;;
        subumbra-probe) echo "subumbra-probe" ;;
        *)
            return 1
            ;;
    esac
}

env_key_for_service() {
    case "$1" in
        subumbra-keys) echo "SUBUMBRA_ADAPTER_REGISTRY" ;;
        *) echo "SUBUMBRA_ACCESS_TOKEN" ;;
    esac
}

expected_value_for_service() {
    case "$1" in
        subumbra-keys) printf '%s' "$expected_registry" ;;
        litellm) printf '%s' "$expected_litellm_token" ;;
        subumbra-ui) printf '%s' "$expected_ui_token" ;;
        subumbra-proxy) printf '%s' "$expected_proxy_token" ;;
        subumbra-probe) printf '%s' "$expected_probe_token" ;;
        *)
            return 1
            ;;
    esac
}

echo "Checking for token drift in running containers..."
drift=0
for svc in litellm subumbra-keys subumbra-ui subumbra-proxy subumbra-probe; do
    if docker compose ps --status running "$svc" 2>/dev/null | grep -q "$svc"; then
        container_name="$(container_for_service "$svc")"
        env_key="$(env_key_for_service "$svc")"
        expected_value="$(expected_value_for_service "$svc")"
        running_token="$(
            docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_name" 2>/dev/null \
            | grep "^${env_key}=" | cut -d= -f2- || true
        )"
        if [[ -n "$running_token" && "$running_token" != "$expected_value" ]]; then
            echo "ERROR: token drift detected for $svc (running container ${env_key} does not match .env)" >&2
            drift=1
        fi
    fi
done

if [[ "$drift" -ne 0 ]]; then
    exit 1
fi

echo "No token drift detected."
