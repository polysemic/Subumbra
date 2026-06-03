#!/usr/bin/env bash
#
# subumbra-session-sync.sh
# Starts both local (laptop) and remote (VPS) Subumbra sessions with a single secure prompt.
#

set -euo pipefail

# Configuration
LOCAL_DIR="${HOME}/git/Subumbra-Local"
VPS_HOST="subumbra-via-agent"
VPS_DIR="/opt/subumbra"

echo "=========================================="
echo "🛡️  Subumbra Session Sync Automator 🛡️"
echo "=========================================="

# 1. Read or Prompt for Cloudflare credentials
if [ -z "${CF_ACCOUNT_ID:-}" ]; then
    read -p "Enter Cloudflare Account ID: " CF_ACCOUNT_ID
fi

# Prompt for the API Token securely (hiding input)
if [ -z "${CF_API_TOKEN:-}" ]; then
    read -sp "Enter Cloudflare API Token: " CF_API_TOKEN
    echo ""
fi

export CF_ACCOUNT_ID
export CF_API_TOKEN

echo "🔄 Starting local Subumbra session..."
cd "${LOCAL_DIR}"
./bootstrap.sh --session start --ttl 4h --consumers sshtest --keys vps_access

echo "🔄 Injecting secure session to remote VPS over SSH tunnel..."
# Enforce using local socket to talk to the VPS
export SSH_AUTH_SOCK="/run/user/1000/subumbra/ssh-agent.sock"

printf '%s\n%s\n' "${CF_API_TOKEN}" "${CF_ACCOUNT_ID}" | \
    ssh -t "${VPS_HOST}" "bash -lc 'IFS= read -r CF_API_TOKEN && IFS= read -r CF_ACCOUNT_ID && export CF_API_TOKEN CF_ACCOUNT_ID && cd \"${VPS_DIR}\" && ./bootstrap.sh --session start --ttl 1h --consumers sshtest --keys github_vps_test'"

echo "=========================================="
echo "✅ Both sessions successfully activated!"
echo "=========================================="
