#!/usr/bin/env bash
set -euo pipefail

artifact_dir="${VERIFY_ARTIFACT_DIR:?VERIFY_ARTIFACT_DIR is required}"

network_artifact="${artifact_dir}/r41-1-subumbra-net-membership.txt"
litellm_artifact="${artifact_dir}/r41-2-bundled-litellm-absent.txt"
proxy_artifact="${artifact_dir}/r41-3-transparent-proxy-direct.txt"

network_output="$(docker network inspect subumbra-net 2>&1)" || {
    printf '# PROOF: round 41 coexistence network check\n%s\n' "$network_output" >"$network_artifact"
    echo "subumbra-net inspect failed" >&2
    exit 1
}

{
    printf '# PROOF: round 41 coexistence network check\n'
    printf '%s\n' "$network_output"
} >"$network_artifact"

if ! grep -q 'subumbra-proxy' "$network_artifact"; then
    echo "subumbra-proxy is not attached to subumbra-net" >&2
    exit 1
fi
if grep -q 'subumbra-keys' "$network_artifact"; then
    echo "subumbra-keys must not be attached to subumbra-net" >&2
    exit 1
fi

bundled_ps="$(docker compose ps 2>&1)" || {
    printf '# PROOF: round 41 bundled LiteLLM absence\n%s\n' "$bundled_ps" >"$litellm_artifact"
    echo "docker compose ps failed" >&2
    exit 1
}

{
    printf '# PROOF: round 41 bundled LiteLLM absence\n'
    printf '%s\n' "$bundled_ps"
} >"$litellm_artifact"

if printf '%s\n' "$bundled_ps" | grep -Eq '(^|[[:space:]])litellm([[:space:]]|$)'; then
    echo "bundled litellm should not be running for round 41 coexistence proof" >&2
    exit 1
fi

proxy_body="$(mktemp)"
proxy_headers="$(mktemp)"
proxy_exit=0
curl --compressed -sS -D "$proxy_headers" -o "$proxy_body" \
    -X POST \
    http://127.0.0.1:8090/t/v1/chat/completions \
    -H 'Authorization: Bearer openai_prod' \
    -H 'Content-Type: application/json' \
    -d '{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"Say test only."}],"max_tokens":5}' \
    >/dev/null 2>&1 || proxy_exit=$?
proxy_status="$(awk 'toupper($1) ~ /^HTTP\// {code=$2} END {print code}' "$proxy_headers")"

{
    printf '# PROOF: round 41 direct transparent proxy request\n'
    printf 'command: curl --compressed -sS -D - -o - -X POST http://127.0.0.1:8090/t/v1/chat/completions -H '\''Authorization: Bearer openai_prod'\'' -H '\''Content-Type: application/json'\'' -d '\''{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"Say test only."}],"max_tokens":5}'\''\n'
    printf 'exit_code: %s\n' "$proxy_exit"
    printf 'http_status: %s\n' "${proxy_status:-none}"
    printf 'response_headers:\n'
    sed 's/^/  /' "$proxy_headers"
    printf 'response_body_excerpt:\n'
    sed -n '1,80p' "$proxy_body" | sed 's/^/  /'
} >"$proxy_artifact"

rm -f "$proxy_body" "$proxy_headers"

if [[ "$proxy_exit" -ne 0 || "${proxy_status:-}" != "200" ]]; then
    echo "direct transparent proxy round 41 proof failed" >&2
    exit 1
fi
