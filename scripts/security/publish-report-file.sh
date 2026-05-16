#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPORTS_DIR="$REPO_ROOT/security/reports"

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 <source-file> [output-name.md] [source-label]" >&2
  exit 1
fi

SOURCE_FILE="$1"
OUTPUT_NAME="${2:-}"
SOURCE_LABEL="${3:-off-repo security tool}"

if [[ ! -f "$SOURCE_FILE" ]]; then
  echo "ERROR: source file not found: $SOURCE_FILE" >&2
  exit 1
fi

mkdir -p "$REPORTS_DIR"

if [[ -z "$OUTPUT_NAME" ]]; then
  DATE_PREFIX="$(date -u +%Y-%m-%d)"
  OUTPUT_NAME="${DATE_PREFIX}-$(basename "$SOURCE_FILE")"
fi

OUTPUT_PATH="$REPORTS_DIR/$OUTPUT_NAME"

python3 - "$SOURCE_FILE" "$OUTPUT_PATH" "$SOURCE_LABEL" <<'PY'
from __future__ import annotations

import pathlib
import re
import sys

source_path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
source_label = sys.argv[3]
text = source_path.read_text(encoding="utf-8", errors="replace")

patterns = [
    (r'(?im)^(\s*(?:authorization|proxy-authorization)\s*:\s*bearer\s+).+$', r'\1<redacted>'),
    (r'(?im)^(\s*x-api-key\s*:\s*).+$', r'\1<redacted>'),
    (r'(?im)^(\s*cookie\s*:\s*).+$', r'\1<redacted>'),
    (r'(?im)^(\s*set-cookie\s*:\s*).+$', r'\1<redacted>'),
    (r'(?im)\b(cf_api_token|anthropic_api_key|openai_api_key|groq_api_key|deepseek_api_key|gemini_api_key|mistral_api_key|openrouter_api_key|together_ai_api_key|xai_api_key|github_token|github_key|slack_bot_token|sendgrid_api_key|subumbra_setup_token|subumbra_management_token|subumbra_hmac_key|subumbra_token_[a-z0-9_]+)\s*[:=]\s*([^\s`"\']+)', r'\1=<redacted>'),
    (r'(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{12,}\b', 'Bearer <redacted>'),
    (r'(?i)\b(sk-(?:ant|proj)?[A-Za-z0-9\-_]+)\b', '<redacted-secret>'),
    (r'(?i)\b(gsk_[A-Za-z0-9\-_]+)\b', '<redacted-secret>'),
    (r'(?i)\b(xox[baprs]-[A-Za-z0-9\-]+)\b', '<redacted-secret>'),
    (r'(?i)\b(gh[pousr]_[A-Za-z0-9]+)\b', '<redacted-secret>'),
]

for pattern, replacement in patterns:
    text = re.sub(pattern, replacement, text)

header = (
    f"> Sanitized report published from {source_label}.\n"
    f"> Source file: `{source_path}`\n\n"
)

output_path.write_text(header + text, encoding="utf-8")
PY

echo "Published sanitized report:"
echo "  source: $SOURCE_FILE"
echo "  output: $OUTPUT_PATH"
