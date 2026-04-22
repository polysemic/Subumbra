#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/council/fetch-run-artifacts.sh <round> <run-id> [remote_host] [remote_repo]
  scripts/council/fetch-run-artifacts.sh <round> <run-id> [remote_host] [remote_repo] --delete-remote

Defaults:
  remote_host = subumbra
  remote_repo = /opt/subumbra

Copies council/<round>/runs/<run-id>/ from the remote repo into the local repo.
Use --delete-remote only after confirming the local copy is present and complete.
EOF
}

if [[ $# -lt 2 || $# -gt 5 ]]; then
    usage >&2
    exit 1
fi

round="$1"
run_id="$2"
remote_host="${3:-subumbra}"
remote_repo="${4:-/opt/subumbra}"
delete_remote=0

if [[ $# -ge 3 ]]; then
    case "${@: -1}" in
        --delete-remote)
            delete_remote=1
            if [[ $# -eq 3 ]]; then
                remote_host="subumbra"
                remote_repo="/opt/subumbra"
            elif [[ $# -eq 4 ]]; then
                remote_repo="/opt/subumbra"
            fi
            ;;
    esac
fi

if [[ "$remote_host" == "--delete-remote" || "$remote_repo" == "--delete-remote" ]]; then
    remote_host="subumbra"
    remote_repo="/opt/subumbra"
    delete_remote=1
fi

local_target="council/${round}/runs"
remote_target="${remote_repo}/council/${round}/runs/${run_id}"

mkdir -p "$local_target"
scp -r "${remote_host}:${remote_target}" "$local_target/"

echo "Fetched ${remote_host}:${remote_target} -> ${local_target}/"

if [[ "$delete_remote" -eq 1 ]]; then
    ssh "$remote_host" "rm -rf '$remote_target'"
    echo "Deleted remote run directory: ${remote_target}"
fi
