"""
Subumbra UI — management dashboard
────────────────────────────────────
Read-only dashboard; never exposes or fetches key values.
Talks to subumbra-keys over the Docker internal network.

Routes:
  GET /health    → lightweight health JSON
  GET /           → dashboard HTML
  GET /api/status → aggregated JSON (health + keys + stats + audit)
"""

from __future__ import annotations

from collections import defaultdict, deque
import logging
import os
import sys
import time
from functools import wraps
from datetime import datetime, timezone

import httpx
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from _hash_utils import verify_ui_password

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

SUBUMBRA_KEYS_URL = os.environ.get("SUBUMBRA_KEYS_URL", "http://subumbra-keys:9090").rstrip("/")
SUBUMBRA_ACCESS_TOKEN = os.environ.get("SUBUMBRA_ACCESS_TOKEN", "")
SUBUMBRA_PROXY_URL = os.environ.get("SUBUMBRA_PROXY_URL", "http://subumbra-proxy:8090").rstrip("/")
CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "").rstrip("/")
CF_ACCESS_CLIENT_ID = os.environ.get("CF_ACCESS_CLIENT_ID", "")
CF_ACCESS_CLIENT_SECRET = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
SUBUMBRA_GATE_VAPID_PUBLIC_KEY = os.environ.get("SUBUMBRA_GATE_VAPID_PUBLIC_KEY", "")
UI_USERNAME = os.environ.get("UI_USERNAME", "")
UI_PASSWORD_HASH = os.environ.get("UI_PASSWORD_HASH", "")
LEGACY_UI_PASSWORD = os.environ.get("UI_PASSWORD", "")
CF_ACCESS_PROTECTED = os.environ.get("CF_ACCESS_PROTECTED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
AUTH_WINDOW_SECONDS = 60
AUTH_FAILURE_THRESHOLD = 5
_auth_failures: defaultdict[str, deque[float]] = defaultdict(deque)

# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__, template_folder="templates", static_folder="static", static_url_path="/static")
log = logging.getLogger("subumbra-ui")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)

if not SUBUMBRA_ACCESS_TOKEN:
    logging.warning("subumbra-ui: SUBUMBRA_ACCESS_TOKEN not set — dashboard will show errors")
if LEGACY_UI_PASSWORD:
    log.warning("ui: UI_PASSWORD is deprecated and ignored; use UI_PASSWORD_HASH via ./bootstrap.sh --update-ui-auth")
if UI_PASSWORD_HASH and not UI_USERNAME:
    log.error("ui: UI_PASSWORD_HASH set but UI_USERNAME is missing")
    sys.exit(1)
if UI_USERNAME and not UI_PASSWORD_HASH:
    log.error("ui: UI_USERNAME set but UI_PASSWORD_HASH is missing")
    sys.exit(1)
if not UI_PASSWORD_HASH and not CF_ACCESS_PROTECTED:
    log.error("ui: missing auth configuration; set UI_PASSWORD_HASH or CF_ACCESS_PROTECTED=true")
    sys.exit(1)
if UI_PASSWORD_HASH and CF_ACCESS_PROTECTED:
    log.info("ui: CF Access outer gate enabled with in-process Basic Auth")
elif UI_PASSWORD_HASH:
    log.info("ui: in-process Basic Auth enabled")
else:
    log.info("ui: CF Access protected mode enabled without in-process Basic Auth")

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

_worker_http = httpx.Client(
    timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0),
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
        if not UI_PASSWORD_HASH:
            return view(*args, **kwargs)
        remote = request.remote_addr or "unknown"
        attempts = _auth_failures[remote]
        now = time.time()
        while attempts and now - attempts[0] > AUTH_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= AUTH_FAILURE_THRESHOLD:
            log.warning("ui: auth_lockout ip=%s", remote)
            return Response("Too Many Requests", 429)

        auth = request.authorization
        if not auth:
            attempts.append(now)
            log.warning("ui: auth_failure ip=%s", remote)
            return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="Subumbra"'})

        user_ok = (auth.username or "") == UI_USERNAME
        pass_ok = verify_ui_password(auth.password or "", UI_PASSWORD_HASH)
        if not (user_ok and pass_ok):
            attempts.append(now)
            log.warning("ui: auth_failure ip=%s", remote)
            return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="Subumbra"'})

        attempts.clear()
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


def _worker_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET:
        headers["CF-Access-Client-Id"] = CF_ACCESS_CLIENT_ID
        headers["CF-Access-Client-Secret"] = CF_ACCESS_CLIENT_SECRET
    return headers


def _worker_request(method: str, path: str, *, json_payload: dict | None = None) -> tuple[dict | list | None, str | None]:
    if not CF_WORKER_URL:
        return None, "worker URL not configured"
    try:
        response = _worker_http.request(
            method,
            f"{CF_WORKER_URL}{path}",
            headers=_worker_headers(),
            json=json_payload,
        )
        response.raise_for_status()
        if not response.content:
            return {}, None
        return response.json(), None
    except httpx.HTTPStatusError as e:
        return None, f"worker returned {e.response.status_code}"
    except httpx.RequestError as e:
        return None, f"worker unreachable: {type(e).__name__}"


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@app.after_request
def add_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'"
    )
    response.headers.setdefault("Cache-Control", "no-store")
    return response

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.get("/")
@_require_auth
def index():
    return render_template("index.html", gate_vapid_public_key=SUBUMBRA_GATE_VAPID_PUBLIC_KEY)


@app.get("/sw.js")
def service_worker():
    response = app.send_static_file("sw.js")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/events")
@_require_auth
def api_events():
    def generate():
        try:
            while True:
                yield ": heartbeat\n\n"
                # SSE comment keep-alive interval (seconds). See docs/operator-guide.md "Heartbeat, polling, and health cadence".
                time.sleep(30)
        except (GeneratorExit, SystemExit):
            return

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)


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
    audit_mode = (request.args.get("audit") or "").strip()

    health_data, health_err = _subumbra_get("/health")
    subumbra_keys_healthy = health_err is None and (health_data or {}).get("status") == "ok"

    proxy_health_data, proxy_health_err = _proxy_get("/health")
    worker_auth = (proxy_health_data or {}).get("worker_auth")
    worker_reachable = proxy_health_err is None and worker_auth == "ok"
    if proxy_health_err is None and worker_auth in {"stale", "token_mismatch", "unreachable"}:
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

    audit_data, audit_err = _subumbra_get("/audit" if audit_mode != "ssh_sign" else "/audit?endpoint=ssh_sign")
    audit_available = audit_err is None
    audit_events: list[dict] = []
    if audit_available and audit_data:
        audit_events = audit_data.get("events", [])

    ssh_audit_data, ssh_audit_err = _subumbra_get("/audit?endpoint=ssh_sign")
    ssh_audit_available = ssh_audit_err is None
    ssh_audit_events: list[dict] = []
    if ssh_audit_available and ssh_audit_data:
        ssh_audit_events = ssh_audit_data.get("events", [])

    ssh_stats: dict[str, dict[str, object]] = {}
    for entry in ssh_audit_events:
        key_id = entry.get("key_id")
        if not isinstance(key_id, str) or not key_id:
            continue
        stats = ssh_stats.setdefault(
            key_id,
            {
                "ssh_sign_count": 0,
                "last_sign_at": None,
                "recent_denials": [],
            },
        )
        if entry.get("verdict") == "allow":
            stats["ssh_sign_count"] = int(stats["ssh_sign_count"]) + 1
            timestamp = entry.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                current = stats.get("last_sign_at")
                if not isinstance(current, str) or timestamp > current:
                    stats["last_sign_at"] = timestamp
        elif entry.get("verdict") == "deny":
            reason_code = entry.get("reason_code")
            if isinstance(reason_code, str) and reason_code:
                recent_denials = stats["recent_denials"]
                if isinstance(recent_denials, list) and reason_code not in recent_denials:
                    recent_denials.append(reason_code)
                    del recent_denials[3:]

    session_data, session_err = _subumbra_get("/sessions")
    active_sessions = (session_data or {}).get("active_sessions", []) if session_err is None else []

    merged_keys = []
    for key in keys_list:
        kid = key["key_id"]
        s = stats_map.get(kid, {})
        ssh = ssh_stats.get(kid, {})
        merged_keys.append({
            "key_id": kid,
            "type": key.get("type", "api_key"),
            "provider": key.get("provider", "unknown"),
            "created_at": key.get("created_at", ""),
            "request_count": s.get("request_count", 0),
            "last_access": s.get("last_access"),
            "ssh_sign_count": ssh.get("ssh_sign_count", 0),
            "last_sign_at": ssh.get("last_sign_at"),
            "ssh_recent_denials": ssh.get("recent_denials", []),
            "policy_id": key.get("policy_id"),
            "policy_hash": key.get("policy_hash"),
            "vault_instance": key.get("vault_instance"),
            "label": key.get("label"),
            "revoked": key.get("revoked", False),
            "paused": key.get("paused", False),
            "capability_class": key.get("capability_class"),
            "protocol": key.get("protocol"),
            "auth_scheme": key.get("auth_scheme"),
            "auth_header": key.get("auth_header"),
            "auth_prefix": key.get("auth_prefix"),
            "target_host": key.get("target_host"),
            "base_path": key.get("base_path"),
            "allow_adapters": key.get("allow_adapters", []),
            "allow_methods": key.get("allow_methods", []),
            "allow_path_prefixes": key.get("allow_path_prefixes", []),
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
        "lockdown_enabled": (session_data or {}).get("lockdown_enabled", True) if session_err is None else True,
        "session_available": session_err is None,
        "session_error": session_err,
        "active_sessions": active_sessions,
        "keys_loaded": len(merged_keys),
        "keys": merged_keys,
        "recent_log": audit_events,
        "audit_filter": audit_mode if audit_mode == "ssh_sign" else "",
        "dashboard_time": now,
    })


@app.get("/api/gate/pending")
@_require_auth
def api_gate_pending():
    payload, error = _worker_request("GET", "/gate/pending")
    if error is not None:
        return jsonify({"error": error}), 502
    return jsonify(payload or {}), 200


@app.post("/api/gate/subscribe")
@_require_auth
def api_gate_subscribe():
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"error": "invalid JSON body"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid JSON body"}), 400
    forwarded, error = _worker_request("POST", "/gate/subscribe", json_payload=payload)
    if error is not None:
        return jsonify({"error": error}), 502
    return jsonify(forwarded or {"status": "ok"}), 200
