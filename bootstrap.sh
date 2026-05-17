#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

env_file=".env"
bootstrap_file=".env.bootstrap"

# Discover manifest: prefer subumbra.yaml, fall back to subumbra.json.
manifest_file=""
if [[ -f "subumbra.yaml" ]]; then
    manifest_file="subumbra.yaml"
elif [[ -f "subumbra.json" ]]; then
    manifest_file="subumbra.json"
fi

# Resolve primary bootstrap subcommand (may appear after flags, e.g. --revoke-key … --offline).
mode=""
for arg in "$@"; do
    case "$arg" in
        --upgrade|--nuke|--rotate|--push-registry|--provision|--revoke-key|--add-adapter|--revoke-adapter|--publish-policy|--update-tunnel|--update-access|--help|-h|--list-key-ids|--list-adapters|--status)
            mode="$arg"
            break
            ;;
    esac
done
if [[ -z "$mode" ]]; then
    mode="${1:-}"
fi

if [[ "$mode" == "--upgrade" ]]; then
    if [[ ! -f "$env_file" ]]; then
        echo "ERROR: $env_file not found. Create it (e.g. cp .env.example .env), run ./bootstrap.sh once, then use --upgrade." >&2
        exit 1
    fi
    echo ""
    echo "▶  Subumbra upgrade — rebuild images and recreate containers"
    echo "   Docker volumes (e.g. encrypted keys) are not removed by this step."
    echo ""
    docker compose build
    docker compose --profile bootstrap build bootstrap
    docker compose up -d --force-recreate
    python3 "$repo_root/scripts/subumbra-print-adapters.py" "$repo_root/$env_file"
    exit 0
fi

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
volume_args+=(-v "$repo_root/$manifest_file:/app/manifest:ro")
if [[ -d "$repo_root/templates" ]]; then
    volume_args+=(-v "$repo_root/templates:/app/user-templates:ro")
fi

if [[ -z "$manifest_file" ]]; then
    echo "ERROR: manifest not found. Create subumbra.yaml (preferred) or subumbra.json." >&2
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

# Full bootstrap / --nuke / --rotate: wizard may prompt (hidden input needs /dev/tty).
# Day-2 commands may prompt for Cloudflare credentials or runtime token values.
# Use -it when the host has a TTY; use -T for CI/pipes (set CF_* in the environment).
bootstrap_rc=0
if [[ "$mode" == "--rotate" || "$mode" == "--nuke" || -z "$mode" ]]; then
    if [[ -t 0 ]]; then
        run_io_flags=(-it)
    else
        run_io_flags=(-T)
    fi
    docker compose --profile bootstrap run "${run_io_flags[@]}" --rm \
        "${volume_args[@]}" \
        "${env_args[@]}" \
        bootstrap "$@" || bootstrap_rc=$?
elif [[ "$mode" == "--push-registry" || "$mode" == "--provision" || "$mode" == "--revoke-key" || "$mode" == "--add-adapter" || "$mode" == "--revoke-adapter" || "$mode" == "--publish-policy" || "$mode" == "--update-tunnel" || "$mode" == "--update-access" || "$mode" == "--status" ]]; then
    if [[ -t 0 ]]; then
        run_io_flags=(-it)
    else
        run_io_flags=(-T)
    fi
    docker compose --profile bootstrap run "${run_io_flags[@]}" --rm \
        "${volume_args[@]}" \
        "${env_args[@]}" \
        bootstrap "$@" || bootstrap_rc=$?
else
    docker compose --profile bootstrap run -T --rm \
        "${volume_args[@]}" \
        "${env_args[@]}" \
        bootstrap "$@" </dev/null || bootstrap_rc=$?
fi

if [[ $bootstrap_rc -eq 0 ]]; then
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
fi

if [[ $bootstrap_rc -eq 0 && ( -z "$mode" || "$mode" == "--nuke" ) ]]; then
    echo ""
    echo "▶  Starting / refreshing core stack (docker compose up -d --force-recreate)"
    docker compose up -d --force-recreate
    python3 "$repo_root/scripts/subumbra-print-adapters.py" "$repo_root/$env_file" || true
fi

exit "$bootstrap_rc"
