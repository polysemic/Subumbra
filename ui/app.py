"""
KeyVault UI — management dashboard
────────────────────────────────────
Read-only dashboard; never exposes or fetches key values.
Talks to forge-keys over the Docker internal network.

Routes:
  GET /           → dashboard HTML
  GET /api/status → aggregated JSON (health + keys + stats)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx
from flask import Flask, jsonify, render_template

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

FORGE_URL          = os.environ.get("FORGE_URL",          "http://forge-keys:9090").rstrip("/")
FORGE_ACCESS_TOKEN = os.environ.get("FORGE_ACCESS_TOKEN", "")

if not FORGE_ACCESS_TOKEN:
    logging.warning("ui: FORGE_ACCESS_TOKEN not set — dashboard will show errors")

# ─────────────────────────────────────────────────────────────────────────────
# HTTP client (shared, connection-pooled)
# ─────────────────────────────────────────────────────────────────────────────

_http = httpx.Client(
    timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0),
    headers={"X-Forge-Token": FORGE_ACCESS_TOKEN},
)

# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
log = logging.getLogger("keyvault-ui")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)


def _forge_get(path: str) -> tuple[dict | list | None, str | None]:
    """
    GET {FORGE_URL}{path}.  Returns (data, error_string).
    error_string is None on success.
    """
    try:
        r = _http.get(f"{FORGE_URL}{path}")
        r.raise_for_status()
        return r.json(), None
    except httpx.HTTPStatusError as e:
        return None, f"forge-keys returned {e.response.status_code}"
    except httpx.RequestError as e:
        return None, f"forge-keys unreachable: {type(e).__name__}"


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    """
    Aggregate health + key list + stats into one response for the dashboard.

    Shape:
    {
      "forge_healthy":   bool,
      "forge_error":     str | null,
      "stats_available": bool,   // false → request counts/last_access may be stale
      "keys_loaded":     int,
      "keys": [
        {
          "key_id":        str,
          "provider":      str,
          "created_at":    str,
          "request_count": int,
          "last_access":   str | null
        }, ...
      ],
      "recent_log": [
        {
          "timestamp": str,
          "key_id":    str,
          "remote":    str,
          "status":    str
        }, ...
      ],
      "dashboard_time": str   // server-side ISO timestamp
    }
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ── health ────────────────────────────────────────────────────────────────
    health_data, health_err = _forge_get("/health")
    forge_healthy = health_err is None and (health_data or {}).get("status") == "ok"

    # ── key list ──────────────────────────────────────────────────────────────
    keys_data, keys_err = _forge_get("/keys")
    keys_list = (keys_data or {}).get("keys", []) if keys_err is None else []

    # ── stats (request counts + recent log) ───────────────────────────────────
    stats_data, stats_err = _forge_get("/stats")
    stats_map: dict[str, dict] = {}
    recent_log: list[dict] = []

    if stats_err is None and stats_data:
        for entry in stats_data.get("per_key", []):
            stats_map[entry["key_id"]] = entry
        recent_log = stats_data.get("recent_log", [])

    # ── merge key list + stats ────────────────────────────────────────────────
    merged_keys = []
    for key in keys_list:
        kid = key["key_id"]
        s   = stats_map.get(kid, {})
        merged_keys.append({
            "key_id":        kid,
            "provider":      key.get("provider", "unknown"),
            "created_at":    key.get("created_at", ""),
            "request_count": s.get("request_count", 0),
            "last_access":   s.get("last_access"),
        })

    # Sort: most recently used first, never-used last, then alphabetically.
    # Two-pass stable sort: secondary (alpha) first, then primary (newest first).
    # last_access or "" gives "" for None; with reverse=True "" sorts last since
    # "" < any ISO timestamp string.
    merged_keys.sort(key=lambda k: k["key_id"])                           # secondary
    merged_keys.sort(key=lambda k: k["last_access"] or "", reverse=True)  # primary

    error = health_err or keys_err
    return jsonify({
        "forge_healthy":   forge_healthy,
        "forge_error":     error,
        "stats_available": stats_err is None,
        "keys_loaded":     len(merged_keys),
        "keys":            merged_keys,
        "recent_log":      list(reversed(recent_log)),  # newest first
        "dashboard_time":  now,
    })
