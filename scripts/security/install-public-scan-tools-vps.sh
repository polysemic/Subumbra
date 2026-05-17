#!/usr/bin/env bash
set -euo pipefail

MODE="install"
if [[ "${1:-}" == "--check" ]]; then
  MODE="check"
elif [[ "${1:-}" == "--install" || -z "${1:-}" ]]; then
  MODE="install"
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--install|--check]" >&2
  exit 1
fi

export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

BIN_DIR="${HOME}/bin"
TOOLS_DIR="${HOME}/security-tools"
STATE_DIR="${TOOLS_DIR}/state"
VENV_DIR="${TOOLS_DIR}/scan-venv"
VENV_PYTHON="${VENV_DIR}/bin/python3"

GITLEAKS_IMAGE="${GITLEAKS_IMAGE:-zricethezav/gitleaks:latest}"
TRIVY_IMAGE="${TRIVY_IMAGE:-aquasec/trivy:latest}"
SEMGREP_IMAGE="${SEMGREP_IMAGE:-semgrep/semgrep:latest}"
NUCLEI_IMAGE="${NUCLEI_IMAGE:-projectdiscovery/nuclei:latest}"
ZAP_IMAGE="${ZAP_IMAGE:-ghcr.io/zaproxy/zaproxy:stable}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

check_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    printf 'PASS  %s -> %s\n' "$1" "$(command -v "$1")"
    return 0
  fi
  printf 'FAIL  %s missing\n' "$1" >&2
  return 1
}

write_wrapper() {
  local path="$1"
  local image="$2"
  local tool_name="$3"

  cat > "$path" <<EOF
#!/usr/bin/env bash
set -euo pipefail
IMAGE="\${${tool_name}_IMAGE:-$image}"
HOST_PWD="\${PWD:-$HOME}"
ARGS=()
for arg in "\$@"; do
  if [[ "\$arg" == /* ]]; then
    ARGS+=("/host\$arg")
  else
    ARGS+=("\$arg")
  fi
done
mkdir -p "$STATE_DIR/trivy-cache" "$STATE_DIR/${tool_name,,}"
exec docker run --rm \\
  -v /:/host \\
  -v "$STATE_DIR/trivy-cache:/root/.cache/trivy" \\
  -w "/host\${HOST_PWD}" \\
  "\$IMAGE" "\${ARGS[@]}"
EOF
  chmod +x "$path"
}

install_python_tools() {
  if [[ ! -x "$VENV_PYTHON" ]]; then
    python3 -m venv "$VENV_DIR"
  fi
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install --upgrade bandit pip-audit
}

pull_images() {
  docker pull "$GITLEAKS_IMAGE"
  docker pull "$TRIVY_IMAGE"
  docker pull "$SEMGREP_IMAGE"
  docker pull "$NUCLEI_IMAGE"
  docker pull "$ZAP_IMAGE"
}

check_python_module() {
  local module="$1"
  local label="$2"
  local python_bin="${VENV_PYTHON}"
  if [[ ! -x "$python_bin" ]]; then
    python_bin="python3"
  fi
  if "$python_bin" -m "$module" --version >/dev/null 2>&1; then
    printf 'PASS  %s available via %s -m %s\n' "$label" "$python_bin" "$module"
    return 0
  fi
  printf 'FAIL  %s missing\n' "$label" >&2
  return 1
}

check_docker_image() {
  local image="$1"
  local label="$2"
  if docker image inspect "$image" >/dev/null 2>&1; then
    printf 'PASS  %s image present: %s\n' "$label" "$image"
    return 0
  fi
  printf 'WARN  %s image not present locally yet: %s\n' "$label" "$image" >&2
  return 1
}

require_cmd python3
require_cmd docker

mkdir -p "$BIN_DIR" "$STATE_DIR"

if [[ "$MODE" == "install" ]]; then
  echo "Installing public scan toolchain into user space"
  echo "  bin dir:   $BIN_DIR"
  echo "  state dir: $STATE_DIR"
  echo "  venv dir:  $VENV_DIR"
  echo

  install_python_tools

  write_wrapper "$BIN_DIR/gitleaks" "$GITLEAKS_IMAGE" "GITLEAKS"
  write_wrapper "$BIN_DIR/trivy" "$TRIVY_IMAGE" "TRIVY"

  pull_images
fi

FAILURES=0
echo
echo "Checking public scan toolchain"
check_cmd gitleaks || FAILURES=1
check_cmd trivy || FAILURES=1
check_python_module bandit "bandit" || FAILURES=1
check_python_module pip_audit "pip-audit" || FAILURES=1
check_docker_image "$SEMGREP_IMAGE" "semgrep" || true
check_docker_image "$NUCLEI_IMAGE" "nuclei" || true
check_docker_image "$ZAP_IMAGE" "zap" || true

echo
echo "PATH reminder:"
echo "  export PATH=\"$HOME/bin:\$PATH\""
echo "Venv reminder:"
echo "  source \"$VENV_DIR/bin/activate\"   # optional for manual scanner use"

if [[ "$FAILURES" -ne 0 ]]; then
  echo
  echo "One or more required tools are still unavailable." >&2
  exit 1
fi

echo
echo "Public scan toolchain is ready."
