#!/usr/bin/env bash
set -euo pipefail

artifact_dir="${VERIFY_ARTIFACT_DIR:?VERIFY_ARTIFACT_DIR is required}"

bundled_artifact="${artifact_dir}/r42-3-1-bundled-litellm-absent.txt"
bootstrap_artifact="${artifact_dir}/r42-3-2-bootstrap-no-litellm-token.txt"
proxy_artifact="${artifact_dir}/r42-3-3-proxy-health-worker-auth.txt"
ui_artifact="${artifact_dir}/r42-3-4-ui-status-proxy-health.txt"
standalone_artifact="${artifact_dir}/r42-3-5-standalone-litellm.txt"
docs_artifact="${artifact_dir}/r42-3-6-doc-truth.txt"

search_pattern() {
    local pattern="$1"
    shift
    if command -v rg >/dev/null 2>&1; then
        rg -n "$pattern" "$@"
    else
        grep -nE "$pattern" "$@"
    fi
}

services_output="$(docker compose config --services 2>&1)" || {
    printf '# PROOF: bundled LiteLLM removed from core stack\n%s\n' "$services_output" >"$bundled_artifact"
    echo "docker compose config --services failed" >&2
    exit 1
}

{
    printf '# PROOF: bundled LiteLLM removed from core stack\n'
    printf '%s\n' "$services_output"
} >"$bundled_artifact"

if printf '%s\n' "$services_output" | grep -Eq '(^|[[:space:]])litellm([[:space:]]|$)'; then
    echo "bundled litellm is still present in docker compose services" >&2
    exit 1
fi

bootstrap_matches="$(search_pattern 'SUBUMBRA_TOKEN_LITELLM|LITELLM_ALLOWED_KEYS' bootstrap/subumbra-bootstrap.py post-bootstrap.sh 2>&1 || true)"
{
    printf '# PROOF: bootstrap/post-bootstrap no longer require bundled LiteLLM token sync\n'
    if [[ -n "$bootstrap_matches" ]]; then
        printf '%s\n' "$bootstrap_matches"
    else
        printf 'No bundled LiteLLM token references found in bootstrap/subumbra-bootstrap.py or post-bootstrap.sh\n'
    fi
} >"$bootstrap_artifact"

if [[ -n "$bootstrap_matches" ]]; then
    echo "found bundled LiteLLM token references in bootstrap/post-bootstrap paths" >&2
    exit 1
fi

proxy_body="$(mktemp)"
proxy_headers="$(mktemp)"
proxy_exit=0
curl --compressed -sS -D "$proxy_headers" -o "$proxy_body" \
    http://127.0.0.1:8090/health >/dev/null 2>&1 || proxy_exit=$?
proxy_status="$(awk 'toupper($1) ~ /^HTTP\// {code=$2} END {print code}' "$proxy_headers")"

{
    printf '# PROOF: proxy health exposes Worker-auth state\n'
    printf 'command: curl --compressed -sS -D - -o - http://127.0.0.1:8090/health\n'
    printf 'exit_code: %s\n' "$proxy_exit"
    printf 'http_status: %s\n' "${proxy_status:-none}"
    printf 'response_headers:\n'
    sed 's/^/  /' "$proxy_headers"
    printf 'response_body:\n'
    sed -n '1,80p' "$proxy_body" | sed 's/^/  /'
} >"$proxy_artifact"

if [[ "$proxy_exit" -ne 0 || "${proxy_status:-}" != "200" ]]; then
    rm -f "$proxy_body" "$proxy_headers"
    echo "proxy health check failed" >&2
    exit 1
fi

python3 - "$proxy_body" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    data = json.load(fh)

if data.get("worker_auth") != "ok":
    raise SystemExit("proxy health worker_auth is not ok")
PY

rm -f "$proxy_body" "$proxy_headers"

ui_body="$(mktemp)"
ui_headers="$(mktemp)"
ui_exit=0
curl --compressed -sS -D "$ui_headers" -o "$ui_body" \
    http://127.0.0.1:8080/api/status >/dev/null 2>&1 || ui_exit=$?
ui_status="$(awk 'toupper($1) ~ /^HTTP\// {code=$2} END {print code}' "$ui_headers")"

{
    printf '# PROOF: UI status reads proxy-owned Worker-auth signal\n'
    printf 'command: curl --compressed -sS -D - -o - http://127.0.0.1:8080/api/status\n'
    printf 'exit_code: %s\n' "$ui_exit"
    printf 'http_status: %s\n' "${ui_status:-none}"
    printf 'response_headers:\n'
    sed 's/^/  /' "$ui_headers"
    printf 'response_body:\n'
    sed -n '1,120p' "$ui_body" | sed 's/^/  /'
} >"$ui_artifact"

if [[ "$ui_exit" -ne 0 || "${ui_status:-}" != "200" ]]; then
    rm -f "$ui_body" "$ui_headers"
    echo "ui status check failed" >&2
    exit 1
fi

python3 - "$ui_body" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    data = json.load(fh)

if data.get("worker_auth") != "ok":
    raise SystemExit("ui status worker_auth is not ok")
if data.get("worker_reachable") is not True:
    raise SystemExit("ui status worker_reachable is not true")
PY

rm -f "$ui_body" "$ui_headers"

if [[ ! -d /opt/litellm ]]; then
    printf '# PROOF: standalone LiteLLM app-owned path\n/opt/litellm not found on this verifier host\n' >"$standalone_artifact"
    echo "/opt/litellm is required for round 42.3 standalone proof" >&2
    exit 1
fi

standalone_env="/opt/litellm/.env"
standalone_config="/opt/litellm/config.yaml"
if [[ ! -f "$standalone_env" || ! -f "$standalone_config" ]]; then
    {
        printf '# PROOF: standalone LiteLLM app-owned path\n'
        printf 'Missing required files:\n'
        printf '  env_present=%s\n' "$(test -f "$standalone_env" && echo yes || echo no)"
        printf '  config_present=%s\n' "$(test -f "$standalone_config" && echo yes || echo no)"
    } >"$standalone_artifact"
    echo "standalone LiteLLM env/config missing" >&2
    exit 1
fi

standalone_key="$(grep '^LITELLM_MASTER_KEY=' "$standalone_env" | cut -d= -f2- || true)"
standalone_api_base_matches="$(search_pattern 'api_base:[[:space:]]*http://subumbra-proxy:8090/t$' "$standalone_config" 2>&1 || true)"
standalone_legacy_matches="$(search_pattern 'api_key:[[:space:]]*[\"'\'']?subumbra:' "$standalone_config" 2>&1 || true)"

standalone_body="$(mktemp)"
standalone_headers="$(mktemp)"
standalone_exit=0
curl --compressed -sS -D "$standalone_headers" -o "$standalone_body" \
    -X POST http://127.0.0.1:4000/v1/chat/completions \
    -H "Authorization: Bearer ${standalone_key}" \
    -H 'Content-Type: application/json' \
    -d '{"model":"claude-sonnet-4","messages":[{"role":"user","content":"Say test only."}],"max_tokens":5}' \
    >/dev/null 2>&1 || standalone_exit=$?
standalone_status="$(awk 'toupper($1) ~ /^HTTP\// {code=$2} END {print code}' "$standalone_headers")"

{
    printf '# PROOF: standalone LiteLLM app-owned path\n'
    printf 'config_api_base_matches:\n'
    if [[ -n "$standalone_api_base_matches" ]]; then
        printf '%s\n' "$standalone_api_base_matches"
    else
        printf '  none\n'
    fi
    printf 'config_legacy_auth_matches:\n'
    if [[ -n "$standalone_legacy_matches" ]]; then
        printf '%s\n' "$standalone_legacy_matches"
    else
        printf '  none\n'
    fi
    printf 'command: curl --compressed -sS -D - -o - -X POST http://127.0.0.1:4000/v1/chat/completions -H '\''Authorization: Bearer [redacted]'\'' -H '\''Content-Type: application/json'\'' -d '\''{\"model\":\"claude-sonnet-4\",\"messages\":[{\"role\":\"user\",\"content\":\"Say test only.\"}],\"max_tokens\":5}'\''\n'
    printf 'exit_code: %s\n' "$standalone_exit"
    printf 'http_status: %s\n' "${standalone_status:-none}"
    printf 'response_headers:\n'
    sed 's/^/  /' "$standalone_headers"
    printf 'response_body_excerpt:\n'
    sed -n '1,120p' "$standalone_body" | sed 's/^/  /'
} >"$standalone_artifact"

if [[ -z "$standalone_key" ]]; then
    rm -f "$standalone_body" "$standalone_headers"
    echo "standalone LiteLLM master key is missing" >&2
    exit 1
fi
if [[ -z "$standalone_api_base_matches" ]]; then
    rm -f "$standalone_body" "$standalone_headers"
    echo "standalone LiteLLM config does not use subumbra-proxy /t api_base" >&2
    exit 1
fi
if [[ -n "$standalone_legacy_matches" ]]; then
    rm -f "$standalone_body" "$standalone_headers"
    echo "standalone LiteLLM config still contains legacy subumbra: auth" >&2
    exit 1
fi
if [[ "$standalone_exit" -ne 0 || "${standalone_status:-}" != "200" ]]; then
    rm -f "$standalone_body" "$standalone_headers"
    echo "standalone LiteLLM request failed" >&2
    exit 1
fi

rm -f "$standalone_body" "$standalone_headers"

docs_output="$(python3 - <<'PY'
from pathlib import Path

checks = [
    ("README.md", ["app-owned installs", "subumbra-proxy", "docs/standalone-litellm.md"], []),
    ("docs/subumbra-install.md", ["/opt/litellm", "LiteLLM is no longer part of the core `/opt/subumbra` compose stack.", "api_key: <key_id>"], []),
    ("docs/subumbra-testing.md", ["/opt/litellm", "worker_auth", "Standalone LiteLLM lives outside `/opt/subumbra`."], []),
    ("docs/adapter-contract.md", ["shared `subumbra-proxy` identity", "App-Owned Integrations"], ["Adapter #1"]),
    ("docs/standalone-litellm.md", ["http://subumbra-proxy:8090/t", "shared `subumbra-proxy` identity", "do **not** use `subumbra:<key_id>`"], []),
]

failures = []
lines = []
for rel, required, forbidden in checks:
    text = Path(rel).read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    lines.append(f"[{rel}]")
    for needle in required:
        ok = " ".join(needle.split()) in normalized
        lines.append(f"required {needle!r}: {'yes' if ok else 'no'}")
        if not ok:
            failures.append(f"{rel}: missing required text {needle!r}")
    for needle in forbidden:
        present = " ".join(needle.split()) in normalized
        lines.append(f"forbidden {needle!r}: {'present' if present else 'absent'}")
        if present:
            failures.append(f"{rel}: forbidden text {needle!r} present")
print("\n".join(lines))
if failures:
    raise SystemExit("\n".join(failures))
PY
)" || {
    {
        printf '# PROOF: docs aligned to app-owned integrations model\n'
        printf '%s\n' "$docs_output"
    } >"$docs_artifact"
    echo "doc truth checks failed" >&2
    exit 1
}

{
    printf '# PROOF: docs aligned to app-owned integrations model\n'
    printf '%s\n' "$docs_output"
} >"$docs_artifact"
