#!/usr/bin/env bash
# Scan filesystem, Docker images, and dependencies for CVEs and misconfigurations.
# Requires: trivy  →  https://aquasecurity.github.io/trivy/latest/getting-started/installation/
#
# Usage:
#   ./scripts/security/trivy/scan.sh           # filesystem + deps scan (no Docker)
#   ./scripts/security/trivy/scan.sh --images  # also scan built Docker images
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
REPORT_DIR="$SCRIPT_DIR/reports"
SCAN_IMAGES=false

[[ "${1:-}" == "--images" ]] && SCAN_IMAGES=true

if ! command -v trivy &>/dev/null; then
  echo "ERROR: trivy not found." >&2
  echo "Install: https://aquasecurity.github.io/trivy/latest/getting-started/installation/" >&2
  exit 1
fi

mkdir -p "$REPORT_DIR"
OVERALL_EXIT=0

# ── Filesystem scan (deps, secrets, misconfigs) ───────────────────────────────
# Path-scoped suppressions live in /.trivyignore.yaml at the repo root.
IGNOREFILE="$REPO_ROOT/.trivyignore.yaml"
IGNORE_ARGS=()
[[ -f "$IGNOREFILE" ]] && IGNORE_ARGS=(--ignorefile "$IGNOREFILE")

echo "Running trivy filesystem scan..."
trivy fs "$REPO_ROOT" \
  --scanners vuln,secret,misconfig \
  "${IGNORE_ARGS[@]}" \
  --format json \
  --output "$REPORT_DIR/trivy-fs-report.json" \
  --exit-code 1 || OVERALL_EXIT=1

trivy fs "$REPO_ROOT" \
  --scanners vuln,secret,misconfig \
  "${IGNORE_ARGS[@]}" \
  --format table || true

# ── Docker image scans (optional) ─────────────────────────────────────────────
if [[ "$SCAN_IMAGES" == "true" ]]; then
  IMAGES=(
    "subumbra-keys:latest"
    "subumbra-proxy:latest"
    "subumbra-ui:latest"
  )

  for IMAGE in "${IMAGES[@]}"; do
    SAFE_NAME="${IMAGE//:/-}"
    REPORT_FILE="$REPORT_DIR/trivy-image-${SAFE_NAME}.json"
    echo ""
    echo "Scanning image $IMAGE ..."
    if docker image inspect "$IMAGE" &>/dev/null; then
      trivy image "$IMAGE" \
        --format json \
        --output "$REPORT_FILE" \
        --exit-code 1 || OVERALL_EXIT=1
    else
      echo "  SKIP — image $IMAGE not built locally (run docker compose build first)"
    fi
  done
fi

echo ""
echo "Reports written to $REPORT_DIR"
exit $OVERALL_EXIT
