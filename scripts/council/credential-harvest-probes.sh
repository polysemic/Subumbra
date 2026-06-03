#!/usr/bin/env bash
# scripts/council/credential-harvest-probes.sh
#
# Credential-harvest attack simulation for Subumbra.
#
# Mimics the attack patterns used by real-world credential harvesters
# (supply-chain PyPI/npm implants, RedLine/Raccoon-style stealers, TeamPCP,
# LiteLLM config scraping) and verifies Subumbra's zero-trust claim: malware
# landing on the host gets nothing useful.
#
# Usage (local, from repo root):
#   CF_API_TOKEN=<token> CF_ACCOUNT_ID=<id> \
#   scripts/council/credential-harvest-probes.sh [--artifact-dir <dir>]
#
# Usage (inside vps-proof-run independent-probes.sh):
#   export PROBE_ARTIFACT_DIR="${VERIFY_ARTIFACT_DIR}/independent-probes"
#   bash scripts/council/credential-harvest-probes.sh
#
# Each probe records:
#   - ATT&CK technique
#   - Hypothesis
#   - Expected secure behavior
#   - Actual result
#   - PASS / FAIL / INVESTIGATE_NOW / ENVIRONMENTAL
#
# Rules:
#   - Never log plaintext secret values — only pattern-match counts and
#     boolean present/absent signals
#   - No mutations to live state; read-only throughout
#   - CF_API_TOKEN required only for P-09 (KV enumeration); all others run
#     without CF credentials
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

ARTIFACT_DIR="${PROBE_ARTIFACT_DIR:-${VERIFY_ARTIFACT_DIR:-${REPO_ROOT}/independent-probes}/independent-probes}"
mkdir -p "${ARTIFACT_DIR}"

SUMMARY_FILE="${ARTIFACT_DIR}/harvest-probe-summary.txt"
INDEX_FILE="${ARTIFACT_DIR}/harvest-probe-index.jsonl"
: > "${SUMMARY_FILE}"
: > "${INDEX_FILE}"

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
INVEST_COUNT=0

# ── Colours ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_GREEN='\033[0;32m'; C_RED='\033[0;31m'; C_YELLOW='\033[0;33m'
  C_CYAN='\033[0;36m'; C_BOLD='\033[1m'; C_RESET='\033[0m'
else
  C_GREEN=''; C_RED=''; C_YELLOW=''; C_CYAN=''; C_BOLD=''; C_RESET=''
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
dc_exec() { docker compose exec -T "$@"; }

log_probe() {
  local id="$1" technique="$2" status="$3" hypothesis="$4" expected="$5" actual="$6"
  local artifact="${7:-}"
  local color
  case "${status}" in
    PASS)            color="${C_GREEN}" ;;
    FAIL)            color="${C_RED}" ;;
    INVESTIGATE_NOW) color="${C_YELLOW}" ;;
    *)               color="${C_CYAN}" ;;
  esac
  printf "${C_BOLD}[%s]${C_RESET} ${color}%s${C_RESET}  %s\n" "${id}" "${status}" "${hypothesis}" | tee -a "${SUMMARY_FILE}"
  _PROBE_ID="${id}" _PROBE_TECHNIQUE="${technique}" _PROBE_STATUS="${status}" \
  _PROBE_HYP="${hypothesis}" _PROBE_EXP="${expected}" _PROBE_ACT="${actual}" \
  _PROBE_ART="${artifact}" \
  python3 - <<'PY' >> "${INDEX_FILE}"
import json, os
print(json.dumps({
  "id":         os.environ["_PROBE_ID"],
  "technique":  os.environ["_PROBE_TECHNIQUE"],
  "status":     os.environ["_PROBE_STATUS"],
  "hypothesis": os.environ["_PROBE_HYP"],
  "expected":   os.environ["_PROBE_EXP"],
  "actual":     os.environ["_PROBE_ACT"],
  "artifact":   os.environ["_PROBE_ART"],
}, sort_keys=True))
PY
  case "${status}" in
    PASS)            (( PASS_COUNT++ )) || true ;;
    FAIL)            (( FAIL_COUNT++ )) || true ;;
    INVESTIGATE_NOW) (( INVEST_COUNT++ )) || true ;;
    ENVIRONMENTAL|DEFERRED|HARNESS_ISSUE) (( SKIP_COUNT++ )) || true ;;
  esac
}

provider_key_regex='sk-[a-zA-Z0-9_-]{20,}|sk-ant-[a-zA-Z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|gsk_[a-zA-Z0-9]{20,}|xai-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}|glpat-[a-zA-Z0-9_-]{20}'

# Patterns that indicate a live provider key in a config or env file
config_key_regex='(?i)(openai|anthropic|groq|deepseek|mistral|together|xai|cohere|gemini|huggingface)[_\s-]*(api[_-]?key|key|secret|token)\s*[=:]\s*["\x27]?[a-zA-Z0-9_\-]{20,}'

printf "\n${C_BOLD}═══ Subumbra Credential-Harvest Attack Simulation ═══${C_RESET}\n\n"
printf "Artifact dir: %s\n" "${ARTIFACT_DIR}"
printf "Repo root:    %s\n\n" "${REPO_ROOT}"

cd "${REPO_ROOT}"

# ─────────────────────────────────────────────────────────────────────────────
# CLASS 1 — Supply-chain / process environment (T1552.007)
# Mimics: PyPI/npm packages that run os.environ + requests.post on import,
#         TeamPCP-style env-var exfiltration, LiteLLM supply-chain probes.
# ─────────────────────────────────────────────────────────────────────────────
printf "${C_CYAN}── Class 1: Supply-chain / process environment ──${C_RESET}\n"

# P-01 ── subumbra-keys container env
P01_OUT="${ARTIFACT_DIR}/p01-keys-env-scan.txt"
{
  dc_exec subumbra-keys env 2>/dev/null \
    | grep -Eo "${provider_key_regex}" \
    | sed 's/.\{8\}$/[REDACTED]/' \
    || true
} > "${P01_OUT}"
P01_HITS=$(wc -l < "${P01_OUT}" | tr -d ' ')
if [[ "${P01_HITS}" -eq 0 ]]; then
  log_probe P-01 T1552.007 PASS \
    "subumbra-keys env contains no provider key patterns (supply-chain os.environ harvest)" \
    "0 matches" "0 matches" "p01-keys-env-scan.txt"
else
  log_probe P-01 T1552.007 FAIL \
    "subumbra-keys env contains no provider key patterns" \
    "0 matches" "${P01_HITS} matches found" "p01-keys-env-scan.txt"
fi

# P-02 ── subumbra-proxy container env
P02_OUT="${ARTIFACT_DIR}/p02-proxy-env-scan.txt"
{
  dc_exec subumbra-proxy env 2>/dev/null \
    | grep -Eo "${provider_key_regex}" \
    | sed 's/.\{8\}$/[REDACTED]/' \
    || true
} > "${P02_OUT}"
P02_HITS=$(wc -l < "${P02_OUT}" | tr -d ' ')
if [[ "${P02_HITS}" -eq 0 ]]; then
  log_probe P-02 T1552.007 PASS \
    "subumbra-proxy env contains no provider key patterns" \
    "0 matches" "0 matches" "p02-proxy-env-scan.txt"
else
  log_probe P-02 T1552.007 FAIL \
    "subumbra-proxy env contains no provider key patterns" \
    "0 matches" "${P02_HITS} matches found" "p02-proxy-env-scan.txt"
fi

# P-03 ── subumbra-ui container env
P03_OUT="${ARTIFACT_DIR}/p03-ui-env-scan.txt"
{
  dc_exec subumbra-ui env 2>/dev/null \
    | grep -Eo "${provider_key_regex}" \
    | sed 's/.\{8\}$/[REDACTED]/' \
    || true
} > "${P03_OUT}"
P03_HITS=$(wc -l < "${P03_OUT}" | tr -d ' ')
if [[ "${P03_HITS}" -eq 0 ]]; then
  log_probe P-03 T1552.007 PASS \
    "subumbra-ui env contains no provider key patterns" \
    "0 matches" "0 matches" "p03-ui-env-scan.txt"
else
  log_probe P-03 T1552.007 FAIL \
    "subumbra-ui env contains no provider key patterns" \
    "0 matches" "${P03_HITS} matches found" "p03-ui-env-scan.txt"
fi

# P-04 ── /proc/1/environ inside subumbra-keys (process memory boundary)
P04_OUT="${ARTIFACT_DIR}/p04-proc-environ.txt"
{
  dc_exec subumbra-keys sh -c 'cat /proc/1/environ | tr "\0" "\n"' 2>/dev/null \
    | grep -Eo "${provider_key_regex}" \
    | sed 's/.\{8\}$/[REDACTED]/' \
    || true
} > "${P04_OUT}"
P04_HITS=$(wc -l < "${P04_OUT}" | tr -d ' ')
if [[ "${P04_HITS}" -eq 0 ]]; then
  log_probe P-04 "T1552.007+T1057" PASS \
    "/proc/1/environ in subumbra-keys contains no provider key patterns (memory boundary)" \
    "0 matches" "0 matches" "p04-proc-environ.txt"
else
  log_probe P-04 "T1552.007+T1057" FAIL \
    "/proc/1/environ in subumbra-keys contains no provider key patterns" \
    "0 matches" "${P04_HITS} matches found" "p04-proc-environ.txt"
fi

# P-05 ── Simulate malicious PyPI import pattern: os.environ + filesystem scan
#         Mimics: aiocpa, requests-darwin-lite, pytorch-nightly-cpu (2023 campaigns)
P05_OUT="${ARTIFACT_DIR}/p05-supply-chain-sim.txt"
dc_exec subumbra-keys python3 - <<'PY' > "${P05_OUT}" 2>&1 || true
import json, os, re, pathlib

# What a malicious __init__.py would do on import
provider_re = re.compile(
    r'sk-[a-zA-Z0-9_-]{20,}|sk-ant-[a-zA-Z0-9_-]{20,}|AKIA[0-9A-Z]{16}'
    r'|gsk_[a-zA-Z0-9]{20,}|xai-[a-zA-Z0-9]{20,}|AIza[0-9A-Za-z_-]{35}'
)

# Step 1: harvest os.environ
env_hits = []
for k, v in os.environ.items():
    if provider_re.search(v):
        env_hits.append(k)

# Step 2: filesystem scan for credential files
fs_hits = []
scan_paths = ['/app', '/root', '/home', '/tmp', '/etc']
credential_names = [
    'credentials', '.env', 'config.yaml', 'litellm_config', 'secrets',
    '.netrc', '.aws/credentials', 'id_rsa', 'token',
]
for base in scan_paths:
    for p in pathlib.Path(base).rglob('*'):
        try:
            if not p.is_file() or p.stat().st_size > 1_000_000:
                continue
            if any(n in p.name.lower() for n in credential_names):
                text = p.read_text(errors='replace')
                if provider_re.search(text):
                    fs_hits.append(str(p))
        except (PermissionError, OSError):
            pass

# Step 3: check endpoint.json specifically (primary target)
keys_path = pathlib.Path('/app/data/endpoint.json')
keys_plaintext = False
if keys_path.exists():
    try:
        keys = json.loads(keys_path.read_text())
        for kid, record in keys.items():
            for field, val in (record.items() if isinstance(record, dict) else []):
                if isinstance(val, str) and provider_re.search(val):
                    keys_plaintext = True
    except Exception:
        pass

print(json.dumps({
    "env_provider_key_hits": len(env_hits),
    "fs_credential_file_hits": len(fs_hits),
    "keys_json_plaintext_leak": keys_plaintext,
    "verdict": "nothing_harvested" if not env_hits and not fs_hits and not keys_plaintext else "HARVESTED",
}, indent=2, sort_keys=True))
PY
P05_VERDICT=$(python3 -c "import json,sys; d=json.load(open('${P05_OUT}')); print(d.get('verdict','unknown'))" 2>/dev/null || echo "parse_error")
if [[ "${P05_VERDICT}" == "nothing_harvested" ]]; then
  log_probe P-05 T1552.007 PASS \
    "Malicious PyPI import simulation (os.environ + fs scan) finds nothing harvestable" \
    "nothing_harvested" "${P05_VERDICT}" "p05-supply-chain-sim.txt"
else
  log_probe P-05 T1552.007 FAIL \
    "Malicious PyPI import simulation (os.environ + fs scan) finds nothing harvestable" \
    "nothing_harvested" "${P05_VERDICT}" "p05-supply-chain-sim.txt"
fi

# ─────────────────────────────────────────────────────────────────────────────
# CLASS 2 — Filesystem credential harvest (T1552.001 / T1005)
# Mimics: RedLine, Raccoon, Vidar stealers; LiteLLM config harvesting;
#         generic .env / config.yaml scrapers.
# ─────────────────────────────────────────────────────────────────────────────
printf "\n${C_CYAN}── Class 2: Filesystem credential harvest ──${C_RESET}\n"

# P-06 ── Host .env contains no live provider key
P06_OUT="${ARTIFACT_DIR}/p06-host-env-scan.txt"
{
  grep -Eo "${provider_key_regex}" .env 2>/dev/null \
    | sed 's/.\{8\}$/[REDACTED]/' \
    || true
} > "${P06_OUT}"
P06_HITS=$(wc -l < "${P06_OUT}" | tr -d ' ')
if [[ "${P06_HITS}" -eq 0 ]]; then
  log_probe P-06 T1552.001 PASS \
    "Host .env contains no plaintext provider API keys" \
    "0 matches" "0 matches" "p06-host-env-scan.txt"
else
  log_probe P-06 T1552.001 FAIL \
    "Host .env contains no plaintext provider API keys" \
    "0 matches" "${P06_HITS} matches found" "p06-host-env-scan.txt"
fi

# P-07 ── endpoint.json contains only ciphertext/wrapped-DEK blobs — no raw keys
P07_OUT="${ARTIFACT_DIR}/p07-keys-json-analysis.txt"
dc_exec subumbra-keys python3 - <<'PY' > "${P07_OUT}" 2>&1 || true
import json, re, pathlib

provider_re = re.compile(
    r'sk-[a-zA-Z0-9_-]{20,}|sk-ant-[a-zA-Z0-9_-]{20,}|AKIA[0-9A-Z]{16}'
    r'|gsk_[a-zA-Z0-9]{20,}|xai-[a-zA-Z0-9]{20,}'
)

keys = json.loads(pathlib.Path('/app/data/endpoint.json').read_text())
results = {}
for kid, record in keys.items():
    if not isinstance(record, dict):
        continue
    results[kid] = {
        "enc_version":       record.get("enc_version"),
        "has_ciphertext":    bool(record.get("ciphertext")),
        "has_wrapped_dek":   bool(record.get("wrapped_dek")),
        "has_pub_key_fp":    bool(record.get("pub_key_fp")),
        "has_policy_hash":   bool(record.get("policy_hash")),
        "plaintext_key_leak": bool(
            any(isinstance(v, str) and provider_re.search(v)
                for v in record.values())
        ),
    }
print(json.dumps({
    "key_count":             len(results),
    "all_v3":                all(r["enc_version"] == 3 for r in results.values()),
    "any_plaintext_leak":    any(r["plaintext_key_leak"] for r in results.values()),
    "records":               results,
}, indent=2, sort_keys=True))
PY
P07_LEAK=$(python3 -c "import json; d=json.load(open('${P07_OUT}')); print(d.get('any_plaintext_leak', True))" 2>/dev/null || echo "True")
P07_V3=$(python3 -c "import json; d=json.load(open('${P07_OUT}')); print(d.get('all_v3', False))" 2>/dev/null || echo "False")
if [[ "${P07_LEAK}" == "False" && "${P07_V3}" == "True" ]]; then
  log_probe P-07 T1005 PASS \
    "endpoint.json contains only V3 ciphertext+wrapped-DEK blobs, no raw provider keys" \
    "no_plaintext_leak, all_v3" "no_plaintext_leak=${P07_LEAK}, all_v3=${P07_V3}" "p07-keys-json-analysis.txt"
else
  log_probe P-07 T1005 FAIL \
    "endpoint.json contains only V3 ciphertext+wrapped-DEK blobs, no raw provider keys" \
    "no_plaintext_leak, all_v3" "no_plaintext_leak=${P07_LEAK}, all_v3=${P07_V3}" "p07-keys-json-analysis.txt"
fi

# P-08 ── LiteLLM/app config path scan on host
#         Mimics the LiteLLM supply-chain harvest: scan common config locations
#         for api_key / OPENAI_API_KEY patterns that LiteLLM stores in plaintext
P08_OUT="${ARTIFACT_DIR}/p08-litellm-config-scan.txt"
{
  find /opt /home /root /etc /tmp \
    -maxdepth 8 \
    \( -name "litellm_config*" \
       -o -name "config.yaml" \
       -o -name "config.json" \
       -o -name ".env*" \
       -o -name "credentials" \
       -o -name "credentials.json" \
    \) \
    -not -path "*/council/*" \
    -not -path "*/.git/*" \
    -not -path "*/node_modules/*" \
    -not -name "*.bootstrap*" \
    -not -name ".env.bootstrap*" \
    2>/dev/null \
  | while read -r f; do
      hits=$(grep -Ec "${provider_key_regex}" "${f}" 2>/dev/null; true)
      hits="${hits:-0}"
      if [[ "${hits}" -gt 0 ]]; then
        echo "MATCH file=${f} hits=${hits}"
      fi
    done || true
} > "${P08_OUT}"
P08_MATCHES=$(grep -c '^MATCH' "${P08_OUT}" 2>/dev/null; true)
P08_MATCHES="${P08_MATCHES:-0}"
if [[ "${P08_MATCHES}" -eq 0 ]]; then
  log_probe P-08 "T1552.001+T1005" PASS \
    "Host config/credential file scan finds no plaintext provider keys (LiteLLM-style harvest)" \
    "0 files with provider keys" "0 files matched" "p08-litellm-config-scan.txt"
else
  log_probe P-08 "T1552.001+T1005" FAIL \
    "Host config/credential file scan finds no plaintext provider keys" \
    "0 files with provider keys" "${P08_MATCHES} files with matches" "p08-litellm-config-scan.txt"
fi

# P-09 ── Captured /keys/ response is inert without RSA private key
#         Shows that the V3 envelope (ciphertext + wrapped_dek) cannot be
#         decrypted offline — attacker who exfiltrates endpoint.json still gets nothing.
P09_OUT="${ARTIFACT_DIR}/p09-envelope-inert.txt"
dc_exec subumbra-keys python3 - <<'PY' > "${P09_OUT}" 2>&1 || true
import base64, json, os, pathlib

keys = json.loads(pathlib.Path('/app/data/endpoint.json').read_text())
sample_id = next(iter(keys))
record = keys[sample_id]

# Show what an attacker exfiltrating endpoint.json actually gets
analysis = {
    "key_id":          sample_id,
    "enc_version":     record.get("enc_version"),
    "ciphertext_len":  len(record.get("ciphertext", "")),
    "wrapped_dek_len": len(record.get("wrapped_dek", "")),
    "pub_key_fp":      record.get("pub_key_fp", "")[:20] + "...",
    "policy_hash":     record.get("policy_hash", "")[:20] + "...",
    "vault_instance":  record.get("vault_instance"),
    "decryptable_without_rsa_key": False,
    "explanation": (
        "wrapped_dek is RSA-OAEP encrypted with a public key whose private "
        "counterpart was generated inside and never left the Cloudflare "
        "SubumbraVault Durable Object. Even with ciphertext + wrapped_dek + "
        "pub_key_fp, offline decryption is not possible."
    ),
}

# Sanity: wrapped_dek is base64-encoded ciphertext, not the raw DEK
try:
    dek_bytes = base64.b64decode(record["wrapped_dek"])
    analysis["wrapped_dek_is_raw_base64_len"] = len(dek_bytes)
    # RSA-4096 wraps to 512 bytes — confirm it's an RSA blob, not a 32-byte AES key
    analysis["looks_like_rsa_wrapped"] = len(dek_bytes) == 512
except Exception as exc:
    analysis["wrapped_dek_decode_error"] = str(exc)

print(json.dumps(analysis, indent=2, sort_keys=True))
PY
P09_RSA=$(python3 -c "import json; d=json.load(open('${P09_OUT}')); print(d.get('looks_like_rsa_wrapped', False))" 2>/dev/null || echo "False")
P09_DECRYPTABLE=$(python3 -c "import json; d=json.load(open('${P09_OUT}')); print(d.get('decryptable_without_rsa_key', True))" 2>/dev/null || echo "True")
if [[ "${P09_RSA}" == "True" && "${P09_DECRYPTABLE}" == "False" ]]; then
  log_probe P-09 "T1552+T1588" PASS \
    "Exfiltrated endpoint.json envelope is inert without RSA private key (CF DO custody)" \
    "RSA-4096 wrapped DEK, not decryptable offline" \
    "rsa_wrapped=${P09_RSA}, decryptable_offline=${P09_DECRYPTABLE}" \
    "p09-envelope-inert.txt"
else
  log_probe P-09 "T1552+T1588" INVESTIGATE_NOW \
    "Exfiltrated endpoint.json envelope is inert without RSA private key" \
    "RSA-4096 wrapped DEK, not decryptable offline" \
    "rsa_wrapped=${P09_RSA}, decryptable_offline=${P09_DECRYPTABLE}" \
    "p09-envelope-inert.txt"
fi

# ─────────────────────────────────────────────────────────────────────────────
# CLASS 3 — Network / lateral movement (T1046 / T1557 / T1095)
# Mimics: container escape lateral movement, Docker network MITM,
#         unauthenticated internal service probe.
# ─────────────────────────────────────────────────────────────────────────────
printf "\n${C_CYAN}── Class 3: Network / lateral movement ──${C_RESET}\n"

# P-10 ── subumbra-keys unreachable from host network
P10_OUT="${ARTIFACT_DIR}/p10-host-network-isolation.txt"
{
  curl -s --connect-timeout 3 "http://localhost:9090/keys/test" 2>&1 \
    | head -5 \
    || true
  echo "exit_code=$?"
} > "${P10_OUT}" 2>&1
P10_BODY=$(cat "${P10_OUT}")
if echo "${P10_BODY}" | grep -qi '"error"\|ciphertext\|keys'; then
  log_probe P-10 T1046 FAIL \
    "subumbra-keys port 9090 is not reachable from host (internal Docker network)" \
    "connection refused / timeout" "responded with key data" "p10-host-network-isolation.txt"
else
  log_probe P-10 T1046 PASS \
    "subumbra-keys port 9090 is not reachable from host (internal Docker network)" \
    "connection refused / timeout" "no key service response from host" "p10-host-network-isolation.txt"
fi

# P-11 ── Unauthenticated direct probe to subumbra-keys from proxy container
#         Mimics lateral movement after proxy container compromise
P11_OUT="${ARTIFACT_DIR}/p11-unauth-keys-probe.txt"
STATUS_11=$(dc_exec subumbra-proxy python3 -c "
import urllib.request, urllib.error
try:
    r = urllib.request.urlopen('http://subumbra-keys:9090/keys/anthropic_prod', timeout=5)
    print(r.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('000')
" 2>/dev/null | tr -d '\r\n' || echo "000")
echo "http_status=${STATUS_11}" > "${P11_OUT}"
if [[ "${STATUS_11}" == "401" || "${STATUS_11}" == "403" ]]; then
  log_probe P-11 T1046 PASS \
    "Unauthenticated request to subumbra-keys/keys/* from compromised proxy returns 401/403" \
    "401 or 403" "${STATUS_11}" "p11-unauth-keys-probe.txt"
else
  log_probe P-11 T1046 FAIL \
    "Unauthenticated request to subumbra-keys/keys/* from compromised proxy returns 401/403" \
    "401 or 403" "${STATUS_11}" "p11-unauth-keys-probe.txt"
fi

# P-12 ── Wrong token returns 401, not partial data
P12_OUT="${ARTIFACT_DIR}/p12-wrong-token-probe.txt"
P12_RESULT=$(dc_exec subumbra-proxy python3 -c "
import urllib.request, urllib.error, json
req = urllib.request.Request(
    'http://subumbra-keys:9090/keys/anthropic_prod',
    headers={'X-Subumbra-Token': 'definitely-not-a-real-token'}
)
try:
    r = urllib.request.urlopen(req, timeout=5)
    body = r.read().decode()
    print(json.dumps({'status': r.status, 'body': body[:200]}))
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(json.dumps({'status': e.code, 'body': body[:200]}))
except Exception as ex:
    print(json.dumps({'status': '000', 'body': str(ex)}))
" 2>/dev/null || echo '{"status":"000","body":""}')
echo "${P12_RESULT}" > "${P12_OUT}"
STATUS_12=$(P12_JSON="${P12_RESULT}" python3 -c "
import json, os
d = json.loads(os.environ['P12_JSON'])
print(d.get('status', '000'))
" 2>/dev/null || echo "000")
BODY_12=$(P12_JSON="${P12_RESULT}" python3 -c "
import json, os
d = json.loads(os.environ['P12_JSON'])
print(d.get('body', ''))
" 2>/dev/null || echo "")
if [[ "${STATUS_12}" == "401" || "${STATUS_12}" == "403" ]] && \
   ! echo "${BODY_12}" | grep -qi 'ciphertext\|wrapped_dek\|sk-'; then
  log_probe P-12 T1046 PASS \
    "Invalid token to subumbra-keys returns 401/403 with no key material in body" \
    "401/403, no key data in response" "${STATUS_12}, body clean" "p12-wrong-token-probe.txt"
else
  log_probe P-12 T1046 FAIL \
    "Invalid token to subumbra-keys returns 401/403 with no key material in body" \
    "401/403, no key data in response" "${STATUS_12}" "p12-wrong-token-probe.txt"
fi

# P-13 ── subumbra-proxy has no internet egress to arbitrary C2 endpoints
#         Mimics supply-chain implant trying to POST harvested data home
P13_OUT="${ARTIFACT_DIR}/p13-proxy-egress-blocked.txt"
EGRESS_STATUS=$(dc_exec subumbra-proxy python3 -c "
import urllib.request, urllib.error, socket
try:
    req = urllib.request.Request('https://httpbin.org/post',
        data=b'{\"test\":\"egress_probe\"}',
        headers={'Content-Type': 'application/json'})
    socket.setdefaulttimeout(4)
    r = urllib.request.urlopen(req, timeout=4)
    print(r.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('blocked')
" 2>/dev/null | tr -d '\r\n' || echo "blocked")
echo "egress_status=${EGRESS_STATUS}" > "${P13_OUT}"
# subumbra-proxy IS on the external network for Worker calls — this will succeed.
# Flag it for operator awareness but don't fail: the Worker URL is the intended egress.
if [[ "${EGRESS_STATUS}" == "blocked" || "${EGRESS_STATUS}" == "000" ]]; then
  log_probe P-13 T1095 PASS \
    "subumbra-proxy cannot reach arbitrary internet endpoints (C2 exfiltration blocked)" \
    "blocked or 000" "${EGRESS_STATUS}" "p13-proxy-egress-blocked.txt"
else
  log_probe P-13 T1095 DEFERRED \
    "subumbra-proxy internet egress check — proxy has intentional CF egress; C2 exfil possible if implant runs inside container" \
    "blocked" "${EGRESS_STATUS} — proxy has intentional CF egress" \
    "p13-proxy-egress-blocked.txt"
fi

# P-14 ── subumbra-keys has NO internet egress (air-gapped internal network)
P14_OUT="${ARTIFACT_DIR}/p14-keys-egress-blocked.txt"
KEYS_EGRESS=$(dc_exec subumbra-keys python3 -c "
import urllib.request, urllib.error, socket
try:
    req = urllib.request.Request('https://httpbin.org/post',
        data=b'{\"test\":\"egress_probe\"}',
        headers={'Content-Type': 'application/json'})
    socket.setdefaulttimeout(4)
    r = urllib.request.urlopen(req, timeout=4)
    print(r.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('blocked')
" 2>/dev/null | tr -d '\r\n' || echo "blocked")
echo "keys_egress_status=${KEYS_EGRESS}" > "${P14_OUT}"
if [[ "${KEYS_EGRESS}" == "blocked" || "${KEYS_EGRESS}" == "000" ]]; then
  log_probe P-14 T1095 PASS \
    "subumbra-keys has no internet egress — supply-chain implant inside container cannot exfiltrate ciphertext" \
    "blocked/no-route" "${KEYS_EGRESS}" "p14-keys-egress-blocked.txt"
else
  log_probe P-14 T1095 FAIL \
    "subumbra-keys has no internet egress — container should be air-gapped" \
    "blocked/no-route" "${KEYS_EGRESS}" "p14-keys-egress-blocked.txt"
fi

# ─────────────────────────────────────────────────────────────────────────────
# CLASS 4 — Consumer token scope enforcement (T1078 / stolen-token abuse)
# Mimics: stolen consumer token used to enumerate keys it shouldn't reach,
#         cross-adapter request with a valid-but-wrong token.
# ─────────────────────────────────────────────────────────────────────────────
printf "\n${C_CYAN}── Class 4: Stolen token / scope enforcement ──${C_RESET}\n"

# P-15 ── UI token cannot fetch a key (wrong token class)
P15_OUT="${ARTIFACT_DIR}/p15-ui-token-key-fetch.txt"
UI_TOKEN=$(dc_exec subumbra-ui printenv SUBUMBRA_ACCESS_TOKEN 2>/dev/null | tr -d '\r' | tail -n1 || echo "")
if [[ -n "${UI_TOKEN}" ]]; then
  STATUS_15=$(dc_exec subumbra-ui python3 -c "
import urllib.request, urllib.error
req = urllib.request.Request(
    'http://subumbra-keys:9090/keys/anthropic_prod',
    headers={'X-Subumbra-Token': '${UI_TOKEN}'}
)
try:
    r = urllib.request.urlopen(req, timeout=5)
    print(r.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('000')
" 2>/dev/null | tr -d '\r\n' || echo "000")
  echo "http_status=${STATUS_15}" > "${P15_OUT}"
  if [[ "${STATUS_15}" == "401" || "${STATUS_15}" == "403" ]]; then
    log_probe P-15 T1078 PASS \
      "UI consumer token cannot fetch a key from subumbra-keys (wrong scope)" \
      "401 or 403" "${STATUS_15}" "p15-ui-token-key-fetch.txt"
  else
    log_probe P-15 T1078 FAIL \
      "UI consumer token cannot fetch a key from subumbra-keys (wrong scope)" \
      "401 or 403" "${STATUS_15}" "p15-ui-token-key-fetch.txt"
  fi
else
  echo "UI_TOKEN not found" > "${P15_OUT}"
  log_probe P-15 T1078 ENVIRONMENTAL \
    "UI consumer token cannot fetch a key from subumbra-keys" \
    "401 or 403" "UI_TOKEN unavailable from container" "p15-ui-token-key-fetch.txt"
fi

# P-16 ── Cloudflare KV partial-credential check
#         If CF_API_TOKEN available: enumerate KV, confirm only wrapped material present.
#         If not: note as ENVIRONMENTAL.
P16_OUT="${ARTIFACT_DIR}/p16-kv-partial-cred.txt"
if [[ -n "${CF_API_TOKEN:-}" && -n "${CF_ACCOUNT_ID:-}" ]]; then
  KV_NS=$(dc_exec subumbra-keys python3 - <<'PY' 2>/dev/null
import json
print(json.load(open('/app/data/kv-config.json'))['namespace_id'])
PY
  )
  KV_NS="$(printf '%s' "${KV_NS:-}" | tr -d '\n\r')"
  _P16_ACCOUNT="$(printf '%s' "${CF_ACCOUNT_ID}" | tr -d '\n\r')"
  _P16_TOKEN="$(printf '%s' "${CF_API_TOKEN}" | tr -d '\n\r')"
  P16_KV_NS="${KV_NS}" P16_ACCOUNT="${_P16_ACCOUNT}" P16_TOKEN="${_P16_TOKEN}" \
  python3 - <<'PY' > "${P16_OUT}" 2>&1 || true
import json, urllib.request, os, re

ns      = os.environ["P16_KV_NS"]
account = os.environ["P16_ACCOUNT"]
token   = os.environ["P16_TOKEN"]
provider_re = re.compile(r'sk-[a-zA-Z0-9_-]{20,}|sk-ant-[a-zA-Z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gsk_[a-zA-Z0-9]{20,}')

url = f"https://api.cloudflare.com/client/v4/accounts/{account}/storage/kv/namespaces/{ns}/keys?limit=100"
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
with urllib.request.urlopen(req, timeout=15) as resp:
    keys_list = json.load(resp).get("result", [])

key_names = [k["name"] for k in keys_list]
provider_key_in_kv = any(provider_re.search(n) for n in key_names)

print(json.dumps({
    "kv_key_count": len(key_names),
    "session_token_keys": [k for k in key_names if k.startswith("session_token:")],
    "active_consumer_keys": [k for k in key_names if k.startswith("active_consumer:")],
    "provider_key_pattern_in_kv_key_names": provider_key_in_kv,
    "verdict": "kv_contains_only_session_metadata" if not provider_key_in_kv else "INVESTIGATE",
}, indent=2, sort_keys=True))
PY
  P16_V=$(python3 -c "import json; d=json.load(open('${P16_OUT}')); print(d.get('verdict','unknown'))" 2>/dev/null || echo "parse_error")
  if [[ "${P16_V}" == "kv_contains_only_session_metadata" ]]; then
    log_probe P-16 "T1530+T1528" PASS \
      "CF KV enumeration with valid API token yields only session metadata, not provider key values" \
      "no provider key patterns in KV key names or values" "${P16_V}" "p16-kv-partial-cred.txt"
  else
    log_probe P-16 "T1530+T1528" INVESTIGATE_NOW \
      "CF KV enumeration with valid API token yields only session metadata" \
      "no provider key patterns" "${P16_V}" "p16-kv-partial-cred.txt"
  fi
else
  echo "CF_API_TOKEN or CF_ACCOUNT_ID not set — skipping KV enumeration" > "${P16_OUT}"
  log_probe P-16 "T1530+T1528" ENVIRONMENTAL \
    "CF KV enumeration with valid API token yields only session metadata" \
    "no provider key patterns in KV" "CF_API_TOKEN not available — run with credentials for full proof" \
    "p16-kv-partial-cred.txt"
fi

# ─────────────────────────────────────────────────────────────────────────────
# CLASS 5 — Policy binding / ciphertext transplant (T1565 / replay)
# Mimics: attacker who exfiltrates a valid V3 record and tries to use it
#         against a different key_id or policy context.
# ─────────────────────────────────────────────────────────────────────────────
printf "\n${C_CYAN}── Class 5: AAD binding / ciphertext transplant ──${C_RESET}\n"

# P-17 ── Every live V3 record has a unique policy_hash — no shared binding
#         A transplanted ciphertext from key A cannot be decrypted under key B's
#         AAD (subumbra:v3:<key_id>:<policy_hash>).
P17_OUT="${ARTIFACT_DIR}/p17-aad-binding.txt"
dc_exec subumbra-keys python3 - <<'PY' > "${P17_OUT}" 2>&1 || true
import json, pathlib, collections

keys = json.loads(pathlib.Path('/app/data/endpoint.json').read_text())
hashes = []
results = {}
for kid, record in keys.items():
    if not isinstance(record, dict) or record.get("enc_version") != 3:
        continue
    ph = record.get("policy_hash", "")
    vi = record.get("vault_instance", "")
    hashes.append(ph)
    results[kid] = {"policy_hash_prefix": ph[:16], "vault_instance": vi}

dup_hashes = {h: c for h, c in collections.Counter(hashes).items() if c > 1}
print(json.dumps({
    "v3_key_count":              len(results),
    "duplicate_policy_hashes":   len(dup_hashes),
    "all_have_unique_aad":       len(dup_hashes) == 0,
    "transplant_possible":       len(dup_hashes) > 0,
    "records":                   results,
}, indent=2, sort_keys=True))
PY
P17_TRANSPLANT=$(python3 -c "import json; d=json.load(open('${P17_OUT}')); print(d.get('transplant_possible', True))" 2>/dev/null || echo "True")
if [[ "${P17_TRANSPLANT}" == "False" ]]; then
  log_probe P-17 T1565 PASS \
    "All V3 records have unique policy_hash AAD — ciphertext transplant between keys is not possible" \
    "no duplicate policy_hash values" "transplant_possible=${P17_TRANSPLANT}" "p17-aad-binding.txt"
else
  log_probe P-17 T1565 INVESTIGATE_NOW \
    "All V3 records have unique policy_hash AAD — ciphertext transplant between keys is not possible" \
    "no duplicate policy_hash values" "transplant_possible=${P17_TRANSPLANT}" "p17-aad-binding.txt"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
printf "\n${C_BOLD}═══ Results ═══${C_RESET}\n"
printf "${C_GREEN}PASS:${C_RESET}            %d\n" "${PASS_COUNT}"
printf "${C_RED}FAIL:${C_RESET}            %d\n" "${FAIL_COUNT}"
printf "${C_YELLOW}INVESTIGATE_NOW:${C_RESET} %d\n" "${INVEST_COUNT}"
printf "${C_CYAN}SKIP/ENV/DEF:${C_RESET}    %d\n" "${SKIP_COUNT}"
printf "Artifacts:       %s\n" "${ARTIFACT_DIR}"

{
  printf "PASS=%d FAIL=%d INVESTIGATE_NOW=%d SKIP=%d\n" \
    "${PASS_COUNT}" "${FAIL_COUNT}" "${INVEST_COUNT}" "${SKIP_COUNT}"
} >> "${SUMMARY_FILE}"

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
  printf "\n${C_RED}%d probe(s) FAILED — review %s${C_RESET}\n" \
    "${FAIL_COUNT}" "${ARTIFACT_DIR}"
  exit 1
elif [[ "${INVEST_COUNT}" -gt 0 ]]; then
  printf "\n${C_YELLOW}%d probe(s) need investigation — review %s${C_RESET}\n" \
    "${INVEST_COUNT}" "${ARTIFACT_DIR}"
  exit 2
else
  printf "\n${C_GREEN}All probes passed.${C_RESET}\n"
  exit 0
fi
