#!/usr/bin/env bash
set -euo pipefail

artifact_dir="${VERIFY_ARTIFACT_DIR:?VERIFY_ARTIFACT_DIR is required}"

network_artifact="${artifact_dir}/r41-1-subumbra-net-membership.txt"
litellm_artifact="${artifact_dir}/r41-2-bundled-litellm-absent.txt"
proxy_artifact="${artifact_dir}/r41-3-transparent-proxy-direct.txt"

# ── r41-1: subumbra-net membership ──────────────────────────────────────────

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

# ── r41-2: bundled LiteLLM absent ───────────────────────────────────────────

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

# ── r41-3: transparent proxy — with retry for CF Secrets propagation lag ────
#
# Retry rationale: a fresh bootstrap pushes SUBUMBRA_ADAPTER_TOKENS to CF
# Secrets; new tokens may not propagate to all Worker isolates immediately.
# worker/src/worker.js:439-444 returns 401 when the token is structurally
# valid but not yet in the live secret — this is the propagation case, not
# misconfiguration (which returns 503 at line 435).
#
# Success condition: proof is stable and reproducible within 5 attempts.
# The artifact retains ALL attempt results so a verifier can distinguish
# genuine flakiness from a real auth failure.

: >"$proxy_artifact"
printf '# PROOF: round 41.6 direct transparent proxy request\n' >>"$proxy_artifact"
printf 'command: curl --compressed -sS -D - -o - -X POST http://127.0.0.1:8090/t/v1/chat/completions -H '\''Authorization: Bearer openai_prod'\'' -H '\''Content-Type: application/json'\'' -d '\''{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"Say test only."}],"max_tokens":5}'\''\n' >>"$proxy_artifact"

proxy_exit=0
proxy_status=""
for attempt in 1 2 3 4 5; do
    proxy_body="$(mktemp)"
    proxy_headers="$(mktemp)"
    proxy_exit=0
    curl --compressed -sS -D "$proxy_headers" -o "$proxy_body" \
        -X POST http://127.0.0.1:8090/t/v1/chat/completions \
        -H 'Authorization: Bearer openai_prod' \
        -H 'Content-Type: application/json' \
        -d '{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"Say test only."}],"max_tokens":5}' \
        >/dev/null 2>&1 || proxy_exit=$?
    proxy_status="$(awk 'toupper($1) ~ /^HTTP\// {code=$2} END {print code}' "$proxy_headers")"
    printf 'attempt: %d  exit_code: %s  http_status: %s\n' \
        "$attempt" "$proxy_exit" "${proxy_status:-none}" >>"$proxy_artifact"
    if [[ "$proxy_exit" -eq 0 && "${proxy_status:-}" == "200" ]]; then
        printf 'response_body_excerpt:\n' >>"$proxy_artifact"
        sed -n '1,80p' "$proxy_body" | sed 's/^/  /' >>"$proxy_artifact"
        rm -f "$proxy_body" "$proxy_headers"
        break
    fi
    rm -f "$proxy_body" "$proxy_headers"
    [[ "$attempt" -lt 5 ]] && sleep 15
done

if [[ "$proxy_exit" -ne 0 || "${proxy_status:-}" != "200" ]]; then
    echo "direct transparent proxy round 41.6 proof failed after 5 attempts" >&2
    exit 1
fi
