#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter


def severity(result: dict) -> str:
    extra = result.get("extra") or {}
    metadata = extra.get("metadata") or {}
    value = extra.get("severity") or metadata.get("impact") or "INFO"
    return str(value).upper()


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: render-semgrep-report.py <semgrep-json> <output-md> <target-label>", file=sys.stderr)
        return 1

    json_path = pathlib.Path(sys.argv[1])
    output_path = pathlib.Path(sys.argv[2])
    target_label = sys.argv[3]

    data = json.loads(json_path.read_text(encoding="utf-8", errors="replace")) if json_path.exists() else {}
    results = data.get("results") or []
    errors = data.get("errors") or []
    counts = Counter(severity(result) for result in results)

    lines = [
        "# Semgrep Report",
        "",
        f"- Target: `{target_label}`",
        f"- Findings: `{len(results)}`",
        f"- Engine errors: `{len(errors)}`",
        "",
        "## Severity Summary",
        "",
    ]

    if counts:
        for key in ("ERROR", "WARNING", "INFO", "CRITICAL", "HIGH", "MEDIUM", "LOW"):
            if counts.get(key):
                lines.append(f"- {key.lower()}: {counts[key]}")
    else:
        lines.append("- No findings in this run.")

    if errors:
        lines.extend(["", "## Engine Errors", ""])
        for error in errors[:20]:
            lines.append(f"- `{error.get('code', 'unknown')}`: {error.get('message', 'unknown error')}")
        if len(errors) > 20:
            lines.append(f"- Truncated to first 20 errors out of {len(errors)} total.")

    lines.extend(["", "## Findings", ""])

    if not results:
        lines.append("No findings were reported by Semgrep.")
    else:
        for result in results[:100]:
            extra = result.get("extra") or {}
            metadata = extra.get("metadata") or {}
            path = result.get("path", "unknown")
            start = result.get("start") or {}
            check_id = result.get("check_id", "unknown-rule")
            message = extra.get("message", "")
            sev = severity(result).lower()

            lines.append(f"### {check_id}")
            lines.append("")
            lines.append(f"- Severity: {sev}")
            lines.append(f"- Location: `{path}:{start.get('line', '?')}`")
            if message:
                lines.append(f"- Message: {message}")
            if metadata.get("cwe"):
                lines.append(f"- CWE: {metadata['cwe']}")
            if metadata.get("owasp"):
                lines.append(f"- OWASP: {metadata['owasp']}")
            lines.append("")

        if len(results) > 100:
            lines.append(f"_Truncated to first 100 findings out of {len(results)} total._")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
