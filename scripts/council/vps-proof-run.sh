#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/council/vps-proof-run.sh --round <round> --agent <llm> --branch <branch> --mode existing-stack|fresh-install [--host subumbra] [--repo /opt/subumbra] [--build <service...>] [--dry-run]

Modes:
  existing-stack  Verify an already initialized VPS deployment. Does not run bootstrap.sh and does not tear down the live stack.
  fresh-install   Run an isolated first-install proof in a temp workspace. Uses a unique Worker name and cleans up isolated proof resources.

Runs one live-VPS verifier proof:
  local sync of council/<round>/ -> VPS
  remote precheck
  one mode-appropriate install/update path
  verify.sh + round hook
  artifact/log collection
  automatic copy-back to local council/<round>/runs/<run-id>/
  mode-aware remote cleanup
EOF
}

round=""
agent=""
branch=""
mode=""
remote_host="subumbra"
remote_repo="/opt/subumbra"
build_targets=()
dry_run=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --round)
            round="${2:-}"
            shift 2
            ;;
        --agent)
            agent="${2:-}"
            shift 2
            ;;
        --branch)
            branch="${2:-}"
            shift 2
            ;;
        --mode)
            mode="${2:-}"
            shift 2
            ;;
        --host)
            remote_host="${2:-}"
            shift 2
            ;;
        --repo)
            remote_repo="${2:-}"
            shift 2
            ;;
        --build)
            shift
            if [[ $# -eq 0 || "$1" == --* ]]; then
                echo "ERROR: --build requires at least one service name" >&2
                usage >&2
                exit 1
            fi
            while [[ $# -gt 0 && "$1" != --* ]]; do
                build_targets+=("$1")
                shift
            done
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 1
            ;;
    esac
done

if [[ -z "$round" || -z "$agent" || -z "$branch" || -z "$mode" ]]; then
    usage >&2
    exit 1
fi

case "$mode" in
    existing-stack|fresh-install) ;;
    *)
        echo "ERROR: --mode must be existing-stack or fresh-install" >&2
        usage >&2
        exit 1
        ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
run_id="${agent}-vps-$(date -u +%Y%m%dT%H%M%SZ)"
local_runs_dir="${repo_root}/council/${round}/runs"
local_target="${local_runs_dir}/${run_id}"
local_ssh_log="$(mktemp)"

mkdir -p "$local_runs_dir"

cleanup_local_tmp() {
    rm -f "$local_ssh_log"
}
trap cleanup_local_tmp EXIT

if [[ ! -d "${repo_root}/council/${round}" ]]; then
    echo "ERROR: local council/${round} not found" >&2
    exit 1
fi

build_targets_string="${build_targets[*]}"

echo "[vps-proof-run] run_id=${run_id}"
echo "[vps-proof-run] mode=${mode}"
echo "[vps-proof-run] syncing council/${round} to ${remote_host}:${remote_repo}/council/"
ssh "$remote_host" "mkdir -p '${remote_repo}/council'"
scp -r "${repo_root}/council/${round}" "${remote_host}:${remote_repo}/council/" >/dev/null

set +e
ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=20 "$remote_host" \
    "ROUND='$round' AGENT='$agent' BRANCH='$branch' MODE='$mode' RUN_ID='$run_id' REPO='$remote_repo' BUILD_TARGETS='$build_targets_string' DRY_RUN='$dry_run' bash -s" >"$local_ssh_log" 2>&1 <<'REMOTE'
set -euo pipefail

round="${ROUND:?ROUND required}"
agent="${AGENT:?AGENT required}"
branch="${BRANCH:?BRANCH required}"
mode="${MODE:?MODE required}"
run_id="${RUN_ID:?RUN_ID required}"
repo="${REPO:?REPO required}"
build_targets_string="${BUILD_TARGETS:-}"
dry_run="${DRY_RUN:-0}"

cd "$repo"
artifact_dir="${repo}/council/${round}/runs/${run_id}"
mkdir -p "$artifact_dir"
timeline_file="${artifact_dir}/timeline.jsonl"
result_file="${artifact_dir}/vps-proof-result.json"
cleanup_info_file="${artifact_dir}/cleanup-info.env"
failed_stage=""
overall="FAIL"
workdir="$repo"
cleanup_policy="preserve-live-stack"
compose_project=""
worker_name=""

case "$mode" in
    existing-stack)
        cleanup_policy="preserve-live-stack"
        ;;
    fresh-install)
        cleanup_policy="remove-isolated-proof"
        # Docker Compose project names must be lowercase (ISO timestamps contain T/Z).
        compose_project="scr-${run_id//[^a-zA-Z0-9]/-}"
        compose_project="$(printf '%s' "$compose_project" | tr '[:upper:]' '[:lower:]')"
        worker_name="$compose_project"
        workdir="" # Must be set by prepare_fresh_workspace
        ;;
    *)
        echo "ERROR: unsupported mode: $mode" >&2
        exit 1
        ;;
esac

json_event() {
    local stage="$1"
    local status="$2"
    local message="${3:-}"
    python3 - "$timeline_file" "$stage" "$status" "$message" "$mode" <<'PY'
import json, sys, datetime
path, stage, status, message, mode = sys.argv[1:6]
event = {
    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "stage": stage,
    "status": status,
    "mode": mode,
}
if message:
    event["message"] = message
with open(path, "a", encoding="utf-8") as fh:
    json.dump(event, fh)
    fh.write("\n")
PY
}

write_result() {
    local status="$1"
    python3 - "$result_file" "$run_id" "$round" "$agent" "$branch" "$mode" "$status" "$failed_stage" "$cleanup_policy" "$workdir" "$compose_project" "$worker_name" <<'PY'
import json, sys
(
    path,
    run_id,
    round_name,
    agent,
    branch,
    mode,
    overall,
    failed_stage,
    cleanup_policy,
    workdir,
    compose_project,
    worker_name,
) = sys.argv[1:13]
data = {
    "run_id": run_id,
    "round": round_name,
    "agent": agent,
    "branch": branch,
    "mode": mode,
    "overall": overall,
    "failed_stage": failed_stage or None,
    "cleanup_policy": cleanup_policy,
    "workdir": workdir,
    "compose_project": compose_project or None,
    "worker_name": worker_name or None,
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
}

run_stage() {
    local stage="$1"
    shift
    local log_file="${artifact_dir}/stage-${stage}.log"
    json_event "$stage" "start"
    if ! "$@" >"$log_file" 2>&1; then
        failed_stage="$stage"
        json_event "$stage" "fail" "see $(basename "$log_file")"
        return 1
    fi
    json_event "$stage" "pass"
    json_event "$stage" "pass2"
    return 0
}

collect_logs() {
    {
        echo "# git"
        (cd "$workdir" && git branch --show-current) || true
        (cd "$workdir" && git rev-parse --short HEAD) || true
        (cd "$workdir" && git status --short) || true
        echo
        echo "# docker ps"
        docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' || true
        echo
        echo "# docker compose ps"
        (cd "$workdir" && docker compose ps) || true
    } >"${artifact_dir}/remote-state.txt" 2>&1 || true

    for svc in subumbra-keys subumbra-proxy subumbra-ui; do
        suffix="${svc#subumbra-}"; suffix="${suffix^^}"
        ctr_var="SUBUMBRA_${suffix}_CONTAINER"
        ctr="${!ctr_var:-$svc}"
        docker logs "$ctr" >"${artifact_dir}/logs-${svc}.txt" 2>&1 || true
    done
}

cleanup_remote() {
    json_event "collect" "start"
    collect_logs
    json_event "collect" "pass"
    {
        printf 'mode=%q\n' "$mode"
        printf 'cleanup_policy=%q\n' "$cleanup_policy"
        printf 'workdir=%q\n' "$workdir"
        printf 'compose_project=%q\n' "$compose_project"
        printf 'worker_name=%q\n' "$worker_name"
    } >"$cleanup_info_file"
    write_result "$overall"
}
trap cleanup_remote EXIT

precheck() {
    cd "$repo"
    git fetch origin
    git checkout "$branch"
    git pull --ff-only origin "$branch"
    git rev-parse --short HEAD >"${artifact_dir}/git-sha.txt"
    # Ignore untracked files: live VPS deployments commonly have data/, temp/,
    # and backup env files that must not block verify. Require a clean *tracked* tree.
    git status --short -uno >"${artifact_dir}/git-status.txt"
    if [[ -s "${artifact_dir}/git-status.txt" ]]; then
        echo "ERROR: VPS checkout has uncommitted tracked changes (see git-status.txt)" >&2
        return 1
    fi

    # No live-container conflict check needed: install_fresh_once patches
    # container_name in the workspace docker-compose.yml to use the
    # compose_project prefix, so proof containers never collide with the
    # live stack's subumbra-keys / subumbra-proxy / subumbra-ui names.

    if [[ "$mode" == "existing-stack" && ! -f .env ]]; then
        echo "ERROR: existing-stack mode requires initialized .env" >&2
        return 1
    fi

    if [[ "$mode" == "fresh-install" && ! -f .env.bootstrap_bak && ! -f .env.bootstrap ]]; then
        echo "ERROR: .env.bootstrap_bak or .env.bootstrap required" >&2
        return 1
    fi
    if [[ ! -f "council/${round}/verify-round.sh" && "${ALLOW_NO_ROUND_HOOK:-0}" != "1" ]]; then
        echo "ERROR: council/${round}/verify-round.sh required for runtime proof" >&2
        return 1
    fi
    if [[ -f "council/${round}/bootstrap-overlay.env" ]]; then
        policy_path="$(awk -F= '/^SUBUMBRA_POLICY_PATH=/ {print $2; exit}' "council/${round}/bootstrap-overlay.env")"
        if [[ -n "$policy_path" && -d "$policy_path" ]]; then
            echo "ERROR: SUBUMBRA_POLICY_PATH points to a directory: $policy_path" >&2
            return 1
        fi
        if [[ -n "$policy_path" && ! -f "$policy_path" ]]; then
            echo "ERROR: SUBUMBRA_POLICY_PATH file not found: $policy_path" >&2
            return 1
        fi
    fi
}

prepare_fresh_workspace() {
    mkdir -p "${repo}/temp"
    workdir="$(mktemp -d "${repo}/temp/vps-proof-${run_id}-XXXXXX")"
    rsync -a \
        --exclude='.git/' \
        --exclude='council/*/runs/' \
        --exclude='local-archive/' \
        --exclude='temp/' \
        "${repo}/" "${workdir}/"
    cp -R "${repo}/council/${round}" "${workdir}/council/"
}

apply_worker_name() {
    local file="$1"
    if grep -q '^CF_WORKER_NAME=' "$file"; then
        sed -i "s|^CF_WORKER_NAME=.*|CF_WORKER_NAME=${worker_name}|" "$file"
    else
        printf '\nCF_WORKER_NAME=%s\n' "$worker_name" >> "$file"
    fi
}

install_fresh_once() {
    local bootstrap_status=0
    prepare_fresh_workspace
    cd "$workdir"
    if [[ -f .env.bootstrap_bak ]]; then
        cp .env.bootstrap_bak .env.bootstrap
    fi
    if [[ -f "council/${round}/bootstrap-overlay.env" ]]; then
        {
            echo
            echo "# overlay from council/${round}/bootstrap-overlay.env"
            cat "council/${round}/bootstrap-overlay.env"
        } >> .env.bootstrap
    fi
    apply_worker_name .env.bootstrap
    export COMPOSE_PROJECT_NAME="$compose_project"
    export CF_WORKER_NAME="$worker_name"
    # Prefix container names so the proof run doesn't conflict with a live stack
    # that uses the same absolute container_name directives.
    sed -i \
        -e "s/^    container_name: subumbra-keys$/    container_name: ${compose_project}-subumbra-keys/" \
        -e "s/^    container_name: subumbra-proxy$/    container_name: ${compose_project}-subumbra-proxy/" \
        -e "s/^    container_name: subumbra-ui$/    container_name: ${compose_project}-subumbra-ui/" \
        "${workdir}/docker-compose.yml"
    # Allocate a free host port for the proof proxy and remap the UI port away.
    # The UI host port is not needed by verify-round.sh; strip it entirely.
    # The proxy host port IS needed by verify-round.sh; remap to a free port.
    proof_proxy_port="$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); p=s.getsockname()[1]; s.close(); print(p)")"
    python3 - "${workdir}/docker-compose.yml" "$proof_proxy_port" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
# Strip the UI host port binding (live stack holds the same port)
text = re.sub(r'\n    ports:\n(      - "127\.0\.0\.1:\d+:8080"[^\n]*\n)', '\n', text)
# Remap the proxy host port to the free port supplied as argv[2]
text = re.sub(r'(      - "127\.0\.0\.1:)\d+(:8090")', rf'\g<1>{sys.argv[2]}\2', text)
open(sys.argv[1], "w").write(text)
PY
    export SUBUMBRA_KEYS_CONTAINER="${compose_project}-subumbra-keys"
    export SUBUMBRA_PROXY_CONTAINER="${compose_project}-subumbra-proxy"
    export SUBUMBRA_UI_CONTAINER="${compose_project}-subumbra-ui"
    export SUBUMBRA_PROXY_HOST_PORT="$proof_proxy_port"
    if [[ -n "$build_targets_string" ]]; then
        # shellcheck disable=SC2086
        docker compose build $build_targets_string
    fi
    if [[ -f "council/${round}/pre-bootstrap.sh" ]]; then
        bash "council/${round}/pre-bootstrap.sh" || return 1
    fi
    # Explicit exit checks: run_stage calls functions via `if ! fn`, which
    # disables set -e inside the function body. Each step must bail manually.
    ./bootstrap.sh 2>&1 | tee "${artifact_dir}/bootstrap.log" || bootstrap_status=$?
    if [[ "$bootstrap_status" -ne 0 ]]; then
        if [[ -f "council/${round}/bootstrap-repair.sh" ]]; then
            BOOTSTRAP_ARTIFACT_DIR="$artifact_dir" \
                ROUND="$round" \
                AGENT="$agent" \
                RUN_ID="$run_id" \
                bash "council/${round}/bootstrap-repair.sh" || return 1
        else
            return "$bootstrap_status"
        fi
    fi
    ./scripts/council/reset.sh || return 1
    ./scripts/council/preflight.sh || return 1
}

update_existing_stack() {
    cd "$repo"
    if [[ -n "$build_targets_string" ]]; then
        # shellcheck disable=SC2086
        docker compose build $build_targets_string
    fi
    docker compose up -d --force-recreate
    ./scripts/council/preflight.sh
}

verify_once() {
    local status=0
    (cd "$workdir" && CLEAN_RUN_ARTIFACT_DIR="$artifact_dir" VERIFY_MODE="$mode" RUN_ID_OVERRIDE="$run_id" AGENT="$agent" ./scripts/council/verify.sh "$round") || status=$?
    if [[ "$workdir" != "$repo" && -d "${workdir}/council/${round}/runs/${run_id}" ]]; then
        cp -R "${workdir}/council/${round}/runs/${run_id}/." "$artifact_dir/"
    fi
    return "$status"
}

run_independent_probes() {
    mkdir -p "${artifact_dir}/independent-probes"
    if [[ -f "${workdir}/council/${round}/independent-probes.sh" ]]; then
        PROBE_ARTIFACT_DIR="${artifact_dir}/independent-probes" \
            VERIFY_ARTIFACT_DIR="$artifact_dir" \
            ROUND="$round" \
            AGENT="$agent" \
            RUN_ID="$run_id" \
            bash "${workdir}/council/${round}/independent-probes.sh"
    else
        {
            echo "No council/${round}/independent-probes.sh found."
            echo "Verifier should document any independent probes in the verification report."
        } >"${artifact_dir}/independent-probes/README.txt"
    fi
}

run_stage remote-precheck precheck
if [[ "$dry_run" == "1" ]]; then
    json_event "dry-run" "pass" "precheck completed; no install, verify, probes, or cleanup performed"
    overall="PASS"
    exit 0
fi

case "$mode" in
    fresh-install)
        run_stage remote-install install_fresh_once
        ;;
    existing-stack)
        run_stage remote-update update_existing_stack
        ;;
    *)
        echo "ERROR: unsupported mode in install/update dispatch: $mode" >&2
        exit 1
        ;;
esac
json_event "diag" "after-esac"
run_stage remote-verify verify_once
run_stage remote-probes run_independent_probes
overall="PASS"
REMOTE
remote_status=$?
set -e

mkdir -p "$local_runs_dir"
if scp -r "${remote_host}:${remote_repo}/council/${round}/runs/${run_id}" "$local_runs_dir/" >/dev/null 2>&1; then
    mkdir -p "$local_target"
    cp "$local_ssh_log" "${local_target}/vps-proof-ssh.log"
    echo "[vps-proof-run] artifacts synced to ${local_target}"
else
    mkdir -p "$local_target"
    cp "$local_ssh_log" "${local_target}/vps-proof-ssh.log"
    echo "ERROR: failed to sync remote artifacts; SSH log saved to ${local_target}/vps-proof-ssh.log" >&2
    exit 1
fi

set +e
ssh "$remote_host" \
    "ROUND='$round' RUN_ID='$run_id' REPO='$remote_repo' bash -s" >>"$local_ssh_log" 2>&1 <<'REMOTE_CLEANUP'
set -euo pipefail
round="${ROUND:?ROUND required}"
run_id="${RUN_ID:?RUN_ID required}"
repo="${REPO:?REPO required}"
artifact_dir="${repo}/council/${round}/runs/${run_id}"
cleanup_info="${artifact_dir}/cleanup-info.env"
cleanup_log="${artifact_dir}/cleanup.log"

if [[ ! -f "$cleanup_info" ]]; then
    echo "cleanup-info.env missing; no cleanup performed" >"$cleanup_log"
    exit 0
fi

# shellcheck disable=SC1090
source "$cleanup_info"
{
    echo "mode=${mode:-unknown}"
    echo "cleanup_policy=${cleanup_policy:-unknown}"
    if [[ "${cleanup_policy:-}" == "remove-isolated-proof" ]]; then
        if [[ -n "${workdir:-}" && -d "$workdir" && "$workdir" != "$repo" ]]; then
            (
                cd "$workdir"
                export COMPOSE_PROJECT_NAME="${compose_project:-}"
                export CF_WORKER_NAME="${worker_name:-}"
                docker compose -p "${compose_project:-}" down -v --remove-orphans || true
            )
            rm -rf "$workdir"
            echo "removed isolated workspace ${workdir}"
        else
            echo "isolated workspace missing, already removed, or PROTECTED (workdir == repo)"
        fi
        if [[ -n "${worker_name:-}" ]]; then
            echo "note: Cloudflare test worker ${worker_name} may require manual deletion"
        fi
    else
        echo "preserved live deployment; no docker compose down executed"
    fi
} >"$cleanup_log" 2>&1
REMOTE_CLEANUP
cleanup_status=$?
set -e

if scp "${remote_host}:${remote_repo}/council/${round}/runs/${run_id}/cleanup.log" "${local_target}/cleanup.log" >/dev/null 2>&1; then
    :
fi
if [[ "$cleanup_status" -ne 0 ]]; then
    echo "WARNING: remote cleanup failed; see ${local_target}/vps-proof-ssh.log" >&2
fi

exit "$remote_status"
