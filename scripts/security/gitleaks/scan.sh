#!/usr/bin/env bash
# Scan full git history for accidentally committed secrets.
# Requires: gitleaks (https://github.com/gitleaks/gitleaks)
#   Install: https://github.com/gitleaks/gitleaks#installing
#
# Usage: ./scripts/security/gitleaks/scan.sh [--no-report]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
REPORT_DIR="$SCRIPT_DIR/reports"
REPORT_FILE="$REPORT_DIR/gitleaks-report.json"
CONFIG="$SCRIPT_DIR/.gitleaks.toml"

if ! command -v gitleaks &>/dev/null; then
  echo "ERROR: gitleaks not found. See https://github.com/gitleaks/gitleaks#installing" >&2
  exit 1
fi

mkdir -p "$REPORT_DIR"

echo "Running gitleaks on $REPO_ROOT ..."

ARGS=(detect --source "$REPO_ROOT" --report-format json --report-path "$REPORT_FILE")
[[ -f "$CONFIG" ]] && ARGS+=(--config "$CONFIG")
[[ "${1:-}" == "--no-report" ]] && ARGS=(detect --source "$REPO_ROOT")

if gitleaks "${ARGS[@]}"; then
  echo "PASS — no secrets detected"
else
  EXIT=$?
  echo ""
  echo "FINDINGS detected — review $REPORT_FILE"
  echo "If findings are false positives, add them to .gitleaks.toml allowlist"
  exit $EXIT
fi
