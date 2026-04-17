#!/bin/bash
set -euo pipefail

# Verify .env contains required variables
required_vars=(
  "SUBUMBRA_ADAPTER_REGISTRY"
  "SUBUMBRA_TOKEN_LITELLM"
  "SUBUMBRA_TOKEN_PROXY"
  "SUBUMBRA_TOKEN_UI"
  "SUBUMBRA_TOKEN_PROBE"
  "SUBUMBRA_HMAC_KEY"
  "CF_WORKER_URL"
  "LITELLM_MASTER_KEY"
)

for var in "${required_vars[@]}"; do
  if ! grep -q "^${var}=" .env; then
    echo "ERROR: Missing $var in .env"
    exit 1
  fi
done

echo "All required variables present in .env"

# Start containers
docker compose up -d --force-recreate

# Verify containers become healthy
timeout=60
interval=5
elapsed=0

while [[ $elapsed -lt $timeout ]]; do
  if docker compose ps | grep -q "unhealthy"; then
    echo "Containers still unhealthy, waiting..."
    sleep $interval
    elapsed=$((elapsed + interval))
  else
    echo "All containers healthy!"
    exit 0
  fi
done

echo "ERROR: Containers did not become healthy within $timeout seconds"
docker compose logs
exit 1