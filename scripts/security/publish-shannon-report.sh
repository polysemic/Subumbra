#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <shannon-workspace-dir> [output-name.md]" >&2
  exit 1
fi

WORKSPACE_DIR="$1"
OUTPUT_NAME="${2:-}"

if [[ ! -d "$WORKSPACE_DIR" ]]; then
  echo "ERROR: workspace directory not found: $WORKSPACE_DIR" >&2
  exit 1
fi

pick_source_report() {
  local dir="$1"
  local candidate

  for candidate in \
    "$dir/deliverables/comprehensive_security_assessment_report.md" \
    "$dir/comprehensive_security_assessment_report.md"
  do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  find "$dir" -maxdepth 3 -type f \( -name '*.md' -o -name '*.markdown' \) | sort | head -n1
}

SOURCE_REPORT="$(pick_source_report "$WORKSPACE_DIR")"
if [[ -z "${SOURCE_REPORT:-}" || ! -f "$SOURCE_REPORT" ]]; then
  echo "ERROR: could not find a markdown report inside $WORKSPACE_DIR" >&2
  exit 1
fi

if [[ -z "$OUTPUT_NAME" ]]; then
  DATE_PREFIX="$(date -u +%Y-%m-%d)"
  WORKSPACE_BASENAME="$(basename "$WORKSPACE_DIR")"
  OUTPUT_NAME="${DATE_PREFIX}-${WORKSPACE_BASENAME}.md"
fi

"$SCRIPT_DIR/publish-report-file.sh" \
  "$SOURCE_REPORT" \
  "$OUTPUT_NAME" \
  "the Shannon workspace $WORKSPACE_DIR"
