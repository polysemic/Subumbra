#!/usr/bin/env bash
# Run all local security scans and print a combined summary.
# Individual tool reports are written to each tool's reports/ subfolder.
#
# Usage: ./scripts/security/scan-all.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PASS=()
FAIL=()
SKIP=()

run_scan() {
  local NAME="$1"
  local SCRIPT="$2"

  echo ""
  echo "════════════════════════════════════════"
  echo " $NAME"
  echo "════════════════════════════════════════"

  if [[ ! -x "$SCRIPT" ]]; then
    echo "SKIP — script not executable: $SCRIPT"
    SKIP+=("$NAME")
    return
  fi

  if bash "$SCRIPT"; then
    PASS+=("$NAME")
  else
    FAIL+=("$NAME")
  fi
}

run_scan "gitleaks  (secret scan)"   "$SCRIPT_DIR/gitleaks/scan.sh"
run_scan "bandit    (Python SAST)"   "$SCRIPT_DIR/bandit/scan.sh"
run_scan "pip-audit (CVE scan)"      "$SCRIPT_DIR/pip-audit/scan.sh"
run_scan "trivy     (fs + deps)"     "$SCRIPT_DIR/trivy/scan.sh"

echo ""
echo "════════════════════════════════════════"
echo " Summary"
echo "════════════════════════════════════════"
for t in "${PASS[@]+"${PASS[@]}"}";  do echo "  PASS  $t"; done
for t in "${FAIL[@]+"${FAIL[@]}"}";  do echo "  FAIL  $t"; done
for t in "${SKIP[@]+"${SKIP[@]}"}";  do echo "  SKIP  $t"; done

[[ ${#FAIL[@]} -eq 0 ]] && echo "" && echo "All scans passed." && exit 0
echo ""
echo "One or more scans failed — review reports above."
exit 1
