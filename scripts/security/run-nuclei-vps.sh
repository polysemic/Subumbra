#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${1:-web-lite}"
STAGE_DIR="${STAGE_DIR:-$HOME/subumbra-staging}"
NUCLEI_DIR="${NUCLEI_DIR:-$HOME/nuclei-subumbra}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_NAME="${RUN_NAME:-subumbra-nuclei-${PROFILE}-${TIMESTAMP}}"
OUTPUT_DIR="${OUTPUT_DIR:-$NUCLEI_DIR/reports/$RUN_NAME}"
TARGET_URL="${TARGET_URL:-}"
NUCLEI_IMAGE="${NUCLEI_IMAGE:-projectdiscovery/nuclei:latest}"
NUCLEI_TAGS="${NUCLEI_TAGS:-exposures,misconfig,headers}"
NUCLEI_SEVERITY="${NUCLEI_SEVERITY:-info,low,medium,high,critical}"
NUCLEI_RATE_LIMIT="${NUCLEI_RATE_LIMIT:-5}"

if [[ "$PROFILE" != "web-lite" ]]; then
  echo "ERROR: supported profile: web-lite" >&2
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

echo "Running Nuclei web-lite scan"
echo "Target URL: $TARGET_URL"
echo "Output dir: $OUTPUT_DIR"

NUCLEI_EXIT=0
docker run --rm \
  -v "$OUTPUT_DIR:/out:rw" \
  "$NUCLEI_IMAGE" \
  -u "$TARGET_URL" \
  -tags "$NUCLEI_TAGS" \
  -severity "$NUCLEI_SEVERITY" \
  -rl "$NUCLEI_RATE_LIMIT" \
  -jsonl \
  -o /out/nuclei.jsonl || NUCLEI_EXIT=$?

python3 "$SCRIPT_DIR/nuclei/render-nuclei-report.py" \
  "$OUTPUT_DIR/nuclei.jsonl" \
  "$OUTPUT_DIR/nuclei-report.md" \
  "$TARGET_URL"

printf 'exit_code=%s\n' "$NUCLEI_EXIT" > "$OUTPUT_DIR/scan-meta.txt"
printf 'target_url=%s\n' "$TARGET_URL" >> "$OUTPUT_DIR/scan-meta.txt"
printf 'profile=%s\n' "$PROFILE" >> "$OUTPUT_DIR/scan-meta.txt"
printf 'tags=%s\n' "$NUCLEI_TAGS" >> "$OUTPUT_DIR/scan-meta.txt"

echo
echo "Finished. Reports are under:"
echo "  $OUTPUT_DIR"
echo "Publish sanitized markdown with:"
echo "  scripts/security/publish-report-file.sh $OUTPUT_DIR/nuclei-report.md"
exit 0
