#!/usr/bin/env bash
set -euo pipefail

PROFILE="${1:-auth}"
STAGE_DIR="${STAGE_DIR:-$HOME/subumbra-staging}"
REPO_DIR="${REPO_DIR:-/opt/subumbra}"
SHANNON_DIR="${SHANNON_DIR:-$HOME/shannon-subumbra}"
STAGE_PROXY_PORT="${STAGE_PROXY_PORT:-10299}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORKSPACE_NAME="${WORKSPACE_NAME:-subumbra-${PROFILE}-${TIMESTAMP}}"
OUTPUT_DIR="${OUTPUT_DIR:-$SHANNON_DIR/reports/$WORKSPACE_NAME}"
CONFIG_FILE="$SHANNON_DIR/configs/${PROFILE}.yaml"
TARGET_URL="${TARGET_URL:-}"
SHANNON_WORKSPACE_ROOT="${SHANNON_WORKSPACE_ROOT:-$HOME/.shannon/workspaces}"
REAL_WORKSPACE_DIR="$SHANNON_WORKSPACE_ROOT/$WORKSPACE_NAME"

case "$PROFILE" in
  auth|authz|ssrf|\
  auth-proxy-lite|auth-worker-lite|authz-worker-lite|\
  ssrf-worker-lite|ssrf-proxy-lite|\
  keys-auth-lite|response-injection-lite|ui-auth-lite) ;;
  *)
    echo "ERROR: profile must be one of:" >&2
    echo "  Full:  auth, authz, ssrf" >&2
    echo "  Lite:  auth-proxy-lite, auth-worker-lite, authz-worker-lite" >&2
    echo "         ssrf-worker-lite, ssrf-proxy-lite" >&2
    echo "         keys-auth-lite, response-injection-lite, ui-auth-lite" >&2
    exit 1
    ;;
esac

if [[ ! -d "$STAGE_DIR" ]]; then
  echo "ERROR: stage directory not found: $STAGE_DIR" >&2
  exit 1
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "ERROR: REPO_DIR is not a git repository: $REPO_DIR" >&2
  exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: config file not found: $CONFIG_FILE" >&2
  exit 1
fi

if [[ -z "$TARGET_URL" && -f "$STAGE_DIR/.env" ]]; then
  TARGET_URL="$(sed -n 's/^CF_WORKER_URL=//p' "$STAGE_DIR/.env" | head -n1)"
fi

if [[ -z "$TARGET_URL" ]]; then
  TARGET_URL="http://host.docker.internal:${STAGE_PROXY_PORT}"
fi

if [[ ! -f "$HOME/.shannon/config.toml" ]] && [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]] && [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: Shannon credentials not configured." >&2
  echo "Run: npx @keygraph/shannon setup" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

curl -fsS "http://127.0.0.1:${STAGE_PROXY_PORT}/health" >/dev/null

echo "Running Shannon profile '$PROFILE'"
echo "Target URL: $TARGET_URL"
echo "Repo path:   $REPO_DIR"
echo "Stage dir:   $STAGE_DIR"
echo "Workspace:   $WORKSPACE_NAME"
echo "Output dir:  $OUTPUT_DIR"

npx @keygraph/shannon start \
  -u "$TARGET_URL" \
  -r "$REPO_DIR" \
  -c "$CONFIG_FILE" \
  -w "$WORKSPACE_NAME" \
  -o "$OUTPUT_DIR"

if [[ -d "$REAL_WORKSPACE_DIR" ]]; then
  rsync -a --delete "$REAL_WORKSPACE_DIR"/ "$OUTPUT_DIR"/
fi

echo
echo "Finished. Deliverables are under:"
echo "  $OUTPUT_DIR"
