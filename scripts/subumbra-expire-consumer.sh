#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: ./scripts/subumbra-expire-adapter.sh <consumer_id>" >&2
    exit 1
fi

consumer_id="$1"
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

python3 - "$env_path" "$tmp_file" "$consumer_id" <<'PY2'
import json
import re
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
consumer_id = sys.argv[3]
text = env_path.read_text(encoding='utf-8')
match = re.search(r'^SUBUMBRA_CONSUMER_REGISTRY=(.+)$', text, re.MULTILINE)
if not match:
    raise SystemExit('error: SUBUMBRA_CONSUMER_REGISTRY not found in .env')
try:
    registry = json.loads(match.group(1))
except json.JSONDecodeError as exc:
    raise SystemExit(f'error: SUBUMBRA_CONSUMER_REGISTRY is not valid JSON: {exc}')
if consumer_id not in registry:
    raise SystemExit(f'error: adapter not found: {consumer_id}')
registry[consumer_id]['expires_at'] = '2000-01-01T00:00:00+00:00'
new_line = 'SUBUMBRA_CONSUMER_REGISTRY=' + json.dumps(registry, separators=(',', ':'))
updated, count = re.subn(r'^SUBUMBRA_CONSUMER_REGISTRY=.+$', new_line, text, flags=re.MULTILINE)
if count != 1:
    raise SystemExit('error: failed to rewrite SUBUMBRA_CONSUMER_REGISTRY')
out_path.write_text(updated, encoding='utf-8')
PY2

mv "$tmp_file" "$env_path"
trap - EXIT

echo "adapter expired: ${consumer_id}"
echo "next: docker compose up -d --force-recreate subumbra-keys"
