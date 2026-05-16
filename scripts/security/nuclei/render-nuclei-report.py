#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: render-nuclei-report.py <jsonl> <output-md> <target-url>", file=sys.stderr)
        return 1

    jsonl_path = pathlib.Path(sys.argv[1])
    output_path = pathlib.Path(sys.argv[2])
    target_url = sys.argv[3]

    findings = []
    if jsonl_path.exists():
      for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            findings.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    severity_counts = Counter()
    for finding in findings:
        info = finding.get("info") or {}
        severity_counts[(info.get("severity") or "unknown").lower()] += 1

    lines = [
        "# Nuclei Report",
        "",
        f"- Target: `{target_url}`",
        f"- Findings: `{len(findings)}`",
        "",
        "## Severity Summary",
        "",
    ]

    if severity_counts:
        for severity in ("critical", "high", "medium", "low", "info", "unknown"):
            if severity_counts.get(severity):
                lines.append(f"- {severity}: {severity_counts[severity]}")
    else:
        lines.append("- No findings in this run.")

    lines.extend(["", "## Findings", ""])

    if not findings:
        lines.append("No findings were reported by Nuclei.")
    else:
        for finding in findings[:100]:
            info = finding.get("info") or {}
            lines.append(f"### {finding.get('template-id', 'unknown-template')}")
            lines.append("")
            lines.append(f"- Name: {info.get('name', 'unknown')}")
            lines.append(f"- Severity: {(info.get('severity') or 'unknown').lower()}")
            if finding.get("matched-at"):
                lines.append(f"- Matched At: `{finding['matched-at']}`")
            if info.get("description"):
                lines.append(f"- Description: {info['description']}")
            if finding.get("curl-command"):
                lines.append("- Reproduction: omitted from repo-published summary by default.")
            lines.append("")

        if len(findings) > 100:
            lines.append(f"_Truncated to first 100 findings out of {len(findings)} total._")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
