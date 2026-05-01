#!/usr/bin/env bash
# fresh-start.sh — Complete Subumbra teardown and CF cleanup
#
# Destroys:
#   - All Subumbra Docker containers and named volumes
#   - Local .env, .env.bootstrap (if present)
#   - Cloudflare Worker (CF_WORKER_NAME)
#   - Cloudflare KV namespace (provider registry)
#   - Legacy Cloudflare secret drift left by older bootstrap runs
#
# Does NOT touch:
#   - App installs (LibreChat, OpenWebUI, AnythingLLM, Bifrost, N8N, LiteLLM)
#   - cloudflared tunnel configuration
#   - This git repository
#
# Usage:
#   ./scripts/fresh-start.sh              # interactive (prompts at each step)
#   ./scripts/fresh-start.sh --force      # skip confirmation prompts
#   ./scripts/fresh-start.sh --no-cf      # skip Cloudflare teardown
#   ./scripts/fresh-start.sh --dry-run    # print what would happen, do nothing

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

FORCE=false
NO_CF=false
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --force)   FORCE=true ;;
    --no-cf)   NO_CF=true ;;
    --dry-run) DRY_RUN=true ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--force] [--no-cf] [--dry-run]"
      exit 1
      ;;
  esac
done

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

run() {
  if [[ "$DRY_RUN" == true ]]; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}

confirm() {
  local msg="$1"
  if [[ "$FORCE" == true || "$DRY_RUN" == true ]]; then
    echo "  → $msg (auto-confirmed)"
    return 0
  fi
  read -r -p "  $msg [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

section() {
  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "  $1"
  echo "════════════════════════════════════════════════════════════"
}

warn() { echo "  ⚠  $1"; }
info() { echo "  →  $1"; }
ok()   { echo "  ✓  $1"; }
skip() { echo "  -  $1 (skipped)"; }

# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight: read values we need before destroying anything
# ─────────────────────────────────────────────────────────────────────────────

section "Pre-flight"

cd "$REPO_ROOT"

# Read CF worker name from .env if present
CF_WORKER_NAME="subumbra-proxy"
if [[ -f .env ]]; then
  _name=$(grep '^CF_WORKER_NAME=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
  [[ -n "$_name" ]] && CF_WORKER_NAME="$_name"
fi
info "CF Worker name: $CF_WORKER_NAME"

# Read KV namespace ID from the Docker volume (if volume exists)
KV_NAMESPACE_ID=""
if docker volume inspect subumbra_keys_data &>/dev/null 2>&1; then
  KV_NAMESPACE_ID=$(
    docker run --rm \
      -v subumbra_keys_data:/data \
      busybox \
      sh -c 'cat /data/kv-config.json 2>/dev/null || true' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('namespace_id',''))" 2>/dev/null || true
  )
fi

if [[ -n "$KV_NAMESPACE_ID" ]]; then
  info "KV namespace ID: $KV_NAMESPACE_ID"
else
  warn "KV namespace ID not found (volume may not exist or bootstrap was not completed)"
fi

# Read CF account / API token from .env or environment
CF_API_TOKEN="${CF_API_TOKEN:-}"
CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-}"
if [[ -f .env ]]; then
  [[ -z "$CF_API_TOKEN"  ]] && CF_API_TOKEN=$(grep '^CF_API_TOKEN='  .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
  [[ -z "$CF_ACCOUNT_ID" ]] && CF_ACCOUNT_ID=$(grep '^CF_ACCOUNT_ID=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
fi

if [[ -z "$CF_API_TOKEN" || "$CF_API_TOKEN" == "REPLACE_ME" ]]; then
  warn "CF_API_TOKEN not found in .env — will skip Cloudflare teardown unless --no-cf is set"
  if [[ "$NO_CF" == false ]]; then
    warn "Set CF_API_TOKEN in your environment or .env, or use --no-cf to skip CF cleanup"
    NO_CF=true
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Summary of what will be destroyed
# ─────────────────────────────────────────────────────────────────────────────

section "What will be destroyed"

echo ""
echo "  Docker:"
echo "    - All containers defined in docker-compose.yml (with volumes)"
echo "    - Named volumes: subumbra_keys_data, subumbra_audit_data"
echo ""
echo "  Local files:"
echo "    - .env"
[[ -f .env.bootstrap ]] && echo "    - .env.bootstrap" || true
echo ""
if [[ "$NO_CF" == false ]]; then
  echo "  Cloudflare:"
  echo "    - Worker:        $CF_WORKER_NAME"
  [[ -n "$KV_NAMESPACE_ID" ]] && echo "    - KV namespace:  $KV_NAMESPACE_ID"
  echo "    - Secrets:       WORKER_PRIVATE_KEY, WORKER_KEY_FINGERPRINT"
  echo ""
else
  echo "  Cloudflare: SKIPPED (--no-cf)"
  echo ""
fi
echo "  NOT touched:"
echo "    - App installs (LibreChat, OpenWebUI, AnythingLLM, Bifrost, N8N, LiteLLM)"
echo "    - cloudflared tunnel config"
echo "    - This git repository"
echo ""

if ! confirm "Proceed with full teardown?"; then
  echo "Aborted."
  exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Docker teardown
# ─────────────────────────────────────────────────────────────────────────────

section "Step 1 — Docker teardown"

if docker compose ps --quiet 2>/dev/null | grep -q .; then
  info "Stopping running containers..."
  run docker compose down -v --remove-orphans
  ok "Containers and volumes removed"
else
  info "No running containers found"
  # Still remove volumes if they exist
  for vol in subumbra_keys_data subumbra_audit_data; do
    if docker volume inspect "$vol" &>/dev/null 2>&1; then
      info "Removing volume: $vol"
      run docker volume rm "$vol"
      ok "Volume $vol removed"
    fi
  done
fi

# Remove built images (optional — only if you want a fully clean image rebuild)
for img in subumbra-keys subumbra-ui subumbra-probe subumbra-proxy bootstrap; do
  if docker image inspect "$img" &>/dev/null 2>&1; then
    info "Removing image: $img"
    run docker image rm "$img" 2>/dev/null || warn "Could not remove $img (may be tagged differently)"
  fi
done

# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Local file cleanup
# ─────────────────────────────────────────────────────────────────────────────

section "Step 2 — Local file cleanup"

if [[ -f .env ]]; then
  info "Removing .env"
  run rm -f .env
  ok ".env removed"
else
  skip ".env not present"
fi

if [[ -f .env.bootstrap ]]; then
  warn ".env.bootstrap found — this contains plaintext API keys"
  if confirm "Shred .env.bootstrap?"; then
    run shred -u .env.bootstrap 2>/dev/null || run rm -f .env.bootstrap
    ok ".env.bootstrap shredded"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Cloudflare teardown
# ─────────────────────────────────────────────────────────────────────────────

if [[ "$NO_CF" == true ]]; then
  section "Step 3 — Cloudflare teardown (SKIPPED)"
  skip "Cloudflare teardown skipped (--no-cf or no CF_API_TOKEN)"
else
  section "Step 3 — Cloudflare teardown"

  export CLOUDFLARE_API_TOKEN="$CF_API_TOKEN"
  [[ -n "$CF_ACCOUNT_ID" ]] && export CLOUDFLARE_ACCOUNT_ID="$CF_ACCOUNT_ID"

  # Verify wrangler is available
  if ! command -v wrangler &>/dev/null && ! npx wrangler --version &>/dev/null 2>&1; then
    warn "wrangler not found — cannot perform CF teardown automatically"
    warn "Manually delete the worker '$CF_WORKER_NAME' from the Cloudflare dashboard"
    warn "  https://dash.cloudflare.com/ → Workers & Pages → $CF_WORKER_NAME → Delete"
    [[ -n "$KV_NAMESPACE_ID" ]] && warn "Also delete KV namespace $KV_NAMESPACE_ID under Workers → KV"
  else
    WRANGLER="npx wrangler"
    command -v wrangler &>/dev/null && WRANGLER="wrangler"

    # Delete secrets first (worker must exist for secret:delete to work)
    info "Deleting CF secret: WORKER_PRIVATE_KEY"
    run $WRANGLER secret delete WORKER_PRIVATE_KEY \
      --name "$CF_WORKER_NAME" --force 2>/dev/null \
      && ok "WORKER_PRIVATE_KEY deleted" \
      || warn "WORKER_PRIVATE_KEY not found or already deleted"

    info "Deleting CF secret: WORKER_KEY_FINGERPRINT"
    run $WRANGLER secret delete WORKER_KEY_FINGERPRINT \
      --name "$CF_WORKER_NAME" --force 2>/dev/null \
      && ok "WORKER_KEY_FINGERPRINT deleted" \
      || warn "WORKER_KEY_FINGERPRINT not found or already deleted"

    # Delete the worker
    info "Deleting CF Worker: $CF_WORKER_NAME"
    run $WRANGLER delete --name "$CF_WORKER_NAME" --force 2>/dev/null \
      && ok "Worker $CF_WORKER_NAME deleted" \
      || warn "Worker $CF_WORKER_NAME not found or already deleted"

    # Delete KV namespace
    if [[ -n "$KV_NAMESPACE_ID" ]]; then
      info "Deleting KV namespace: $KV_NAMESPACE_ID"
      run $WRANGLER kv namespace delete \
        --namespace-id "$KV_NAMESPACE_ID" --force 2>/dev/null \
        && ok "KV namespace $KV_NAMESPACE_ID deleted" \
        || warn "KV namespace $KV_NAMESPACE_ID not found or already deleted"
    else
      warn "No KV namespace ID available — skip KV deletion"
      warn "If the namespace exists, delete it manually:"
      warn "  Cloudflare dashboard → Workers → KV → find 'subumbra-provider-registry' → Delete"
    fi
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────

section "Done"

echo ""
echo "  Subumbra has been fully torn down."
echo ""
echo "  Next steps:"
echo "    1. Edit .env.bootstrap.example → copy to .env.bootstrap and fill in values"
echo "    2. Set PROXY_ALLOWED_KEYS and LITELLM_ALLOWED_KEYS to the key_ids you want"
echo "    3. Run bootstrap:"
echo "         ./bootstrap.sh"
echo "    4. Start the stack:"
echo "         docker compose up -d --force-recreate"
echo ""
[[ "$DRY_RUN" == true ]] && echo "  (dry-run mode — nothing was actually changed)"
