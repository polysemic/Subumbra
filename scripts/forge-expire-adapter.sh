#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: ./scripts/forge-expire-adapter.sh <adapter_id>" >&2
    exit 1
fi

adapter_id="$1"
env_path=".env"
if [[ ! -f "$env_path" ]]; then
    echo "error: .env not found" >&2
    exit 1
fi

tmp_file="$(mktemp "${env_path}.tmp.XXXXXX")"
cleanup() {
    rm -f "$tmp_file"
}
trap cleanup EXIT

python3 - "$env_path" "$tmp_file" "$adapter_id" <<'PY2'
import json
import re
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
adapter_id = sys.argv[3]
text = env_path.read_text(encoding='utf-8')
match = re.search(r'^FORGE_ADAPTER_REGISTRY=(.+)$', text, re.MULTILINE)
if not match:
    raise SystemExit('error: FORGE_ADAPTER_REGISTRY not found in .env')
try:
    registry = json.loads(match.group(1))
except json.JSONDecodeError as exc:
    raise SystemExit(f'error: FORGE_ADAPTER_REGISTRY is not valid JSON: {exc}')
if adapter_id not in registry:
    raise SystemExit(f'error: adapter not found: {adapter_id}')
registry[adapter_id]['expires_at'] = '2000-01-01T00:00:00+00:00'
new_line = 'FORGE_ADAPTER_REGISTRY=' + json.dumps(registry, separators=(',', ':'))
updated, count = re.subn(r'^FORGE_ADAPTER_REGISTRY=.+$', new_line, text, flags=re.MULTILINE)
if count != 1:
    raise SystemExit('error: failed to rewrite FORGE_ADAPTER_REGISTRY')
out_path.write_text(updated, encoding='utf-8')
PY2

mv "$tmp_file" "$env_path"
trap - EXIT

echo "adapter expired: ${adapter_id}"
echo "next: docker compose up -d --force-recreate forge-keys"
