#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

env_file=".env"
bootstrap_file=".env.bootstrap"

require_xdg_runtime_dir() {
    local xdg_runtime_dir="${XDG_RUNTIME_DIR:-}"
    if [[ -z "$xdg_runtime_dir" ]]; then
        echo "ERROR: XDG_RUNTIME_DIR is required for Subumbra SSH agent support." >&2
        echo "Run bootstrap/update commands as a regular logged-in user so XDG_RUNTIME_DIR is set." >&2
        exit 1
    fi
    if [[ "$xdg_runtime_dir" != /* ]]; then
        echo "ERROR: XDG_RUNTIME_DIR must be an absolute path (got: $xdg_runtime_dir)." >&2
        exit 1
    fi
    install -d -m 700 "$xdg_runtime_dir/subumbra"
}

env_key_present() {
    local key="$1"
    [[ -f "$env_file" ]] || return 1
    grep -q "^${key}=" "$env_file" 2>/dev/null
}

env_key_value() {
    local key="$1"
    [[ -f "$env_file" ]] || return 0
    sed -n "s/^${key}=//p" "$env_file" 2>/dev/null | head -n 1
}

env_key_is_true() {
    local value
    value="$(env_key_value "$1" | tr '[:upper:]' '[:lower:]')"
    [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

compose_profile_args() {
    local -a profiles=()
    local has_deploy_ui=0
    local has_deploy_ssh=0
    if env_key_present "DEPLOY_UI"; then
        has_deploy_ui=1
        if env_key_is_true "DEPLOY_UI"; then
            profiles+=("--profile" "ui")
        fi
    fi
    if env_key_present "DEPLOY_SSH"; then
        has_deploy_ssh=1
        if env_key_is_true "DEPLOY_SSH"; then
            profiles+=("--profile" "ssh")
        fi
    fi
    if [[ "$has_deploy_ui" -eq 0 ]] && [[ -n "$(env_key_value UI_USERNAME)" ]]; then
        profiles+=("--profile" "ui")
    fi
    if [[ "$has_deploy_ssh" -eq 0 ]] && [[ -n "$(env_key_value SUBUMBRA_TOKEN_SSHTEST)" ]]; then
        profiles+=("--profile" "ssh")
    fi
    printf '%s\n' "${profiles[@]}"
}

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
        --upgrade|--nuke|--rotate|--add-ssh-key|--rotate-ssh-key|--revoke-ssh-key|--push-registry|--deploy-worker|--session|--provision|--revoke-key|--add-adapter|--revoke-adapter|--publish-policy|--update-tunnel|--update-access|--update-ui-auth|--update-gate|--nuke-cloudflare|--help|-h|--list-key-ids|--list-adapters|--show|--status)
            mode="$arg"
            break
            ;;
    esac
done
if [[ -z "$mode" ]]; then
    mode="${1:-}"
fi

if [[ "$mode" == "--upgrade" ]]; then
    require_xdg_runtime_dir
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
    mapfile -t _profiles < <(compose_profile_args)
    docker compose "${_profiles[@]}" up -d --force-recreate
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

# Forward Cloudflare credentials from the host environment into the container
# so non-interactive day-2 commands (--session, --push-registry, etc.) work
# without requiring .env.bootstrap or an interactive TTY when CF_API_TOKEN and
# CF_ACCOUNT_ID are already exported in the host shell (e.g. CI, verify-round.sh).
if [[ -n "${CF_API_TOKEN:-}" ]]; then
    env_args+=(-e "CF_API_TOKEN=${CF_API_TOKEN}")
fi
if [[ -n "${CF_ACCOUNT_ID:-}" ]]; then
    env_args+=(-e "CF_ACCOUNT_ID=${CF_ACCOUNT_ID}")
fi

volume_args+=(-v "$repo_root/$env_file:/app/host-env:rw")
volume_args+=(-v "$repo_root/$manifest_file:/app/manifest:ro")
if [[ -d "$repo_root/templates" ]]; then
    volume_args+=(-v "$repo_root/templates:/app/user-templates:ro")
fi

if [[ -z "$manifest_file" ]]; then
    echo "ERROR: manifest not found. Create subumbra.yaml (preferred) or subumbra.json." >&2
    exit 1
fi

case "$mode" in
    --help|-h|--list-key-ids|--list-adapters|--show|--status)
        echo "▶  Skipping source preflight for read-only mode: ${mode}"
        ;;
    *)
        if [[ "${SUBUMBRA_ALLOW_UNVERIFIED_SOURCE:-}" == "I_ACCEPT_RISK" ]]; then
            echo "WARNING: SUBUMBRA_ALLOW_UNVERIFIED_SOURCE=I_ACCEPT_RISK set." >&2
            echo "WARNING: Skipping Subumbra source verification before secret-handling bootstrap path." >&2
            echo "WARNING: If local source is compromised, secrets entered during this run may be exposed." >&2
        else
            echo "▶  Running Subumbra source preflight"
            ./scripts/subumbra-verify --preflight
        fi
        ;;
esac

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
if [[ "$mode" == "--nuke-cloudflare" ]]; then
    echo ""
    echo "▶  Stopping cloudflared before Tunnel teardown"
    docker compose stop cloudflared >/dev/null 2>&1 || true
fi
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
elif [[ "$mode" == "--push-registry" || "$mode" == "--session" || "$mode" == "--provision" || "$mode" == "--revoke-key" || "$mode" == "--add-ssh-key" || "$mode" == "--rotate-ssh-key" || "$mode" == "--revoke-ssh-key" || "$mode" == "--add-adapter" || "$mode" == "--revoke-adapter" || "$mode" == "--publish-policy" || "$mode" == "--update-tunnel" || "$mode" == "--update-access" || "$mode" == "--update-ui-auth" || "$mode" == "--update-gate" || "$mode" == "--nuke-cloudflare" || "$mode" == "--status" ]]; then
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
    require_xdg_runtime_dir
    echo ""
    echo "▶  Starting / refreshing core stack (docker compose up -d --force-recreate)"
    mapfile -t _profiles < <(compose_profile_args)
    docker compose "${_profiles[@]}" up -d --force-recreate
    python3 "$repo_root/scripts/subumbra-print-adapters.py" "$repo_root/$env_file" || true
fi

exit "$bootstrap_rc"
