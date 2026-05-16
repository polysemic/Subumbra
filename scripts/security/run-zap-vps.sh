#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${1:-baseline}"
STAGE_DIR="${STAGE_DIR:-$HOME/subumbra-staging}"
ZAP_DIR="${ZAP_DIR:-$HOME/zap-subumbra}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_NAME="${RUN_NAME:-subumbra-zap-${PROFILE}-${TIMESTAMP}}"
OUTPUT_DIR="${OUTPUT_DIR:-$ZAP_DIR/reports/$RUN_NAME}"
TARGET_URL="${TARGET_URL:-}"
SPIDER_MINS="${SPIDER_MINS:-2}"
MAX_MINS="${MAX_MINS:-10}"
ZAP_IMAGE="${ZAP_IMAGE:-ghcr.io/zaproxy/zaproxy:stable}"
CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/zap/baseline-subumbra.conf}"

if [[ "$PROFILE" != "baseline" ]]; then
  echo "ERROR: supported profile: baseline" >&2
  exit 1
fi

if [[ -z "$TARGET_URL" && -f "$STAGE_DIR/.env" ]]; then
  TARGET_URL="$(sed -n 's/^CF_WORKER_URL=//p' "$STAGE_DIR/.env" | head -n1)"
fi

if [[ -z "$TARGET_URL" ]]; then
  echo "ERROR: TARGET_URL not set and no stage CF_WORKER_URL found." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
mkdir -p "$ZAP_DIR/config"

echo "Running ZAP baseline scan"
echo "Target URL: $TARGET_URL"
echo "Output dir: $OUTPUT_DIR"

ZAP_EXIT=0
docker run --rm \
  -v "$OUTPUT_DIR:/zap/wrk:rw" \
  -v "$CONFIG_FILE:/zap/config/baseline.conf:ro" \
  "$ZAP_IMAGE" \
  zap-baseline.py \
  -t "$TARGET_URL" \
  -m "$SPIDER_MINS" \
  -T "$MAX_MINS" \
  -c /zap/config/baseline.conf \
  -J zap-report.json \
  -w zap-report.md \
  -r zap-report.html \
  -I 2>&1 | tee "$OUTPUT_DIR/zap-run.log" || ZAP_EXIT=$?

printf 'exit_code=%s\n' "$ZAP_EXIT" > "$OUTPUT_DIR/scan-meta.txt"
printf 'target_url=%s\n' "$TARGET_URL" >> "$OUTPUT_DIR/scan-meta.txt"
printf 'profile=%s\n' "$PROFILE" >> "$OUTPUT_DIR/scan-meta.txt"

echo
echo "Finished. Reports are under:"
echo "  $OUTPUT_DIR"
echo "Diagnostic log:"
echo "  $OUTPUT_DIR/zap-run.log"
echo "Publish sanitized markdown with:"
echo "  scripts/security/publish-report-file.sh $OUTPUT_DIR/zap-report.md"
exit 0
