#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  ./scripts/council/clean-run.sh
  ./scripts/council/clean-run.sh --keep-workspace --round round-35-adapter-flexibility --agent debug
  ./scripts/council/clean-run.sh --build subumbra-keys
  ./scripts/council/clean-run.sh --bootstrap-overlay council/clean-run-test/bootstrap-overlay-round35.env --round round-35-adapter-flexibility --agent codex
  ./scripts/council/clean-run.sh --build subumbra-ui subumbra-proxy --round round-32-rotation-recovery-ergonomics --agent codex
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

round_dir=""
agent_name="clean-run"
build_targets=()
keep_workspace=0
bootstrap_overlay=""
failed_step=""
verify_run_id=""
overall=""

while [[ $# -gt 0 ]]; do
    case "$1" in
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
        --round)
            shift
            if [[ $# -eq 0 || "$1" == --* ]]; then
                echo "ERROR: --round requires a round directory" >&2
                usage >&2
                exit 1
            fi
            round_dir="$1"
            shift
            ;;
        --agent)
            shift
            if [[ $# -eq 0 || "$1" == --* ]]; then
                echo "ERROR: --agent requires a name" >&2
                usage >&2
                exit 1
            fi
            agent_name="$1"
            shift
            ;;
        --keep-workspace)
            keep_workspace=1
            shift
            ;;
        --bootstrap-overlay)
            shift
            if [[ $# -eq 0 || "$1" == --* ]]; then
                echo "ERROR: --bootstrap-overlay requires a file path" >&2
                usage >&2
                exit 1
            fi
            bootstrap_overlay="$1"
            shift
            ;;
        *)
            usage >&2
            exit 1
            ;;
    esac
done

run_id="clean-run-$(date +%Y%m%dT%H%M%S)"
if [[ -n "$round_dir" ]]; then
    artifact_dir="${repo_root}/council/${round_dir}/runs/${run_id}"
else
    artifact_dir="${repo_root}/council/clean-run-harness/runs/${run_id}"
fi
harness_log="${artifact_dir}/clean-run-log.txt"
workspace=""
credential_source=""

if [[ -n "$bootstrap_overlay" && "$bootstrap_overlay" != /* ]]; then
    bootstrap_overlay="${repo_root}/${bootstrap_overlay#./}"
fi

mkdir -p "$artifact_dir"

log() {
    printf '[clean-run %s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$harness_log"
}

fail() {
    overall="FAIL"
    log "ERROR: $*"
    exit 1
}

fail_step() {
    failed_step="$1"
    shift
    fail "$*"
}

capture_diagnostics() {
    local docker_file="${artifact_dir}/diag-docker-ps.txt"
    local api_file="${artifact_dir}/diag-api-status.json"
    local api_tmp=""
    local api_ok=0
    local attempt=0

    {
        printf '# DIAG: docker ps snapshot\n'
        docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
    } >"$docker_file" 2>/dev/null || printf '# DIAG: docker ps snapshot unavailable\n' >"$docker_file"

    api_tmp="$(mktemp)"
    for attempt in 1 2 3 4 5; do
        if curl -sS --max-time 5 http://127.0.0.1:6563/api/status >"$api_tmp" 2>/dev/null; then
            api_ok=1
            break
        fi
        sleep 1
    done

    if [[ "$api_ok" -eq 1 ]]; then
        cp "$api_tmp" "$api_file"
    else
        printf '{\n  "capture_error": "api_status_unavailable_after_retries"\n}\n' >"$api_file"
    fi
    rm -f "$api_tmp"

    log "diagnostics captured"
}

write_result_manifest() {
    local result_file="${artifact_dir}/result.json"
    local proof_path="null"
    local verify_value="None"
    local failed_value="None"
    local overall_value="$overall"

    if [[ -z "$overall_value" ]]; then
        if [[ -n "$round_dir" && -n "$verify_run_id" ]]; then
            overall_value="PASS"
        else
            overall_value="FAIL"
        fi
    fi

    if [[ -n "$verify_run_id" ]]; then
        proof_path="council/${round_dir}/runs/${verify_run_id}/"
        verify_value="$verify_run_id"
    fi
    if [[ -n "$failed_step" ]]; then
        failed_value="$failed_step"
    fi

    python3 - "$result_file" "$run_id" "$round_dir" "$agent_name" "$overall_value" "$verify_value" "$failed_value" "$proof_path" "$workspace" <<'PY'
import json
import sys

result_file, clean_run_id, round_dir, agent_name, overall, verify_run_id, failed_step, proof_path, workspace = sys.argv[1:10]

data = {
    "clean_run_id": clean_run_id,
    "round": round_dir or None,
    "agent": agent_name,
    "overall": overall,
    "verify_run_id": None if verify_run_id == "None" else verify_run_id,
    "failed_step": None if failed_step == "None" else failed_step,
    "proof_path": None if proof_path == "null" else proof_path,
    "workspace": workspace or None,
}

with open(result_file, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
}

cleanup() {
    local status=$?
    log "cleanup start"
    if [[ -n "$workspace" && -d "$workspace" ]]; then
        export_round_runs_if_present
        capture_diagnostics
        if [[ "$keep_workspace" -eq 1 ]]; then
            log "stack preserved (--keep-workspace): volumes and networks remain active"
        else
            (
                cd "$workspace"
                export COMPOSE_PROJECT_NAME="subumbra-clean-run"
                export CF_WORKER_NAME="subumbra-clean-run"
                docker compose -p subumbra-clean-run down -v >/dev/null 2>&1 || true
            )
        fi
        if [[ "$keep_workspace" -eq 1 ]]; then
            log "workspace preserved (--keep-workspace): ${workspace}"
        else
            rm -rf "$workspace" || true
        fi
    fi
    if [[ -n "$round_dir" ]]; then
        write_result_manifest
    fi
    log "cleanup end"
    log "note: fixed clean-run worker subumbra-clean-run may persist and may require manual deletion"
    log "run id: ${run_id}"
    log "artifacts: ${artifact_dir}"
    exit "$status"
}

trap cleanup EXIT

run_step() {
    local step="$1"
    shift
    local step_log="${artifact_dir}/step-${step}.log"
    log "${step} start"
    {
        printf 'Run_ID: %s\n' "$run_id"
        printf 'Step: %s\n' "$step"
        printf 'Start_Time: %s\n' "$(date -Is)"
        printf 'Workspace: %s\n' "$workspace"
        printf '%s\n' '---'
    } >"$step_log"
    if ! (
        cd "$workspace"
        export COMPOSE_PROJECT_NAME="subumbra-clean-run"
        export CF_WORKER_NAME="subumbra-clean-run"
        "$@"
    ) >>"$step_log" 2>&1; then
        log "step log: ${step_log}"
        fail_step "$step" "failed ${step}"
    fi
    log "${step} end"
}

copy_proof_artifacts() {
    local round_path="${workspace}/council/${round_dir}/runs"
    local proof_dir="${repo_root}/council/${round_dir}/runs"

    if [[ ! -d "$round_path" ]]; then
        fail_step "artifact-export" "failed artifact export"
    fi

    mkdir -p "$proof_dir"
    if ! cp -R "${round_path}/." "$proof_dir/"; then
        fail_step "artifact-export" "failed artifact export"
    fi

    verify_run_id="$(find "$round_path" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort | head -n 1 || true)"
    if [[ -n "$verify_run_id" ]]; then
        overall="$(awk -F': ' '/^overall:/ {print $2}' "${proof_dir}/${verify_run_id}/summary.txt" 2>/dev/null || true)"
    fi

    log "artifact export path: ${artifact_dir}"
}

export_round_runs_if_present() {
    local round_path="${workspace}/council/${round_dir}/runs"
    local proof_dir="${repo_root}/council/${round_dir}/runs"

    [[ -n "$round_dir" ]] || return 0
    [[ -d "$round_path" ]] || return 0

    mkdir -p "$proof_dir"
    cp -R "${round_path}/." "$proof_dir/" >/dev/null 2>&1 || true

    # If verify_run_id was not already set by copy_proof_artifacts (e.g. because
    # verify failed and went straight to the cleanup trap), resolve it now so
    # result.json captures the run folder rather than writing verify_run_id: null.
    if [[ -z "$verify_run_id" ]]; then
        verify_run_id="$(find "$round_path" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort | head -n 1 || true)"
        if [[ -n "$verify_run_id" ]]; then
            # overall is already FAIL from the failed step; preserve it
            log "verify run folder found after cleanup: ${verify_run_id}"
        fi
    fi
}

log "run id creation: ${run_id}"

if [[ -f "${repo_root}/.env.bootstrap" ]]; then
    credential_source="${repo_root}/.env.bootstrap"
elif [[ -f "${repo_root}/.env.bootstrap_bak" ]]; then
    credential_source="${repo_root}/.env.bootstrap_bak"
else
    failed_step="bootstrap-credentials"
    fail "missing bootstrap credentials"
fi

log "credential source selected: $(basename "$credential_source")"

running_containers="$(docker ps --format '{{.Names}}')"
collisions=()
# Bundled LiteLLM is profile-gated in Round 41+ coexistence flows, so an
# existing standalone `litellm` container must not block clean-run. The
# hard blockers here are only the long-running Subumbra core container names
# that clean-run itself will recreate.
for name in subumbra-keys subumbra-proxy subumbra-ui; do
    if printf '%s\n' "$running_containers" | grep -Fxq "$name"; then
        collisions+=("$name")
    fi
done

if [[ ${#collisions[@]} -gt 0 ]]; then
    failed_step="stack-running-gate"
    fail "normal stack already running"
fi

log "stack-running gate result: clear"

workspace="$(mktemp -d "${repo_root}/temp/subumbra-clean-run-XXXXXX")"
log "temp workspace path creation: ${workspace}"

if ! rsync -a \
    --exclude='.git/' \
    --exclude='council/closed/' \
    --exclude='local-archive/' \
    --exclude='council/*/runs/' \
    --exclude="temp/" \
    "${repo_root}/" "${workspace}/" >/dev/null 2>&1; then
    failed_step="workspace-copy"
    fail "failed workspace copy"
fi

if [[ -f "${repo_root}/.env" ]]; then
    cp "${repo_root}/.env" "${workspace}/.env"
elif [[ -f "${repo_root}/.env.example" ]]; then
    cp "${repo_root}/.env.example" "${workspace}/.env"
    log "workspace env initialized from .env.example"
else
    failed_step="workspace-env"
    fail ".env and .env.example both missing"
fi

cp "$credential_source" "${workspace}/.env.bootstrap"
if grep -q "^CF_WORKER_NAME=" "${workspace}/.env.bootstrap"; then
    sed -i "s|^CF_WORKER_NAME=.*|CF_WORKER_NAME=subumbra-clean-run|" "${workspace}/.env.bootstrap"
else
    printf "\nCF_WORKER_NAME=subumbra-clean-run\n" >> "${workspace}/.env.bootstrap"
fi

if [[ -n "$bootstrap_overlay" ]]; then
    if [[ ! -f "$bootstrap_overlay" ]]; then
        failed_step="bootstrap-overlay"
        fail "bootstrap overlay file not found: ${bootstrap_overlay}"
    fi
    printf '\n# overlay from %s\n' "$(basename "$bootstrap_overlay")" >> "${workspace}/.env.bootstrap"
    cat "$bootstrap_overlay" >> "${workspace}/.env.bootstrap"
    log "bootstrap overlay applied: $(basename "$bootstrap_overlay")"
fi

if [[ ${#build_targets[@]} -gt 0 ]]; then
    run_step "build" docker compose build "${build_targets[@]}"
fi

if [[ -f ./bootstrap.sh ]]; then
    run_step "bootstrap" ./bootstrap.sh
else
    run_step "bootstrap" docker compose --profile bootstrap run --rm bootstrap
fi
run_step "reset" ./scripts/council/reset.sh
run_step "preflight" ./scripts/council/preflight.sh

if [[ -n "$round_dir" ]]; then
    run_step "verify" env AGENT="$agent_name" ./scripts/council/verify.sh "$round_dir"
    copy_proof_artifacts
else
    log "artifact export path: ${artifact_dir}"
fi
