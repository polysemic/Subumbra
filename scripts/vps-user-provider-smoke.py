#!/usr/bin/env python3
"""
VPS-only: exercise every Subumbra key_id through the real transparent proxy path.

Run from repo root on the VPS (e.g. cd /opt/subumbra && python3 scripts/vps-user-provider-smoke.py).

- Uses `docker compose exec` to read keys.json and SUBUMBRA_ADAPTER_REGISTRY from
  running containers (no `source .env` — avoids JSON-in-shell issues).
- For each key_id, picks the first non-expired adapter whose allowed_keys contains it.
- Sends one minimal live request per provider (real upstream API key via Worker).

Exit 0 only if every resolvable (adapter, key_id) pair returns HTTP 2xx from the proxy.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


def run_dc(args: list[str], *, input_bytes: bytes | None = None) -> bytes:
    cmd = ["docker", "compose", *args]
    p = subprocess.run(cmd, capture_output=True, input=input_bytes)
    if p.returncode != 0:
        sys.stderr.buffer.write(p.stderr or b"")
        raise SystemExit(
            f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr.decode(errors='replace')}"
        )
    return p.stdout


def proxy_base() -> str:
    port = os.environ.get("SUBUMBRA_PROXY_HOST_PORT", "").strip()
    if not port:
        out = run_dc(["port", "subumbra-proxy", "8090"]).decode().strip()
        # e.g. 127.0.0.1:10199
        if ":" in out:
            port = out.rsplit(":", 1)[-1].strip()
    if not port or not port.isdigit():
        raise SystemExit(
            "Could not resolve proxy port; set SUBUMBRA_PROXY_HOST_PORT or run from a compose project "
            "where `docker compose port subumbra-proxy 8090` works."
        )
    return f"http://127.0.0.1:{port}"


def load_keys() -> dict[str, Any]:
    raw = run_dc(["exec", "-T", "subumbra-keys", "cat", "/app/data/keys.json"])
    return json.loads(raw.decode())


def load_registry() -> dict[str, Any]:
    raw = run_dc(
        [
            "exec",
            "-T",
            "subumbra-proxy",
            "python3",
            "-c",
            "import json, os; print(os.environ['SUBUMBRA_ADAPTER_REGISTRY'])",
        ]
    )
    return json.loads(raw.decode())


def parse_iso_z(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def adapter_allows_key(registry: dict[str, Any], key_id: str) -> tuple[str, str] | None:
    """Return (adapter_id, token) for first adapter that lists key_id and is not expired."""
    now = datetime.now(timezone.utc)
    for adapter_id, cfg in registry.items():
        if not isinstance(cfg, dict):
            continue
        allowed = cfg.get("allowed_keys") or []
        if key_id not in allowed:
            continue
        try:
            exp = parse_iso_z(str(cfg["expires_at"]))
        except (KeyError, TypeError, ValueError):
            continue
        if exp <= now:
            continue
        token = cfg.get("token")
        if isinstance(token, str) and token:
            return adapter_id, token
    return None


def curl_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: int = 60,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def request_for_provider(provider: str, base: str, key_id: str, token: str) -> tuple[str, str, int, str]:
    """
    Returns (label, url, http_code, snippet) where snippet is short error hint.
    """
    auth = f"Bearer {token}"
    p = (provider or "").lower().strip()

    if p == "anthropic":
        url = f"{base}/t/{key_id}/v1/messages"
        payload = json.dumps(
            {
                "model": "claude-3-5-haiku-20241022",
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "ping"}],
            }
        ).encode()
        headers = {
            "Authorization": auth,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        code, body = curl_json("POST", url, headers=headers, body=payload)
        return "POST /v1/messages", url, code, body[:200].decode(errors="replace")

    if p == "groq":
        url = f"{base}/t/{key_id}/openai/v1/models"
        headers = {"Authorization": auth}
        code, body = curl_json("GET", url, headers=headers)
        return "GET /openai/v1/models", url, code, body[:120].decode(errors="replace")

    if p == "openrouter":
        url = f"{base}/t/{key_id}/api/v1/models"
        headers = {"Authorization": auth}
        code, body = curl_json("GET", url, headers=headers)
        return "GET /api/v1/models", url, code, body[:120].decode(errors="replace")

    if p in ("google", "gemini"):
        return (
            "SKIP",
            "",
            0,
            "google/gemini OpenAI-compat path is known broken on transparent sidecar (see docs/provider-matrix.md GEMINI-PATH).",
        )

    # OpenAI-compatible default (openai, mistral, deepseek, together, xai, cerebras, …)
    url = f"{base}/t/{key_id}/v1/models"
    headers = {"Authorization": auth}
    code, body = curl_json("GET", url, headers=headers)
    return "GET /v1/models", url, code, body[:120].decode(errors="replace")


def main() -> None:
    os.chdir(os.environ.get("SUBUMBRA_SMOKE_REPO_ROOT", os.getcwd()))
    base = proxy_base()
    keys = load_keys()
    registry = load_registry()

    print(f"# proxy base: {base}")
    print(f"# keys.json entries: {len(keys)}")

    failures = 0
    skips = 0
    for key_id in sorted(keys.keys()):
        rec = keys[key_id]
        provider = rec.get("provider", "")
        pair = adapter_allows_key(registry, key_id)
        if pair is None:
            print(f"SKIP {key_id} reason=no_adapter_allowed_this_key")
            skips += 1
            continue
        adapter_id, token = pair
        label, url, code, snippet = request_for_provider(provider, base, key_id, token)
        if label == "SKIP":
            print(f"SKIP {key_id} provider={provider} adapter={adapter_id} — {snippet}")
            skips += 1
            continue
        ok = 200 <= code < 300
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"{status} key_id={key_id} provider={provider} adapter={adapter_id} {label} http={code}")
        if not ok:
            print(f"       url={url}")
            print(f"       body_prefix={snippet!r}")

    if failures:
        raise SystemExit(f"exit 1 — {failures} failing provider(s); {skips} skipped")
    print(f"# done: 0 failures, {skips} skipped")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
