#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

env_file=".env"
bootstrap_file=".env.bootstrap"
manifest_file="subumbra.json"

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

if [[ ! -f "$manifest_file" ]]; then
    echo "ERROR: $manifest_file is required for manifest-era bootstrap and must be a regular file." >&2
    exit 1
fi

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

        if [[ "$key" == "CF_WORKER_NAME" ]]; then
            env_args+=(-e "CF_WORKER_NAME=${value}")
        fi
    done < "$bootstrap_file"
fi

# --rotate and full-bootstrap modes need stdin for the interactive wizard / nuke prompt.
# Non-interactive subcommands keep stdin closed.
mode="${1:-}"
if [[ "$mode" == "--rotate" || "$mode" == "--nuke" || -z "$mode" ]]; then
    docker compose --profile bootstrap run -T --rm \
        "${volume_args[@]}" \
        "${env_args[@]}" \
        bootstrap "$@"
else
    docker compose --profile bootstrap run -T --rm \
        "${volume_args[@]}" \
        "${env_args[@]}" \
        bootstrap "$@" </dev/null
fi

if [[ -f "$bootstrap_file" && "$mode" != "--provision" && "$mode" != "--add-adapter" && "$mode" != "--revoke-adapter" && "$mode" != "--publish-policy" ]]; then
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
elif [[ -f "$bootstrap_file" && ( "$mode" == "--provision" || "$mode" == "--add-adapter" || "$mode" == "--revoke-adapter" || "$mode" == "--publish-policy" ) ]]; then
    echo "WARNING: .env.bootstrap retained after $mode for additional secure mutation steps. Shred it manually when repairs are complete." >&2
fi
