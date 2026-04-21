#!/usr/bin/env bash
set -euo pipefail

artifact_dir="${VERIFY_ARTIFACT_DIR:?VERIFY_ARTIFACT_DIR is required}"
mkdir -p "$artifact_dir"

r43_0="${artifact_dir}/r43-1-allm-0-baseline-direct.txt"
r43_1="${artifact_dir}/r43-1-allm-1-proxy-health.txt"
r43_2="${artifact_dir}/r43-1-allm-2-takeover-applied.txt"
r43_3="${artifact_dir}/r43-1-allm-3-post-takeover-chat.txt"
r43_4="${artifact_dir}/r43-1-allm-4-existing-workspace-continuity.txt"
r43_5="${artifact_dir}/r43-1-allm-5-fresh-mediated-embedding.txt"
r43_6="${artifact_dir}/r43-1-allm-6-post-ingest-chat-rag.txt"
r43_7="${artifact_dir}/r43-1-allm-7-fail-closed.txt"

ALLM_API_KEY="${ALLM_API_KEY:-}"
ALLM_BASE="${ALLM_BASE:-http://127.0.0.1:3001}"
PROXY_BASE="${PROXY_BASE:-http://127.0.0.1:8090}"
ANYTHINGLLM_DIR="${ANYTHINGLLM_DIR:-/opt/anythingllm}"
SUBUMBRA_DIR="${SUBUMBRA_DIR:-/opt/subumbra}"
ROUND_FIXTURE="${ROUND_FIXTURE:-council/round-43-1-anythingllm/anythingllm.env}"
KEY_ID="${ALLM_KEY_ID:-openai_prod}"
MODEL_NAME="${ALLM_MODEL_NAME:-gpt-4o-mini}"
EMBED_MODEL_NAME="${ALLM_EMBED_MODEL_NAME:-text-embedding-3-small}"
EXISTING_WORKSPACE_SLUG="${ALLM_EXISTING_WORKSPACE_SLUG:-}"
SUCCESS_MARKER=0

if [[ -z "$ALLM_API_KEY" ]]; then
    echo "ALLM_API_KEY is required. Generate an AnythingLLM API key from the admin UI and export it before running verification." >&2
    exit 1
fi

if [[ ! -d "$ANYTHINGLLM_DIR" ]]; then
    echo "ANYTHINGLLM_DIR not found: $ANYTHINGLLM_DIR" >&2
    exit 1
fi

if [[ ! -f "$ROUND_FIXTURE" ]]; then
    echo "round fixture not found: $ROUND_FIXTURE" >&2
    exit 1
fi

if [[ ! -f "${SUBUMBRA_DIR}/.env.bootstrap_bak" ]]; then
    echo "required bootstrap backup missing: ${SUBUMBRA_DIR}/.env.bootstrap_bak" >&2
    exit 1
fi

if [[ ! -f "${ANYTHINGLLM_DIR}/.env" ]]; then
    echo "required AnythingLLM env missing: ${ANYTHINGLLM_DIR}/.env" >&2
    exit 1
fi

if [[ ! -f "${ANYTHINGLLM_DIR}/docker-compose.yml" ]]; then
    echo "required AnythingLLM compose file missing: ${ANYTHINGLLM_DIR}/docker-compose.yml" >&2
    exit 1
fi

backup_env="$(mktemp /tmp/anythingllm-r43-1-env.XXXXXX)"
cp "${ANYTHINGLLM_DIR}/.env" "$backup_env"

restore_original_env() {
    cp "$backup_env" "${ANYTHINGLLM_DIR}/.env"
    (
        cd "$ANYTHINGLLM_DIR"
        docker compose up -d --force-recreate anythingllm >/dev/null 2>&1 || true
    )
}

cleanup() {
    if [[ "$SUCCESS_MARKER" -ne 1 ]]; then
        restore_original_env
    fi
    rm -f "$backup_env"
}
trap cleanup EXIT

log_proxy_since() {
    local since="$1"
    docker logs subumbra-proxy --since "$since" 2>&1 || true
}

log_anythingllm_since() {
    local since="$1"
    docker logs anythingllm --since "$since" 2>&1 || true
}

ensure_contains() {
    local haystack="$1"
    local needle="$2"
    local label="$3"
    if ! printf '%s\n' "$haystack" | grep -Fq "$needle"; then
        echo "missing evidence for ${label}: ${needle}" >&2
        exit 1
    fi
}

ensure_not_contains() {
    local haystack="$1"
    local needle="$2"
    local label="$3"
    if printf '%s\n' "$haystack" | grep -Fq "$needle"; then
        echo "unexpected evidence for ${label}: ${needle}" >&2
        exit 1
    fi
}

anythingllm_request() {
    local method="$1"
    local path="$2"
    local payload="${3:-}"
    python3 - "$ALLM_BASE" "$ALLM_API_KEY" "$method" "$path" "$payload" <<'PY'
import json
import sys
import urllib.request
import urllib.error

base, api_key, method, path, payload = sys.argv[1:6]
data = payload.encode("utf-8") if payload else None
req = urllib.request.Request(
    base.rstrip("/") + path,
    data=data,
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    method=method,
)
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = resp.read().decode("utf-8")
        print(resp.status)
        print(body)
except urllib.error.HTTPError as exc:
    print(exc.code)
    print(exc.read().decode("utf-8"))
    raise
PY
}

choose_existing_workspace_slug() {
    local listing
    listing="$(anythingllm_request GET "/api/v1/workspaces")"
    local status
    status="$(printf '%s\n' "$listing" | sed -n '1p')"
    if [[ "$status" != "200" ]]; then
        echo "AnythingLLM workspace listing failed" >&2
        exit 1
    fi
    python3 - "$EXISTING_WORKSPACE_SLUG" "$(printf '%s\n' "$listing" | sed -n '2,$p')" <<'PY'
import json
import sys

requested = sys.argv[1]
body = json.loads(sys.argv[2])
workspaces = body.get("workspaces") or []
if requested:
    for workspace in workspaces:
        if workspace.get("slug") == requested:
            print(requested)
            raise SystemExit(0)
    raise SystemExit(f"requested workspace slug not found: {requested}")
if not workspaces:
    raise SystemExit("no workspaces found; cannot prove existing-workspace continuity")
print(workspaces[0]["slug"])
PY
}

write_baseline_env() {
    local openai_key="$1"
    python3 - "$backup_env" "${ANYTHINGLLM_DIR}/.env" "$openai_key" <<'PY'
import sys
from pathlib import Path

source_path, dest_path, openai_key = sys.argv[1:4]
managed = {
    "LLM_PROVIDER",
    "OPEN_AI_KEY",
    "OPEN_MODEL_PREF",
    "GENERIC_OPEN_AI_BASE_PATH",
    "GENERIC_OPEN_AI_API_KEY",
    "GENERIC_OPEN_AI_MODEL_PREF",
    "GENERIC_OPEN_AI_EMBEDDING_API_KEY",
    "EMBEDDING_ENGINE",
    "EMBEDDING_BASE_PATH",
    "EMBEDDING_MODEL_PREF",
    "EMBEDDING_MODEL_MAX_CHUNK_LENGTH",
    "VECTOR_DB",
}

lines = []
for raw in Path(source_path).read_text(encoding="utf-8").splitlines():
    key = raw.split("=", 1)[0] if "=" in raw else None
    if key in managed:
        continue
    lines.append(raw)

lines.extend(
    [
        "VECTOR_DB=lancedb",
        "LLM_PROVIDER=openai",
        f"OPEN_AI_KEY={openai_key}",
        "OPEN_MODEL_PREF=gpt-4o-mini",
        "EMBEDDING_ENGINE=native",
    ]
)

Path(dest_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

write_takeover_env() {
    python3 - "$backup_env" "${ANYTHINGLLM_DIR}/.env" "$KEY_ID" "$MODEL_NAME" <<'PY'
import sys
from pathlib import Path

source_path, dest_path, key_id, model_name = sys.argv[1:5]
managed = {
    "LLM_PROVIDER",
    "OPEN_AI_KEY",
    "OPEN_MODEL_PREF",
    "GENERIC_OPEN_AI_BASE_PATH",
    "GENERIC_OPEN_AI_API_KEY",
    "GENERIC_OPEN_AI_MODEL_PREF",
    "GENERIC_OPEN_AI_EMBEDDING_API_KEY",
    "EMBEDDING_ENGINE",
    "EMBEDDING_BASE_PATH",
    "EMBEDDING_MODEL_PREF",
    "EMBEDDING_MODEL_MAX_CHUNK_LENGTH",
    "VECTOR_DB",
}

lines = []
for raw in Path(source_path).read_text(encoding="utf-8").splitlines():
    key = raw.split("=", 1)[0] if "=" in raw else None
    if key in managed:
        continue
    lines.append(raw)

lines.extend(
    [
        "VECTOR_DB=lancedb",
        "LLM_PROVIDER=generic-openai",
        "GENERIC_OPEN_AI_BASE_PATH=http://subumbra-proxy:8090/t/v1",
        f"GENERIC_OPEN_AI_API_KEY={key_id}",
        f"GENERIC_OPEN_AI_MODEL_PREF={model_name}",
        "EMBEDDING_ENGINE=native",
    ]
)

Path(dest_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

write_embedding_env() {
    python3 - "$backup_env" "${ANYTHINGLLM_DIR}/.env" "$KEY_ID" "$MODEL_NAME" "$EMBED_MODEL_NAME" <<'PY'
import sys
from pathlib import Path

source_path, dest_path, key_id, model_name, embed_model_name = sys.argv[1:6]
managed = {
    "LLM_PROVIDER",
    "OPEN_AI_KEY",
    "OPEN_MODEL_PREF",
    "GENERIC_OPEN_AI_BASE_PATH",
    "GENERIC_OPEN_AI_API_KEY",
    "GENERIC_OPEN_AI_MODEL_PREF",
    "GENERIC_OPEN_AI_EMBEDDING_API_KEY",
    "EMBEDDING_ENGINE",
    "EMBEDDING_BASE_PATH",
    "EMBEDDING_MODEL_PREF",
    "EMBEDDING_MODEL_MAX_CHUNK_LENGTH",
    "VECTOR_DB",
}

lines = []
for raw in Path(source_path).read_text(encoding="utf-8").splitlines():
    key = raw.split("=", 1)[0] if "=" in raw else None
    if key in managed:
        continue
    lines.append(raw)

lines.extend(
    [
        "VECTOR_DB=lancedb",
        "LLM_PROVIDER=generic-openai",
        "GENERIC_OPEN_AI_BASE_PATH=http://subumbra-proxy:8090/t/v1",
        f"GENERIC_OPEN_AI_API_KEY={key_id}",
        f"GENERIC_OPEN_AI_MODEL_PREF={model_name}",
        "EMBEDDING_ENGINE=generic-openai",
        "EMBEDDING_BASE_PATH=http://subumbra-proxy:8090/t/v1",
        f"GENERIC_OPEN_AI_EMBEDDING_API_KEY={key_id}",
        f"EMBEDDING_MODEL_PREF={embed_model_name}",
        "EMBEDDING_MODEL_MAX_CHUNK_LENGTH=8192",
    ]
)

Path(dest_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

force_recreate_anythingllm() {
    (
        cd "$ANYTHINGLLM_DIR"
        docker compose up -d --force-recreate anythingllm
    )
}

openai_key="$(grep '^OPENAI_KEY=' "${SUBUMBRA_DIR}/.env.bootstrap_bak" | cut -d= -f2- || true)"
if [[ -z "$openai_key" ]]; then
    echo "OPENAI_KEY missing in ${SUBUMBRA_DIR}/.env.bootstrap_bak" >&2
    exit 1
fi

workspace_slug="$(choose_existing_workspace_slug)"

write_baseline_env "$openai_key"
force_recreate_anythingllm >/dev/null
sleep 15

baseline_since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sleep 1
baseline_chat="$(anythingllm_request POST "/api/v1/workspace/${workspace_slug}/chat" '{"message":"baseline direct-provider verification","mode":"chat"}')"
sleep 2
baseline_proxy_log="$(log_proxy_since "$baseline_since")"
baseline_anythingllm_log="$(log_anythingllm_since "$baseline_since")"
{
    printf '# PROOF: r43_1_allm_0 baseline direct-provider success before takeover\n'
    printf 'workspace_slug: %s\n' "$workspace_slug"
    printf 'response:\n%s\n' "$baseline_chat"
    printf 'proxy_log:\n%s\n' "$baseline_proxy_log"
    printf 'anythingllm_log:\n%s\n' "$baseline_anythingllm_log"
} >"$r43_0"

baseline_status="$(printf '%s\n' "$baseline_chat" | sed -n '1p')"
if [[ "$baseline_status" != "200" ]]; then
    echo "baseline direct-provider chat failed" >&2
    exit 1
fi
ensure_not_contains "$baseline_proxy_log" 'key_id=' "baseline direct-provider no-proxy hard gate"

health_body="$(curl -sS "${PROXY_BASE}/health")"
{
    printf '# PROOF: r43_1_allm_1 proxy health\n'
    printf 'command: curl -sS %s/health\n' "$PROXY_BASE"
    printf '%s\n' "$health_body"
} >"$r43_1"

python3 - <<'PY' "$health_body"
import json
import sys
body = json.loads(sys.argv[1])
if body.get("status") != "ok" or body.get("worker_auth") != "ok":
    raise SystemExit("proxy health is not ok/ok")
PY

write_takeover_env
takeover_recreate_output="$(force_recreate_anythingllm 2>&1)"
sleep 15
takeover_env="$(docker exec anythingllm env | sort | grep -E 'LLM_PROVIDER|GENERIC_OPEN_AI_BASE_PATH|GENERIC_OPEN_AI_API_KEY|EMBEDDING_ENGINE' || true)"
{
    printf '# PROOF: r43_1_allm_2 takeover applied\n'
    printf 'recreate_output:\n%s\n' "$takeover_recreate_output"
    printf 'container_env:\n%s\n' "$takeover_env"
} >"$r43_2"

ensure_contains "$takeover_env" 'LLM_PROVIDER=generic-openai' "takeover env"
ensure_contains "$takeover_env" 'GENERIC_OPEN_AI_BASE_PATH=http://subumbra-proxy:8090/t/v1' "takeover env"
ensure_contains "$takeover_env" "GENERIC_OPEN_AI_API_KEY=${KEY_ID}" "takeover env"
ensure_contains "$takeover_env" 'EMBEDDING_ENGINE=native' "takeover env"

takeover_chat_since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sleep 1
takeover_chat="$(anythingllm_request POST "/api/v1/workspace/${workspace_slug}/chat" '{"message":"post takeover chat verification","mode":"chat"}')"
sleep 2
takeover_proxy_log="$(log_proxy_since "$takeover_chat_since")"
takeover_anythingllm_log="$(log_anythingllm_since "$takeover_chat_since")"
{
    printf '# PROOF: r43_1_allm_3 post-takeover chat via proxy\n'
    printf 'workspace_slug: %s\n' "$workspace_slug"
    printf 'response:\n%s\n' "$takeover_chat"
    printf 'proxy_log:\n%s\n' "$takeover_proxy_log"
    printf 'anythingllm_log:\n%s\n' "$takeover_anythingllm_log"
} >"$r43_3"

takeover_chat_status="$(printf '%s\n' "$takeover_chat" | sed -n '1p')"
if [[ "$takeover_chat_status" != "200" ]]; then
    echo "post-takeover chat failed" >&2
    exit 1
fi
ensure_contains "$takeover_proxy_log" "key_id=${KEY_ID} method=POST target_url=https://api.openai.com/v1/chat/completions" "post-takeover chat"
ensure_contains "$takeover_proxy_log" "complete key_id=${KEY_ID} status=200" "post-takeover chat"

{
    printf '# PROOF: r43_1_allm_4 existing workspace continuity\n'
    printf 'workspace_slug: %s\n' "$workspace_slug"
    printf 'response:\n%s\n' "$takeover_chat"
    printf 'anythingllm_log:\n%s\n' "$takeover_anythingllm_log"
    printf 'proxy_log:\n%s\n' "$takeover_proxy_log"
} >"$r43_4"

ensure_contains "$takeover_anythingllm_log" 'fillSourceWindow' "existing workspace continuity"
ensure_contains "$takeover_anythingllm_log" 'Citations backfilled' "existing workspace continuity"

write_embedding_env
embed_recreate_output="$(force_recreate_anythingllm 2>&1)"
sleep 15
embed_env="$(docker exec anythingllm env | sort | grep -E 'EMBEDDING_|GENERIC_OPEN_AI(_EMBEDDING)?_API_KEY|GENERIC_OPEN_AI_BASE_PATH|LLM_PROVIDER' || true)"

embed_doc_name="r43-1-embed-$(date +%s)"
embed_doc_text="Subumbra takeover fresh embedding proof ${embed_doc_name}"
embed_since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sleep 1
upload_response="$(anythingllm_request POST "/api/v1/document/raw-text" "{\"textContent\":\"${embed_doc_text}\",\"metadata\":{\"title\":\"${embed_doc_name}\"}}")"
upload_status="$(printf '%s\n' "$upload_response" | sed -n '1p')"
if [[ "$upload_status" != "200" ]]; then
    {
        printf '# PROOF: r43_1_allm_5 fresh mediated embedding proof\n'
        printf 'recreate_output:\n%s\n' "$embed_recreate_output"
        printf 'container_env:\n%s\n' "$embed_env"
        printf 'upload_response:\n%s\n' "$upload_response"
    } >"$r43_5"
    echo "fresh document upload failed" >&2
    exit 1
fi

location="$(
    printf '%s\n' "$upload_response" | sed -n '2,$p' | python3 -c 'import json,sys; print(json.load(sys.stdin)["documents"][0]["location"])'
)"
update_payload="$(python3 - "$location" <<'PY'
import json
import sys
print(json.dumps({"adds":[sys.argv[1]],"deletes":[]}))
PY
)"
update_response="$(anythingllm_request POST "/api/v1/workspace/${workspace_slug}/update-embeddings" "$update_payload")"
sleep 8
embed_proxy_log="$(log_proxy_since "$embed_since")"
embed_anythingllm_log="$(log_anythingllm_since "$embed_since")"
{
    printf '# PROOF: r43_1_allm_5 fresh mediated embedding proof\n'
    printf 'recreate_output:\n%s\n' "$embed_recreate_output"
    printf 'container_env:\n%s\n' "$embed_env"
    printf 'upload_response:\n%s\n' "$upload_response"
    printf 'update_response:\n%s\n' "$update_response"
    printf 'proxy_log:\n%s\n' "$embed_proxy_log"
    printf 'anythingllm_log:\n%s\n' "$embed_anythingllm_log"
} >"$r43_5"

ensure_contains "$embed_env" 'EMBEDDING_ENGINE=generic-openai' "embedding env"
ensure_contains "$embed_env" 'EMBEDDING_BASE_PATH=http://subumbra-proxy:8090/t/v1' "embedding env"
ensure_contains "$embed_env" "GENERIC_OPEN_AI_EMBEDDING_API_KEY=${KEY_ID}" "embedding env"
ensure_contains "$embed_anythingllm_log" '[GenericOpenAiEmbedder] Initialized' "mediated embedding"
ensure_contains "$embed_proxy_log" "key_id=${KEY_ID} method=POST target_url=https://api.openai.com/v1/embeddings" "mediated embedding"
ensure_contains "$embed_proxy_log" "complete key_id=${KEY_ID} status=200" "mediated embedding"

post_ingest_since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sleep 1
post_ingest_chat="$(anythingllm_request POST "/api/v1/workspace/${workspace_slug}/chat" '{"message":"post embedding rag verification","mode":"chat"}')"
sleep 2
post_ingest_proxy_log="$(log_proxy_since "$post_ingest_since")"
post_ingest_anythingllm_log="$(log_anythingllm_since "$post_ingest_since")"
{
    printf '# PROOF: r43_1_allm_6 post-ingest chat/rag check\n'
    printf 'response:\n%s\n' "$post_ingest_chat"
    printf 'proxy_log:\n%s\n' "$post_ingest_proxy_log"
    printf 'anythingllm_log:\n%s\n' "$post_ingest_anythingllm_log"
} >"$r43_6"

post_ingest_status="$(printf '%s\n' "$post_ingest_chat" | sed -n '1p')"
if [[ "$post_ingest_status" != "200" ]]; then
    echo "post-ingest chat/rag check failed" >&2
    exit 1
fi
ensure_contains "$post_ingest_proxy_log" "key_id=${KEY_ID} method=POST target_url=https://api.openai.com/v1/chat/completions" "post-ingest chat"
ensure_contains "$post_ingest_anythingllm_log" 'fillSourceWindow' "post-ingest rag"

fail_response="$(curl -sS -w '\nHTTP_STATUS:%{http_code}\n' \
    -H 'Authorization: Bearer definitely_not_allowed' \
    -H 'Content-Type: application/json' \
    -X POST \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"test\"}]}" \
    "${PROXY_BASE}/t/v1/chat/completions")"
{
    printf '# PROOF: r43_1_allm_7 fail-closed negative\n'
    printf '%s\n' "$fail_response"
} >"$r43_7"

fail_status="$(printf '%s\n' "$fail_response" | sed -n 's/^HTTP_STATUS://p')"
if [[ "$fail_status" == "200" || -z "$fail_status" ]]; then
    echo "fail-closed negative unexpectedly returned 200" >&2
    exit 1
fi

SUCCESS_MARKER=1
