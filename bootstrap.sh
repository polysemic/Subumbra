#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

env_file=".env"
bootstrap_file=".env.bootstrap"

if [[ ! -f "$env_file" ]]; then
    cp .env.example "$env_file"
fi

if [[ -e "$env_file" && ! -f "$env_file" ]]; then
    echo "ERROR: $env_file exists but is not a regular file." >&2
    exit 1
fi

declare -a volume_args=()
declare -a env_args=()

volume_args+=(-v "$repo_root/$env_file:/app/host-env:rw")

if [[ -f "$bootstrap_file" ]]; then
    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" == *=* ]] || continue

        key="${line%%=*}"
        value="${line#*=}"
        key="${key#"${key%%[![:space:]]*}"}"
        key="${key%"${key##*[![:space:]]}"}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        if [[ ${#value} -ge 2 ]]; then
            quote="${value:0:1}"
            if [[ "$quote" == "'" || "$quote" == "\"" ]]; then
                if [[ "${value: -1}" == "$quote" ]]; then
                    value="${value:1:${#value}-2}"
                fi
            fi
        fi

        if [[ "$key" == "SUBUMBRA_POLICY_PATH" ]]; then
            if [[ ! -f "$value" ]]; then
                echo "ERROR: SUBUMBRA_POLICY_PATH is missing or not a regular file: $value" >&2
                exit 1
            fi
            mount_path="/app/bootstrap-policy/$(basename "$value")"
            volume_args+=(-v "$repo_root/$value:$mount_path:ro")
            env_args+=(-e "SUBUMBRA_POLICY_PATH=$mount_path")
        elif [[ "$key" =~ ^IMPORT_PATH_([0-9]+)$ ]]; then
            idx="${BASH_REMATCH[1]}"
            label_key="IMPORT_APP_LABEL_${idx}"
            label="$(sed -n "s/^${label_key}=//p" "$bootstrap_file" | tail -n 1)"
            if [[ -z "$label" ]]; then
                echo "ERROR: ${label_key} is required when ${key} is set." >&2
                exit 1
            fi
            if [[ ! -f "$value" ]]; then
                echo "ERROR: ${key} path is missing or not a regular file: $value" >&2
                exit 1
            fi
            mount_path="/app/bootstrap-imports/${idx}/$(basename "$value")"
            volume_args+=(-v "$repo_root/$value:$mount_path:ro")
            env_args+=(-e "${key}=${mount_path}")
            env_args+=(-e "${label_key}=${label}")
        fi
    done < "$bootstrap_file"
fi

docker compose --profile bootstrap run -T --rm \
    "${volume_args[@]}" \
    "${env_args[@]}" \
    bootstrap "$@"

if [[ -f "$bootstrap_file" ]]; then
    if command -v shred >/dev/null 2>&1; then
        shred -u "$bootstrap_file"
    else
        python3 - "$bootstrap_file" <<'PY'
import os
import sys

path = sys.argv[1]
size = os.path.getsize(path)
with open(path, "r+b") as fh:
    fh.write(b"\x00" * size)
    fh.flush()
    os.fsync(fh.fileno())
os.remove(path)
PY
    fi
fi
