"""
subumbra-keys — encrypted envelope store
──────────────────────────────────────
Runs on the Docker-internal network only. External callers cannot reach this
service; only internal Subumbra services such as the proxy, UI, and probe tooling can.

Auth model:
  All endpoints except /health require:
    X-Subumbra-Token: <adapter token from SUBUMBRA_ADAPTER_REGISTRY>

  GET /keys/<key_id> additionally requires a per-request HMAC signature to
  prevent replay attacks:
    X-Subumbra-Timestamp: <unix epoch, seconds>
    X-Subumbra-Nonce: <single-use hex nonce>
    X-Subumbra-Signature: HMAC-SHA256(f"{key_id}:{timestamp}:{nonce}", SUBUMBRA_HMAC_KEY)

  The timestamp must be within ±TIMESTAMP_TOLERANCE seconds of server time,
  and the nonce must be unique per key fetch.

Keys file (written by bootstrap, read-only at runtime):
  /app/data/keys.json
  {
    "anthropic_prod": {
      "key_id":       "anthropic_prod",
      "enc_version":  3,
      "pub_key_fp":   "sha256:<hex>",
      "wrapped_dek":  "<base64-encoded RSA-OAEP-wrapped AES-256 DEK>",
      "ciphertext":   "<base64-encoded AES-256-GCM blob (nonce || ct || tag)>",
      "policy_id":    "auto-app-anthropic_prod",
      "policy_hash":  "<sha256-policy-hash>",
      "vault_instance":"vault",
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
import sqlite3
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from flask import Flask, Response, jsonify, request

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
KEYS_FILE = DATA_DIR / "keys.json"
AUDIT_DIR = Path(os.environ.get("AUDIT_DIR", "/app/audit"))
AUDIT_DB_PATH = AUDIT_DIR / "audit.db"
AUDIT_MAX_ROWS = int(os.environ.get("AUDIT_MAX_ROWS", "10000"))
if AUDIT_MAX_ROWS <= 0:
    AUDIT_MAX_ROWS = 10000

_required = ("SUBUMBRA_ADAPTER_REGISTRY", "SUBUMBRA_HMAC_KEY")
for _var in _required:
    if not os.environ.get(_var):
        raise RuntimeError(f"Required environment variable {_var!r} is not set")


@dataclass(frozen=True)
class _AdapterDenial:
    adapter_id: str | None
    reason_code: str


def _parse_registry_timestamp(adapter_id: str, field: str, raw_value: object) -> tuple[str, datetime]:
    if not isinstance(raw_value, str) or not raw_value:
        raise RuntimeError(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id!r}].{field} must be a non-empty string")
    normalized = raw_value[:-1] + "+00:00" if raw_value.endswith("Z") else raw_value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RuntimeError(
            f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id!r}].{field} must be a valid ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise RuntimeError(
            f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id!r}].{field} must include a timezone offset"
        )
    return raw_value, parsed.astimezone(timezone.utc)


def _load_adapter_registry(raw: str) -> dict[str, dict]:
    try:
        registry = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("SUBUMBRA_ADAPTER_REGISTRY must be valid JSON") from exc

    if not isinstance(registry, dict) or not registry:
        raise RuntimeError("SUBUMBRA_ADAPTER_REGISTRY must be a non-empty JSON object")

    parsed: dict[str, dict] = {}
    for adapter_id, config in registry.items():
        if not isinstance(adapter_id, str) or not adapter_id:
            raise RuntimeError("SUBUMBRA_ADAPTER_REGISTRY keys must be non-empty strings")
        if not isinstance(config, dict):
            raise RuntimeError(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id!r}] must be an object")

        token = config.get("token")
        allowed_keys = config.get("allowed_keys")
        can_list_keys = config.get("can_list_keys")
        can_read_stats = config.get("can_read_stats")
        issued_at_raw, issued_at_dt = _parse_registry_timestamp(adapter_id, "issued_at", config.get("issued_at"))
        expires_at_raw, expires_at_dt = _parse_registry_timestamp(adapter_id, "expires_at", config.get("expires_at"))

        if not isinstance(token, str) or not token:
            raise RuntimeError(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id!r}].token must be a non-empty string")
        if not isinstance(allowed_keys, list) or any(not isinstance(key_id, str) or not key_id for key_id in allowed_keys):
            raise RuntimeError(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id!r}].allowed_keys must be a list of non-empty strings")
        if not isinstance(can_list_keys, bool):
            raise RuntimeError(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id!r}].can_list_keys must be true/false")
        if not isinstance(can_read_stats, bool):
            raise RuntimeError(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id!r}].can_read_stats must be true/false")

        parsed[adapter_id] = {
            "adapter_id": adapter_id,
            "token": token,
            "allowed_keys": allowed_keys,
            "can_list_keys": can_list_keys,
            "can_read_stats": can_read_stats,
            "issued_at": issued_at_raw,
            "expires_at": expires_at_raw,
            "issued_at_dt": issued_at_dt,
            "expires_at_dt": expires_at_dt,
        }

    return parsed


SUBUMBRA_ADAPTER_REGISTRY: dict[str, dict] = _load_adapter_registry(os.environ["SUBUMBRA_ADAPTER_REGISTRY"])
SUBUMBRA_HMAC_KEY: bytes = os.environ["SUBUMBRA_HMAC_KEY"].encode()

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
log = logging.getLogger("subumbra-keys")

_expired_at_startup = sorted(
    adapter["adapter_id"]
    for adapter in SUBUMBRA_ADAPTER_REGISTRY.values()
    if adapter["expires_at_dt"] <= datetime.now(timezone.utc)
)
if _expired_at_startup:
    log.warning(
        "adapter_expired_at_startup count=%d ids=%s",
        len(_expired_at_startup),
        ",".join(_expired_at_startup),
    )

# ─────────────────────────────────────────────────────────────────────────────
# In-memory stats and audit
# ─────────────────────────────────────────────────────────────────────────────

_stats_lock = Lock()
_request_counts: dict[str, int] = defaultdict(int)
_last_access: dict[str, str] = {}
_recent_log: deque[dict] = deque(maxlen=LOG_RING_SIZE)
_audit_write_count: int = 0
_audit_prune_logged: bool = False
_nonce_write_count: int = 0


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
        log.error("subumbra-keys: keys.json is corrupt — returning empty set: %s", exc)
        return {}
    except OSError as exc:
        log.error("subumbra-keys: cannot read keys.json: %s", exc)
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _init_audit_db() -> sqlite3.Connection | None:
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(AUDIT_DB_PATH, check_same_thread=False, timeout=30)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                adapter_id TEXT,
                endpoint TEXT NOT NULL,
                key_id TEXT,
                verdict TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                remote TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subumbra_nonces (
                nonce TEXT NOT NULL CHECK(length(nonce) BETWEEN 1 AND 64),
                key_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (nonce, key_id)
            )
            """
        )
        conn.commit()
        return conn
    except (OSError, sqlite3.Error) as exc:
        log.warning("audit_init_error path=%s error=%s", AUDIT_DB_PATH, exc)
        return None


_audit_conn = _init_audit_db()

if _audit_conn is not None:
    _rows = _audit_conn.execute(
        "SELECT key_id, COUNT(*) as cnt, MAX(timestamp) as last "
        "FROM audit_events WHERE endpoint = 'get_key' AND verdict = 'allow' "
        "GROUP BY key_id"
    ).fetchall()
    for _row in _rows:
        _request_counts[_row[0]] = _row[1]
        _last_access[_row[0]] = _row[2]


def _record_audit(
    *,
    adapter_id: str | None,
    key_id: str | None,
    endpoint: str,
    verdict: str,
    reason_code: str,
    remote: str,
) -> None:
    ts = _now_iso()
    event = {
        "timestamp": ts,
        "adapter_id": adapter_id,
        "endpoint": endpoint,
        "key_id": key_id,
        "verdict": verdict,
        "reason_code": reason_code,
        "remote": remote,
    }

    with _stats_lock:
        if key_id is not None:
            _request_counts[key_id] += 1
            _last_access[key_id] = ts
        _recent_log.append(event)

        if _audit_conn is not None:
            global _audit_write_count, _audit_prune_logged
            try:
                _audit_conn.execute(
                    """
                    INSERT INTO audit_events (timestamp, adapter_id, endpoint, key_id, verdict, reason_code, remote)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ts, adapter_id, endpoint, key_id, verdict, reason_code, remote),
                )
                _audit_conn.commit()
                _audit_write_count += 1
                if _audit_write_count % 100 == 0:
                    try:
                        _audit_conn.execute(
                            """
                            DELETE FROM audit_events
                            WHERE id NOT IN (
                                SELECT id FROM audit_events
                                ORDER BY id DESC
                                LIMIT ?
                            )
                            """,
                            (AUDIT_MAX_ROWS,),
                        )
                        _audit_conn.commit()
                        if not _audit_prune_logged:
                            log.info("audit_pruned retained=%s", AUDIT_MAX_ROWS)
                            _audit_prune_logged = True
                    except sqlite3.Error as exc:
                        log.warning("audit_prune_error error=%s", exc)
            except sqlite3.Error as exc:
                log.warning("audit_write_error error=%s", exc)


def _resolve_adapter() -> dict | _AdapterDenial:
    """Resolve X-Subumbra-Token to an adapter config using constant-time comparison."""
    token = request.headers.get("X-Subumbra-Token", "")
    matched: dict | None = None
    for adapter in SUBUMBRA_ADAPTER_REGISTRY.values():
        if hmac.compare_digest(token, adapter["token"]):
            matched = adapter
    if matched is None:
        return _AdapterDenial(adapter_id=None, reason_code="adapter_unknown")
    if matched["expires_at_dt"] <= datetime.now(timezone.utc):
        log.warning(
            "token_expired adapter=%s expires_at=%s remote=%s",
            matched["adapter_id"],
            matched["expires_at"],
            request.remote_addr,
        )
        return _AdapterDenial(adapter_id=matched["adapter_id"], reason_code="adapter_expired")
    return matched


def _hmac_ok(key_id: str, nonce: str) -> tuple[bool, str]:
    """
    Validate per-request HMAC signature for ciphertext endpoints.
    Returns (valid: bool, reason_code: str).
    """
    timestamp_str = request.headers.get("X-Subumbra-Timestamp", "")
    signature = request.headers.get("X-Subumbra-Signature", "")

    if not nonce:
        return False, "nonce_missing"
    if len(nonce) > 64:
        return False, "nonce_too_long"
    if not timestamp_str or not signature:
        return False, "subumbra_header_missing"

    try:
        ts = int(timestamp_str)
    except ValueError:
        return False, "timestamp_invalid"

    now = int(time.time())
    if abs(now - ts) > TIMESTAMP_TOLERANCE:
        return False, "timestamp_outside_window"

    expected = hmac.new(
        SUBUMBRA_HMAC_KEY,
        f"{key_id}:{timestamp_str}:{nonce}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return False, "signature_invalid"

    return True, ""


def _nonce_ok(key_id: str, nonce: str) -> tuple[bool, str]:
    # Round 40 makes the nonce store a required dependency for secure key fetches.
    if _audit_conn is None:
        return False, "nonce_store_unavailable"

    created_at = int(time.time())
    prune_before = created_at - TIMESTAMP_TOLERANCE - 5

    with _stats_lock:
        global _nonce_write_count
        try:
            cursor = _audit_conn.execute(
                """
                INSERT OR IGNORE INTO subumbra_nonces (nonce, key_id, created_at)
                VALUES (?, ?, ?)
                """,
                (nonce, key_id, created_at),
            )
            _audit_conn.commit()
            if cursor.rowcount == 0:
                return False, "nonce_reused"
            _nonce_write_count += 1
            if _nonce_write_count % 50 == 0:
                _audit_conn.execute(
                    "DELETE FROM subumbra_nonces WHERE created_at < ?",
                    (prune_before,),
                )
                _audit_conn.commit()
            return True, ""
        except sqlite3.Error:
            return False, "nonce_store_error"


def _err(msg: str, code: int) -> tuple[Response, int]:
    return jsonify({"error": msg}), code


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> tuple[Response, int]:
    """No auth — used by Docker healthcheck."""
    keys = _load_keys()
    return jsonify({
        "status": "ok",
        "keys_loaded": len(keys),
        "timestamp": _now_iso(),
    }), 200


@app.get("/keys")
def list_keys() -> tuple[Response, int]:
    """List available key IDs (names only, never values).  Requires token."""
    remote = request.remote_addr or ""
    adapter_result = _resolve_adapter()
    if isinstance(adapter_result, _AdapterDenial):
        _record_audit(
            adapter_id=adapter_result.adapter_id,
            key_id=None,
            endpoint="list_keys",
            verdict="deny",
            reason_code=adapter_result.reason_code,
            remote=remote,
        )
        if adapter_result.reason_code == "adapter_expired":
            log.warning("list_keys: rejected — expired token adapter=%s remote=%s", adapter_result.adapter_id, remote)
        else:
            log.warning("list_keys: rejected — bad token remote=%s", remote)
        return _err("unauthorized", 401)

    adapter = adapter_result
    if not adapter["can_list_keys"]:
        log.warning(
            "list_keys: forbidden adapter=%s remote=%s",
            adapter["adapter_id"],
            remote,
        )
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=None,
            endpoint="list_keys",
            verdict="deny",
            reason_code="list_scope_denied",
            remote=remote,
        )
        return _err("forbidden", 403)

    keys = _load_keys()
    payload = []
    with _stats_lock:
        for kid, meta in keys.items():
            policy = meta.get("policy") or {}
            auth = policy.get("auth") or {}
            target = policy.get("target") or {}
            allow = policy.get("allow") or {}
            payload.append({
                "key_id": kid,
                "provider": meta.get("provider", "unknown"),
                "created_at": meta.get("created_at", ""),
                "request_count": _request_counts.get(kid, 0),
                "last_access": _last_access.get(kid, None),
                "policy_id": meta.get("policy_id") or policy.get("policy_id"),
                "policy_hash": meta.get("policy_hash"),
                "vault_instance": meta.get("vault_instance"),
                "label": meta.get("label"),
                "revoked": bool(meta.get("revoked", False)),
                "paused": bool(meta.get("paused", False)),
                "capability_class": policy.get("capability_class"),
                "protocol": policy.get("protocol"),
                "auth_scheme": auth.get("scheme"),
                "auth_header": auth.get("header_name"),
                "auth_prefix": auth.get("prefix"),
                "target_host": target.get("host") or meta.get("target_host"),
                "base_path": target.get("base_path"),
                "allow_adapters": allow.get("adapters", []),
                "allow_methods": allow.get("methods", []),
                "allow_path_prefixes": allow.get("path_prefixes", []),
            })

    return jsonify({"keys": payload}), 200


@app.get("/keys/<key_id>")
def get_key(key_id: str) -> tuple[Response, int]:
    """
    Return the encrypted ciphertext blob for key_id.
    Requires bearer token + valid HMAC signature.
    """
    remote = request.remote_addr or ""

    adapter_result = _resolve_adapter()
    if isinstance(adapter_result, _AdapterDenial):
        log.warning("get_key: rejected key_id=%s remote=%s reason=%s", key_id, remote, adapter_result.reason_code)
        _record_audit(
            adapter_id=adapter_result.adapter_id,
            key_id=key_id,
            endpoint="get_key",
            verdict="deny",
            reason_code=adapter_result.reason_code,
            remote=remote,
        )
        return _err("unauthorized", 401)

    adapter = adapter_result
    nonce = request.headers.get("X-Subumbra-Nonce", "")
    valid, reason = _hmac_ok(key_id, nonce)
    if not valid:
        status = 400 if reason in {"nonce_missing", "nonce_too_long", "subumbra_header_missing", "timestamp_invalid"} else 401
        log.warning("get_key: rejected key_id=%s remote=%s reason=%s", key_id, remote, reason)
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=key_id,
            endpoint="get_key",
            verdict="deny",
            reason_code=reason,
            remote=remote,
        )
        return _err("bad request" if status == 400 else "unauthorized", status)

    valid, reason = _nonce_ok(key_id, nonce)
    if not valid:
        if reason == "nonce_reused":
            log.warning("get_key: rejected key_id=%s remote=%s reason=%s", key_id, remote, reason)
            message = "unauthorized"
            status = 401
        else:
            log.error("get_key: nonce_store_failure key_id=%s reason=%s", key_id, reason)
            message = "internal error"
            status = 500
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=key_id,
            endpoint="get_key",
            verdict="deny",
            reason_code=reason,
            remote=remote,
        )
        return _err(message, status)

    if key_id not in adapter["allowed_keys"]:
        log.warning(
            "get_key: forbidden adapter=%s key_id=%s remote=%s",
            adapter["adapter_id"],
            key_id,
            remote,
        )
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=key_id,
            endpoint="get_key",
            verdict="deny",
            reason_code="key_scope_denied",
            remote=remote,
        )
        return _err("forbidden", 403)

    keys = _load_keys()
    if key_id not in keys:
        log.warning("get_key: not found key_id=%s remote=%s", key_id, remote)
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=key_id,
            endpoint="get_key",
            verdict="deny",
            reason_code="key_not_found",
            remote=remote,
        )
        return _err("key not found", 404)

    entry = keys[key_id]
    if entry.get("revoked") is True:
        log.warning("get_key: revoked key_id=%s remote=%s", key_id, remote)
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=key_id,
            endpoint="get_key",
            verdict="deny",
            reason_code="key_revoked",
            remote=remote,
        )
        return _err("key revoked", 403)

    log.info("get_key: served key_id=%s remote=%s", key_id, remote)
    _record_audit(
        adapter_id=adapter["adapter_id"],
        key_id=key_id,
        endpoint="get_key",
        verdict="allow",
        reason_code="allowed",
        remote=remote,
    )

    return jsonify({
        "key_id": key_id,
        "ciphertext": entry["ciphertext"],
        "provider": entry.get("provider", "unknown"),
        "target_host": entry.get("target_host"),
        "wrapped_dek": entry.get("wrapped_dek"),
        "pub_key_fp": entry.get("pub_key_fp"),
        "enc_version": entry.get("enc_version", 1),
        "policy_id": entry.get("policy_id"),
        "policy_hash": entry.get("policy_hash"),
        "vault_instance": entry.get("vault_instance"),
    }), 200


@app.get("/stats")
def stats() -> tuple[Response, int]:
    """Usage stats for the UI dashboard.  Requires token (no HMAC needed)."""
    remote = request.remote_addr or ""
    adapter_result = _resolve_adapter()
    if isinstance(adapter_result, _AdapterDenial):
        _record_audit(
            adapter_id=adapter_result.adapter_id,
            key_id=None,
            endpoint="stats",
            verdict="deny",
            reason_code=adapter_result.reason_code,
            remote=remote,
        )
        return _err("unauthorized", 401)

    adapter = adapter_result
    if not adapter["can_read_stats"]:
        log.warning(
            "stats: forbidden adapter=%s remote=%s",
            adapter["adapter_id"],
            remote,
        )
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=None,
            endpoint="stats",
            verdict="deny",
            reason_code="stats_scope_denied",
            remote=remote,
        )
        return _err("forbidden", 403)

    with _stats_lock:
        per_key = [
            {
                "key_id": kid,
                "request_count": cnt,
                "last_access": _last_access.get(kid),
            }
            for kid, cnt in _request_counts.items()
        ]
        recent = list(_recent_log)

    return jsonify({
        "per_key": per_key,
        "recent_log": recent[-50:],
        "timestamp": _now_iso(),
    }), 200


@app.get("/audit")
def audit() -> tuple[Response, int]:
    """Durable structured audit trail for operator-facing reads."""
    remote = request.remote_addr or ""
    adapter_result = _resolve_adapter()
    if isinstance(adapter_result, _AdapterDenial):
        _record_audit(
            adapter_id=adapter_result.adapter_id,
            key_id=None,
            endpoint="audit",
            verdict="deny",
            reason_code=adapter_result.reason_code,
            remote=remote,
        )
        return _err("unauthorized", 401)

    adapter = adapter_result
    if not adapter["can_read_stats"]:
        log.warning(
            "audit: forbidden adapter=%s remote=%s",
            adapter["adapter_id"],
            remote,
        )
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=None,
            endpoint="audit",
            verdict="deny",
            reason_code="audit_scope_denied",
            remote=remote,
        )
        return _err("forbidden", 403)

    if _audit_conn is None:
        return _err("audit unavailable", 503)

    try:
        rows = _audit_conn.execute(
            """
            SELECT timestamp, adapter_id, endpoint, key_id, verdict, reason_code, remote
            FROM audit_events
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()
    except sqlite3.Error as exc:
        log.warning("audit_read_error error=%s", exc)
        return _err("audit unavailable", 503)

    events = [
        {
            "timestamp": row[0],
            "adapter_id": row[1],
            "endpoint": row[2],
            "key_id": row[3],
            "verdict": row[4],
            "reason_code": row[5],
            "remote": row[6],
        }
        for row in rows
    ]

    return jsonify({
        "events": events,
        "count": len(events),
        "timestamp": _now_iso(),
    }), 200


# ── startup log ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Development only — gunicorn used in production
    keys = _load_keys()
    log.info(
        "subumbra-keys starting keys_loaded=%d data_dir=%s audit_db=%s",
        len(keys),
        DATA_DIR,
        AUDIT_DB_PATH,
    )
    app.run(host="0.0.0.0", port=9090, debug=False)
else:
    # Log once at gunicorn worker startup
    _startup_keys = _load_keys()
    log.info(
        "subumbra-keys ready keys_loaded=%d data_dir=%s audit_db=%s",
        len(_startup_keys),
        DATA_DIR,
        AUDIT_DB_PATH,
    )
