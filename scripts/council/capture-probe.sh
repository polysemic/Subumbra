#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/council/capture-probe.sh <probe-name> [options] -- <command...>

Options:
  --hypothesis <text>      What bug, bypass, or regression this probe checks
  --scope-link <text>      Why the probe is related to the current round
  --expected <text>        Expected secure behavior
  --classification <name>  PASS, FAIL, INVESTIGATE_NOW, DEFERRED, HARNESS_ISSUE, or ENVIRONMENTAL
  --artifact-dir <path>    Defaults to $PROBE_ARTIFACT_DIR, $VERIFY_ARTIFACT_DIR/independent-probes, or ./independent-probes
  --fail-on-error          Return the wrapped command's exit code instead of 0

The command is executed once. stdout, stderr, exit code, metadata, and a JSONL
index entry are written under the probe artifact directory.
EOF
}

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 1
fi

probe_name="$1"
shift
hypothesis=""
scope_link=""
expected=""
classification="PASS"
artifact_dir="${PROBE_ARTIFACT_DIR:-}"
fail_on_error=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hypothesis)
            hypothesis="${2:-}"
            shift 2
            ;;
        --scope-link)
            scope_link="${2:-}"
            shift 2
            ;;
        --expected)
            expected="${2:-}"
            shift 2
            ;;
        --classification)
            classification="${2:-}"
            shift 2
            ;;
        --artifact-dir)
            artifact_dir="${2:-}"
            shift 2
            ;;
        --fail-on-error)
            fail_on_error=1
            shift
            ;;
        --)
            shift
            break
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 1
            ;;
    esac
done

if [[ $# -eq 0 ]]; then
    echo "ERROR: probe command required after --" >&2
    usage >&2
    exit 1
fi

case "$classification" in
    PASS|FAIL|INVESTIGATE_NOW|DEFERRED|HARNESS_ISSUE|ENVIRONMENTAL) ;;
    *)
        echo "ERROR: unsupported classification: $classification" >&2
        exit 1
        ;;
esac

if [[ -z "$artifact_dir" ]]; then
    if [[ -n "${VERIFY_ARTIFACT_DIR:-}" ]]; then
        artifact_dir="${VERIFY_ARTIFACT_DIR}/independent-probes"
    else
        artifact_dir="./independent-probes"
    fi
fi

safe_name="$(printf '%s' "$probe_name" | tr -c 'A-Za-z0-9_.-' '_' | sed 's/^_*//; s/_*$//')"
if [[ -z "$safe_name" ]]; then
    echo "ERROR: probe name must contain at least one safe character" >&2
    exit 1
fi

probe_dir="${artifact_dir}/${safe_name}"
mkdir -p "$probe_dir"

stdout_file="${probe_dir}/stdout.txt"
stderr_file="${probe_dir}/stderr.txt"
command_file="${probe_dir}/command.txt"
meta_file="${probe_dir}/metadata.json"
index_file="${artifact_dir}/probes.jsonl"

printf '%q ' "$@" > "$command_file"
printf '\n' >> "$command_file"

started_at="$(date -Is)"
exit_code=0
"$@" >"$stdout_file" 2>"$stderr_file" || exit_code=$?
finished_at="$(date -Is)"

python3 - "$meta_file" "$index_file" "$probe_name" "$hypothesis" "$scope_link" "$expected" "$classification" "$exit_code" "$started_at" "$finished_at" "$probe_dir" "$command_file" "$stdout_file" "$stderr_file" <<'PY'
import json
import sys

(
    meta_file,
    index_file,
    probe_name,
    hypothesis,
    scope_link,
    expected,
    classification,
    exit_code,
    started_at,
    finished_at,
    probe_dir,
    command_file,
    stdout_file,
    stderr_file,
) = sys.argv[1:15]

data = {
    "probe": probe_name,
    "hypothesis": hypothesis,
    "scope_link": scope_link,
    "expected": expected,
    "classification": classification,
    "exit_code": int(exit_code),
    "started_at": started_at,
    "finished_at": finished_at,
    "artifact_paths": {
        "directory": probe_dir,
        "command": command_file,
        "stdout": stdout_file,
        "stderr": stderr_file,
        "metadata": meta_file,
    },
}

with open(meta_file, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")

with open(index_file, "a", encoding="utf-8") as fh:
    json.dump(data, fh)
    fh.write("\n")
PY

if [[ "$fail_on_error" -eq 1 ]]; then
    exit "$exit_code"
fi
exit 0
