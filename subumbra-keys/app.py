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
    X-Subumbra-Signature: HMAC-SHA256(
        f"{len(key_id)}:{key_id}:{len(timestamp)}:{timestamp}:{len(nonce)}:{nonce}",
        SUBUMBRA_HMAC_KEY,
    )

  The timestamp must be within ±TIMESTAMP_TOLERANCE seconds of server time,
  and the nonce must be globally unique per key fetch.

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
SUBUMBRA_RATE_LIMIT_RPM = int(os.environ.get("SUBUMBRA_RATE_LIMIT_RPM", "60"))
if AUDIT_MAX_ROWS <= 0:
    AUDIT_MAX_ROWS = 10000
if SUBUMBRA_RATE_LIMIT_RPM <= 0:
    SUBUMBRA_RATE_LIMIT_RPM = 60

_required = ("SUBUMBRA_ADAPTER_REGISTRY", "SUBUMBRA_HMAC_KEY")
for _var in _required:
    if not os.environ.get(_var):
        raise RuntimeError(f"Required environment variable {_var!r} is not set")


@dataclass(frozen=True)
class _AdapterDenial:
    adapter_id: str | None
    reason_code: str
    status_code: int = 401
    message: str = "unauthorized"
    retry_after: str | None = None


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
        can_list_all_keys = config.get("can_list_all_keys", False)
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
            "can_list_all_keys": bool(can_list_all_keys),
            "issued_at": issued_at_raw,
            "expires_at": expires_at_raw,
            "issued_at_dt": issued_at_dt,
            "expires_at_dt": expires_at_dt,
        }

    return parsed


SUBUMBRA_ADAPTER_REGISTRY: dict[str, dict] = _load_adapter_registry(os.environ["SUBUMBRA_ADAPTER_REGISTRY"])
SUBUMBRA_HMAC_KEY: bytes = os.environ["SUBUMBRA_HMAC_KEY"].encode()

TIMESTAMP_TOLERANCE: int = 30   # seconds; adjust down for tighter replay window

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
# Stats (SQLite-backed) and audit
# ─────────────────────────────────────────────────────────────────────────────

_stats_lock = Lock()
_audit_write_count: int = 0
_audit_prune_logged: bool = False
_nonce_write_count: int = 0
_auth_attempt_write_count: int = 0
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_PRUNE_SLACK_SECONDS = 5
_RATE_LIMIT_PRUNE_EVERY = 20


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


def _current_nonce_schema(conn: sqlite3.Connection) -> tuple[set[str], list[str]]:
    rows = conn.execute("PRAGMA table_info(subumbra_nonces)").fetchall()
    columns = {str(row[1]) for row in rows}
    pk_columns = [str(row[1]) for row in sorted(rows, key=lambda row: int(row[5])) if int(row[5])]
    return columns, pk_columns


def _create_nonce_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subumbra_nonces (
            nonce TEXT NOT NULL PRIMARY KEY CHECK(length(nonce) BETWEEN 1 AND 64),
            created_at INTEGER NOT NULL
        )
        """
    )


def _ensure_nonce_schema(conn: sqlite3.Connection) -> None:
    columns, pk_columns = _current_nonce_schema(conn)
    if not columns:
        _create_nonce_table(conn)
        return
    if columns == {"nonce", "created_at"} and pk_columns == ["nonce"]:
        return

    conn.execute("BEGIN IMMEDIATE")
    migrated = False
    try:
        columns, pk_columns = _current_nonce_schema(conn)
        if columns == {"nonce", "created_at"} and pk_columns == ["nonce"]:
            conn.commit()
            return
        expected_legacy_columns = {"nonce", "key_id", "created_at"}
        if columns != expected_legacy_columns or pk_columns != ["nonce", "key_id"]:
            raise sqlite3.Error(
                f"unexpected subumbra_nonces schema columns={sorted(columns)} pk={pk_columns}"
            )
        conn.execute("DROP TABLE IF EXISTS subumbra_nonces_new")
        conn.execute(
            """
            CREATE TABLE subumbra_nonces_new (
                nonce TEXT NOT NULL PRIMARY KEY CHECK(length(nonce) BETWEEN 1 AND 64),
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO subumbra_nonces_new (nonce, created_at)
            SELECT nonce, MAX(created_at)
            FROM subumbra_nonces
            GROUP BY nonce
            """
        )
        conn.execute("DROP TABLE subumbra_nonces")
        conn.execute("ALTER TABLE subumbra_nonces_new RENAME TO subumbra_nonces")
        conn.commit()
        migrated = True
    except Exception:
        conn.rollback()
        raise

    if migrated:
        log.info("nonce_schema_migrated old=composite_pk new=single_pk")


def _record_auth_attempt(remote: str) -> tuple[bool, int]:
    if _audit_conn is None:
        return True, 0

    now_ts = int(time.time())
    window_start = now_ts - _RATE_LIMIT_WINDOW_SECONDS + 1
    prune_before = now_ts - _RATE_LIMIT_WINDOW_SECONDS - _RATE_LIMIT_PRUNE_SLACK_SECONDS

    with _stats_lock:
        global _auth_attempt_write_count
        try:
            _audit_conn.execute(
                "INSERT INTO auth_attempts (remote, ts) VALUES (?, ?)",
                (remote, now_ts),
            )
            _auth_attempt_write_count += 1
            if _auth_attempt_write_count % _RATE_LIMIT_PRUNE_EVERY == 0:
                _audit_conn.execute(
                    "DELETE FROM auth_attempts WHERE ts < ?",
                    (prune_before,),
                )
            count = int(
                _audit_conn.execute(
                    "SELECT COUNT(*) FROM auth_attempts WHERE remote = ? AND ts >= ?",
                    (remote, window_start),
                ).fetchone()[0]
            )
            _audit_conn.commit()
        except sqlite3.Error as exc:
            log.warning("rate_limit_store_error remote=%s error=%s", remote, exc)
            return True, 0

    return count <= SUBUMBRA_RATE_LIMIT_RPM, count


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
            CREATE TABLE IF NOT EXISTS auth_attempts (
                remote TEXT NOT NULL,
                ts INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_attempts_remote_ts ON auth_attempts(remote, ts)"
        )
        _ensure_nonce_schema(conn)
        conn.commit()
        return conn
    except (OSError, sqlite3.Error) as exc:
        log.warning("audit_init_error path=%s error=%s", AUDIT_DB_PATH, exc)
        return None


_audit_conn = _init_audit_db()


def _sqlite_per_key_usage_map() -> dict[str, tuple[int, str | None]]:
    """
    Per-key request_count and last_access from audit_events only.
    Counts rows with non-null key_id (matches historical _record_audit increment rule).
    """
    if _audit_conn is None:
        return {}
    rows = _audit_conn.execute(
        """
        SELECT key_id, COUNT(*) AS cnt, MAX(timestamp) AS last_ts
        FROM audit_events
        WHERE key_id IS NOT NULL
        GROUP BY key_id
        """
    ).fetchall()
    return {str(row[0]): (int(row[1]), row[2]) for row in rows}


def _sqlite_recent_log_last50() -> list[dict]:
    """Last 50 audit rows, oldest-first within the window (matches former deque order)."""
    if _audit_conn is None:
        return []
    rows = _audit_conn.execute(
        """
        SELECT timestamp, adapter_id, endpoint, key_id, verdict, reason_code, remote
        FROM audit_events
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()
    recent: list[dict] = [
        {
            "timestamp": r[0],
            "adapter_id": r[1],
            "endpoint": r[2],
            "key_id": r[3],
            "verdict": r[4],
            "reason_code": r[5],
            "remote": r[6],
        }
        for r in rows
    ]
    recent.reverse()
    return recent


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

    with _stats_lock:
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
    remote = request.remote_addr or ""
    allowed, attempt_count = _record_auth_attempt(remote)
    if not allowed:
        log.warning(
            "rate_limit_exceeded remote=%s attempts=%d window_seconds=%d",
            remote,
            attempt_count,
            _RATE_LIMIT_WINDOW_SECONDS,
        )
        return _AdapterDenial(
            adapter_id=None,
            reason_code="rate_limit_exceeded",
            status_code=429,
            message="rate limit exceeded",
            retry_after=str(_RATE_LIMIT_WINDOW_SECONDS),
        )

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
        f"{len(key_id)}:{key_id}:{len(timestamp_str)}:{timestamp_str}:{len(nonce)}:{nonce}".encode(),
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
                INSERT OR IGNORE INTO subumbra_nonces (nonce, created_at)
                VALUES (?, ?)
                """,
                (nonce, created_at),
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


def _denial_response(denial: _AdapterDenial) -> tuple[Response, int]:
    if denial.reason_code == "rate_limit_exceeded":
        response, status = _err("rate limit exceeded", 429)
    else:
        response, status = _err(denial.message, denial.status_code)
    if denial.retry_after is not None:
        response.headers["Retry-After"] = denial.retry_after
    return response, status


@app.after_request
def _set_cache_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


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
        elif adapter_result.reason_code == "rate_limit_exceeded":
            log.warning("list_keys: rejected — rate limited remote=%s", remote)
        else:
            log.warning("list_keys: rejected — bad token remote=%s", remote)
        return _denial_response(adapter_result)

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
    try:
        usage = _sqlite_per_key_usage_map()
    except sqlite3.Error as exc:
        log.warning("stats_read_error endpoint=list_keys error=%s", exc)
        return _err("audit unavailable", 503)

    payload = []
    allowed = set(adapter["allowed_keys"])
    list_all = adapter.get("can_list_all_keys", False)
    for kid, meta in keys.items():
        if not list_all and kid not in allowed:
            continue
        policy = meta.get("policy") or {}
        auth = policy.get("auth") or {}
        target = policy.get("target") or {}
        allow = policy.get("allow") or {}
        cnt, last_ts = usage.get(kid, (0, None))
        payload.append({
            "key_id": kid,
            "provider": meta.get("provider", "unknown"),
            "created_at": meta.get("created_at", ""),
            "request_count": cnt,
            "last_access": last_ts,
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
        return _denial_response(adapter_result)

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

    keys = _load_keys()
    if key_id not in keys or key_id not in adapter["allowed_keys"]:
        log.warning(
            "get_key: not found or forbidden adapter=%s key_id=%s remote=%s",
            adapter["adapter_id"],
            key_id,
            remote,
        )
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=key_id,
            endpoint="get_key",
            verdict="deny",
            reason_code="key_not_found_or_denied",
            remote=remote,
        )
        return _err("key not found or not permitted", 403)

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
        "policy": entry.get("policy"),
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
        return _denial_response(adapter_result)

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

    if _audit_conn is None:
        return _err("audit unavailable", 503)

    try:
        usage = _sqlite_per_key_usage_map()
        recent = _sqlite_recent_log_last50()
    except sqlite3.Error as exc:
        log.warning("stats_read_error endpoint=stats error=%s", exc)
        return _err("audit unavailable", 503)

    allowed = set(adapter["allowed_keys"])
    list_all = adapter.get("can_list_all_keys", False)
    per_key = [
        {"key_id": kid, "request_count": cnt, "last_access": last_ts}
        for kid, (cnt, last_ts) in usage.items()
        if list_all or kid in allowed
    ]

    return jsonify({
        "per_key": per_key,
        "recent_log": recent,
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
        return _denial_response(adapter_result)

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

    key_id_filter = (request.args.get("key_id") or "").strip()
    verdict_filter = (request.args.get("verdict") or "").strip()
    if verdict_filter and verdict_filter not in {"allow", "deny"}:
        return _err("invalid verdict filter", 400)

    where_clauses: list[str] = []
    query_params: list[str] = []
    allowed = adapter["allowed_keys"]
    list_all = adapter.get("can_list_all_keys", False)
    if not list_all:
        if allowed:
            placeholders = ",".join("?" * len(allowed))
            where_clauses.append(f"(key_id IS NULL OR key_id IN ({placeholders}))")
            query_params.extend(allowed)
        else:
            where_clauses.append("key_id IS NULL")
    if key_id_filter:
        where_clauses.append("key_id = ?")
        query_params.append(key_id_filter)
    if verdict_filter:
        where_clauses.append("verdict = ?")
        query_params.append(verdict_filter)
    where_sql = ""
    if where_clauses:
        where_sql = " WHERE " + " AND ".join(where_clauses)

    try:
        rows = _audit_conn.execute(
            f"""
            SELECT timestamp, adapter_id, endpoint, key_id, verdict, reason_code, remote
            FROM audit_events
            {where_sql}
            ORDER BY id DESC
            LIMIT 100
            """,
            tuple(query_params),
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
