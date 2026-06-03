#!/usr/bin/env bash
set -euo pipefail

LIVE_DIR="${LIVE_DIR:-/opt/subumbra}"
STAGE_DIR="${STAGE_DIR:-/opt/subumbra-staging}"
SHANNON_DIR="${SHANNON_DIR:-/opt/shannon-subumbra}"
STAGE_WORKER_NAME="${STAGE_WORKER_NAME:-subumbra-proxy-stage}"
STAGE_PROXY_PORT="${STAGE_PROXY_PORT:-10299}"
STAGE_UI_PORT="${STAGE_UI_PORT:-6664}"
STAGE_PROJECT_NAME="${STAGE_PROJECT_NAME:-subumbra-stage}"

echo "Preparing staging checkout at $STAGE_DIR"
mkdir -p "$STAGE_DIR"
rsync -a --delete \
  --exclude 'council/' \
  --exclude 'data/' \
  "$LIVE_DIR"/ "$STAGE_DIR"/

cd "$STAGE_DIR"

python3 - "$STAGE_PROXY_PORT" "$STAGE_UI_PORT" docker-compose.yml <<'PY'
import pathlib
import sys

proxy_port = sys.argv[1]
ui_port = sys.argv[2]
path = pathlib.Path(sys.argv[3])
text = path.read_text()
replacements = {
    "container_name: subumbra-keys": "container_name: subumbra-staging-keys",
    "container_name: subumbra-ui": "container_name: subumbra-staging-ui",
    "container_name: subumbra-probe": "container_name: subumbra-staging-probe",
    "container_name: subumbra-proxy": "container_name: subumbra-staging-proxy",
    "container_name: subumbra-bootstrap": "container_name: subumbra-staging-bootstrap",
    "container_name: cloudflared": "container_name: subumbra-staging-cloudflared",
    '- "127.0.0.1:6563:8080"': f'- "127.0.0.1:{ui_port}:8080"',
    '- "127.0.0.1:10199:8090"': f'- "0.0.0.0:{proxy_port}:8090"',
}
for old, new in replacements.items():
    text = text.replace(old, new)
path.write_text(text)
PY

if [[ ! -f .env.bootstrap && -f .env.bootstrap_bak ]]; then
    cp .env.bootstrap_bak .env.bootstrap
fi

if [[ ! -f .env.bootstrap ]]; then
  echo "ERROR: missing $STAGE_DIR/.env.bootstrap and no .env.bootstrap_bak fallback found." >&2
  exit 1
fi

python3 - "$STAGE_WORKER_NAME" .env.bootstrap <<'PY'
import pathlib
import sys

worker_name = sys.argv[1]
path = pathlib.Path(sys.argv[2])
lines = path.read_text().splitlines()
updated = []
found = False
for line in lines:
    if line.startswith("CF_WORKER_NAME="):
        updated.append(f"CF_WORKER_NAME={worker_name}")
        found = True
    else:
        updated.append(line)
if not found:
    updated.append(f"CF_WORKER_NAME={worker_name}")
path.write_text("\n".join(updated) + "\n")
PY

mkdir -p "$SHANNON_DIR/configs" "$SHANNON_DIR/reports"
cp -f scripts/security/shannon/*.yaml "$SHANNON_DIR/configs/"

cat > "$SHANNON_DIR/README.txt" <<EOF
Shannon helper workspace for Subumbra staging.

Full passes (broader scope, higher token cost):
  $SHANNON_DIR/configs/auth.yaml
  $SHANNON_DIR/configs/authz.yaml
  $SHANNON_DIR/configs/ssrf.yaml

Lite passes (narrow scope, lower token cost):
  auth-worker-lite    Worker token verification and auth routes
  auth-proxy-lite     Proxy bearer-token handling
  authz-worker-lite   Worker adapter/key/policy boundaries
  ssrf-worker-lite    Worker target_url validation and upstream routing
  ssrf-proxy-lite     Proxy transparent route target construction
  keys-auth-lite      Keys service consumer token and HMAC validation
  response-injection-lite  Worker response handling and deny_patterns
  ui-auth-lite        UI dashboard token and read-only API access

Run:
  PROFILE=<name> bash $STAGE_DIR/scripts/security/run-shannon-vps.sh <profile>

Reports:
  $SHANNON_DIR/reports/
EOF

export COMPOSE_PROJECT_NAME="$STAGE_PROJECT_NAME"
export SUBUMBRA_STAGE_PROXY_PORT="$STAGE_PROXY_PORT"
export SUBUMBRA_STAGE_UI_PORT="$STAGE_UI_PORT"

echo "Bootstrapping isolated staging Worker and stack"
./bootstrap.sh

echo "Stage Worker URL:"
sed -n 's/^CF_WORKER_URL=//p' .env
echo "Stage proxy health:"
curl -fsS "http://127.0.0.1:${STAGE_PROXY_PORT}/health"
echo
echo "Stage UI health:"
curl -fsS "http://127.0.0.1:${STAGE_UI_PORT}/health"
echo
echo "Shannon configs copied to $SHANNON_DIR/configs"
