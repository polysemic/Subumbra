#!/usr/bin/env bash
# Scan all Python requirements files for known CVEs.
# Requires: pip-audit  →  pip install pip-audit
#
# Usage: ./scripts/security/pip-audit/scan.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
REPORT_DIR="$SCRIPT_DIR/reports"
SCAN_VENV_PYTHON="${HOME}/security-tools/scan-venv/bin/python3"
PYTHON_BIN="${SECURITY_PYTHON:-python3}"

if ! "$PYTHON_BIN" -m pip_audit --version &>/dev/null; then
  if [[ -x "$SCAN_VENV_PYTHON" ]] && "$SCAN_VENV_PYTHON" -m pip_audit --version &>/dev/null; then
    PYTHON_BIN="$SCAN_VENV_PYTHON"
  else
    echo "ERROR: pip-audit not found. Run: scripts/security/install-public-scan-tools-vps.sh" >&2
    exit 1
  fi
fi

mkdir -p "$REPORT_DIR"

REQUIREMENTS=(
  "bootstrap/requirements.txt"
  "subumbra-keys/requirements.txt"
  "subumbra-proxy/requirements.txt"
  "subumbra-probe/requirements.txt"
  "ui/requirements.txt"
)

OVERALL_EXIT=0

for REQ in "${REQUIREMENTS[@]}"; do
  COMPONENT=$(echo "$REQ" | cut -d/ -f1)
  REPORT_FILE="$REPORT_DIR/pip-audit-${COMPONENT}.json"

  echo "Scanning $REQ ..."

  if "$PYTHON_BIN" -m pip_audit \
      -r "$REPO_ROOT/$REQ" \
      --format json \
      --output "$REPORT_FILE" 2>/dev/null; then
    echo "  PASS — $COMPONENT: no known vulnerabilities"
  else
    echo "  FAIL — $COMPONENT: vulnerabilities found — see $REPORT_FILE"
    OVERALL_EXIT=1
  fi
done

echo ""
if [[ $OVERALL_EXIT -eq 0 ]]; then
  echo "PASS — all components clean"
else
  echo "FAIL — one or more components have known CVEs. Update pinned versions."
fi

echo "Reports written to $REPORT_DIR"
exit $OVERALL_EXIT
