#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${1:-baseline}"
STAGE_DIR="${STAGE_DIR:-$HOME/subumbra-staging}"
SEMGREP_DIR="${SEMGREP_DIR:-$HOME/semgrep-subumbra}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_NAME="${RUN_NAME:-subumbra-semgrep-${PROFILE}-${TIMESTAMP}}"
OUTPUT_DIR="${OUTPUT_DIR:-$SEMGREP_DIR/reports/$RUN_NAME}"
SEMGREP_IMAGE="${SEMGREP_IMAGE:-semgrep/semgrep:latest}"

case "$PROFILE" in
  baseline)
    CONFIGS=(--config p/owasp-top-ten --config p/security-audit)
    ;;
  secrets)
    CONFIGS=(--config p/secrets)
    ;;
  python)
    CONFIGS=(--config p/python)
    ;;
  javascript)
    CONFIGS=(--config p/javascript)
    ;;
  *)
    echo "ERROR: supported profiles: baseline, secrets, python, javascript" >&2
    exit 1
    ;;
esac

if [[ ! -d "$STAGE_DIR" ]]; then
  echo "ERROR: stage directory not found: $STAGE_DIR" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "Running Semgrep profile '$PROFILE'"
echo "Target dir:  $STAGE_DIR"
echo "Output dir:  $OUTPUT_DIR"

SEMGREP_EXIT=0
docker run --rm \
  -v "$STAGE_DIR:/src:ro" \
  -v "$OUTPUT_DIR:/out:rw" \
  "$SEMGREP_IMAGE" \
  semgrep scan \
  "${CONFIGS[@]}" \
  --metrics=off \
  --exclude council \
  --exclude data \
  --exclude .git \
  --json \
  --output /out/semgrep.json \
  /src 2>&1 | tee "$OUTPUT_DIR/semgrep-run.log" || SEMGREP_EXIT=$?

python3 "$SCRIPT_DIR/semgrep/render-semgrep-report.py" \
  "$OUTPUT_DIR/semgrep.json" \
  "$OUTPUT_DIR/semgrep-report.md" \
  "$STAGE_DIR"

printf 'exit_code=%s\n' "$SEMGREP_EXIT" > "$OUTPUT_DIR/scan-meta.txt"
printf 'target_dir=%s\n' "$STAGE_DIR" >> "$OUTPUT_DIR/scan-meta.txt"
printf 'profile=%s\n' "$PROFILE" >> "$OUTPUT_DIR/scan-meta.txt"

echo
echo "Finished. Reports are under:"
echo "  $OUTPUT_DIR"
echo "Publish sanitized markdown with:"
echo "  scripts/security/publish-report-file.sh $OUTPUT_DIR/semgrep-report.md"
exit 0
