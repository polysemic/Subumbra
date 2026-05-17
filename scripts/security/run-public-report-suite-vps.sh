#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

REMOTE_HOST="${REMOTE_HOST:-subumbra}"
REMOTE_REPO="${REMOTE_REPO:-/opt/subumbra}"
BRANCH="${BRANCH:-main}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SUITE_NAME="${SUITE_NAME:-public-security-suite-${TIMESTAMP}}"
REMOTE_ROOT="${REMOTE_ROOT:-\$HOME/security-scan-workspaces/${SUITE_NAME}}"
LOCAL_STAGE="$(mktemp -d "${TMPDIR:-/tmp}/${SUITE_NAME}.XXXXXX")"
INCLUDE_WEB_SCANS="${INCLUDE_WEB_SCANS:-1}"

cleanup() {
  rm -rf "$LOCAL_STAGE"
}
trap cleanup EXIT

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

require_cmd ssh
require_cmd scp
require_cmd python3

echo "Running public security suite on VPS"
echo "  host:   $REMOTE_HOST"
echo "  repo:   $REMOTE_REPO"
echo "  branch: $BRANCH"
echo "  suite:  $SUITE_NAME"
echo
echo "This uses a clean staging clone under ~/ on the VPS so the scans do not dirty /opt/subumbra."

ssh "$REMOTE_HOST" \
  "REMOTE_REPO='$REMOTE_REPO' BRANCH='$BRANCH' SUITE_NAME='$SUITE_NAME' REMOTE_ROOT='$REMOTE_ROOT' INCLUDE_WEB_SCANS='$INCLUDE_WEB_SCANS' bash -s" <<'REMOTE'
set -euo pipefail

remote_repo="${REMOTE_REPO:?}"
branch="${BRANCH:?}"
suite_name="${SUITE_NAME:?}"
remote_root="${REMOTE_ROOT:?}"
include_web_scans="${INCLUDE_WEB_SCANS:-1}"

stage_dir="${remote_root}/repo"
publish_dir="${remote_root}/publish"
status_file="${remote_root}/suite-status.tsv"
mkdir -p "${publish_dir}"
: > "${status_file}"

git -C "${remote_repo}" fetch origin
git -C "${remote_repo}" checkout "${branch}" >/dev/null 2>&1
git -C "${remote_repo}" pull --ff-only origin "${branch}" >/dev/null 2>&1

rm -rf "${stage_dir}"
git clone --local --branch "${branch}" "${remote_repo}" "${stage_dir}" >/dev/null 2>&1

target_url=""
if [[ -f "${stage_dir}/.env" ]]; then
  target_url="$(sed -n 's/^CF_WORKER_URL=//p' "${stage_dir}/.env" | head -n1)"
fi

write_log_summary() {
  local tool_slug="$1"
  local title="$2"
  local command_text="$3"
  local scope_text="$4"
  local exit_code="$5"
  local raw_hint="$6"
  local log_file="$7"
  local out_file="${publish_dir}/${tool_slug}.md"

  {
    printf '# %s Report — %s\n\n' "$title" "$(date -u +%Y-%m-%d)"
    printf '## Scope\n%s\n\n' "$scope_text"
    printf '## Command / Profile\n```bash\n%s\n```\n\n' "$command_text"
    printf '## Result\n- Exit code: `%s`\n' "$exit_code"
    if [[ "$exit_code" == "0" ]]; then
      printf -- '- Status: `PASS`\n\n'
    else
      printf -- '- Status: `REVIEW REQUIRED`\n\n'
    fi
    printf '## Raw Artifacts\n- `%s`\n\n' "$raw_hint"
    printf '## Console Summary\n```text\n'
    tail -n 200 "$log_file" || true
    printf '\n```\n'
  } > "${out_file}"
}

copy_existing_md() {
  local source_file="$1"
  local tool_slug="$2"
  local title="$3"
  local scope_text="$4"
  local command_text="$5"
  local exit_code="$6"
  local out_file="${publish_dir}/${tool_slug}.md"

  {
    printf '# %s Report — %s\n\n' "$title" "$(date -u +%Y-%m-%d)"
    printf '## Scope\n%s\n\n' "$scope_text"
    printf '## Command / Profile\n```bash\n%s\n```\n\n' "$command_text"
    printf '## Result\n- Exit code: `%s`\n' "$exit_code"
    if [[ "$exit_code" == "0" ]]; then
      printf -- '- Status: `PASS`\n\n'
    else
      printf -- '- Status: `REVIEW REQUIRED`\n\n'
    fi
    printf '## Sanitized Report Body\n\n'
    cat "$source_file"
    printf '\n'
  } > "${out_file}"
}

run_step() {
  local tool_slug="$1"
  local title="$2"
  local command_text="$3"
  local raw_hint="$4"
  local scope_text="$5"
  shift 5

  local log_file="${remote_root}/${tool_slug}.log"
  local exit_code=0

  echo
  echo "=== ${title} ==="
  (
    cd "${stage_dir}"
    set +e
    "$@" 2>&1 | tee "${log_file}"
    exit_code="${PIPESTATUS[0]}"
    set -e
    exit "${exit_code}"
  ) || exit_code=$?

  printf '%s\t%s\n' "${tool_slug}" "${exit_code}" >> "${status_file}"
  write_log_summary "${tool_slug}" "${title}" "${command_text}" "${scope_text}" "${exit_code}" "${raw_hint}" "${log_file}"
}

run_step \
  "gitleaks" \
  "Gitleaks" \
  "bash scripts/security/gitleaks/scan.sh" \
  "scripts/security/gitleaks/reports/gitleaks-report.json" \
  "Git history and tracked source in a clean VPS clone of ${remote_repo} on branch ${branch}." \
  bash scripts/security/gitleaks/scan.sh

run_step \
  "bandit" \
  "Bandit" \
  "bash scripts/security/bandit/scan.sh" \
  "scripts/security/bandit/reports/bandit-report.json and bandit-report.html" \
  "Python components in the clean VPS clone of ${remote_repo} on branch ${branch}." \
  bash scripts/security/bandit/scan.sh

run_step \
  "pip-audit" \
  "pip-audit" \
  "bash scripts/security/pip-audit/scan.sh" \
  "scripts/security/pip-audit/reports/*.json" \
  "Pinned Python dependency sets in the clean VPS clone of ${remote_repo} on branch ${branch}." \
  bash scripts/security/pip-audit/scan.sh

run_step \
  "trivy" \
  "Trivy" \
  "bash scripts/security/trivy/scan.sh" \
  "scripts/security/trivy/reports/trivy-fs-report.json" \
  "Filesystem, dependency, secret, and misconfiguration scan of the clean VPS clone of ${remote_repo} on branch ${branch}." \
  bash scripts/security/trivy/scan.sh

if [[ "${include_web_scans}" == "1" ]]; then
  semgrep_base_dir="${remote_root}/semgrep-baseline"
  semgrep_secrets_dir="${remote_root}/semgrep-secrets"
  nuclei_dir="${remote_root}/nuclei-web-lite"
  zap_dir="${remote_root}/zap-baseline"

  set +e
  (cd "${stage_dir}" && STAGE_DIR="${stage_dir}" SEMGREP_DIR="${remote_root}/semgrep-base-root" RUN_NAME="semgrep-baseline" OUTPUT_DIR="${semgrep_base_dir}" bash scripts/security/run-semgrep-vps.sh baseline) > "${remote_root}/semgrep-baseline.log" 2>&1
  semgrep_baseline_exit=$?
  set -e
  printf 'semgrep-baseline\t%s\n' "${semgrep_baseline_exit}" >> "${status_file}"
  if [[ -f "${semgrep_base_dir}/semgrep-report.md" ]]; then
    copy_existing_md \
      "${semgrep_base_dir}/semgrep-report.md" \
      "semgrep-baseline" \
      "Semgrep Baseline" \
      "Semgrep baseline rules against the clean VPS clone of ${remote_repo} on branch ${branch}." \
      "STAGE_DIR=${stage_dir} bash scripts/security/run-semgrep-vps.sh baseline" \
      "${semgrep_baseline_exit}"
  else
    write_log_summary \
      "semgrep-baseline" \
      "Semgrep Baseline" \
      "STAGE_DIR=${stage_dir} bash scripts/security/run-semgrep-vps.sh baseline" \
      "Semgrep baseline rules against the clean VPS clone of ${remote_repo} on branch ${branch}." \
      "${semgrep_baseline_exit}" \
      "${semgrep_base_dir}/semgrep-report.md" \
      "${remote_root}/semgrep-baseline.log"
  fi

  set +e
  (cd "${stage_dir}" && STAGE_DIR="${stage_dir}" SEMGREP_DIR="${remote_root}/semgrep-secrets-root" RUN_NAME="semgrep-secrets" OUTPUT_DIR="${semgrep_secrets_dir}" bash scripts/security/run-semgrep-vps.sh secrets) > "${remote_root}/semgrep-secrets.log" 2>&1
  semgrep_secrets_exit=$?
  set -e
  printf 'semgrep-secrets\t%s\n' "${semgrep_secrets_exit}" >> "${status_file}"
  if [[ -f "${semgrep_secrets_dir}/semgrep-report.md" ]]; then
    copy_existing_md \
      "${semgrep_secrets_dir}/semgrep-report.md" \
      "semgrep-secrets" \
      "Semgrep Secrets" \
      "Semgrep secret-detection rules against the clean VPS clone of ${remote_repo} on branch ${branch}." \
      "STAGE_DIR=${stage_dir} bash scripts/security/run-semgrep-vps.sh secrets" \
      "${semgrep_secrets_exit}"
  else
    write_log_summary \
      "semgrep-secrets" \
      "Semgrep Secrets" \
      "STAGE_DIR=${stage_dir} bash scripts/security/run-semgrep-vps.sh secrets" \
      "Semgrep secret-detection rules against the clean VPS clone of ${remote_repo} on branch ${branch}." \
      "${semgrep_secrets_exit}" \
      "${semgrep_secrets_dir}/semgrep-report.md" \
      "${remote_root}/semgrep-secrets.log"
  fi

  set +e
  (cd "${stage_dir}" && STAGE_DIR="${stage_dir}" NUCLEI_DIR="${remote_root}/nuclei-root" RUN_NAME="nuclei-web-lite" OUTPUT_DIR="${nuclei_dir}" bash scripts/security/run-nuclei-vps.sh web-lite) > "${remote_root}/nuclei-web-lite.log" 2>&1
  nuclei_exit=$?
  set -e
  printf 'nuclei-web-lite\t%s\n' "${nuclei_exit}" >> "${status_file}"
  if [[ -f "${nuclei_dir}/nuclei-report.md" ]]; then
    copy_existing_md \
      "${nuclei_dir}/nuclei-report.md" \
      "nuclei-web-lite" \
      "Nuclei Web Lite" \
      "Low-rate public web scan against the live Worker URL from ${stage_dir}/.env." \
      "STAGE_DIR=${stage_dir} bash scripts/security/run-nuclei-vps.sh web-lite" \
      "${nuclei_exit}"
  else
    write_log_summary \
      "nuclei-web-lite" \
      "Nuclei Web Lite" \
      "STAGE_DIR=${stage_dir} bash scripts/security/run-nuclei-vps.sh web-lite" \
      "Low-rate public web scan against the live Worker URL from ${stage_dir}/.env." \
      "${nuclei_exit}" \
      "${nuclei_dir}/nuclei-report.md" \
      "${remote_root}/nuclei-web-lite.log"
  fi

  set +e
  (cd "${stage_dir}" && STAGE_DIR="${stage_dir}" ZAP_DIR="${remote_root}/zap-root" RUN_NAME="zap-baseline" OUTPUT_DIR="${zap_dir}" bash scripts/security/run-zap-vps.sh baseline) > "${remote_root}/zap-baseline.log" 2>&1
  zap_exit=$?
  set -e
  printf 'zap-baseline\t%s\n' "${zap_exit}" >> "${status_file}"
  if [[ -f "${zap_dir}/zap-report.md" ]]; then
    copy_existing_md \
      "${zap_dir}/zap-report.md" \
      "zap-baseline" \
      "ZAP Baseline" \
      "Baseline passive web scan against the live Worker URL from ${stage_dir}/.env." \
      "STAGE_DIR=${stage_dir} bash scripts/security/run-zap-vps.sh baseline" \
      "${zap_exit}"
  else
    write_log_summary \
      "zap-baseline" \
      "ZAP Baseline" \
      "STAGE_DIR=${stage_dir} bash scripts/security/run-zap-vps.sh baseline" \
      "Baseline passive web scan against the live Worker URL from ${stage_dir}/.env." \
      "${zap_exit}" \
      "${zap_dir}/zap-report.md" \
      "${remote_root}/zap-baseline.log"
  fi
fi

printf 'branch=%s\n' "${branch}" > "${remote_root}/suite-meta.txt"
printf 'remote_repo=%s\n' "${remote_repo}" >> "${remote_root}/suite-meta.txt"
printf 'stage_dir=%s\n' "${stage_dir}" >> "${remote_root}/suite-meta.txt"
printf 'target_url=%s\n' "${target_url}" >> "${remote_root}/suite-meta.txt"
printf 'suite_name=%s\n' "${suite_name}" >> "${remote_root}/suite-meta.txt"
REMOTE

mkdir -p "$LOCAL_STAGE/publish"
scp -r "${REMOTE_HOST}:${REMOTE_ROOT}/publish/." "$LOCAL_STAGE/publish/" >/dev/null
scp "${REMOTE_HOST}:${REMOTE_ROOT}/suite-status.tsv" "$LOCAL_STAGE/suite-status.tsv" >/dev/null
scp "${REMOTE_HOST}:${REMOTE_ROOT}/suite-meta.txt" "$LOCAL_STAGE/suite-meta.txt" >/dev/null

echo
echo "Fetched publish-ready markdown sources to:"
echo "  $LOCAL_STAGE/publish"

declare -A OUTPUT_NAMES=(
  ["gitleaks"]="gitleaks.md"
  ["bandit"]="bandit.md"
  ["pip-audit"]="pip-audit.md"
  ["trivy"]="trivy.md"
  ["semgrep-baseline"]="semgrep-baseline.md"
  ["semgrep-secrets"]="semgrep-secrets.md"
  ["nuclei-web-lite"]="nuclei-web-lite.md"
  ["zap-baseline"]="zap-baseline.md"
)

while IFS=$'\t' read -r tool_slug exit_code; do
  [[ -n "${tool_slug}" ]] || continue
  src_file="$LOCAL_STAGE/publish/${tool_slug}.md"
  if [[ ! -f "$src_file" ]]; then
    echo "WARN: missing publish source for ${tool_slug}: ${src_file}" >&2
    continue
  fi
  output_name="${OUTPUT_NAMES[$tool_slug]:-${tool_slug}.md}"
  "$SCRIPT_DIR/publish-report-file.sh" \
    "$src_file" \
    "$output_name" \
    "the VPS public security suite ${SUITE_NAME}"
done < "$LOCAL_STAGE/suite-status.tsv"

echo
echo "Suite status:"
cat "$LOCAL_STAGE/suite-status.tsv"
echo
echo "Remote suite metadata:"
cat "$LOCAL_STAGE/suite-meta.txt"
echo
echo "Published reports now live under:"
echo "  $REPO_ROOT/security/reports"

if awk -F '\t' '$2 != 0 { found = 1 } END { exit(found ? 0 : 1) }' "$LOCAL_STAGE/suite-status.tsv"; then
  echo
  echo "One or more scans exited non-zero. Review the published reports." >&2
  exit 1
fi

echo
echo "All scans completed with zero exit status."
