#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: scripts/council/verify.sh <round-dir-name>" >&2
    exit 1
fi

round="$1"
agent="${AGENT:-manual}"
run_id="${RUN_ID_OVERRIDE:-${agent}-$(date +%Y%m%dT%H%M%S)}"
timestamp="$(date +%Y-%m-%dT%H:%M:%S%z)"
artifact_dir="council/${round}/runs/${run_id}"
round_hook_status="NOT-RUN"
round_hook_scripts=()

# Baseline check keys present in every run.
# Round-specific checks are handled entirely by verify-round.sh hooks.
baseline_check_keys=(p9_5 p9_6)
declare -A check_labels=(
    [p9_5]="P9.5 UI status"
    [p9_6]="P9.6 Worker invalid token"
)

mkdir -p "$artifact_dir"

manifest_file="${artifact_dir}/manifest.json"
preflight_file="${artifact_dir}/preflight.txt"
summary_file="${artifact_dir}/summary.txt"

# Collect round-local hook scripts.
shopt -s nullglob
for hook in "council/${round}/verify-round.sh" council/${round}/verify-round-*.sh; do
    if [[ -f "$hook" ]]; then
        round_hook_scripts+=("$hook")
    fi
done
shopt -u nullglob

# Pre-seed baseline proof files so they exist even if a check errors out early.
proof_files=(
    "${artifact_dir}/p9-5-ui-status.txt"
    "${artifact_dir}/p9-6-worker-invalid-token.txt"
)
for file in "${proof_files[@]}"; do
    printf '# PROOF: host-facing path\n' > "$file"
done

declare -A exit_codes=()
declare -A results=()
for key in "${baseline_check_keys[@]}"; do
    results["$key"]="HARNESS-ERROR"
done

# --- Environment ---

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found" >&2
    exit 1
fi

worker_url="$(grep '^CF_WORKER_URL=' .env | cut -d= -f2- || true)"
cf_access_client_id="${CF_ACCESS_CLIENT_ID:-$(grep '^CF_ACCESS_CLIENT_ID=' .env | cut -d= -f2- || true)}"
cf_access_client_secret="${CF_ACCESS_CLIENT_SECRET:-$(grep '^CF_ACCESS_CLIENT_SECRET=' .env | cut -d= -f2- || true)}"
ui_username="$(grep '^UI_USERNAME=' .env | cut -d= -f2- || true)"
ui_password="$(grep '^UI_PASSWORD=' .env | cut -d= -f2- || true)"

if [[ -z "$worker_url" ]]; then
    echo "ERROR: CF_WORKER_URL not found in .env" >&2
    exit 1
fi

# --- Helpers ---

write_manifest() {
    local round_hook_scripts_json
    round_hook_scripts_json="$(printf '%s\n' "${round_hook_scripts[@]}" | python3 -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin if line.rstrip("\n")]))')"
    python3 - "$manifest_file" <<PY
import json
from collections import OrderedDict

manifest_path = ${manifest_file@Q}
data = OrderedDict()
data["run_id"] = ${run_id@Q}
data["agent"] = ${agent@Q}
data["timestamp"] = ${timestamp@Q}
data["round"] = ${round@Q}
data["round_hook_status"] = ${round_hook_status@Q}
data["round_hook_scripts"] = json.loads(${round_hook_scripts_json@Q})
data["exit_codes"] = {
    "preflight": ${exit_codes[preflight]:--1},
    "p9_5": ${exit_codes[p9_5]:--1},
    "p9_6": ${exit_codes[p9_6]:--1},
}
with open(manifest_path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
}

write_summary() {
    local overall="PASS"
    if [[ "${exit_codes[preflight]:-0}" -ne 0 ]]; then
        overall="HARNESS-ERROR"
    fi
    for key in "${baseline_check_keys[@]}"; do
        if [[ "${results[$key]}" == "HARNESS-ERROR" ]]; then
            overall="HARNESS-ERROR"
            break
        fi
        if [[ "${results[$key]}" == "FAIL" ]]; then
            overall="FAIL"
        fi
        # SKIP (e.g. P9.5 isolated fresh-install) is non-failing for overall.
    done
    if [[ "$round_hook_status" == "FAIL" ]]; then
        overall="FAIL"
    fi

    {
        printf 'run_id: %s\n' "$run_id"
        printf 'timestamp: %s\n' "$timestamp"
        for key in "${baseline_check_keys[@]}"; do
            printf '%s: %s\n' "${check_labels[$key]}" "${results[$key]}"
        done
        printf 'Round hook status: %s\n' "$round_hook_status"
        if [[ ${#round_hook_scripts[@]} -gt 0 ]]; then
            printf 'Round hook scripts: %s\n' "${round_hook_scripts[*]}"
        fi
        printf 'overall: %s\n' "$overall"
    } > "$summary_file"
}

run_round_hooks() {
    local hook
    local hook_name
    local hook_log

    if [[ "${VERIFY_SKIP_ROUND_HOOK:-0}" == "1" ]]; then
        round_hook_status="NOT-RUN"
        return 0
    fi

    if [[ ${#round_hook_scripts[@]} -eq 0 ]]; then
        round_hook_status="NOT-RUN"
        return 0
    fi

    round_hook_status="PASS"
    for hook in "${round_hook_scripts[@]}"; do
        hook_name="$(basename "$hook" .sh)"
        hook_log="${artifact_dir}/${hook_name}.log"
        if ! ROUND="$round" \
            AGENT="$agent" \
            RUN_ID="$run_id" \
            VERIFY_ARTIFACT_DIR="$artifact_dir" \
            VERIFY_PREFLIGHT_FILE="$preflight_file" \
            VERIFY_SUMMARY_FILE="$summary_file" \
            bash "$hook" >"$hook_log" 2>&1; then
            round_hook_status="FAIL"
            return 1
        fi
    done
}

capture_headers=""
capture_body=""
capture_exit=0
capture_status=""

run_curl_capture() {
    local method="$1"
    local url="$2"
    shift 2

    capture_headers="$(mktemp)"
    capture_body="$(mktemp)"
    capture_exit=0
    curl --compressed -sS -N -D "$capture_headers" -o "$capture_body" -X "$method" "$url" "$@" >/dev/null 2>&1 || capture_exit=$?
    capture_status="$(awk 'toupper($1) ~ /^HTTP\// {code=$2} END {print code}' "$capture_headers")"
}

write_artifact() {
    local artifact_file="$1"
    local recorded_command="$2"

    {
        printf '# PROOF: host-facing path\n'
        printf 'command: %s\n' "$recorded_command"
        printf 'exit_code: %s\n' "$capture_exit"
        printf 'http_status: %s\n' "${capture_status:-none}"
        printf 'response_headers:\n'
        sed 's/^/  /' "$capture_headers"
        printf 'response_body_excerpt:\n'
        sed -n '1,60p' "$capture_body" | sed 's/^/  /'
    } > "$artifact_file"
}

cleanup_capture() {
    rm -f "$capture_headers" "$capture_body"
    capture_headers=""
    capture_body=""
    capture_exit=0
    capture_status=""
}

# --- Preflight ---

./scripts/council/preflight.sh > "$preflight_file" 2>&1 || exit_codes[preflight]=$?
exit_codes[preflight]="${exit_codes[preflight]:-0}"
if [[ "${exit_codes[preflight]}" -ne 0 ]]; then
    write_summary
    write_manifest
    exit 1
fi

# --- Baseline: P9.5 — UI status ---

if [[ -n "${SUBUMBRA_UI_CONTAINER:-}" ]]; then
    {
        printf '%s\n' '# PROOF: isolated mode — host UI port intentionally absent'
        printf '%s\n' 'status: SKIP'
        printf '%s\n' 'reason: isolated-mode-no-host-port'
        printf 'subumbra_ui_container: %s\n' "$SUBUMBRA_UI_CONTAINER"
    } >"${artifact_dir}/p9-5-ui-status.txt"
    results[p9_5]="SKIP"
    exit_codes[p9_5]=0
else
    ui_args=()
    ui_recorded_auth=""
    if [[ -n "$ui_username" && -n "$ui_password" ]]; then
        ui_args+=(-u "${ui_username}:${ui_password}")
        ui_recorded_auth=" -u '${ui_username}:<redacted>'"
    fi
    run_curl_capture \
        GET \
        http://127.0.0.1:6563/api/status \
        "${ui_args[@]}"
    write_artifact \
        "${artifact_dir}/p9-5-ui-status.txt" \
        "curl --compressed -sS -N -D - -o - -X GET${ui_recorded_auth} http://127.0.0.1:6563/api/status"
    exit_codes[p9_5]="$capture_exit"
    if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]] && grep -q '"subumbra_keys_healthy"' "$capture_body"; then
        results[p9_5]="PASS"
    else
        results[p9_5]="FAIL"
    fi
    cleanup_capture
fi

# --- Baseline: P9.6 — Worker invalid token ---

worker_args=(
    -H "Content-Type: application/json"
    -H "X-Subumbra-Token: invalid-token"
    -d '{}'
)
if [[ -n "$cf_access_client_id" ]]; then
    worker_args+=(-H "CF-Access-Client-Id: ${cf_access_client_id}")
    worker_args+=(-H "CF-Access-Client-Secret: ${cf_access_client_secret}")
fi
run_curl_capture \
    POST \
    "${worker_url}/proxy" \
    "${worker_args[@]}"
write_artifact \
    "${artifact_dir}/p9-6-worker-invalid-token.txt" \
    "curl --compressed -sS -N -D - -o - -X POST ${worker_url}/proxy -H 'Content-Type: application/json' -H 'X-Subumbra-Token: invalid-token' -d '{}'"
exit_codes[p9_6]="$capture_exit"
if [[ "$capture_exit" -eq 0 && "$capture_status" == "401" ]]; then
    results[p9_6]="PASS"
else
    results[p9_6]="FAIL"
fi
cleanup_capture

# --- Round-local hooks ---

if ! run_round_hooks; then
    write_summary
    write_manifest
    exit 1
fi

write_summary
write_manifest

overall="$(awk -F': ' '/^overall:/ {print $2}' "$summary_file")"
if [[ "$overall" == "PASS" ]]; then
    exit 0
fi
exit 1
