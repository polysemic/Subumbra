#!/usr/bin/env python3
"""Summarize Subumbra adapter tokens and allowed key_ids from a repo-local .env file.

Reads SUBUMBRA_ADAPTER_REGISTRY plus matching SUBUMBRA_TOKEN_* lines.
When stdout is a TTY, prints full token values after a one-line stderr warning.
When stdout is not a TTY, prints only env var names and key_ids (no token values).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _token_env_key(adapter_id: str) -> str:
    if adapter_id == "subumbra-proxy":
        return "SUBUMBRA_TOKEN_PROXY"
    if adapter_id == "subumbra-ui":
        return "SUBUMBRA_TOKEN_UI"
    if adapter_id == "subumbra-probe":
        return "SUBUMBRA_TOKEN_PROBE"
    return f"SUBUMBRA_TOKEN_{adapter_id.upper().replace('-', '_')}"


def _parse_env_file(path: Path) -> dict[str, str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("SUBUMBRA_ADAPTER_REGISTRY="):
            out["SUBUMBRA_ADAPTER_REGISTRY"] = line[len("SUBUMBRA_ADAPTER_REGISTRY=") :]
            continue
        m = re.match(r"^(SUBUMBRA_TOKEN_[A-Z0-9_]+)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def _sort_adapter_ids(adapter_ids: list[str]) -> list[str]:
    builtins = ("subumbra-proxy", "subumbra-ui", "subumbra-probe")
    first = [a for a in builtins if a in adapter_ids]
    rest = sorted(a for a in adapter_ids if a not in builtins)
    return first + rest


def main() -> int:
    env_path = Path(sys.argv[1] if len(sys.argv) > 1 else ".env").resolve()
    if not env_path.is_file():
        print(f"error: env file not found: {env_path}", file=sys.stderr)
        return 1

    env = _parse_env_file(env_path)
    reg_raw = env.get("SUBUMBRA_ADAPTER_REGISTRY", "").strip()
    if not reg_raw:
        print("error: SUBUMBRA_ADAPTER_REGISTRY missing or empty in .env", file=sys.stderr)
        return 1
    try:
        registry = json.loads(reg_raw)
    except json.JSONDecodeError as exc:
        print(f"error: SUBUMBRA_ADAPTER_REGISTRY is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(registry, dict) or not registry:
        print("error: SUBUMBRA_ADAPTER_REGISTRY must be a non-empty JSON object", file=sys.stderr)
        return 1

    show_tokens = sys.stdout.isatty()
    if show_tokens:
        print(
            "⚠  Printing adapter token material to your terminal — avoid shared sessions "
            "and clear scrollback if needed.",
            file=sys.stderr,
            flush=True,
        )

    print()
    print("Adapter tokens and allowed key_ids (from .env)")
    print("─" * 72)
    for adapter_id in _sort_adapter_ids(list(registry.keys())):
        entry = registry[adapter_id]
        if not isinstance(entry, dict):
            continue
        keys = entry.get("allowed_keys")
        if not isinstance(keys, list):
            keys = []
        key_list = ", ".join(str(k) for k in keys if k)
        tok_key = _token_env_key(adapter_id)
        token = env.get(tok_key, "")
        if show_tokens:
            tok_disp = token if token else "(missing in .env)"
        else:
            tok_disp = "(omitted — not a TTY; open .env or re-run in a terminal)"
        print(f"  adapter:     {adapter_id}")
        print(f"  env var:     {tok_key}")
        print(f"  key_ids:     {key_list or '(none)'}")
        print(f"  token:       {tok_disp}")
        print("─" * 72)
    print(f"  source file: {env_path}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
