#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: scripts/council/verify.sh <round-dir-name>" >&2
    exit 1
fi

round="$1"
agent="${AGENT:-manual}"
run_id="${agent}-$(date +%Y%m%dT%H%M%S)"
timestamp="$(date +%Y-%m-%dT%H:%M:%S%z)"
artifact_dir="council/${round}/runs/${run_id}"
round_hook_status="NOT-RUN"
round_hook_scripts=()
legacy_matrix_enabled=0

core_check_keys=(p9_1 p9_2 p9_3 p9_4 p9_5 p9_6)
legacy_check_keys=(
    p30_1 p30_2
    p31_1 p31_2 p31_3 p31_4 p31_5
    p32_1 p32_2 p32_3 p32_4
    p33_1
    p34_1 p34_2 p34_3
    p35_1 p35_2 p35_3 p35_4
    p36_1 p36_2 p36_3 p36_4 p36_5
)
all_check_keys=("${core_check_keys[@]}" "${legacy_check_keys[@]}")
declare -A check_labels=(
    [p9_1]="P9.1 LiteLLM allowed key"
    [p9_2]="P9.2 LiteLLM disallowed key"
    [p9_3]="P9.3 sidecar allowed key"
    [p9_4]="P9.4 sidecar disallowed key"
    [p9_5]="P9.5 UI status"
    [p9_6]="P9.6 Worker invalid token"
    [p30_1]="P30.1 sidecar expired denied"
    [p30_2]="P30.2 sidecar restored accepted"
    [p31_1]="P31.1 audit allow"
    [p31_2]="P31.2 audit scope denied"
    [p31_3]="P31.3 audit list_keys"
    [p31_4]="P31.4 audit expired restart"
    [p31_5]="P31.5 audit no secrets"
    [p32_1]="P32.1 rotate entrypoint"
    [p32_2]="P32.2 expire deny restore"
    [p32_3]="P32.3 audit retention"
    [p32_4]="P32.4 recovery playbook"
    [p33_1]="P33.1 transparent sidecar path"
    [p34_1]="P34.1 provider catalog"
    [p34_2]="P34.2 litellm routes"
    [p34_3]="P34.3 mistral litellm allow"
    [p35_1]="P35.1 adapter ids doc"
    [p35_2]="P35.2 registry custom adapter"
    [p35_3]="P35.3 runtime token custom adapter"
    [p35_4]="P35.4 custom adapter scope enforcement"
    [p36_1]="P36.1 worker KV runtime"
    [p36_2]="P36.2 KV bootstrap state"
    [p36_3]="P36.3 push-registry live update"
    [p36_4]="P36.4 custom provider persistence"
    [p36_5]="P36.5 worker fail-closed"
)
active_summary_keys=("${core_check_keys[@]}")

mkdir -p "$artifact_dir"

manifest_file="${artifact_dir}/manifest.json"
preflight_file="${artifact_dir}/preflight.txt"
summary_file="${artifact_dir}/summary.txt"
matrix_file="${artifact_dir}/matrix.json"

shopt -s nullglob
for hook in "council/${round}/verify-round.sh" council/${round}/verify-round-*.sh; do
    if [[ -f "$hook" ]]; then
        round_hook_scripts+=("$hook")
    fi
done
shopt -u nullglob

proof_files=(
    "${artifact_dir}/p9-1-litellm-allowed.txt"
    "${artifact_dir}/p9-2-litellm-disallowed.txt"
    "${artifact_dir}/p9-3-sidecar-allowed.txt"
    "${artifact_dir}/p9-4-sidecar-disallowed.txt"
    "${artifact_dir}/p9-5-ui-status.txt"
    "${artifact_dir}/p9-6-worker-invalid-token.txt"
)

round30_enabled=0
round31_enabled=0
round32_enabled=0
round33_enabled=0
round34_enabled=0
round35_enabled=0
round36_enabled=0
if [[ "$round" == "round-30-revocation-ttl-guardrails" ]]; then
    legacy_matrix_enabled=1
    round30_enabled=1
    proof_files+=(
        "${artifact_dir}/p30-1-sidecar-expired-denied.txt"
        "${artifact_dir}/p30-2-sidecar-restored-accepted.txt"
    )
elif [[ "$round" == "round-31-structured-audit-trail" ]]; then
    legacy_matrix_enabled=1
    round31_enabled=1
    proof_files+=(
        "${artifact_dir}/p31-1-audit-allow.txt"
        "${artifact_dir}/p31-2-audit-scope-denied.txt"
        "${artifact_dir}/p31-3-audit-list-keys.txt"
        "${artifact_dir}/p31-4-audit-expired-restart.txt"
        "${artifact_dir}/p31-5-audit-no-secrets.txt"
    )
elif [[ "$round" == "round-32-rotation-recovery-ergonomics" ]]; then
    legacy_matrix_enabled=1
    round32_enabled=1
    proof_files+=(
        "${artifact_dir}/p32-1-rotate-entrypoint.txt"
        "${artifact_dir}/p32-2-expire-deny-restore.txt"
        "${artifact_dir}/p32-3-audit-retention.txt"
        "${artifact_dir}/p32-4-recovery-playbook.txt"
    )
elif [[ "$round" == "round-33-transparent-sidecar" ]]; then
    legacy_matrix_enabled=1
    round33_enabled=1
    proof_files+=(
        "${artifact_dir}/p33-1-transparent-allowed.txt"
    )
elif [[ "$round" == "round-34-provider-flexibility" ]]; then
    legacy_matrix_enabled=1
    round34_enabled=1
    proof_files+=(
        "${artifact_dir}/p34-1-provider-catalog.txt"
        "${artifact_dir}/p34-2-litellm-routes.txt"
        "${artifact_dir}/p34-3-mistral-allowed.txt"
    )
elif [[ "$round" == "round-35-adapter-flexibility" ]]; then
    legacy_matrix_enabled=1
    round35_enabled=1
    proof_files+=(
        "${artifact_dir}/p35-1-adapter-ids-doc.txt"
        "${artifact_dir}/p35-2-registry-custom-adapter.txt"
        "${artifact_dir}/p35-3-runtime-token-custom-adapter.txt"
        "${artifact_dir}/p35-4-custom-adapter-scope-enforcement.txt"
    )
elif [[ "$round" == "round-36-live-provider-registry" ]]; then
    legacy_matrix_enabled=1
    round36_enabled=1
    proof_files+=(
        "${artifact_dir}/p36-1-worker-kv-runtime.txt"
        "${artifact_dir}/p36-2-kv-bootstrap-state.txt"
        "${artifact_dir}/p36-3-push-registry-live-update.txt"
        "${artifact_dir}/p36-4-custom-provider-persistence.txt"
        "${artifact_dir}/p36-5-worker-fail-closed.txt"
    )
elif [[ "$round" == "round-42-2-runtime-auth-reconciliation" ]]; then
    legacy_matrix_enabled=1
    # Round 42.2 uses the same core completion matrix as Round 29/34
fi

if [[ "$legacy_matrix_enabled" -ne 1 ]]; then
    active_summary_keys=(p9_5 p9_6)
elif [[ "$round30_enabled" -eq 1 ]]; then
    active_summary_keys+=(
        p30_1 p30_2
    )
elif [[ "$round31_enabled" -eq 1 ]]; then
    active_summary_keys+=(
        p31_1 p31_2 p31_3 p31_4 p31_5
    )
elif [[ "$round32_enabled" -eq 1 ]]; then
    active_summary_keys+=(
        p32_1 p32_2 p32_3 p32_4
    )
elif [[ "$round33_enabled" -eq 1 ]]; then
    active_summary_keys+=(
        p33_1
    )
elif [[ "$round34_enabled" -eq 1 ]]; then
    active_summary_keys+=(
        p34_1 p34_2 p34_3
    )
elif [[ "$round35_enabled" -eq 1 ]]; then
    active_summary_keys+=(
        p35_1 p35_2 p35_3 p35_4
    )
elif [[ "$round36_enabled" -eq 1 ]]; then
    active_summary_keys+=(
        p36_1 p36_2 p36_3 p36_4 p36_5
    )
fi

for file in "${proof_files[@]}"; do
    printf '# PROOF: host-facing path\n' > "$file"
done
# printf '# DIAG: matrix derivation, not counted toward PASS\n' > "$matrix_file"

declare -A exit_codes=()
declare -A results=()
for key in "${core_check_keys[@]}"; do
    results["$key"]="HARNESS-ERROR"
done
for key in "${legacy_check_keys[@]}"; do
    results["$key"]="NOT-RUN"
done

env_source="unknown"
if [[ -f .env.bootstrap ]]; then
    env_source=".env.bootstrap"
elif [[ -f .env.bootstrap_bak ]]; then
    env_source=".env.bootstrap_bak"
fi

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found" >&2
    exit 1
fi

env_value_from_source() {
    local key="$1"
    if [[ "$env_source" == "unknown" ]]; then
        return 1
    fi
    grep "^${key}=" "$env_source" | cut -d= -f2- || true
}

master_key="$(grep '^LITELLM_MASTER_KEY=' .env | cut -d= -f2- || true)"
worker_url="$(grep '^CF_WORKER_URL=' .env | cut -d= -f2- || true)"
forge_registry="${SUBUMBRA_ADAPTER_REGISTRY:-$(grep '^SUBUMBRA_ADAPTER_REGISTRY=' .env | cut -d= -f2- || true)}"
forge_hmac_key="$(grep '^SUBUMBRA_HMAC_KEY=' .env | cut -d= -f2- || true)"
forge_token_proxy="$(grep '^SUBUMBRA_TOKEN_PROXY=' .env | cut -d= -f2- || true)"
cf_access_client_id="${CF_ACCESS_CLIENT_ID:-$(grep '^CF_ACCESS_CLIENT_ID=' .env | cut -d= -f2- || true)}"
cf_access_client_secret="${CF_ACCESS_CLIENT_SECRET:-$(grep '^CF_ACCESS_CLIENT_SECRET=' .env | cut -d= -f2- || true)}"

if [[ -z "$master_key" || -z "$worker_url" || -z "$forge_registry" ]]; then
    echo "ERROR: .env is missing one or more required Round 29 values" >&2
    exit 1
fi

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
data["env_source"] = ${env_source@Q}
data["round_hook_status"] = ${round_hook_status@Q}
data["round_hook_scripts"] = json.loads(${round_hook_scripts_json@Q})
data["exit_codes"] = {
    "preflight": ${exit_codes[preflight]:--1},
    "p9_1": ${exit_codes[p9_1]:--1},
    "p9_2": ${exit_codes[p9_2]:--1},
    "p9_3": ${exit_codes[p9_3]:--1},
    "p9_4": ${exit_codes[p9_4]:--1},
    "p9_5": ${exit_codes[p9_5]:--1},
    "p9_6": ${exit_codes[p9_6]:--1},
    "p30_1": ${exit_codes[p30_1]:--1},
    "p30_2": ${exit_codes[p30_2]:--1},
    "p31_1": ${exit_codes[p31_1]:--1},
    "p31_2": ${exit_codes[p31_2]:--1},
    "p31_3": ${exit_codes[p31_3]:--1},
    "p31_4": ${exit_codes[p31_4]:--1},
    "p31_5": ${exit_codes[p31_5]:--1},
    "p32_1": ${exit_codes[p32_1]:--1},
    "p32_2": ${exit_codes[p32_2]:--1},
    "p32_3": ${exit_codes[p32_3]:--1},
    "p32_4": ${exit_codes[p32_4]:--1},
    "p33_1": ${exit_codes[p33_1]:--1},
    "p34_1": ${exit_codes[p34_1]:--1},
    "p34_2": ${exit_codes[p34_2]:--1},
    "p34_3": ${exit_codes[p34_3]:--1},
    "p35_1": ${exit_codes[p35_1]:--1},
    "p35_2": ${exit_codes[p35_2]:--1},
    "p35_3": ${exit_codes[p35_3]:--1},
    "p35_4": ${exit_codes[p35_4]:--1},
    "p36_1": ${exit_codes[p36_1]:--1},
    "p36_2": ${exit_codes[p36_2]:--1},
    "p36_3": ${exit_codes[p36_3]:--1},
    "p36_4": ${exit_codes[p36_4]:--1},
    "p36_5": ${exit_codes[p36_5]:--1},
}
with open(manifest_path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\\n")
PY
}

write_summary() {
    local overall="PASS"
    if [[ "${exit_codes[preflight]:-0}" -ne 0 ]]; then
        overall="HARNESS-ERROR"
    fi
    for key in "${active_summary_keys[@]}"; do
        if [[ "${results[$key]}" == "HARNESS-ERROR" ]]; then
            overall="HARNESS-ERROR"
            break
        fi
        if [[ "${results[$key]}" == "FAIL" ]]; then
            overall="FAIL"
        fi
    done
    if [[ "$round_hook_status" == "FAIL" ]]; then
        overall="FAIL"
    fi

    {
        printf 'run_id: %s\n' "$run_id"
        printf 'timestamp: %s\n' "$timestamp"
        for key in "${all_check_keys[@]}"; do
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
            VERIFY_MATRIX_FILE="$matrix_file" \
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

audit_event_in_file() {
    local body_file="$1"
    local adapter_id="$2"
    local endpoint="$3"
    local verdict="$4"
    local reason_code="$5"
    local key_id="${6:-}"
    python3 - "$body_file" "$adapter_id" "$endpoint" "$verdict" "$reason_code" "$key_id" <<'PY'
import json
import sys

body_path, adapter_id, endpoint, verdict, reason_code, key_id = sys.argv[1:7]
with open(body_path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
for event in data.get("recent_log", []):
    if (
        event.get("adapter_id") == adapter_id
        and event.get("endpoint") == endpoint
        and event.get("verdict") == verdict
        and event.get("reason_code") == reason_code
        and (not key_id or event.get("key_id") == key_id)
    ):
        raise SystemExit(0)
raise SystemExit(1)
PY
}

round30_original_registry="$forge_registry"

rewrite_registry_line() {
    local registry_json="$1"
    python3 - "$registry_json" <<'PY'
import json
import re
import sys
from pathlib import Path

registry = json.loads(sys.argv[1])
env_path = Path('.env')
env_text = env_path.read_text(encoding='utf-8')
new_line = 'SUBUMBRA_ADAPTER_REGISTRY=' + json.dumps(registry, separators=(",", ":"))
updated = re.sub(r'^SUBUMBRA_ADAPTER_REGISTRY=.+$', new_line, env_text, flags=re.MULTILINE)
env_path.write_text(updated, encoding='utf-8')
PY
}

wait_for_running_service() {
    local service="$1"
    local container_name
    local status
    case "$service" in
        subumbra-keys) container_name="subumbra-keys" ;;
        *) container_name="$service" ;;
    esac
    for _ in $(seq 1 30); do
        status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name" 2>/dev/null || true)"
        if [[ "$status" == "healthy" || "$status" == "running" ]]; then
            return 0
        fi
        sleep 1
    done
    return 1
}

round_restore_registry() {
    if [[ "$round30_enabled" -ne 1 && "$round31_enabled" -ne 1 && "$round32_enabled" -ne 1 ]]; then
        return 0
    fi
    rewrite_registry_line "$round30_original_registry" || return 1
    env -u SUBUMBRA_ADAPTER_REGISTRY docker compose up -d --force-recreate subumbra-keys >/dev/null
    wait_for_running_service subumbra-keys || return 1
}

if [[ "$round30_enabled" -eq 1 || "$round31_enabled" -eq 1 || "$round32_enabled" -eq 1 ]]; then
    trap 'round_restore_registry >/dev/null 2>&1 || true' EXIT
fi

./scripts/council/preflight.sh > "$preflight_file" 2>&1 || exit_codes[preflight]=$?
exit_codes[preflight]="${exit_codes[preflight]:-0}"
if [[ "${exit_codes[preflight]}" -ne 0 ]]; then
    write_summary
    write_manifest
    exit 1
fi

if [[ "$legacy_matrix_enabled" -eq 1 ]]; then
keys_json="$(docker compose run --rm -u 0 -T subumbra-keys cat /app/data/keys.json 2>/dev/null || true)"
if [[ -z "$keys_json" ]]; then
    echo "ERROR: unable to read keys.json for legacy verification matrix" >&2
    write_summary
    write_manifest
    exit 1
fi

if ! python3 - "$forge_registry" "$keys_json" worker/src/providers.json "$matrix_file" <<'PY'
import json
import sys
from pathlib import Path

registry = json.loads(sys.argv[1])
keys = json.loads(sys.argv[2])
providers = {entry["provider_id"]: entry for entry in json.loads(Path(sys.argv[3]).read_text())}
matrix_path = Path(sys.argv[4])

LITELLM_MODEL_BY_PROVIDER = {
    "anthropic": "claude-sonnet-4",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.1-8b",
    "deepseek": "deepseek-chat",
}

SIDECAR_REQUEST_BY_PROVIDER = {
    "anthropic": {
        "method": "POST",
        "headers": {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        },
        "body": {
            "model": "claude-sonnet-4-5",
            "messages": [{"role": "user", "content": "Say test"}],
            "max_tokens": 16,
        },
        "success_pattern": "\"content\"",
    },
    "openai": {
        "method": "POST",
        "headers": {"content-type": "application/json"},
        "body": {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Say test"}],
            "max_tokens": 16,
        },
        "success_pattern": "\"choices\"",
    },
    "groq": {
        "method": "POST",
        "headers": {"content-type": "application/json"},
        "body": {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": "Say test"}],
            "max_tokens": 16,
        },
        "success_pattern": "\"choices\"",
    },
    "deepseek": {
        "method": "POST",
        "headers": {"content-type": "application/json"},
        "body": {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "Say test"}],
            "max_tokens": 16,
        },
        "success_pattern": "\"choices\"",
    },
    "github": {
        "method": "GET",
        "headers": {
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
            "user-agent": "subumbra-proxy/1.0",
        },
        "body": None,
        "success_pattern": "\"login\"",
    },
    "slack": {
        "method": "POST",
        "headers": {"accept": "application/json"},
        "body": None,
        "success_pattern": "\"ok\":true",
    },
}

def find_allowed(adapter_id, supported_providers):
    allowed = registry.get(adapter_id, {}).get("allowed_keys", [])
    for key_id in allowed:
        provider = keys.get(key_id, {}).get("provider")
        if provider in supported_providers:
            return key_id
    return None

all_known_keys = sorted(set(keys.keys()) | {key_id for adapter in registry.values() for key_id in adapter.get("allowed_keys", [])})

litellm_allowed = find_allowed("litellm", set(LITELLM_MODEL_BY_PROVIDER))
proxy_allowed = find_allowed("subumbra-proxy", set(SIDECAR_REQUEST_BY_PROVIDER))

litellm_allowed_list = registry.get("litellm", {}).get("allowed_keys", [])
proxy_allowed_list = registry.get("subumbra-proxy", {}).get("allowed_keys", [])
litellm_disallowed = next((key_id for key_id in all_known_keys if key_id not in litellm_allowed_list), None)
proxy_disallowed = next((key_id for key_id in all_known_keys if key_id not in proxy_allowed_list), None)

if not litellm_allowed or not litellm_disallowed or not proxy_allowed or not proxy_disallowed:
    raise SystemExit("matrix unavailable: could not derive both allowed and disallowed key_ids for Round 29")

proxy_provider = keys[proxy_allowed]["provider"]
provider_entry = providers[proxy_provider]
sidecar_request = dict(SIDECAR_REQUEST_BY_PROVIDER[proxy_provider])
sidecar_request["target_url"] = f"https://{provider_entry['target_host']}{provider_entry.get('api_base_path', '')}"
if proxy_provider == "anthropic":
    sidecar_request["target_url"] += "/v1/messages"
elif proxy_provider == "github":
    sidecar_request["target_url"] += "/user"
elif proxy_provider == "slack":
    sidecar_request["target_url"] += "/api/auth.test"
else:
    sidecar_request["target_url"] += "/chat/completions"

matrix = {
    "litellm_allowed_key": litellm_allowed,
    "litellm_allowed_model": LITELLM_MODEL_BY_PROVIDER[keys[litellm_allowed]["provider"]],
    "litellm_disallowed_key": litellm_disallowed,
    "proxy_allowed_key": proxy_allowed,
    "proxy_disallowed_key": proxy_disallowed,
    "proxy_request": sidecar_request,
}

matrix_path.write_text("# DIAG: matrix derivation, not counted toward PASS\n" + json.dumps(matrix, indent=2) + "\n", encoding="utf-8")
PY
then
    echo "ERROR: Round 29 verification matrix unavailable" >&2
    write_summary
    write_manifest
    exit 1
fi

matrix_json="$(sed '1d' "$matrix_file")"

matrix_value() {
    python3 - "$matrix_file" "$1" <<'PY'
import json
import sys
from pathlib import Path

text = [line for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
data = json.loads("\n".join(text))
value = data
for part in sys.argv[2].split("."):
    value = value.get(part) if isinstance(value, dict) else None
if isinstance(value, (dict, list)):
    print(json.dumps(value, separators=(",", ":")))
elif value is None:
    print("null")
else:
    print(value)
PY
}

litellm_allowed_key="$(matrix_value litellm_allowed_key)"
litellm_allowed_model="$(matrix_value litellm_allowed_model)"
litellm_disallowed_key="$(matrix_value litellm_disallowed_key)"
proxy_allowed_key="$(matrix_value proxy_allowed_key)"
proxy_disallowed_key="$(matrix_value proxy_disallowed_key)"
proxy_method="$(matrix_value proxy_request.method)"
proxy_target_url="$(matrix_value proxy_request.target_url)"
proxy_headers_json="$(matrix_value proxy_request.headers)"
proxy_body_json="$(matrix_value proxy_request.body)"
proxy_success_pattern="$(matrix_value proxy_request.success_pattern)"

litellm_allowed_payload="$(python3 - "$litellm_allowed_model" "$litellm_allowed_key" <<'PY'
import json
import sys
print(json.dumps({
    "model": sys.argv[1],
    "messages": [{"role": "user", "content": "Say test only."}],
    "api_key": sys.argv[2],
    "max_tokens": 5,
}, separators=(",", ":")))
PY
)"

litellm_disallowed_payload="$(python3 - "$litellm_allowed_model" "$litellm_disallowed_key" <<'PY'
import json
import sys
print(json.dumps({
    "model": sys.argv[1],
    "messages": [{"role": "user", "content": "Say test only."}],
    "api_key": sys.argv[2],
    "max_tokens": 5,
}, separators=(",", ":")))
PY
)"

sidecar_allowed_payload="$(python3 - "$proxy_allowed_key" "$proxy_target_url" "$proxy_method" "$proxy_headers_json" "$proxy_body_json" <<'PY'
import json
import sys

body = None if sys.argv[5] == "null" else json.loads(sys.argv[5])
print(json.dumps({
    "key_id": sys.argv[1],
    "target_url": sys.argv[2],
    "method": sys.argv[3],
    "headers": json.loads(sys.argv[4]),
    "body": body,
}, separators=(",", ":")))
PY
)"

sidecar_disallowed_payload="$(python3 - "$proxy_disallowed_key" "$proxy_target_url" "$proxy_method" "$proxy_headers_json" "$proxy_body_json" <<'PY'
import json
import sys

body = None if sys.argv[5] == "null" else json.loads(sys.argv[5])
print(json.dumps({
    "key_id": sys.argv[1],
    "target_url": sys.argv[2],
    "method": sys.argv[3],
    "headers": json.loads(sys.argv[4]),
    "body": body,
}, separators=(",", ":")))
PY
)"
fi

if [[ "$legacy_matrix_enabled" -eq 1 ]]; then
p9_1_request_body="$(mktemp)"
p9_1_status_body="$(mktemp)"
run_curl_capture \
    POST \
    http://127.0.0.1:4000/v1/chat/completions \
    -H "Authorization: Bearer ${master_key}" \
    -H "Content-Type: application/json" \
    -d "$litellm_allowed_payload"
p9_1_request_exit="$capture_exit"
p9_1_request_status="$capture_status"
cp "$capture_body" "$p9_1_request_body"
write_artifact \
    "${artifact_dir}/p9-1-litellm-allowed.txt" \
    "curl --compressed -sS -N -D - -o - -X POST http://127.0.0.1:4000/v1/chat/completions -H 'Authorization: Bearer [REDACTED]' -H 'Content-Type: application/json' -d '${litellm_allowed_payload}'"
run_curl_capture \
    GET \
    http://127.0.0.1:8080/api/status
p9_1_status_exit="$capture_exit"
p9_1_status_code="$capture_status"
if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]]; then
    cp "$capture_body" "$p9_1_status_body"
fi
{
    printf 'forge_status_http: %s\n' "${p9_1_status_code:-none}"
    printf 'forge_status_body_excerpt:\n'
    sed -n '1,60p' "$capture_body" | sed 's/^/  /'
} >> "${artifact_dir}/p9-1-litellm-allowed.txt"
cleanup_capture
p9_1_ok=0
# Distinguish forge-side failure from upstream provider auth failure.
if [[ "$p9_1_request_exit" -eq 0 ]]; then
    if [[ "$p9_1_request_status" == "200" ]] && grep -q '"choices"' "$p9_1_request_body"; then
        p9_1_ok=1
    elif ! grep -q 'subumbra-keys returned' "$p9_1_request_body" \
        && [[ "$p9_1_status_exit" -eq 0 && "$p9_1_status_code" == "200" ]] \
        && audit_event_in_file "$p9_1_status_body" litellm get_key allow allowed "$litellm_allowed_key"; then
        p9_1_ok=1
    fi
fi
rm -f "$p9_1_request_body" "$p9_1_status_body"
exit_codes[p9_1]="$p9_1_request_exit"
if [[ "$p9_1_ok" -eq 1 ]]; then
    results[p9_1]="PASS"
else
    results[p9_1]="FAIL"
fi

run_curl_capture \
    POST \
    http://127.0.0.1:4000/v1/chat/completions \
    -H "Authorization: Bearer ${master_key}" \
    -H "Content-Type: application/json" \
    -d "$litellm_disallowed_payload"
write_artifact \
    "${artifact_dir}/p9-2-litellm-disallowed.txt" \
    "curl --compressed -sS -N -D - -o - -X POST http://127.0.0.1:4000/v1/chat/completions -H 'Authorization: Bearer [REDACTED]' -H 'Content-Type: application/json' -d '${litellm_disallowed_payload}'"
exit_codes[p9_2]="$capture_exit"
if [[ "$capture_exit" -eq 0 && "$capture_status" != "200" ]] && grep -q '403' "$capture_body"; then
    results[p9_2]="PASS"
else
    results[p9_2]="FAIL"
fi
cleanup_capture

p9_3_request_body="$(mktemp)"
p9_3_status_body="$(mktemp)"
run_curl_capture \
    POST \
    http://127.0.0.1:8090/v1/request \
    -H "Content-Type: application/json" \
    -d "$sidecar_allowed_payload"
p9_3_request_exit="$capture_exit"
p9_3_request_status="$capture_status"
cp "$capture_body" "$p9_3_request_body"
write_artifact \
    "${artifact_dir}/p9-3-sidecar-allowed.txt" \
    "curl --compressed -sS -N -D - -o - -X POST http://127.0.0.1:8090/v1/request -H 'Content-Type: application/json' -d '${sidecar_allowed_payload}'"
run_curl_capture \
    GET \
    http://127.0.0.1:8080/api/status
p9_3_status_exit="$capture_exit"
p9_3_status_code="$capture_status"
if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]]; then
    cp "$capture_body" "$p9_3_status_body"
fi
{
    printf 'forge_status_http: %s\n' "${p9_3_status_code:-none}"
    printf 'forge_status_body_excerpt:\n'
    sed -n '1,60p' "$capture_body" | sed 's/^/  /'
} >> "${artifact_dir}/p9-3-sidecar-allowed.txt"
cleanup_capture
p9_3_ok=0
# Distinguish forge-side failure from upstream provider auth failure.
if [[ "$p9_3_request_exit" -eq 0 ]]; then
    if [[ "$p9_3_request_status" == "200" ]] && grep -q "$proxy_success_pattern" "$p9_3_request_body"; then
        p9_3_ok=1
    elif ! grep -q 'forge record fetch failed' "$p9_3_request_body" \
        && [[ "$p9_3_status_exit" -eq 0 && "$p9_3_status_code" == "200" ]] \
        && audit_event_in_file "$p9_3_status_body" subumbra-proxy get_key allow allowed "$proxy_allowed_key"; then
        p9_3_ok=1
    fi
fi
rm -f "$p9_3_request_body" "$p9_3_status_body"
exit_codes[p9_3]="$p9_3_request_exit"
if [[ "$p9_3_ok" -eq 1 ]]; then
    results[p9_3]="PASS"
else
    results[p9_3]="FAIL"
fi

run_curl_capture \
    POST \
    http://127.0.0.1:8090/v1/request \
    -H "Content-Type: application/json" \
    -d "$sidecar_disallowed_payload"
write_artifact \
    "${artifact_dir}/p9-4-sidecar-disallowed.txt" \
    "curl --compressed -sS -N -D - -o - -X POST http://127.0.0.1:8090/v1/request -H 'Content-Type: application/json' -d '${sidecar_disallowed_payload}'"
exit_codes[p9_4]="$capture_exit"
if [[ "$capture_exit" -eq 0 && "$capture_status" != "200" ]] && grep -q '403' "$capture_body"; then
    results[p9_4]="PASS"
else
    results[p9_4]="FAIL"
fi
cleanup_capture
else
    results[p9_1]="NOT-RUN"
    results[p9_2]="NOT-RUN"
    results[p9_3]="NOT-RUN"
    results[p9_4]="NOT-RUN"
fi

run_curl_capture \
    GET \
    http://127.0.0.1:8080/api/status
write_artifact \
    "${artifact_dir}/p9-5-ui-status.txt" \
    "curl --compressed -sS -N -D - -o - -X GET http://127.0.0.1:8080/api/status"
exit_codes[p9_5]="$capture_exit"
if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]] && grep -q '"subumbra_keys_healthy"' "$capture_body"; then
    results[p9_5]="PASS"
else
    results[p9_5]="FAIL"
fi
cleanup_capture

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

if [[ "$round30_enabled" -eq 1 ]]; then
    expired_registry="$(
        python3 - "$forge_registry" <<'PY'
import json
import sys

registry = json.loads(sys.argv[1])
registry["subumbra-proxy"]["expires_at"] = "2000-01-01T00:00:00+00:00"
print(json.dumps(registry, separators=(",", ":")))
PY
    )"
    rewrite_registry_line "$expired_registry"
    env -u SUBUMBRA_ADAPTER_REGISTRY docker compose up -d --force-recreate subumbra-keys >/dev/null
    if wait_for_running_service subumbra-keys; then
        run_curl_capture \
            POST \
            http://127.0.0.1:8090/v1/request \
            -H "Content-Type: application/json" \
            -d "$sidecar_allowed_payload"
        write_artifact \
            "${artifact_dir}/p30-1-sidecar-expired-denied.txt" \
            "curl --compressed -sS -N -D - -o - -X POST http://127.0.0.1:8090/v1/request -H 'Content-Type: application/json' -d '${sidecar_allowed_payload}'"
        exit_codes[p30_1]="$capture_exit"
        if [[ "$capture_exit" -eq 0 && "$capture_status" == "502" ]] && grep -q 'forge record fetch failed' "$capture_body"; then
            results[p30_1]="PASS"
        else
            results[p30_1]="FAIL"
        fi
        cleanup_capture
    else
        exit_codes[p30_1]=1
        results[p30_1]="FAIL"
    fi

    round_restore_registry
    run_curl_capture \
        POST \
        http://127.0.0.1:8090/v1/request \
        -H "Content-Type: application/json" \
        -d "$sidecar_allowed_payload"
    write_artifact \
        "${artifact_dir}/p30-2-sidecar-restored-accepted.txt" \
        "curl --compressed -sS -N -D - -o - -X POST http://127.0.0.1:8090/v1/request -H 'Content-Type: application/json' -d '${sidecar_allowed_payload}'"
    exit_codes[p30_2]="$capture_exit"
    if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]] && grep -q "$proxy_success_pattern" "$capture_body"; then
        results[p30_2]="PASS"
    else
        results[p30_2]="FAIL"
    fi
    cleanup_capture
fi

if [[ "$round31_enabled" -eq 1 ]]; then
    run_curl_capture \
        POST \
        http://127.0.0.1:8090/v1/request \
        -H "Content-Type: application/json" \
        -d "$sidecar_allowed_payload"
    p31_allow_exit="$capture_exit"
    p31_allow_status="$capture_status"
    p31_allow_body="$(mktemp)"
    cp "$capture_body" "$p31_allow_body"
    cleanup_capture

    run_curl_capture \
        GET \
        http://127.0.0.1:8080/api/status
    write_artifact \
        "${artifact_dir}/p31-1-audit-allow.txt" \
        "sidecar allowed call then curl --compressed -sS -N -D - -o - -X GET http://127.0.0.1:8080/api/status"
    exit_codes[p31_1]="$capture_exit"
    if [[ "$p31_allow_exit" -eq 0 && "$p31_allow_status" == "200" ]] && grep -q "$proxy_success_pattern" "$p31_allow_body" \
        && [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]] \
        && audit_event_in_file "$capture_body" "subumbra-proxy" "get_key" "allow" "allowed"; then
        results[p31_1]="PASS"
    else
        results[p31_1]="FAIL"
    fi
    rm -f "$p31_allow_body"
    cleanup_capture

    run_curl_capture \
        POST \
        http://127.0.0.1:8090/v1/request \
        -H "Content-Type: application/json" \
        -d "$sidecar_disallowed_payload"
    p31_deny_exit="$capture_exit"
    p31_deny_status="$capture_status"
    p31_deny_body="$(mktemp)"
    cp "$capture_body" "$p31_deny_body"
    cleanup_capture

    run_curl_capture \
        GET \
        http://127.0.0.1:8080/api/status
    write_artifact \
        "${artifact_dir}/p31-2-audit-scope-denied.txt" \
        "sidecar disallowed call then curl --compressed -sS -N -D - -o - -X GET http://127.0.0.1:8080/api/status"
    exit_codes[p31_2]="$capture_exit"
    if [[ "$p31_deny_exit" -eq 0 && "$p31_deny_status" != "200" ]] && grep -q '403' "$p31_deny_body" \
        && [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]] \
        && audit_event_in_file "$capture_body" "subumbra-proxy" "get_key" "deny" "key_scope_denied"; then
        results[p31_2]="PASS"
    else
        results[p31_2]="FAIL"
    fi
    rm -f "$p31_deny_body"
    cleanup_capture

    run_curl_capture \
        GET \
        http://127.0.0.1:8080/api/status
    write_artifact \
        "${artifact_dir}/p31-3-audit-list-keys.txt" \
        "curl --compressed -sS -N -D - -o - -X GET http://127.0.0.1:8080/api/status"
    exit_codes[p31_3]="$capture_exit"
    if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]] \
        && audit_event_in_file "$capture_body" "subumbra-ui" "list_keys" "allow" "allowed"; then
        results[p31_3]="PASS"
    else
        results[p31_3]="FAIL"
    fi
    cleanup_capture

    expired_registry="$(
        python3 - "$forge_registry" <<'PY'
import json
import sys

registry = json.loads(sys.argv[1])
registry["subumbra-proxy"]["expires_at"] = "2000-01-01T00:00:00+00:00"
print(json.dumps(registry, separators=(",", ":")))
PY
    )"
    rewrite_registry_line "$expired_registry"
    env -u SUBUMBRA_ADAPTER_REGISTRY docker compose up -d --force-recreate subumbra-keys >/dev/null

    p31_4_ok=0
    p31_4_tmp="$(mktemp)"
    {
        echo '# PROOF: host-facing path'
        echo 'step: expire subumbra-proxy in SUBUMBRA_ADAPTER_REGISTRY and recreate subumbra-keys'
    } > "$p31_4_tmp"

    if wait_for_running_service subumbra-keys; then
        run_curl_capture \
            POST \
            http://127.0.0.1:8090/v1/request \
            -H "Content-Type: application/json" \
            -d "$sidecar_allowed_payload"
        {
            echo 'expired_sidecar_status:'
            echo "  exit_code: $capture_exit"
            echo "  http_status: ${capture_status:-none}"
            sed -n '1,40p' "$capture_body" | sed 's/^/  /'
        } >> "$p31_4_tmp"
        p31_expired_sidecar_ok=0
        if [[ "$capture_exit" -eq 0 && "$capture_status" == "502" ]] && grep -q 'forge record fetch failed' "$capture_body"; then
            p31_expired_sidecar_ok=1
        fi
        cleanup_capture

        run_curl_capture \
            GET \
            http://127.0.0.1:8080/api/status
        cp "$capture_body" "$artifact_dir/p31-4-before-restart-status.json"
        {
            echo 'before_restart_status:'
            echo "  exit_code: $capture_exit"
            echo "  http_status: ${capture_status:-none}"
            sed -n '1,40p' "$capture_body" | sed 's/^/  /'
        } >> "$p31_4_tmp"
        p31_before_event_ok=0
        if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]] \
            && audit_event_in_file "$capture_body" "subumbra-proxy" "get_key" "deny" "adapter_expired"; then
            p31_before_event_ok=1
        fi
        cleanup_capture

        round_restore_registry

        run_curl_capture \
            GET \
            http://127.0.0.1:8080/api/status
        cp "$capture_body" "$artifact_dir/p31-4-after-restart-status.json"
        {
            echo 'after_restart_status:'
            echo "  exit_code: $capture_exit"
            echo "  http_status: ${capture_status:-none}"
            sed -n '1,40p' "$capture_body" | sed 's/^/  /'
        } >> "$p31_4_tmp"
        p31_after_event_ok=0
        if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]] \
            && audit_event_in_file "$capture_body" "subumbra-proxy" "get_key" "deny" "adapter_expired"; then
            p31_after_event_ok=1
        fi
        cleanup_capture

        if [[ "$p31_expired_sidecar_ok" -eq 1 && "$p31_before_event_ok" -eq 1 && "$p31_after_event_ok" -eq 1 ]]; then
            p31_4_ok=1
        fi
    fi

    mv "$p31_4_tmp" "${artifact_dir}/p31-4-audit-expired-restart.txt"
    exit_codes[p31_4]=$(( p31_4_ok == 1 ? 0 : 1 ))
    if [[ "$p31_4_ok" -eq 1 ]]; then
        results[p31_4]="PASS"
    else
        results[p31_4]="FAIL"
    fi

    if python3 - "${artifact_dir}/p31-1-audit-allow.txt" "${artifact_dir}/p31-2-audit-scope-denied.txt" "${artifact_dir}/p31-3-audit-list-keys.txt" "${artifact_dir}/p31-4-audit-expired-restart.txt" "$forge_registry" > "${artifact_dir}/p31-5-audit-no-secrets.txt" <<'PY'
import json
import sys
from pathlib import Path

paths = [Path(sys.argv[i]) for i in range(1, 5)]
registry = json.loads(sys.argv[5])
forbidden_literals = ["ciphertext", "wrapped_dek", "X-Subumbra-Token", "SUBUMBRA_HMAC_KEY"]
forbidden_values = [cfg.get("token", "") for cfg in registry.values()]
violations = []

for p in paths:
    text = p.read_text(encoding="utf-8", errors="replace")
    for token in forbidden_literals:
        if token in text:
            violations.append(f"{p.name}: contains forbidden literal '{token}'")
    for value in forbidden_values:
        if value and value in text:
            violations.append(f"{p.name}: contains adapter token value")

print("# PROOF: host-facing path")
if violations:
    print("status: FAIL")
    for item in violations:
        print(f"violation: {item}")
    raise SystemExit(1)
print("status: PASS")
print("checked_files:")
for p in paths:
    print(f"  - {p.name}")
print("forbidden_literals:")
for token in forbidden_literals:
    print(f"  - {token}")
PY
    then
        exit_codes[p31_5]=0
        results[p31_5]="PASS"
    else
        exit_codes[p31_5]=1
        results[p31_5]="FAIL"
    fi
fi


if [[ "$round32_enabled" -eq 1 ]]; then
    p32_1_tmp="$(mktemp)"
    set +e
    docker compose --profile bootstrap run --rm -T bootstrap --rotate < /dev/null >"$p32_1_tmp" 2>&1
    p32_1_exit=$?
    set -e
    {
        echo '# PROOF: host-facing path'
        echo 'command: docker compose --profile bootstrap run --rm -T bootstrap --rotate < /dev/null'
        echo "exit_code: $p32_1_exit"
        echo 'output_excerpt:'
        sed -n '1,80p' "$p32_1_tmp" | sed 's/^/  /'
    } > "${artifact_dir}/p32-1-rotate-entrypoint.txt"
    exit_codes[p32_1]="$p32_1_exit"
    if grep -q 'Subumbra — Per-Key Rotation' "$p32_1_tmp" && ! grep -q 'node: bad option: --rotate' "$p32_1_tmp"; then
        results[p32_1]="PASS"
    else
        results[p32_1]="FAIL"
    fi
    rm -f "$p32_1_tmp"

    p32_2_ok=0
    p32_2_tmp="$(mktemp)"
    p32_2_helper_out="$(mktemp)"
    {
        echo '# PROOF: host-facing path'
        echo 'step: expire subumbra-proxy via helper, recreate subumbra-keys, verify deny, restore registry, verify allow'
        set +e
        ./scripts/subumbra-expire-adapter.sh subumbra-proxy >"$p32_2_helper_out" 2>&1
        p32_2_helper_exit=$?
        set -e
        echo "helper_exit: $p32_2_helper_exit"
        echo 'helper_output:'
        sed -n '1,20p' "$p32_2_helper_out" | sed 's/^/  /'
        if [[ "$p32_2_helper_exit" -eq 0 ]]; then
            env -u SUBUMBRA_ADAPTER_REGISTRY docker compose up -d --force-recreate subumbra-keys >/dev/null
            if wait_for_running_service subumbra-keys; then
                run_curl_capture                     POST                     http://127.0.0.1:8090/v1/request                     -H "Content-Type: application/json"                     -d "$sidecar_allowed_payload"
                echo 'expired_sidecar_status:'
                echo "  exit_code: $capture_exit"
                echo "  http_status: ${capture_status:-none}"
                sed -n '1,40p' "$capture_body" | sed 's/^/  /'
                p32_2_denied_ok=0
                if [[ "$capture_exit" -eq 0 && "$capture_status" == "502" ]] && grep -q 'forge record fetch failed' "$capture_body"; then
                    p32_2_denied_ok=1
                fi
                cleanup_capture

                round_restore_registry

                p32_2_restore_body="$(mktemp)"
                p32_2_restore_status_body="$(mktemp)"
                run_curl_capture                     POST                     http://127.0.0.1:8090/v1/request                     -H "Content-Type: application/json"                     -d "$sidecar_allowed_payload"
                p32_2_restore_exit="$capture_exit"
                p32_2_restore_status="$capture_status"
                cp "$capture_body" "$p32_2_restore_body"
                echo 'restored_sidecar_status:'
                echo "  exit_code: $p32_2_restore_exit"
                echo "  http_status: ${p32_2_restore_status:-none}"
                sed -n '1,40p' "$p32_2_restore_body" | sed 's/^/  /'
                run_curl_capture                     GET                     http://127.0.0.1:8080/api/status
                p32_2_restore_status_exit="$capture_exit"
                p32_2_restore_status_code="$capture_status"
                if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]]; then
                    cp "$capture_body" "$p32_2_restore_status_body"
                fi
                echo 'restored_forge_status:'
                echo "  exit_code: $p32_2_restore_status_exit"
                echo "  http_status: ${p32_2_restore_status_code:-none}"
                sed -n '1,40p' "$capture_body" | sed 's/^/  /'
                cleanup_capture
                p32_2_allowed_ok=0
                # Distinguish forge-side failure from upstream provider auth failure after restore.
                if [[ "$p32_2_restore_exit" -eq 0 ]]; then
                    if [[ "$p32_2_restore_status" == "200" ]] && grep -q "$proxy_success_pattern" "$p32_2_restore_body"; then
                        p32_2_allowed_ok=1
                    elif ! grep -q 'forge record fetch failed' "$p32_2_restore_body" \
                        && [[ "$p32_2_restore_status_exit" -eq 0 && "$p32_2_restore_status_code" == "200" ]] \
                        && audit_event_in_file "$p32_2_restore_status_body" subumbra-proxy get_key allow allowed "$proxy_allowed_key"; then
                        p32_2_allowed_ok=1
                    fi
                fi
                rm -f "$p32_2_restore_body" "$p32_2_restore_status_body"

                if [[ "$p32_2_denied_ok" -eq 1 && "$p32_2_allowed_ok" -eq 1 ]]; then
                    p32_2_ok=1
                fi
            fi
        fi
    } > "$p32_2_tmp"
    mv "$p32_2_tmp" "${artifact_dir}/p32-2-expire-deny-restore.txt"
    rm -f "$p32_2_helper_out"
    exit_codes[p32_2]=$(( p32_2_ok == 1 ? 0 : 1 ))
    if [[ "$p32_2_ok" -eq 1 ]]; then
        results[p32_2]="PASS"
    else
        results[p32_2]="FAIL"
    fi

    p32_3_ok=0
    p32_3_tmp="$(mktemp)"
    p32_3_probe_out="$(mktemp)"
    p32_3_env_value="$(docker exec subumbra-keys printenv AUDIT_MAX_ROWS 2>/dev/null || true)"
    set +e
    docker compose run --rm -T         -e AUDIT_MAX_ROWS=3         -e DATA_DIR=/tmp/data         -e AUDIT_DIR=/tmp/audit         subumbra-keys         python3 - >"$p32_3_probe_out" 2>&1 <<'PY'
import sqlite3
import app

for i in range(100):
    app._record_audit(
        adapter_id='probe-adapter',
        key_id=f'probe-{i}',
        endpoint='probe',
        verdict='allow',
        reason_code='allowed',
        remote='127.0.0.1',
    )

conn = sqlite3.connect('/tmp/audit/audit.db')
row_count = conn.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]
print(f'AUDIT_MAX_ROWS={app.AUDIT_MAX_ROWS}')
print(f'row_count={row_count}')
if app.AUDIT_MAX_ROWS != 3:
    raise SystemExit(1)
if row_count > 3:
    raise SystemExit(1)
PY
    p32_3_probe_exit=$?
    set -e
    {
        echo '# PROOF: round-specific safe retention check'
        echo 'running_env_audit_max_rows:'
        echo "  ${p32_3_env_value:-missing}"
        echo 'probe_command: docker compose run --rm -T -e AUDIT_MAX_ROWS=3 -e DATA_DIR=/tmp/data -e AUDIT_DIR=/tmp/audit subumbra-keys python3 -'
        echo "probe_exit: $p32_3_probe_exit"
        echo 'probe_output:'
        sed -n '1,80p' "$p32_3_probe_out" | sed 's/^/  /'
    } > "$p32_3_tmp"
    if [[ -n "$p32_3_env_value" ]] && [[ "$p32_3_probe_exit" -eq 0 ]]; then
        p32_3_ok=1
    fi
    mv "$p32_3_tmp" "${artifact_dir}/p32-3-audit-retention.txt"
    rm -f "$p32_3_probe_out"
    exit_codes[p32_3]=$(( p32_3_ok == 1 ? 0 : 1 ))
    if [[ "$p32_3_ok" -eq 1 ]]; then
        results[p32_3]="PASS"
    else
        results[p32_3]="FAIL"
    fi

    p32_4_ok=0
    p32_4_tmp="$(mktemp)"
    {
        echo '# PROOF: local doc/helper presence'
        echo "helper_executable: $(test -x ./scripts/subumbra-expire-adapter.sh && echo yes || echo no)"
        echo 'operator_guide_sections:'
        grep -nE '^## 5\. Recovery Playbook$|^### Single-Key Rotation$|^### Full Re-Bootstrap$|^### Emergency Adapter Expiry$|^### Token Drift Recovery$' docs/operator-guide.md | sed 's/^/  /'
    } > "$p32_4_tmp"
    if test -x ./scripts/subumbra-expire-adapter.sh \
        && grep -qE '^## 5\. Recovery Playbook$' docs/operator-guide.md \
        && grep -qE '^### Single-Key Rotation$' docs/operator-guide.md \
        && grep -qE '^### Full Re-Bootstrap$' docs/operator-guide.md \
        && grep -qE '^### Emergency Adapter Expiry$' docs/operator-guide.md \
        && grep -qE '^### Token Drift Recovery$' docs/operator-guide.md; then
        p32_4_ok=1
    fi
    mv "$p32_4_tmp" "${artifact_dir}/p32-4-recovery-playbook.txt"
    exit_codes[p32_4]=$(( p32_4_ok == 1 ? 0 : 1 ))
    if [[ "$p32_4_ok" -eq 1 ]]; then
        results[p32_4]="PASS"
    else
        results[p32_4]="FAIL"
    fi

fi

if [[ "$round33_enabled" -eq 1 ]]; then
    p33_1_request_body="$(mktemp)"
    p33_1_status_body="$(mktemp)"
    run_curl_capture         GET         http://127.0.0.1:8090/t/user         -H "Authorization: Bearer github_prod"         -H "Accept: application/json"
    p33_1_request_exit="$capture_exit"
    p33_1_request_status="$capture_status"
    cp "$capture_body" "$p33_1_request_body"
    write_artifact         "${artifact_dir}/p33-1-transparent-allowed.txt"         "curl --compressed -sS -N -D - -o - -X GET http://127.0.0.1:8090/t/user -H 'Authorization: Bearer github_prod' -H 'Accept: application/json'"
    run_curl_capture         GET         http://127.0.0.1:8080/api/status
    p33_1_status_exit="$capture_exit"
    p33_1_status_code="$capture_status"
    if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]]; then
        cp "$capture_body" "$p33_1_status_body"
    fi
    {
        printf 'forge_status_http: %s
' "${p33_1_status_code:-none}"
        printf 'forge_status_body_excerpt:
'
        sed -n '1,60p' "$capture_body" | sed 's/^/  /'
    } >> "${artifact_dir}/p33-1-transparent-allowed.txt"
    cleanup_capture
    p33_1_ok=0
    if [[ "$p33_1_request_exit" -eq 0 ]]; then
        if [[ "$p33_1_request_status" == "200" ]] && grep -q '"login"' "$p33_1_request_body"; then
            p33_1_ok=1
        elif ! grep -q 'forge record fetch failed' "$p33_1_request_body"             && [[ "$p33_1_status_exit" -eq 0 && "$p33_1_status_code" == "200" ]]             && audit_event_in_file "$p33_1_status_body" subumbra-proxy get_key allow allowed github_prod; then
            p33_1_ok=1
        fi
    fi
    rm -f "$p33_1_request_body" "$p33_1_status_body"
    exit_codes[p33_1]="$p33_1_request_exit"
    if [[ "$p33_1_ok" -eq 1 ]]; then
        results[p33_1]="PASS"
    else
        results[p33_1]="FAIL"
    fi
fi

if [[ "$round34_enabled" -eq 1 ]]; then
    if python3 - worker/src/providers.json > "${artifact_dir}/p34-1-provider-catalog.txt" <<'PY'
import json
import sys
from pathlib import Path

providers = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = [
    ("cerebras", "api.cerebras.ai", "authorization", "Bearer ", "CEREBRAS_API_KEY"),
    ("gemini", "generativelanguage.googleapis.com", "authorization", "Bearer ", "GEMINI_API_KEY"),
    ("mistral", "api.mistral.ai", "authorization", "Bearer ", "MISTRAL_API_KEY"),
    ("openrouter", "openrouter.ai", "authorization", "Bearer ", "OPENROUTER_API_KEY"),
    ("together", "api.together.xyz", "authorization", "Bearer ", "TOGETHER_AI_API_KEY"),
    ("xai", "api.x.ai", "authorization", "Bearer ", "XAI_API_KEY"),
]
rows = {p["provider_id"]: p for p in providers}
print("# PROOF: local file content")
ok = True
for provider_id, target_host, auth_header, auth_prefix, env_var in expected:
    row = rows.get(provider_id)
    print(f"provider_id={provider_id}")
    if row is None:
        print("  missing=true")
        ok = False
        continue
    print(f"  target_host={row.get('target_host')}")
    print(f"  auth_header={row.get('auth_header')}")
    print(f"  auth_prefix={row.get('auth_prefix')}")
    print(f"  env_var={row.get('env_var')}")
    print(f"  api_base_path={row.get('api_base_path')}")
    if row.get("target_host") != target_host or row.get("auth_header") != auth_header or row.get("auth_prefix") != auth_prefix or row.get("env_var") != env_var:
        ok = False
if not ok:
    raise SystemExit(1)
PY
    then
        exit_codes[p34_1]=0
        results[p34_1]="PASS"
    else
        exit_codes[p34_1]=1
        results[p34_1]="FAIL"
    fi

    if python3 - litellm/config.yaml > "${artifact_dir}/p34-2-litellm-routes.txt" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
checks = [
    ("cerebras-llama-3.3-70b", 'model: cerebras/llama3.1-8b', 'api_key: "subumbra:cerebras_prod"'),
    ("gemini-2.0-flash", 'model: openai/gemini-2.0-flash-001', 'api_key: "subumbra:gemini_prod"', 'api_base: https://generativelanguage.googleapis.com/v1beta/openai/'),
    ("mistral-large", 'model: mistral/mistral-large-latest', 'api_key: "subumbra:mistral_prod"'),
    ("openrouter-claude-sonnet-4", 'model: openrouter/anthropic/claude-sonnet-4', 'api_key: "subumbra:openrouter_prod"'),
    ("together-llama-3.3-70b", 'model: together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo', 'api_key: "subumbra:together_prod"'),
    ("grok-2", 'model: xai/grok-3', 'api_key: "subumbra:xai_prod"'),
]
print("# PROOF: local file content")
ok = True
for parts in checks:
    print(f"route={parts[0]}")
    for part in parts:
        found = part in text
        print(f"  contains={part!r}: {str(found).lower()}")
        ok = ok and found
if not ok:
    raise SystemExit(1)
PY
    then
        exit_codes[p34_2]=0
        results[p34_2]="PASS"
    else
        exit_codes[p34_2]=1
        results[p34_2]="FAIL"
    fi

    p34_3_request_body="$(mktemp)"
    p34_3_status_body="$(mktemp)"
    p34_3_payload="$(python3 - <<'PY'
import json
print(json.dumps({
    "model": "mistral-large",
    "messages": [{"role": "user", "content": "Say test only."}],
    "api_key": "subumbra:mistral_prod",
    "max_tokens": 5,
}, separators=(",", ":")))
PY
    )"
    run_curl_capture         POST         http://127.0.0.1:4000/v1/chat/completions         -H "Authorization: Bearer ${master_key}"         -H "Content-Type: application/json"         -d "$p34_3_payload"
    p34_3_request_exit="$capture_exit"
    p34_3_request_status="$capture_status"
    cp "$capture_body" "$p34_3_request_body"
    write_artifact         "${artifact_dir}/p34-3-mistral-allowed.txt"         "curl --compressed -sS -N -D - -o - -X POST http://127.0.0.1:4000/v1/chat/completions -H 'Authorization: Bearer [REDACTED]' -H 'Content-Type: application/json' -d '${p34_3_payload}'"
    run_curl_capture         GET         http://127.0.0.1:8080/api/status
    p34_3_status_exit="$capture_exit"
    p34_3_status_code="$capture_status"
    if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]]; then
        cp "$capture_body" "$p34_3_status_body"
    fi
    {
        printf 'forge_status_http: %s
' "${p34_3_status_code:-none}"
        printf 'forge_status_body_excerpt:
'
        sed -n '1,60p' "$capture_body" | sed 's/^/  /'
    } >> "${artifact_dir}/p34-3-mistral-allowed.txt"
    cleanup_capture
    p34_3_ok=0
    if [[ "$p34_3_request_exit" -eq 0 ]]; then
        if [[ "$p34_3_request_status" == "200" ]] && grep -q '"choices"' "$p34_3_request_body"; then
            p34_3_ok=1
        elif [[ "$p34_3_request_status" != "403" ]] && ! grep -q 'target_url not allowed' "$p34_3_request_body" && ! grep -q 'subumbra-keys returned' "$p34_3_request_body" && [[ "$p34_3_status_exit" -eq 0 && "$p34_3_status_code" == "200" ]] && audit_event_in_file "$p34_3_status_body" litellm get_key allow allowed mistral_prod; then
            p34_3_ok=1
        fi
    fi
    rm -f "$p34_3_request_body" "$p34_3_status_body"
    exit_codes[p34_3]="$p34_3_request_exit"
    if [[ "$p34_3_ok" -eq 1 ]]; then
        results[p34_3]="PASS"
    else
        results[p34_3]="FAIL"
    fi
fi

if [[ "$round35_enabled" -eq 1 ]]; then
    if python3 - .env.bootstrap.example > "${artifact_dir}/p35-1-adapter-ids-doc.txt" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
checks = ["ADAPTER_IDS=", "TEST_ADAPTER_ALLOWED_KEYS="]
print("# PROOF: local file content")
ok = True
for check in checks:
    found = check in text
    print(f"contains={check!r}: {str(found).lower()}")
    ok = ok and found
if not ok:
    raise SystemExit(1)
PY
    then
        exit_codes[p35_1]=0
        results[p35_1]="PASS"
    else
        exit_codes[p35_1]=1
        results[p35_1]="FAIL"
    fi

    p35_matrix_file="$(mktemp)"
    if python3 - "$forge_registry" "$keys_json" "$p35_matrix_file" > "${artifact_dir}/p35-2-registry-custom-adapter.txt" <<'PY'
import json
import sys
from pathlib import Path

registry = json.loads(sys.argv[1])
keys = json.loads(sys.argv[2])
matrix_path = Path(sys.argv[3])
builtins = {"litellm", "subumbra-proxy", "subumbra-probe", "subumbra-ui"}
custom_ids = [adapter_id for adapter_id in registry if adapter_id not in builtins]
print("# PROOF: live .env SUBUMBRA_ADAPTER_REGISTRY")
if not custom_ids:
    print("custom_adapter_present=false")
    raise SystemExit(1)
adapter_id = custom_ids[0]
row = registry[adapter_id]
allowed_keys = row.get("allowed_keys", [])
if not allowed_keys:
    print(f"adapter_id={adapter_id}")
    print("allowed_keys=[]")
    raise SystemExit(1)
all_keys = sorted(keys)
disallowed_key = next((key_id for key_id in all_keys if key_id not in allowed_keys), None)
if disallowed_key is None:
    print(f"adapter_id={adapter_id}")
    print("disallowed_key_present=false")
    raise SystemExit(1)
normalized = adapter_id.upper().replace("-", "_")
matrix = {
    "adapter_id": adapter_id,
    "normalized_id": normalized,
    "allowed_key": allowed_keys[0],
    "disallowed_key": disallowed_key,
}
matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
print(f"adapter_id={adapter_id}")
print(f"normalized_id={normalized}")
print(f"allowed_keys={allowed_keys}")
print(f"can_list_keys={row.get('can_list_keys')}")
print(f"can_read_stats={row.get('can_read_stats')}")
print(f"token_present={bool(row.get('token'))}")
print(f"allowed_key={allowed_keys[0]}")
print(f"disallowed_key={disallowed_key}")
if row.get("can_list_keys") is not False or row.get("can_read_stats") is not False or not row.get("token"):
    raise SystemExit(1)
PY
    then
        exit_codes[p35_2]=0
        results[p35_2]="PASS"
        p35_custom_adapter_id="$(python3 - "$p35_matrix_file" <<'PY'
import json
import sys
print(json.loads(open(sys.argv[1], encoding="utf-8").read())["adapter_id"])
PY
)"
        p35_custom_adapter_norm="$(python3 - "$p35_matrix_file" <<'PY'
import json
import sys
print(json.loads(open(sys.argv[1], encoding="utf-8").read())["normalized_id"])
PY
)"
        p35_allowed_key="$(python3 - "$p35_matrix_file" <<'PY'
import json
import sys
print(json.loads(open(sys.argv[1], encoding="utf-8").read())["allowed_key"])
PY
)"
        p35_disallowed_key="$(python3 - "$p35_matrix_file" <<'PY'
import json
import sys
print(json.loads(open(sys.argv[1], encoding="utf-8").read())["disallowed_key"])
PY
)"
    else
        exit_codes[p35_2]=1
        results[p35_2]="FAIL"
        p35_custom_adapter_id=""
        p35_custom_adapter_norm=""
        p35_allowed_key=""
        p35_disallowed_key=""
    fi

    if [[ -n "$p35_custom_adapter_norm" ]] && python3 - .env "$p35_custom_adapter_norm" > "${artifact_dir}/p35-3-runtime-token-custom-adapter.txt" <<'PY'
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
token_key = f"SUBUMBRA_TOKEN_{sys.argv[2]}"
text = env_path.read_text(encoding="utf-8")
print("# PROOF: live .env token presence")
for line in text.splitlines():
    if line.startswith(token_key + "="):
        value = line.split("=", 1)[1]
        print(f"token_key={token_key}")
        print(f"token_present={bool(value)}")
        print(f"token_prefix={value[:8]}...")
        raise SystemExit(0 if value else 1)
print(f"token_key={token_key}")
print("token_present=false")
raise SystemExit(1)
PY
    then
        exit_codes[p35_3]=0
        results[p35_3]="PASS"
    else
        exit_codes[p35_3]=1
        results[p35_3]="FAIL"
    fi

    p35_custom_token=""
    if [[ -n "$p35_custom_adapter_norm" ]]; then
        p35_custom_token="$(grep "^SUBUMBRA_TOKEN_${p35_custom_adapter_norm}=" .env | cut -d= -f2- || true)"
    fi
    p35_4_probe_output="$(mktemp)"
    p35_4_status_body="$(mktemp)"
    p35_4_exit=1
    p35_4_ok=0
    if [[ -n "$p35_custom_token" && -n "$forge_hmac_key" && -n "$p35_allowed_key" && -n "$p35_disallowed_key" ]]; then
        p35_4_exit=0
        docker exec -i subumbra-keys python - "$p35_custom_token" "$forge_hmac_key" "$p35_allowed_key" "$p35_disallowed_key" > "$p35_4_probe_output" 2>&1 <<'PY' || p35_4_exit=$?
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

token, hmac_key, allowed_key, disallowed_key = sys.argv[1:5]

def fetch(key_id):
    timestamp = str(int(time.time()))
    signature = hmac.new(
        hmac_key.encode(),
        f"{key_id}:{timestamp}".encode(),
        hashlib.sha256,
    ).hexdigest()
    req = urllib.request.Request(
        f"http://127.0.0.1:9090/keys/{key_id}",
        headers={
            "X-Subumbra-Token": token,
            "X-Subumbra-Timestamp": timestamp,
            "X-Subumbra-Signature": signature,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")

allowed_status, allowed_body = fetch(allowed_key)
denied_status, denied_body = fetch(disallowed_key)
print(f"allowed_status={allowed_status}")
print(f"allowed_body={allowed_body[:400]}")
print(f"denied_status={denied_status}")
print(f"denied_body={denied_body[:400]}")
if allowed_status != 200:
    raise SystemExit(1)
json.loads(allowed_body)
if denied_status != 403:
    raise SystemExit(1)
PY
        if [[ "$p35_4_exit" -eq 0 ]]; then
            run_curl_capture GET http://127.0.0.1:8080/api/status
            p35_4_status_exit="$capture_exit"
            p35_4_status_code="$capture_status"
            if [[ "$capture_exit" -eq 0 && "$capture_status" == "200" ]]; then
                cp "$capture_body" "$p35_4_status_body"
            fi
            {
                printf '# PROOF: container-hosted HTTP path\n'
                printf 'command: docker exec subumbra-keys python - [allowed=%s disallowed=%s token/hmac redacted]\n' "$p35_allowed_key" "$p35_disallowed_key"
                cat "$p35_4_probe_output"
                printf 'forge_status_http: %s\n' "${p35_4_status_code:-none}"
                printf 'forge_status_body_excerpt:\n'
                sed -n '1,60p' "$capture_body" | sed 's/^/  /'
            } > "${artifact_dir}/p35-4-custom-adapter-scope-enforcement.txt"
            cleanup_capture
            if [[ "$p35_4_status_exit" -eq 0 && "$p35_4_status_code" == "200" ]] \
                && audit_event_in_file "$p35_4_status_body" "$p35_custom_adapter_id" get_key allow allowed "$p35_allowed_key" \
                && audit_event_in_file "$p35_4_status_body" "$p35_custom_adapter_id" get_key deny key_scope_denied "$p35_disallowed_key"; then
                p35_4_ok=1
            fi
        else
            {
                printf '# PROOF: container-hosted HTTP path\n'
                printf 'command: docker exec subumbra-keys python - [allowed=%s disallowed=%s token/hmac redacted]\n' "$p35_allowed_key" "$p35_disallowed_key"
                cat "$p35_4_probe_output"
            } > "${artifact_dir}/p35-4-custom-adapter-scope-enforcement.txt"
        fi
    else
        printf '# PROOF: container-hosted HTTP path\nmissing custom adapter token, HMAC key, or derived key ids\n' > "${artifact_dir}/p35-4-custom-adapter-scope-enforcement.txt"
    fi
    rm -f "$p35_matrix_file" "$p35_4_probe_output" "$p35_4_status_body"
    exit_codes[p35_4]="$p35_4_exit"
    if [[ "$p35_4_ok" -eq 1 ]]; then
        results[p35_4]="PASS"
    else
        results[p35_4]="FAIL"
    fi
fi

if [[ "$round36_enabled" -eq 1 ]]; then
    if python3 - worker/src/worker.js > "${artifact_dir}/p36-1-worker-kv-runtime.txt" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
checks = {
    'no_import': 'import PROVIDER_REGISTRY from "./providers.json"' not in text,
    'no_registry_helper': 'function registryEntryByHostname(' not in text,
    'no_upstream_registry': 'const UPSTREAM_REGISTRY =' not in text,
    'has_get_registry_entry': 'async function getRegistryEntry(env, hostname)' in text,
    'has_kv_binding_check': '!env.PROVIDER_REGISTRY_KV' in text,
    'has_cache_ttl': 'cacheTtl: 30' in text,
}
print("# PROOF: local file content")
ok = True
for key, value in checks.items():
    print(f"{key}={str(value).lower()}")
    ok = ok and value
if not ok:
    raise SystemExit(1)
PY
    then
        exit_codes[p36_1]=0
        results[p36_1]="PASS"
    else
        exit_codes[p36_1]=1
        results[p36_1]="FAIL"
    fi

    p36_cf_api_token="$(env_value_from_source CF_API_TOKEN)"
    p36_cf_account_id="$(env_value_from_source CF_ACCOUNT_ID)"
    p36_cf_worker_name="$(env_value_from_source CF_WORKER_NAME)"
    if [[ -z "${p36_cf_worker_name:-}" ]]; then
        p36_cf_worker_name="subumbra-proxy"
    fi

    p36_namespace_id="$(docker compose run --rm -u 0 -T subumbra-keys cat /app/data/kv-config.json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["namespace_id"])' 2>/dev/null || true)"
    p36_remote_registry_tmp="$(mktemp)"
    p36_2_ok=0
    {
        echo '# PROOF: host-facing KV bootstrap state'
        echo "env_source=${env_source}"
        echo "namespace_id_present=$([[ -n "$p36_namespace_id" ]] && echo true || echo false)"
        docker compose run --rm -u 0 -T subumbra-keys cat /app/data/kv-config.json 2>/dev/null | sed 's/^/kv_config: /'
    } > "${artifact_dir}/p36-2-kv-bootstrap-state.txt"
    if [[ -n "${p36_cf_api_token:-}" && -n "${p36_cf_account_id:-}" && -n "${p36_namespace_id:-}" ]]; then
        set +e
        docker compose --profile bootstrap run --rm -T --entrypoint sh \
            -e CLOUDFLARE_API_TOKEN="$p36_cf_api_token" \
            -e CLOUDFLARE_ACCOUNT_ID="$p36_cf_account_id" \
            bootstrap -lc 'wrangler kv key get subumbra_registry_v1 --namespace-id "$1" --remote' \
            _ "$p36_namespace_id" >"$p36_remote_registry_tmp" 2>&1
        p36_remote_registry_exit=$?
        set -e
        {
            echo "remote_registry_exit=$p36_remote_registry_exit"
            echo 'remote_registry_excerpt:'
            sed -n '1,40p' "$p36_remote_registry_tmp" | sed 's/^/  /'
        } >> "${artifact_dir}/p36-2-kv-bootstrap-state.txt"
        if [[ "$p36_remote_registry_exit" -eq 0 ]] && python3 - "$p36_remote_registry_tmp" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(data, list) or not data:
    raise SystemExit(1)
required = {"provider_id", "target_host", "auth_header", "auth_prefix"}
for entry in data:
    if set(entry) != required:
        raise SystemExit(1)
PY
        then
            p36_2_ok=1
        fi
    else
        echo 'remote_registry_exit=missing_cf_credentials_or_namespace' >> "${artifact_dir}/p36-2-kv-bootstrap-state.txt"
    fi
    rm -f "$p36_remote_registry_tmp"
    exit_codes[p36_2]=$(( p36_2_ok == 1 ? 0 : 1 ))
    if [[ "$p36_2_ok" -eq 1 ]]; then
        results[p36_2]="PASS"
    else
        results[p36_2]="FAIL"
    fi

    p36_verify_provider_id="round36verify"
    p36_verify_target_host="round36.verify.example.com"
    p36_verify_entry="$(python3 - <<'PY'
import json
print(json.dumps([{
    "provider_id": "round36verify",
    "target_host": "round36.verify.example.com",
    "auth_header": "authorization",
    "auth_prefix": "Bearer "
}], separators=(",", ":")))
PY
)"
    p36_custom_backup="$(mktemp)"
    p36_custom_before_present=0
    if docker compose run --rm -u 0 -T subumbra-keys sh -lc 'cat /app/data/custom-providers.json' >"$p36_custom_backup" 2>/dev/null; then
        p36_custom_before_present=1
    fi
    printf '%s' "$p36_verify_entry" | docker compose run --rm -u 0 -T subumbra-keys sh -lc 'cat > /app/data/custom-providers.json'

    p36_push_tmp="$(mktemp)"
    p36_remote_after_tmp="$(mktemp)"
    p36_code_tmp="$(mktemp)"
    p36_3_ok=0
    p36_4_ok=0
    {
        echo '# PROOF: standalone registry publish'
        echo 'custom_providers_json:'
        docker compose run --rm -u 0 -T subumbra-keys sh -lc 'cat /app/data/custom-providers.json' | sed 's/^/  /'
    } > "${artifact_dir}/p36-3-push-registry-live-update.txt"
    set +e
    docker compose --profile bootstrap run --rm -T \
        -e CF_API_TOKEN="$p36_cf_api_token" \
        -e CF_ACCOUNT_ID="$p36_cf_account_id" \
        -e CF_WORKER_NAME="$p36_cf_worker_name" \
        bootstrap --push-registry >"$p36_push_tmp" 2>&1
    p36_push_exit=$?
    set -e
    {
        echo "push_registry_exit=$p36_push_exit"
        echo 'push_registry_output:'
        sed -n '1,80p' "$p36_push_tmp" | sed 's/^/  /'
    } >> "${artifact_dir}/p36-3-push-registry-live-update.txt"
    if [[ "$p36_push_exit" -eq 0 ]]; then
        set +e
        docker compose --profile bootstrap run --rm -T --entrypoint sh \
            -e CLOUDFLARE_API_TOKEN="$p36_cf_api_token" \
            -e CLOUDFLARE_ACCOUNT_ID="$p36_cf_account_id" \
            bootstrap -lc 'wrangler kv key get subumbra_registry_v1 --namespace-id "$1" --remote' \
            _ "$p36_namespace_id" >"$p36_remote_after_tmp" 2>&1
        p36_remote_after_exit=$?
        set -e
        {
            echo "remote_after_exit=$p36_remote_after_exit"
            echo 'remote_after_excerpt:'
            sed -n '1,60p' "$p36_remote_after_tmp" | sed 's/^/  /'
        } >> "${artifact_dir}/p36-3-push-registry-live-update.txt"
        if [[ "$p36_remote_after_exit" -eq 0 ]] && python3 - "$p36_remote_after_tmp" "$p36_verify_provider_id" "$p36_verify_target_host" <<'PY'
import json
import sys
from pathlib import Path

registry = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
provider_id = sys.argv[2]
target_host = sys.argv[3]
for entry in registry:
    if entry.get("provider_id") == provider_id and entry.get("target_host") == target_host:
        raise SystemExit(0)
raise SystemExit(1)
PY
        then
            p36_3_ok=1
        fi
    fi

    if python3 - bootstrap/subumbra-bootstrap.py >"$p36_code_tmp" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
checks = {
    "auth_header_prompt": 'Auth header name' in text,
    "auth_prefix_prompt": 'Auth prefix' in text,
    "custom_provider_upsert": '_upsert_custom_provider_registry_entry(' in text,
    "custom_registry_file": 'custom-providers.json' in text,
}
print("# PROOF: local file content")
ok = True
for key, value in checks.items():
    print(f"{key}={str(value).lower()}")
    ok = ok and value
if not ok:
    raise SystemExit(1)
PY
    then
        {
            echo '# PROOF: custom provider persistence path'
            cat "$p36_code_tmp"
            echo 'custom_providers_json:'
            docker compose run --rm -u 0 -T subumbra-keys sh -lc 'cat /app/data/custom-providers.json' | sed 's/^/  /'
        } > "${artifact_dir}/p36-4-custom-provider-persistence.txt"
        p36_4_ok=1
    else
        {
            echo '# PROOF: custom provider persistence path'
            cat "$p36_code_tmp"
        } > "${artifact_dir}/p36-4-custom-provider-persistence.txt"
    fi

    if [[ "$p36_custom_before_present" -eq 1 ]]; then
        cat "$p36_custom_backup" | docker compose run --rm -u 0 -T subumbra-keys sh -lc 'cat > /app/data/custom-providers.json'
    else
        docker compose run --rm -u 0 -T subumbra-keys sh -lc 'rm -f /app/data/custom-providers.json' >/dev/null
    fi
    if [[ -n "${p36_cf_api_token:-}" && -n "${p36_cf_account_id:-}" ]]; then
        docker compose --profile bootstrap run --rm -T \
            -e CF_API_TOKEN="$p36_cf_api_token" \
            -e CF_ACCOUNT_ID="$p36_cf_account_id" \
            -e CF_WORKER_NAME="$p36_cf_worker_name" \
            bootstrap --push-registry >/dev/null 2>&1 || true
    fi
    rm -f "$p36_custom_backup" "$p36_push_tmp" "$p36_remote_after_tmp" "$p36_code_tmp"
    exit_codes[p36_3]=$(( p36_3_ok == 1 ? 0 : 1 ))
    results[p36_3]=$([[ "$p36_3_ok" -eq 1 ]] && echo PASS || echo FAIL)
    exit_codes[p36_4]=$(( p36_4_ok == 1 ? 0 : 1 ))
    results[p36_4]=$([[ "$p36_4_ok" -eq 1 ]] && echo PASS || echo FAIL)

    p36_direct_payload="$(python3 - "$keys_json" "$proxy_allowed_key" "$proxy_target_url" "$proxy_method" "$proxy_headers_json" "$proxy_body_json" <<'PY'
import json
import sys

keys = json.loads(sys.argv[1])
key_id = sys.argv[2]
record = keys[key_id]
body = None if sys.argv[6] == "null" else json.loads(sys.argv[6])
payload = {
    "ciphertext": record["ciphertext"],
    "provider": record["provider"],
    "target_url": sys.argv[3],
    "method": sys.argv[4],
    "headers": json.loads(sys.argv[5]),
    "body": body,
    "wrapped_dek": record["wrapped_dek"],
    "pub_key_fp": record["pub_key_fp"],
    "key_id": record["key_id"],
    "enc_version": record["enc_version"],
}
print(json.dumps(payload, separators=(",", ":")))
PY
)"
    p36_missing_host_payload="$(python3 - "$keys_json" "$proxy_allowed_key" "$proxy_method" "$proxy_headers_json" "$proxy_body_json" <<'PY'
import json
import sys

keys = json.loads(sys.argv[1])
key_id = sys.argv[2]
record = keys[key_id]
body = None if sys.argv[5] == "null" else json.loads(sys.argv[5])
payload = {
    "ciphertext": record["ciphertext"],
    "provider": record["provider"],
    "target_url": "https://example.com/not-allowed",
    "method": sys.argv[3],
    "headers": json.loads(sys.argv[4]),
    "body": body,
    "wrapped_dek": record["wrapped_dek"],
    "pub_key_fp": record["pub_key_fp"],
    "key_id": record["key_id"],
    "enc_version": record["enc_version"],
}
print(json.dumps(payload, separators=(",", ":")))
PY
)"
    p36_worker_args=(
        -H "Content-Type: application/json"
        -H "X-Subumbra-Token: ${forge_token_proxy}"
    )
    if [[ -n "$cf_access_client_id" ]]; then
        p36_worker_args+=(-H "CF-Access-Client-Id: ${cf_access_client_id}")
        p36_worker_args+=(-H "CF-Access-Client-Secret: ${cf_access_client_secret}")
    fi
    p36_registry_backup="$(mktemp)"
    p36_5_ok=0
    if [[ -n "${p36_cf_api_token:-}" && -n "${p36_cf_account_id:-}" && -n "${p36_namespace_id:-}" && -n "${forge_token_proxy:-}" ]]; then
        docker compose --profile bootstrap run --rm -T --entrypoint sh \
            -e CLOUDFLARE_API_TOKEN="$p36_cf_api_token" \
            -e CLOUDFLARE_ACCOUNT_ID="$p36_cf_account_id" \
            bootstrap -lc 'wrangler kv key get subumbra_registry_v1 --namespace-id "$1" --remote' \
            _ "$p36_namespace_id" >"$p36_registry_backup" 2>/dev/null || true
    fi
    {
        echo '# PROOF: direct Worker fail-closed behavior'
        run_curl_capture \
            POST \
            "${worker_url}/proxy" \
            "${p36_worker_args[@]}" \
            -d "$p36_missing_host_payload"
        echo "missing_host_exit=$capture_exit"
        echo "missing_host_status=${capture_status:-none}"
        echo 'missing_host_body:'
        sed -n '1,40p' "$capture_body" | sed 's/^/  /'
        p36_missing_host_ok=0
        if [[ "$capture_exit" -eq 0 && "$capture_status" == "403" ]] && grep -q 'target_url not allowed' "$capture_body"; then
            p36_missing_host_ok=1
        fi
        cleanup_capture

        p36_missing_registry_ok=0
        if [[ -s "$p36_registry_backup" ]]; then
            set +e
            docker compose --profile bootstrap run --rm -T --entrypoint sh \
                -e CLOUDFLARE_API_TOKEN="$p36_cf_api_token" \
                -e CLOUDFLARE_ACCOUNT_ID="$p36_cf_account_id" \
                bootstrap -lc 'wrangler kv key delete subumbra_registry_v1 --namespace-id "$1" --remote' \
                _ "$p36_namespace_id" >/dev/null 2>&1
            p36_delete_exit=$?
            set -e
            echo "delete_registry_exit=$p36_delete_exit"
            for _ in $(seq 1 19); do
                sleep 5
                run_curl_capture \
                    POST \
                    "${worker_url}/proxy" \
                    "${p36_worker_args[@]}" \
                    -d "$p36_direct_payload"
                echo "missing_registry_exit=$capture_exit"
                echo "missing_registry_status=${capture_status:-none}"
                echo 'missing_registry_body:'
                sed -n '1,40p' "$capture_body" | sed 's/^/  /'
                if [[ "$capture_exit" -eq 0 && "$capture_status" == "503" ]] && grep -q 'worker not configured' "$capture_body"; then
                    p36_missing_registry_ok=1
                    cleanup_capture
                    break
                fi
                cleanup_capture
            done
            docker compose --profile bootstrap run --rm -T --entrypoint sh \
                -e CLOUDFLARE_API_TOKEN="$p36_cf_api_token" \
                -e CLOUDFLARE_ACCOUNT_ID="$p36_cf_account_id" \
                bootstrap -lc 'wrangler kv key put subumbra_registry_v1 "$2" --namespace-id "$1" --remote' \
                _ "$p36_namespace_id" "$(cat "$p36_registry_backup")" >/dev/null 2>&1 || true
        fi

        if [[ "$p36_missing_host_ok" -eq 1 && "$p36_missing_registry_ok" -eq 1 ]]; then
            p36_5_ok=1
        fi
    } > "${artifact_dir}/p36-5-worker-fail-closed.txt"
    rm -f "$p36_registry_backup"
    exit_codes[p36_5]=$(( p36_5_ok == 1 ? 0 : 1 ))
    results[p36_5]=$([[ "$p36_5_ok" -eq 1 ]] && echo PASS || echo FAIL)
fi

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
