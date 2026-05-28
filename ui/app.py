"""
Subumbra UI — management console
─────────────────────────────────
Flask + Jinja shell with web-component pages. Real read paths against
subumbra-keys + subumbra-proxy; write flows are stubbed pending the
hardened management API (see ROADMAP.md, R45+).

Routes:
  Pages
    GET /                  → /overview (redirect)
    GET /overview          → vault posture
    GET /vault             → vault › api keys
    GET /vault/ssh         → vault › ssh keys
    GET /sessions          → active sessions + open-session form
    GET /adapters          → connected apps
    GET /policies          → signed catalog + local overrides
    GET /audit             → audit log
    GET /observability     → service health + velocity
    GET /cloudflare        → tunnel / access / worker
    GET /upcoming          → roadmap
    GET /settings          → console configuration

  Read API
    GET /health            → lightweight health JSON
    GET /api/status        → aggregated status (existing contract preserved)
    GET /api/events        → SSE heartbeat (existing contract preserved)
    GET /api/console       → full dataset used by every page
    GET /api/gate/pending  → Gate pending approvals (proxied to CF Worker)
    GET /sw.js             → service worker for browser push

  Write API (stubbed — pending management API)
    POST   /api/gate/subscribe     → browser push subscription registration
    GET    /api/key-session        → mint ephemeral RSA-OAEP keypair (UI-side only, safe)
    DELETE /api/key-session/<sid>  → drop session (UI-side only, safe)
    POST   /api/add-key            → 501 (forward to mgmt API)
    POST   /api/rotate-key         → 501
    POST   /api/sessions/open      → 501
    POST   /api/sessions/close     → 501
    POST   /api/lock-all           → 501
"""

from __future__ import annotations

from collections import defaultdict, deque
import base64
import logging
import os
import secrets
import sys
import time
from functools import wraps
from datetime import datetime, timezone

import httpx
from flask import (
    Flask, Response, jsonify, redirect, render_template, request, stream_with_context, url_for,
)

from _hash_utils import verify_ui_password
from console_data import CONSOLE_DATA, NAV, ORG

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

SUBUMBRA_KEYS_URL     = os.environ.get("SUBUMBRA_KEYS_URL", "http://subumbra-keys:9090").rstrip("/")
SUBUMBRA_ACCESS_TOKEN = os.environ.get("SUBUMBRA_ACCESS_TOKEN", "")
SUBUMBRA_PROXY_URL    = os.environ.get("SUBUMBRA_PROXY_URL", "http://subumbra-proxy:8090").rstrip("/")
CF_WORKER_URL         = os.environ.get("CF_WORKER_URL", "").rstrip("/")
CF_WORKER_NAME        = os.environ.get("CF_WORKER_NAME", "")
CF_ACCESS_CLIENT_ID   = os.environ.get("CF_ACCESS_CLIENT_ID", "")
CF_ACCESS_CLIENT_SECRET = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
SUBUMBRA_GATE_VAPID_PUBLIC_KEY = os.environ.get("SUBUMBRA_GATE_VAPID_PUBLIC_KEY", "")
UI_USERNAME           = os.environ.get("UI_USERNAME", "")
UI_PASSWORD_HASH      = os.environ.get("UI_PASSWORD_HASH", "")
LEGACY_UI_PASSWORD    = os.environ.get("UI_PASSWORD", "")
CF_ACCESS_PROTECTED   = os.environ.get("CF_ACCESS_PROTECTED", "").strip().lower() in {
    "1", "true", "yes", "on",
}
# When SUBUMBRA_UI_DEMO=1, render the mock dataset so the console is usable
# standalone (during install, dev, demos).
DEMO_MODE             = os.environ.get("SUBUMBRA_UI_DEMO", "").lower() in {"1", "true", "yes"}

AUTH_WINDOW_SECONDS    = 60
AUTH_FAILURE_THRESHOLD = 5
_auth_failures: defaultdict[str, deque[float]] = defaultdict(deque)

KEY_SESSION_WINDOW_SECONDS = 60
KEY_SESSION_RATE_LIMIT     = 10
_key_session_requests: defaultdict[str, deque[float]] = defaultdict(deque)

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
    log.warning("subumbra-ui: SUBUMBRA_ACCESS_TOKEN not set — dashboard will show errors")
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

if DEMO_MODE:
    log.warning("ui: SUBUMBRA_UI_DEMO=1 — serving mock console data")

# ─────────────────────────────────────────────────────────────────────────────
# Ephemeral key-session store (in-memory, single process)
# Used by the secure-paste add-key flow. The actual encryption + storage
# happens downstream against the management API — the UI just mints the
# ephemeral keypair so plaintext never crosses the wire as plaintext.
# ─────────────────────────────────────────────────────────────────────────────

try:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric.padding import OAEP, MGF1
    _HAS_CRYPTO = True
except Exception:
    _HAS_CRYPTO = False

_key_sessions: dict[str, dict] = {}
SESSION_TTL_SEC = 300  # 5 minutes


def _sweep_sessions() -> None:
    now = time.time()
    for sid in list(_key_sessions.keys()):
        if _key_sessions[sid].get("used") or _key_sessions[sid].get("expires_at", 0) < now:
            _key_sessions.pop(sid, None)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP clients
# ─────────────────────────────────────────────────────────────────────────────

_http = httpx.Client(
    timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0),
    headers={"X-Subumbra-Token": SUBUMBRA_ACCESS_TOKEN} if SUBUMBRA_ACCESS_TOKEN else {},
)
_proxy_http = httpx.Client(timeout=httpx.Timeout(connect=3.0, read=3.0, write=3.0, pool=3.0))
_worker_http = httpx.Client(timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _subumbra_get(path: str):
    if DEMO_MODE:
        return None, "demo mode"
    try:
        r = _http.get(f"{SUBUMBRA_KEYS_URL}{path}")
        r.raise_for_status()
        return r.json(), None
    except httpx.HTTPStatusError as e:
        return None, f"subumbra-keys returned {e.response.status_code}"
    except httpx.RequestError as e:
        return None, f"subumbra-keys unreachable: {type(e).__name__}"


def _proxy_get(path: str):
    if DEMO_MODE:
        return {"status": "ok", "worker_auth": "ok"}, None
    try:
        r = _proxy_http.get(f"{SUBUMBRA_PROXY_URL}{path}")
        r.raise_for_status()
        return r.json(), None
    except httpx.HTTPStatusError as e:
        return None, f"Proxy returned {e.response.status_code}"
    except httpx.RequestError as e:
        return None, f"Proxy unreachable: {type(e).__name__}"


def _worker_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET:
        headers["CF-Access-Client-Id"] = CF_ACCESS_CLIENT_ID
        headers["CF-Access-Client-Secret"] = CF_ACCESS_CLIENT_SECRET
    return headers


def _worker_request(
    method: str, path: str, *, json_payload: dict | None = None
) -> tuple[dict | list | None, str | None]:
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


def _require_json(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not request.is_json:
            return jsonify({"error": "Content-Type must be application/json"}), 415
        return view(*args, **kwargs)
    return wrapped


def build_console_data() -> dict:
    """
    Merge live data from subumbra-keys + proxy + CF Worker with the mock skeleton.
    Demo mode (or unreachable backends) returns the mock as-is so the
    console is usable for first-time install, dev, and demos.
    """
    data = CONSOLE_DATA.copy()

    if DEMO_MODE:
        data["health"] = {**data["health"], "demo": True}
        return data

    health, h_err       = _subumbra_get("/health")
    proxy_h, p_err      = _proxy_get("/health")
    keys_data, k_err    = _subumbra_get("/keys")
    stats_data, _       = _subumbra_get("/stats")
    audit_data, a_err   = _subumbra_get("/audit")
    sess_data, s_err    = _subumbra_get("/sessions")
    gate_data, gate_err = _worker_request("GET", "/gate/pending")

    if keys_data:
        all_keys = _merge_keys(keys_data.get("keys", []), stats_data or {})
        data["keys"]     = [k for k in all_keys if k.get("type") != "ssh"]
        data["ssh_keys"] = [k for k in all_keys if k.get("type") == "ssh"]
    if audit_data:
        data["audit"] = audit_data.get("events", [])[:50]
    if sess_data:
        data["sessions"] = {
            "lockdown_enabled": sess_data.get("lockdown_enabled", True),
            "active": sess_data.get("active_sessions", []),
        }

    data["gate"] = gate_data if gate_err is None else None

    cf_data = dict(data.get("cloudflare") or {})
    worker_cf = dict(cf_data.get("worker") or {})
    if CF_WORKER_URL:
        worker_cf["url"] = CF_WORKER_URL.replace("https://", "").rstrip("/")
    if CF_WORKER_NAME:
        worker_cf["name"] = CF_WORKER_NAME
    cf_data["worker"] = worker_cf
    data["cloudflare"] = cf_data

    data["health"] = {
        **data["health"],
        "keysService":  (health or {}).get("status") == "ok",
        "proxy":        proxy_h is not None,
        "workerAuth":   (proxy_h or {}).get("worker_auth", "unknown"),
        "keysError":    h_err,
        "proxyError":   p_err,
        "demo":         False,
    }
    return data


def _merge_keys(keys: list, stats: dict) -> list:
    per_key = {s["key_id"]: s for s in (stats.get("per_key") or [])}
    merged = []
    for k in keys:
        s = per_key.get(k.get("key_id"), {})
        merged.append({
            "id":          k.get("key_id"),
            "type":        "ssh" if k.get("type") == "ssh_key" else "api",
            "provider":    k.get("provider", "generic"),
            "capability":  k.get("capability_class", "llm-chat"),
            "vault":       "isolated" if k.get("vault_instance", "").startswith("vault-") else "shared",
            "lastUsed":    _fmt_rel(s.get("last_access")),
            "lastUsedAbs": s.get("last_access"),
            "requests":    s.get("request_count", 0),
            "signs":       0,
            "status":      "paused" if k.get("paused") else ("revoked" if k.get("revoked") else "active"),
            "target":      k.get("target_host", "—"),
            "policyHash":  (k.get("policy_hash") or "")[:8] + "…",
            "policyId":    k.get("policy_id", "—"),
            "rpm":         k.get("velocity_rpm", 60),
            "adapters":    k.get("allow_adapters", []),
            "created":     k.get("created_at", "")[:10],
        })
    return merged


def _fmt_rel(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        diff = (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return iso
    if diff < 60:    return f"{int(diff)}s ago"
    if diff < 3600:  return f"{int(diff/60)}m ago"
    if diff < 86400: return f"{int(diff/3600)}h ago"
    return f"{int(diff/86400)}d ago"


# ─────────────────────────────────────────────────────────────────────────────
# Global response headers + template context
# ─────────────────────────────────────────────────────────────────────────────

@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'")
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "clipboard-read=()")
    return response


@app.context_processor
def inject_globals():
    return {
        "NAV":                   NAV,
        "ORG":                   ORG,
        "VERSION":               os.environ.get("SUBUMBRA_VERSION", "1.1.1-alpha"),
        "now":                   datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gate_vapid_public_key": SUBUMBRA_GATE_VAPID_PUBLIC_KEY,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Page routes
# ─────────────────────────────────────────────────────────────────────────────

def page(template: str, **extra):
    """Render a page template with the merged console dataset."""
    data = build_console_data()
    return render_template(template, data=data, **extra)


@app.get("/")
@_require_auth
def root():
    return redirect(url_for("overview"))


@app.get("/overview")
@_require_auth
def overview():
    return page("overview.html", active="overview", crumbs=["Overview"])


@app.get("/vault")
@_require_auth
def vault_api():
    return page("vault_api.html", active="vault", crumbs=["Vault", "API keys"])


@app.get("/vault/ssh")
@_require_auth
def vault_ssh():
    return page("vault_ssh.html", active="vault", crumbs=["Vault", "SSH keys"])


@app.get("/sessions")
@_require_auth
def sessions():
    return page("sessions.html", active="sessions", crumbs=["Sessions"])


@app.get("/adapters")
@_require_auth
def adapters():
    return page("adapters.html", active="adapters", crumbs=["Adapters"])


@app.get("/policies")
@_require_auth
def policies():
    return page("policies.html", active="policies", crumbs=["Policies & Templates"])


@app.get("/audit")
@_require_auth
def audit():
    return page("audit.html", active="audit", crumbs=["Audit log"])


@app.get("/observability")
@_require_auth
def observability():
    return page("observability.html", active="observability", crumbs=["Observability"])


@app.get("/cloudflare")
@_require_auth
def cloudflare():
    return page("cloudflare.html", active="cloudflare", crumbs=["Cloudflare"])


@app.get("/upcoming")
@_require_auth
def upcoming():
    return page("upcoming.html", active="upcoming", crumbs=["Upcoming"])


@app.get("/settings")
@_require_auth
def settings():
    return page("settings.html", active="settings", crumbs=["Settings"])


# ─────────────────────────────────────────────────────────────────────────────
# Read API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/console")
@_require_auth
def api_console():
    """Single endpoint returning the full dataset used by every page."""
    return jsonify(build_console_data())


@app.get("/api/status")
@_require_auth
def api_status():
    """Legacy aggregated status shape — preserved for compatibility."""
    data = build_console_data()
    return jsonify({
        "subumbra_keys_healthy": data["health"].get("keysService", True),
        "subumbra_keys_error":   data["health"].get("keysError"),
        "worker_reachable":      data["health"].get("workerAuth") == "ok",
        "worker_auth":           data["health"].get("workerAuth", "ok"),
        "worker_error":          data["health"].get("proxyError"),
        "stats_available":       True,
        "audit_available":       True,
        "audit_error":           None,
        "lockdown_enabled":      data["sessions"]["lockdown_enabled"],
        "session_available":     True,
        "session_error":         None,
        "active_sessions":       data["sessions"]["active"],
        "keys_loaded":           len(data["keys"]),
        "keys":                  data["keys"],
        "recent_log":            data["audit"],
        "dashboard_time":        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


@app.get("/api/events")
@_require_auth
def api_events():
    """SSE heartbeat. Client falls back to /api/status polling when this drops."""
    def generate():
        try:
            while True:
                yield ": heartbeat\n\n"
                time.sleep(30)
        except (GeneratorExit, SystemExit):
            return
    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/gate/pending")
@_require_auth
def api_gate_pending():
    payload, error = _worker_request("GET", "/gate/pending")
    if error is not None:
        return jsonify({"error": error}), 502
    return jsonify(payload or {}), 200


@app.get("/sw.js")
def service_worker():
    response = app.send_static_file("sw.js")
    response.headers["Cache-Control"] = "no-store"
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Write API (mostly stubbed pending management API)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/key-session")
@_require_auth
def api_key_session():
    """Mint an ephemeral RSA-OAEP keypair so the browser can encrypt a paste
    before it crosses the wire. UI-side only; the private key never sees
    any provider material — it just unwraps the user's paste to forward
    to the management API downstream."""
    remote = request.remote_addr or "unknown"
    ks_attempts = _key_session_requests[remote]
    now = time.time()
    while ks_attempts and now - ks_attempts[0] > KEY_SESSION_WINDOW_SECONDS:
        ks_attempts.popleft()
    if len(ks_attempts) >= KEY_SESSION_RATE_LIMIT:
        return Response("Too Many Requests", 429)
    ks_attempts.append(now)

    if not _HAS_CRYPTO:
        return jsonify({"error": "cryptography library not installed in UI image"}), 503
    _sweep_sessions()
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = priv.public_key()
    nums = pub.public_numbers()
    sid = secrets.token_urlsafe(18)
    _key_sessions[sid] = {
        "private_key": priv,
        "expires_at":  time.time() + SESSION_TTL_SEC,
        "used":        False,
    }
    n_b64 = base64.urlsafe_b64encode(
        nums.n.to_bytes((nums.n.bit_length() + 7) // 8, "big")
    ).rstrip(b"=").decode()
    e_b64 = base64.urlsafe_b64encode(
        nums.e.to_bytes((nums.e.bit_length() + 7) // 8, "big")
    ).rstrip(b"=").decode()
    return jsonify({
        "sessionId":    sid,
        "expiresAt":    datetime.fromtimestamp(
            _key_sessions[sid]["expires_at"], tz=timezone.utc
        ).isoformat(timespec="seconds"),
        "publicKeyJwk": {"kty": "RSA", "alg": "RSA-OAEP-256", "use": "enc", "n": n_b64, "e": e_b64},
    })


@app.delete("/api/key-session/<sid>")
@_require_auth
@_require_json
def api_key_session_drop(sid: str):
    _key_sessions.pop(sid, None)
    return ("", 204)


def _not_implemented(action: str):
    return jsonify({
        "error":    "management_api_not_implemented",
        "action":   action,
        "fallback": "Use the bootstrap CLI on the host until the management API ships (ROADMAP R45+).",
        "cli_hint": "./bootstrap.sh --session start --ttl 4h --adapters … --keys …",
    }), 501


@app.post("/api/gate/subscribe")
@_require_auth
@_require_json
def api_gate_subscribe():
    try:
        payload = request.get_json(force=False, silent=False)
    except Exception:
        return jsonify({"error": "invalid JSON body"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid JSON body"}), 400
    forwarded, error = _worker_request("POST", "/gate/subscribe", json_payload=payload)
    if error is not None:
        return jsonify({"error": error}), 502
    return jsonify(forwarded or {"status": "ok"}), 200


@app.post("/api/add-key")
@_require_auth
@_require_json
def api_add_key():
    return _not_implemented("add_key")


@app.post("/api/rotate-key")
@_require_auth
@_require_json
def api_rotate_key():
    return _not_implemented("rotate_key")


@app.post("/api/keys/<kid>/pause")
@_require_auth
@_require_json
def api_pause_key(kid):
    return _not_implemented("pause_key")


@app.post("/api/keys/<kid>/resume")
@_require_auth
@_require_json
def api_resume_key(kid):
    return _not_implemented("resume_key")


@app.delete("/api/keys/<kid>")
@_require_auth
@_require_json
def api_revoke_key(kid):
    return _not_implemented("revoke_key")


@app.post("/api/sessions/open")
@_require_auth
@_require_json
def api_open_session():
    return _not_implemented("open_session")


@app.post("/api/sessions/close")
@_require_auth
@_require_json
def api_close_session():
    return _not_implemented("close_session")


@app.post("/api/lock-all")
@_require_auth
@_require_json
def api_lock_all():
    return _not_implemented("lock_all")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6563, debug=False)
