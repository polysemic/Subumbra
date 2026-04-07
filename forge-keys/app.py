"""
forge-keys — encrypted envelope store
──────────────────────────────────────
Runs on the Docker-internal network only.  External callers cannot reach this
service; only the litellm container (and ui) can.

Auth model:
  All endpoints except /health require:
    X-Forge-Token: <adapter token from FORGE_ADAPTER_REGISTRY>

  GET /keys/<key_id> additionally requires a per-request HMAC signature to
  prevent replay attacks:
    X-Forge-Timestamp: <unix epoch, seconds>
    X-Forge-Signature: HMAC-SHA256(f"{key_id}:{timestamp}", FORGE_HMAC_KEY)

  The timestamp must be within ±TIMESTAMP_TOLERANCE seconds of server time.
  This means a captured request cannot be replayed after ~30 s.

Keys file (written by bootstrap, read-only at runtime):
  /app/data/keys.json
  {
    "anthropic_prod": {
      "key_id":       "anthropic_prod",
      "enc_version":  2,
      "pub_key_fp":   "sha256:<hex>",
      "wrapped_dek":  "<base64-encoded RSA-OAEP-wrapped AES-256 DEK>",
      "ciphertext":   "<base64-encoded AES-256-GCM blob (nonce || ct || tag)>",
      "provider":     "anthropic",
      "target_host":  "api.anthropic.com",
      "created_at":   "2026-01-01T00:00:00+00:00",
      "label":        "anthropic_prod"
    },
    ...
  }
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from flask import Flask, Response, jsonify, request

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
KEYS_FILE = DATA_DIR / "keys.json"

_required = ("FORGE_ADAPTER_REGISTRY", "FORGE_HMAC_KEY")
for _var in _required:
    if not os.environ.get(_var):
        raise RuntimeError(f"Required environment variable {_var!r} is not set")

def _load_adapter_registry(raw: str) -> dict[str, dict]:
    try:
        registry = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("FORGE_ADAPTER_REGISTRY must be valid JSON") from exc

    if not isinstance(registry, dict) or not registry:
        raise RuntimeError("FORGE_ADAPTER_REGISTRY must be a non-empty JSON object")

    parsed: dict[str, dict] = {}
    for adapter_id, config in registry.items():
        if not isinstance(adapter_id, str) or not adapter_id:
            raise RuntimeError("FORGE_ADAPTER_REGISTRY keys must be non-empty strings")
        if not isinstance(config, dict):
            raise RuntimeError(f"FORGE_ADAPTER_REGISTRY[{adapter_id!r}] must be an object")

        token = config.get("token")
        allowed_keys = config.get("allowed_keys")
        can_list_keys = config.get("can_list_keys")
        can_read_stats = config.get("can_read_stats")

        if not isinstance(token, str) or not token:
            raise RuntimeError(f"FORGE_ADAPTER_REGISTRY[{adapter_id!r}].token must be a non-empty string")
        if not isinstance(allowed_keys, list) or any(not isinstance(key_id, str) or not key_id for key_id in allowed_keys):
            raise RuntimeError(f"FORGE_ADAPTER_REGISTRY[{adapter_id!r}].allowed_keys must be a list of non-empty strings")
        if not isinstance(can_list_keys, bool):
            raise RuntimeError(f"FORGE_ADAPTER_REGISTRY[{adapter_id!r}].can_list_keys must be true/false")
        if not isinstance(can_read_stats, bool):
            raise RuntimeError(f"FORGE_ADAPTER_REGISTRY[{adapter_id!r}].can_read_stats must be true/false")

        parsed[adapter_id] = {
            "adapter_id": adapter_id,
            "token": token,
            "allowed_keys": allowed_keys,
            "can_list_keys": can_list_keys,
            "can_read_stats": can_read_stats,
        }

    return parsed


FORGE_ADAPTER_REGISTRY: dict[str, dict] = _load_adapter_registry(os.environ["FORGE_ADAPTER_REGISTRY"])
FORGE_HMAC_KEY: bytes = os.environ["FORGE_HMAC_KEY"].encode()

TIMESTAMP_TOLERANCE: int = 30   # seconds; adjust down for tighter replay window
LOG_RING_SIZE: int = 200        # recent request log kept in memory

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("forge-keys")

# ─────────────────────────────────────────────────────────────────────────────
# In-memory stats  (reset on container restart — acceptable for this POC)
# ─────────────────────────────────────────────────────────────────────────────

_stats_lock = Lock()
_request_counts: dict[str, int] = defaultdict(int)
_last_access: dict[str, str] = {}                          # key_id → ISO timestamp
_recent_log: deque[dict] = deque(maxlen=LOG_RING_SIZE)     # ring buffer

# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_keys() -> dict:
    """
    Load keys.json from disk.  Returns empty dict if file missing or unreadable.

    Uses a try/except rather than a pre-existence check to avoid a TOCTOU race
    between existence check and open.  JSONDecodeError can occur transiently if
    a write is in progress (though bootstrap now uses atomic rename, a corrupted
    file from a prior interrupted run should not crash this service).
    """
    try:
        with KEYS_FILE.open() as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        log.error("forge-keys: keys.json is corrupt — returning empty set: %s", exc)
        return {}
    except OSError as exc:
        log.error("forge-keys: cannot read keys.json: %s", exc)
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_access(key_id: str, remote: str, status: str) -> None:
    ts = _now_iso()
    with _stats_lock:
        _request_counts[key_id] += 1
        _last_access[key_id] = ts
        _recent_log.append({
            "key_id":    key_id,
            "remote":    remote,
            "status":    status,
            "timestamp": ts,
        })


def _resolve_adapter() -> dict | None:
    """Resolve X-Forge-Token to an adapter config using constant-time comparison."""
    token = request.headers.get("X-Forge-Token", "")
    matched: dict | None = None
    for adapter in FORGE_ADAPTER_REGISTRY.values():
        if hmac.compare_digest(token, adapter["token"]):
            matched = adapter
    return matched


def _hmac_ok(key_id: str) -> tuple[bool, str]:
    """
    Validate per-request HMAC signature for ciphertext endpoints.
    Returns (valid: bool, reason: str).
    """
    timestamp_str = request.headers.get("X-Forge-Timestamp", "")
    signature     = request.headers.get("X-Forge-Signature", "")

    if not timestamp_str or not signature:
        return False, "missing timestamp or signature headers"

    try:
        ts = int(timestamp_str)
    except ValueError:
        return False, "invalid timestamp"

    now = int(time.time())
    if abs(now - ts) > TIMESTAMP_TOLERANCE:
        return False, f"timestamp outside ±{TIMESTAMP_TOLERANCE}s window"

    expected = hmac.new(
        FORGE_HMAC_KEY,
        f"{key_id}:{timestamp_str}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return False, "invalid HMAC signature"

    return True, ""


def _err(msg: str, code: int) -> tuple[Response, int]:
    return jsonify({"error": msg}), code


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> tuple[Response, int]:
    """No auth — used by Docker healthcheck."""
    keys = _load_keys()
    return jsonify({
        "status":      "ok",
        "keys_loaded": len(keys),
        "timestamp":   _now_iso(),
    }), 200


@app.get("/keys")
def list_keys() -> tuple[Response, int]:
    """List available key IDs (names only, never values).  Requires token."""
    adapter = _resolve_adapter()
    if adapter is None:
        log.warning("list_keys: rejected — bad token  remote=%s", request.remote_addr)
        return _err("unauthorized", 401)
    if not adapter["can_list_keys"]:
        log.warning(
            "list_keys: forbidden  adapter=%s remote=%s",
            adapter["adapter_id"], request.remote_addr,
        )
        return _err("forbidden", 403)

    keys = _load_keys()
    payload = []
    with _stats_lock:
        for kid, meta in keys.items():
            payload.append({
                "key_id":       kid,
                "provider":     meta.get("provider", "unknown"),
                "created_at":   meta.get("created_at", ""),
                "request_count": _request_counts.get(kid, 0),
                "last_access":  _last_access.get(kid, None),
            })

    return jsonify({"keys": payload}), 200


@app.get("/keys/<key_id>")
def get_key(key_id: str) -> tuple[Response, int]:
    """
    Return the encrypted ciphertext blob for key_id.
    Requires bearer token + valid HMAC signature.
    """
    remote = request.remote_addr

    adapter = _resolve_adapter()
    if adapter is None:
        log.warning("get_key: rejected — bad token  key_id=%s remote=%s", key_id, remote)
        _record_access(key_id, remote, "denied_token")
        return _err("unauthorized", 401)

    valid, reason = _hmac_ok(key_id)
    if not valid:
        log.warning(
            "get_key: rejected — bad HMAC  key_id=%s remote=%s reason=%s",
            key_id, remote, reason,
        )
        _record_access(key_id, remote, "denied_hmac")
        return _err("unauthorized", 401)

    if key_id not in adapter["allowed_keys"]:
        log.warning(
            "get_key: forbidden  adapter=%s key_id=%s remote=%s",
            adapter["adapter_id"], key_id, remote,
        )
        _record_access(key_id, remote, "denied_scope")
        return _err("forbidden", 403)

    keys = _load_keys()
    if key_id not in keys:
        log.warning("get_key: not found  key_id=%s remote=%s", key_id, remote)
        _record_access(key_id, remote, "not_found")
        return _err("key not found", 404)

    entry = keys[key_id]
    log.info("get_key: served  key_id=%s remote=%s", key_id, remote)
    _record_access(key_id, remote, "ok")

    return jsonify({
        "key_id":      key_id,
        "ciphertext":  entry["ciphertext"],
        "provider":    entry.get("provider", "unknown"),
        "target_host": entry.get("target_host"),
        "wrapped_dek": entry.get("wrapped_dek"),
        "pub_key_fp":  entry.get("pub_key_fp"),
        "enc_version": entry.get("enc_version", 1),
    }), 200


@app.get("/stats")
def stats() -> tuple[Response, int]:
    """Usage stats for the UI dashboard.  Requires token (no HMAC needed)."""
    adapter = _resolve_adapter()
    if adapter is None:
        return _err("unauthorized", 401)
    if not adapter["can_read_stats"]:
        log.warning(
            "stats: forbidden  adapter=%s remote=%s",
            adapter["adapter_id"], request.remote_addr,
        )
        return _err("forbidden", 403)

    with _stats_lock:
        per_key = [
            {
                "key_id":        kid,
                "request_count": cnt,
                "last_access":   _last_access.get(kid),
            }
            for kid, cnt in _request_counts.items()
        ]
        recent = list(_recent_log)   # copy while lock held

    return jsonify({
        "per_key":    per_key,
        "recent_log": recent[-50:],  # last 50 for UI
        "timestamp":  _now_iso(),
    }), 200


# ── startup log ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Development only — gunicorn used in production
    keys = _load_keys()
    log.info("forge-keys starting  keys_loaded=%d  data_dir=%s", len(keys), DATA_DIR)
    app.run(host="0.0.0.0", port=9090, debug=False)
else:
    # Log once at gunicorn worker startup
    _startup_keys = _load_keys()
    log.info(
        "forge-keys ready  keys_loaded=%d  data_dir=%s",
        len(_startup_keys), DATA_DIR,
    )
