#!/usr/bin/env bash
# Static security analysis of all Python source files.
# Requires: bandit  →  pip install bandit
#
# Usage: ./scripts/security/bandit/scan.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
REPORT_DIR="$SCRIPT_DIR/reports"
REPORT_JSON="$REPORT_DIR/bandit-report.json"
REPORT_HTML="$REPORT_DIR/bandit-report.html"
SCAN_VENV_PYTHON="${HOME}/security-tools/scan-venv/bin/python3"
PYTHON_BIN="${SECURITY_PYTHON:-python3}"

if ! "$PYTHON_BIN" -m bandit --version &>/dev/null; then
  if [[ -x "$SCAN_VENV_PYTHON" ]] && "$SCAN_VENV_PYTHON" -m bandit --version &>/dev/null; then
    PYTHON_BIN="$SCAN_VENV_PYTHON"
  else
    echo "ERROR: bandit not found. Run: scripts/security/install-public-scan-tools-vps.sh" >&2
    exit 1
  fi
fi

mkdir -p "$REPORT_DIR"

# Python components to scan (bootstrap is a one-shot admin tool — included but
# subprocess/URL-open findings there are expected and documented)
TARGETS=(
  "$REPO_ROOT/subumbra-keys/app.py"
  "$REPO_ROOT/subumbra-proxy/app.py"
  "$REPO_ROOT/ui/app.py"
  "$REPO_ROOT/subumbra-probe/probe.py"
  "$REPO_ROOT/bootstrap/subumbra-bootstrap.py"
)

echo "Running bandit on Python source files..."

"$PYTHON_BIN" -m bandit \
  "${TARGETS[@]}" \
  --format json \
  --output "$REPORT_JSON" \
  --exit-zero  # don't fail CI on LOW findings; review manually

"$PYTHON_BIN" -m bandit \
  "${TARGETS[@]}" \
  --format html \
  --output "$REPORT_HTML" \
  --exit-zero

# Print console summary — fail if any HIGH severity findings exist
"$PYTHON_BIN" -c "
import json, sys
with open('$REPORT_JSON') as f:
    data = json.load(f)
results = data.get('results', [])
high   = [r for r in results if r['issue_severity'] == 'HIGH']
medium = [r for r in results if r['issue_severity'] == 'MEDIUM']
low    = [r for r in results if r['issue_severity'] == 'LOW']
print(f'Bandit results — HIGH: {len(high)}  MEDIUM: {len(medium)}  LOW: {len(low)}')
if high:
    print()
    print('HIGH severity findings (require immediate review):')
    for r in high:
        print(f'  [{r[\"test_id\"]}] {r[\"issue_text\"]}')
        print(f'  {r[\"filename\"]}:{r[\"line_number\"]}')
    sys.exit(1)
else:
    print('PASS — no HIGH severity findings')
"

echo ""
echo "Reports written to $REPORT_DIR"
