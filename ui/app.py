"""
Subumbra UI — management dashboard
────────────────────────────────────
Read-only dashboard; never exposes or fetches key values.
Talks to subumbra-keys over the Docker internal network.

Routes:
  GET /           → dashboard HTML
  GET /api/status → aggregated JSON (health + keys + stats + audit)
"""

from __future__ import annotations

import hmac
import logging
import os
from functools import wraps
from datetime import datetime, timezone

import httpx
from flask import Flask, Response, jsonify, render_template, request

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

SUBUMBRA_KEYS_URL = os.environ.get("SUBUMBRA_KEYS_URL", "http://subumbra-keys:9090").rstrip("/")
SUBUMBRA_ACCESS_TOKEN = os.environ.get("SUBUMBRA_ACCESS_TOKEN", "")
SUBUMBRA_PROXY_URL = os.environ.get("SUBUMBRA_PROXY_URL", "http://subumbra-proxy:8090").rstrip("/")
UI_USERNAME = os.environ.get("UI_USERNAME", "")
UI_PASSWORD = os.environ.get("UI_PASSWORD", "")

# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
log = logging.getLogger("subumbra-ui")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)

if not SUBUMBRA_ACCESS_TOKEN:
    logging.warning("subumbra-ui: SUBUMBRA_ACCESS_TOKEN not set — dashboard will show errors")
if not UI_USERNAME:
    log.info("ui: UI auth not configured; running unauthenticated (localhost only)")

# ─────────────────────────────────────────────────────────────────────────────
# HTTP client (shared, connection-pooled)
# ─────────────────────────────────────────────────────────────────────────────

_http = httpx.Client(
    timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0),
    headers={"X-Subumbra-Token": SUBUMBRA_ACCESS_TOKEN},
)

_proxy_http = httpx.Client(
    timeout=httpx.Timeout(connect=3.0, read=3.0, write=3.0, pool=3.0),
)


def _subumbra_get(path: str) -> tuple[dict | list | None, str | None]:
    """
    GET {SUBUMBRA_KEYS_URL}{path}.  Returns (data, error_string).
    error_string is None on success.
    """
    try:
        r = _http.get(f"{SUBUMBRA_KEYS_URL}{path}")
        r.raise_for_status()
        return r.json(), None
    except httpx.HTTPStatusError as e:
        return None, f"subumbra-keys returned {e.response.status_code}"
    except httpx.RequestError as e:
        return None, f"subumbra-keys unreachable: {type(e).__name__}"


def _require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not UI_USERNAME:
            return view(*args, **kwargs)

        auth = request.authorization
        if not auth:
            log.warning("ui: auth failed remote=%s", request.remote_addr)
            return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="Subumbra"'})

        user_ok = hmac.compare_digest(auth.username or "", UI_USERNAME)
        pass_ok = hmac.compare_digest(auth.password or "", UI_PASSWORD)
        if not (user_ok and pass_ok):
            log.warning("ui: auth failed remote=%s", request.remote_addr)
            return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="Subumbra"'})

        return view(*args, **kwargs)

    return wrapped


def _proxy_get(path: str) -> tuple[dict | list | None, str | None]:
    try:
        r = _proxy_http.get(f"{SUBUMBRA_PROXY_URL}{path}")
        r.raise_for_status()
        return r.json(), None
    except httpx.HTTPStatusError as e:
        log.warning("ui: proxy probe failed status=%s", e.response.status_code)
        return None, f"Proxy returned {e.response.status_code}"
    except httpx.RequestError as e:
        log.warning("ui: proxy probe failed error=%s", type(e).__name__)
        return None, f"Proxy unreachable: {type(e).__name__}"


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
@_require_auth
def index():
    return render_template("index.html")


@app.get("/api/status")
@_require_auth
def api_status():
    """
    Aggregate health + key list + stats + audit into one response for the dashboard.

    Shape:
    {
      "subumbra_keys_healthy":   bool,
      "subumbra_keys_error":     str | null,
      "worker_reachable": bool,
      "worker_error":    str | null,
      "stats_available": bool,
      "audit_available": bool,
      "audit_error":     str | null,
      "keys_loaded":     int,
      "keys": [...],
      "recent_log":      [...],
      "dashboard_time":  str
    }
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    health_data, health_err = _subumbra_get("/health")
    subumbra_keys_healthy = health_err is None and (health_data or {}).get("status") == "ok"

    proxy_health_data, proxy_health_err = _proxy_get("/health")
    worker_auth = (proxy_health_data or {}).get("worker_auth")
    worker_reachable = proxy_health_err is None and worker_auth == "ok"
    if proxy_health_err is None and worker_auth in {"stale", "unreachable"}:
        worker_err = f"Worker auth {worker_auth}"
    else:
        worker_err = proxy_health_err

    keys_data, keys_err = _subumbra_get("/keys")
    keys_list = (keys_data or {}).get("keys", []) if keys_err is None else []

    stats_data, stats_err = _subumbra_get("/stats")
    stats_map: dict[str, dict] = {}
    if stats_err is None and stats_data:
        for entry in stats_data.get("per_key", []):
            stats_map[entry["key_id"]] = entry

    audit_data, audit_err = _subumbra_get("/audit")
    audit_available = audit_err is None
    audit_events: list[dict] = []
    if audit_available and audit_data:
        audit_events = audit_data.get("events", [])

    merged_keys = []
    for key in keys_list:
        kid = key["key_id"]
        s = stats_map.get(kid, {})
        merged_keys.append({
            "key_id": kid,
            "provider": key.get("provider", "unknown"),
            "created_at": key.get("created_at", ""),
            "request_count": s.get("request_count", 0),
            "last_access": s.get("last_access"),
        })

    # Two-pass stable sort: key_id secondary, newest last_access primary.
    merged_keys.sort(key=lambda k: k["key_id"])
    merged_keys.sort(key=lambda k: k["last_access"] or "", reverse=True)

    error = health_err or keys_err
    return jsonify({
        "subumbra_keys_healthy": subumbra_keys_healthy,
        "subumbra_keys_error": error,
        "worker_reachable": worker_reachable,
        "worker_auth": worker_auth,
        "worker_error": worker_err,
        "stats_available": stats_err is None,
        "audit_available": audit_available,
        "audit_error": None if audit_available else audit_err,
        "keys_loaded": len(merged_keys),
        "keys": merged_keys,
        "recent_log": audit_events,
        "dashboard_time": now,
    })
