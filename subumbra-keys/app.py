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
        f"{len(adapter_id)}:{adapter_id}:{len(key_id)}:{key_id}:{len(timestamp)}:{timestamp}:{len(nonce)}:{nonce}",
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
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock

from flask import Flask, Response, jsonify, request

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
KEYS_FILE = DATA_DIR / "keys.json"
AUDIT_DIR = Path(os.environ.get("AUDIT_DIR", "/app/audit"))
AUDIT_DB_PATH = AUDIT_DIR / "audit.db"
SESSION_DB_PATH = DATA_DIR / "sessions.db"
AUDIT_MAX_ROWS = int(os.environ.get("AUDIT_MAX_ROWS", "10000"))
SUBUMBRA_RATE_LIMIT_RPM = int(os.environ.get("SUBUMBRA_RATE_LIMIT_RPM", "60"))
if AUDIT_MAX_ROWS <= 0:
    AUDIT_MAX_ROWS = 10000
if SUBUMBRA_RATE_LIMIT_RPM <= 0:
    SUBUMBRA_RATE_LIMIT_RPM = 60

SSH_HOST_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
AUDIT_ENDPOINT_FILTERS = {
    "ssh_sign",
    "get_key",
    "stats",
    "audit",
    "sessions",
    "adapters",
    "observability",
    "gate",
}

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
        # Backward-compatibility for pre-r85 registries already deployed in .env:
        # subumbra-proxy is the only writer allowed to emit audit rows.
        default_can_write_audit = adapter_id == "subumbra-proxy"
        can_write_audit = config.get("can_write_audit", default_can_write_audit)
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
        if not isinstance(can_write_audit, bool):
            raise RuntimeError(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id!r}].can_write_audit must be true/false")

        parsed[adapter_id] = {
            "adapter_id": adapter_id,
            "token": token,
            "allowed_keys": allowed_keys,
            "can_list_keys": can_list_keys,
            "can_read_stats": can_read_stats,
            "can_write_audit": can_write_audit,
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
_session_lock = RLock()
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
        return False, -1

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
            return False, -1

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
                target_host TEXT,
                verdict TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                remote TEXT
            )
            """
        )
        existing_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(audit_events)").fetchall()
        }
        if "target_host" not in existing_columns:
            conn.execute("ALTER TABLE audit_events ADD COLUMN target_host TEXT")
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


def _init_session_db() -> sqlite3.Connection | None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(SESSION_DB_PATH, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id        TEXT PRIMARY KEY,
                name              TEXT,
                allowed_adapters  TEXT,
                allowed_keys      TEXT,
                max_queries       INTEGER,
                max_sign_ops      INTEGER,
                queries_used      INTEGER NOT NULL DEFAULT 0,
                ssh_sign_count    INTEGER NOT NULL DEFAULT 0,
                created_at        TEXT NOT NULL,
                expires_at        TEXT NOT NULL,
                status            TEXT NOT NULL
            )
            """
        )
        existing_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "owner_id" not in existing_columns:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'operator'"
            )
        if "session_type" not in existing_columns:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN session_type TEXT NOT NULL DEFAULT 'operator'"
            )
        if "max_sign_ops" not in existing_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN max_sign_ops INTEGER")
        if "ssh_sign_count" not in existing_columns:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN ssh_sign_count INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lockdown_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                lockdown_enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO lockdown_config (id, lockdown_enabled) VALUES (1, 1)"
        )
        conn.execute(
            """
            UPDATE sessions
            SET status = 'expired'
            WHERE status = 'active' AND datetime(expires_at) < datetime('now')
            """
        )
        conn.commit()
        return conn
    except (OSError, sqlite3.Error) as exc:
        log.warning("session_init_error path=%s error=%s", SESSION_DB_PATH, exc)
        return None


_session_conn = _init_session_db()


def _ensure_session_conn() -> sqlite3.Connection | None:
    global _session_conn
    if _session_conn is not None:
        return _session_conn
    with _session_lock:
        if _session_conn is None:
            _session_conn = _init_session_db()
        return _session_conn


def _decode_scope_json(raw_value: object) -> list[str] | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, str) or not raw_value:
        return None
    parsed = json.loads(raw_value)
    if not isinstance(parsed, list) or not parsed or any(not isinstance(item, str) or not item for item in parsed):
        raise ValueError("invalid session scope encoding")
    return parsed


def _session_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "session_id": row["session_id"],
        "name": row["name"],
        "allowed_adapters": _decode_scope_json(row["allowed_adapters"]),
        "allowed_keys": _decode_scope_json(row["allowed_keys"]),
        "max_queries": row["max_queries"],
        "max_sign_ops": row["max_sign_ops"],
        "queries_used": row["queries_used"],
        "ssh_sign_count": row["ssh_sign_count"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "status": row["status"],
        "owner_id": row["owner_id"],
        "session_type": row["session_type"],
    }


def _expire_sessions_if_needed() -> None:
    conn = _ensure_session_conn()
    if conn is None:
        return
    with _session_lock:
        conn.execute(
            """
            UPDATE sessions
            SET status = 'expired'
            WHERE status = 'active' AND datetime(expires_at) < datetime('now')
            """
        )
        conn.commit()


def _get_lockdown_enabled() -> bool:
    conn = _ensure_session_conn()
    if conn is None:
        return True
    try:
        row = conn.execute(
            "SELECT lockdown_enabled FROM lockdown_config WHERE id = 1"
        ).fetchone()
    except sqlite3.Error as exc:
        log.warning("session_read_error helper=lockdown_config error=%s", exc)
        return True
    if row is None:
        return True
    return bool(row["lockdown_enabled"])


def _list_active_session_rows() -> list[sqlite3.Row]:
    conn = _ensure_session_conn()
    if conn is None:
        raise sqlite3.Error("session connection unavailable")
    _expire_sessions_if_needed()
    return conn.execute(
        """
        SELECT session_id, name, allowed_adapters, allowed_keys, max_queries,
               max_sign_ops, queries_used, ssh_sign_count, created_at, expires_at,
               status, owner_id, session_type
        FROM sessions
        WHERE status = 'active'
        ORDER BY created_at DESC
        """
    ).fetchall()


def _list_recent_sessions(limit: int = 10) -> list[dict[str, object]]:
    conn = _ensure_session_conn()
    if conn is None:
        return []
    rows = conn.execute(
        """
        SELECT session_id, name, allowed_adapters, allowed_keys, max_queries,
               max_sign_ops, queries_used, ssh_sign_count, created_at, expires_at,
               status, owner_id, session_type
        FROM sessions
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_session_row_to_dict(row) for row in rows]


def _try_consume_session_query(session_id: str, max_queries: int) -> bool:
    conn = _ensure_session_conn()
    if conn is None:
        return False
    with _session_lock:
        _expire_sessions_if_needed()
        cursor = conn.execute(
            """
            UPDATE sessions
            SET queries_used = queries_used + 1
            WHERE session_id = ?
              AND status = 'active'
              AND queries_used < ?
            """,
            (session_id, max_queries),
        )
        conn.commit()
        return cursor.rowcount == 1


def _reflect_session_ssh_sign(session_id: str, ssh_sign_count: int, limit_reached: bool) -> tuple[bool, str]:
    conn = _ensure_session_conn()
    if conn is None:
        return False, "session_store_unavailable"
    with _session_lock:
        _expire_sessions_if_needed()
        row = conn.execute(
            "SELECT status, ssh_sign_count FROM sessions WHERE session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return True, "session_missing"
        if str(row["status"]) != "active":
            return True, "session_inactive"
        updates = ["ssh_sign_count = MAX(ssh_sign_count, ?)"]
        params: list[object] = [ssh_sign_count]
        if limit_reached:
            updates.append("status = 'closed'")
        params.append(session_id)
        conn.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ? AND status = 'active'",
            tuple(params),
        )
        conn.commit()
        return True, "reflected"


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


def _sqlite_recent_log_last50(allowed_keys: set[str], list_all: bool) -> list[dict]:
    """Last 50 audit rows, oldest-first within the window (matches former deque order)."""
    if _audit_conn is None:
        return []
    if not list_all and not allowed_keys:
        return []

    query = """
        SELECT timestamp, adapter_id, endpoint, key_id, target_host, verdict, reason_code, remote
        FROM audit_events
    """
    params: tuple[str, ...] = ()
    if not list_all:
        placeholders = ",".join("?" * len(allowed_keys))
        query += f" WHERE key_id IN ({placeholders})"
        params = tuple(sorted(allowed_keys))
    query += """
        ORDER BY id DESC
        LIMIT 50
    """
    rows = _audit_conn.execute(query, params).fetchall()
    recent: list[dict] = [
        {
            "timestamp": r[0],
            "adapter_id": r[1],
            "endpoint": r[2],
            "key_id": r[3],
            "target_host": r[4],
            "verdict": r[5],
            "reason_code": r[6],
            "remote": r[7],
        }
        for r in rows
    ]
    recent.reverse()
    return recent


def _audit_allowed_key_sql(allowed_keys: set[str], list_all: bool) -> tuple[str, list[str]]:
    if list_all:
        return "", []
    if not allowed_keys:
        return " WHERE 1=0", []
    placeholders = ",".join("?" * len(allowed_keys))
    return f" WHERE key_id IN ({placeholders})", sorted(allowed_keys)


def _sqlite_recent_velocity_rows(allowed_keys: set[str], list_all: bool) -> list[dict]:
    if _audit_conn is None:
        return []
    where_sql, query_params = _audit_allowed_key_sql(allowed_keys, list_all)
    rows = _audit_conn.execute(
        f"""
        SELECT key_id, COUNT(*) AS request_count
        FROM audit_events
        {where_sql}
        {"AND" if where_sql else "WHERE"} key_id IS NOT NULL
          AND timestamp >= ?
        GROUP BY key_id
        ORDER BY request_count DESC, key_id ASC
        """,
        tuple(query_params + [_recent_cutoff_iso(60)]),
    ).fetchall()
    return [
        {"key_id": str(row[0]), "request_count": int(row[1])}
        for row in rows
        if row[0]
    ]


def _sqlite_recent_deny_reason_rows(allowed_keys: set[str], list_all: bool) -> list[dict]:
    if _audit_conn is None:
        return []
    where_sql, query_params = _audit_allowed_key_sql(allowed_keys, list_all)
    rows = _audit_conn.execute(
        f"""
        SELECT reason_code, COUNT(*) AS deny_count
        FROM audit_events
        {where_sql}
        {"AND" if where_sql else "WHERE"} verdict = 'deny'
          AND timestamp >= ?
        GROUP BY reason_code
        ORDER BY deny_count DESC, reason_code ASC
        """,
        tuple(query_params + [_recent_cutoff_iso(24 * 60 * 60)]),
    ).fetchall()
    return [
        {"reason_code": str(row[0]), "count": int(row[1])}
        for row in rows
        if row[0]
    ]


def _recent_cutoff_iso(seconds: int) -> str:
    return datetime.fromtimestamp(time.time() - seconds, tz=timezone.utc).isoformat(timespec="seconds")


def _record_audit(
    *,
    adapter_id: str | None,
    key_id: str | None,
    endpoint: str,
    target_host: str | None = None,
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
                    INSERT INTO audit_events (
                        timestamp, adapter_id, endpoint, key_id, target_host, verdict, reason_code, remote
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ts, adapter_id, endpoint, key_id, target_host, verdict, reason_code, remote),
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
    if attempt_count == -1:
        return _AdapterDenial(
            adapter_id=None,
            reason_code="audit_unavailable",
            status_code=503,
            message="service unavailable",
            retry_after=None,
        )
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


def _hmac_ok_for_subject(adapter_id: str, subject_id: str, nonce: str) -> tuple[bool, str]:
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
        (
            f"{len(adapter_id)}:{adapter_id}:{len(subject_id)}:{subject_id}:"
            f"{len(timestamp_str)}:{timestamp_str}:{len(nonce)}:{nonce}"
        ).encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return False, "signature_invalid"

    return True, ""


def _hmac_ok(adapter_id: str, key_id: str, nonce: str) -> tuple[bool, str]:
    return _hmac_ok_for_subject(adapter_id, key_id, nonce)


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
            "type": meta.get("type", "api_key"),
            "provider": meta.get("provider", "ssh" if meta.get("type") == "ssh_key" else "unknown"),
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
    valid, reason = _hmac_ok(adapter["adapter_id"], key_id, nonce)
    if not valid:
        status = 400 if reason == "subumbra_header_missing" else 401
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

    lockdown_enabled = _get_lockdown_enabled()
    matching_session: dict[str, object] | None = None
    if lockdown_enabled:
        try:
            active_rows = _list_active_session_rows()
        except sqlite3.Error as exc:
            log.warning("session_read_error helper=active_sessions error=%s", exc)
            active_rows = []
        if not active_rows:
            log.warning(
                "get_key: locked adapter=%s key_id=%s remote=%s reason=system_locked",
                adapter["adapter_id"],
                key_id,
                remote,
            )
            _record_audit(
                adapter_id=adapter["adapter_id"],
                key_id=key_id,
                endpoint="get_key",
                verdict="deny",
                reason_code="system_locked",
                remote=remote,
            )
            return _err("system_locked", 403)

        active_sessions: list[dict[str, object]] = []
        for active_row in active_rows:
            try:
                active_sessions.append(_session_row_to_dict(active_row))
            except ValueError:
                log.error("get_key: invalid_session_scope session_id=%s", active_row["session_id"])
                _record_audit(
                    adapter_id=adapter["adapter_id"],
                    key_id=key_id,
                    endpoint="get_key",
                    verdict="deny",
                    reason_code="invalid_session_scope",
                    remote=remote,
                )
                return _err("internal error", 500)

        adapter_matches = [
            session_dict
            for session_dict in active_sessions
            if session_dict["allowed_adapters"] is None
            or adapter["adapter_id"] in session_dict["allowed_adapters"]
        ]
        if not adapter_matches:
            log.warning(
                "get_key: denied adapter=%s key_id=%s remote=%s reason=adapter_not_in_session_scope",
                adapter["adapter_id"],
                key_id,
                remote,
            )
            _record_audit(
                adapter_id=adapter["adapter_id"],
                key_id=key_id,
                endpoint="get_key",
                verdict="deny",
                reason_code="adapter_not_in_session_scope",
                remote=remote,
            )
            return _err("adapter_not_in_session_scope", 403)

        full_matches = [
            session_dict
            for session_dict in adapter_matches
            if session_dict["allowed_keys"] is None
            or key_id in session_dict["allowed_keys"]
        ]
        if not full_matches:
            log.warning(
                "get_key: denied adapter=%s key_id=%s remote=%s reason=key_not_in_session_scope",
                adapter["adapter_id"],
                key_id,
                remote,
            )
            _record_audit(
                adapter_id=adapter["adapter_id"],
                key_id=key_id,
                endpoint="get_key",
                verdict="deny",
                reason_code="key_not_in_session_scope",
                remote=remote,
            )
            return _err("key_not_in_session_scope", 403)
        if len(full_matches) > 1:
            session_ids = ",".join(str(session_dict["session_id"]) for session_dict in full_matches)
            log.error(
                "get_key: ambiguous_session_match adapter=%s key_id=%s remote=%s session_ids=%s",
                adapter["adapter_id"],
                key_id,
                remote,
                session_ids,
            )
            _record_audit(
                adapter_id=adapter["adapter_id"],
                key_id=key_id,
                endpoint="get_key",
                verdict="deny",
                reason_code="ambiguous_session_match",
                remote=remote,
            )
            return _err("internal error", 500)
        matching_session = full_matches[0]

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
    if entry.get("paused") is True:
        log.warning("get_key: paused key_id=%s remote=%s", key_id, remote)
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=key_id,
            endpoint="get_key",
            verdict="deny",
            reason_code="key_paused",
            remote=remote,
        )
        return _err("key paused", 403)

    if lockdown_enabled and matching_session is not None:
        max_queries = matching_session["max_queries"]
        if isinstance(max_queries, int) and max_queries >= 0:
            if not _try_consume_session_query(str(matching_session["session_id"]), max_queries):
                log.warning(
                    "get_key: denied adapter=%s key_id=%s remote=%s reason=session_query_limit_reached",
                    adapter["adapter_id"],
                    key_id,
                    remote,
                )
                _record_audit(
                    adapter_id=adapter["adapter_id"],
                    key_id=key_id,
                    endpoint="get_key",
                    verdict="deny",
                    reason_code="session_query_limit_reached",
                    remote=remote,
                )
                return _err("session_query_limit_reached", 403)

    log.info("get_key: served key_id=%s remote=%s", key_id, remote)
    _record_audit(
        adapter_id=adapter["adapter_id"],
        key_id=key_id,
        endpoint="get_key",
        verdict="allow",
        reason_code="allowed",
        remote=remote,
    )

    if entry.get("type") == "ssh_key":
        return jsonify({
            "key_id": key_id,
            "type": "ssh_key",
            "key_source": entry.get("key_source"),
            "algorithm": entry.get("algorithm", "ed25519"),
            "public_key": entry.get("public_key"),
            "vault_instance": entry.get("vault_instance"),
            "policy_hash": entry.get("policy_hash"),
            "policy": entry.get("policy"),
            "adapters": entry.get("adapters", []),
            "created_at": entry.get("created_at"),
            "status": entry.get("status", "active"),
        }), 200

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
            "stats: forbidden adapter=%s remote=%s reason=stats_scope_denied",
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
        allowed = set(adapter["allowed_keys"])
        list_all = adapter.get("can_list_all_keys", False)
        recent = _sqlite_recent_log_last50(allowed, list_all)
    except sqlite3.Error as exc:
        log.warning("stats_read_error endpoint=stats error=%s", exc)
        return _err("audit unavailable", 503)

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


@app.get("/adapters")
def adapters() -> tuple[Response, int]:
    remote = request.remote_addr or ""
    adapter_result = _resolve_adapter()
    if isinstance(adapter_result, _AdapterDenial):
        _record_audit(
            adapter_id=adapter_result.adapter_id,
            key_id=None,
            endpoint="adapters",
            verdict="deny",
            reason_code=adapter_result.reason_code,
            remote=remote,
        )
        return _denial_response(adapter_result)

    adapter = adapter_result
    if not adapter.get("can_list_all_keys", False):
        log.warning(
            "adapters: forbidden adapter=%s remote=%s reason=adapters_scope_denied",
            adapter["adapter_id"],
            remote,
        )
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=None,
            endpoint="adapters",
            verdict="deny",
            reason_code="adapters_scope_denied",
            remote=remote,
        )
        return _err("forbidden", 403)

    payload = [
        {
            "adapter_id": entry["adapter_id"],
            "token": entry["token"],
            "allowed_keys": entry["allowed_keys"],
            "can_list_keys": entry["can_list_keys"],
            "can_read_stats": entry["can_read_stats"],
            "can_write_audit": entry["can_write_audit"],
            "can_list_all_keys": entry["can_list_all_keys"],
            "issued_at": entry["issued_at"],
            "expires_at": entry["expires_at"],
        }
        for entry in sorted(
            SUBUMBRA_ADAPTER_REGISTRY.values(),
            key=lambda item: item["adapter_id"],
        )
    ]
    return jsonify({"adapters": payload, "timestamp": _now_iso()}), 200


@app.get("/observability")
def observability() -> tuple[Response, int]:
    remote = request.remote_addr or ""
    adapter_result = _resolve_adapter()
    if isinstance(adapter_result, _AdapterDenial):
        _record_audit(
            adapter_id=adapter_result.adapter_id,
            key_id=None,
            endpoint="observability",
            verdict="deny",
            reason_code=adapter_result.reason_code,
            remote=remote,
        )
        return _denial_response(adapter_result)

    adapter = adapter_result
    if not adapter["can_read_stats"]:
        log.warning(
            "observability: forbidden adapter=%s remote=%s reason=observability_scope_denied",
            adapter["adapter_id"],
            remote,
        )
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=None,
            endpoint="observability",
            verdict="deny",
            reason_code="observability_scope_denied",
            remote=remote,
        )
        return _err("forbidden", 403)

    if _audit_conn is None:
        return _err("audit unavailable", 503)

    try:
        allowed = set(adapter["allowed_keys"])
        list_all = adapter.get("can_list_all_keys", False)
        velocity = _sqlite_recent_velocity_rows(allowed, list_all)
        decrypt_errors = _sqlite_recent_deny_reason_rows(allowed, list_all)
    except sqlite3.Error as exc:
        log.warning("observability_read_error error=%s", exc)
        return _err("audit unavailable", 503)

    return jsonify({
        "timestamp": _now_iso(),
        "velocity": velocity,
        "decrypt_errors": decrypt_errors,
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
            "audit: forbidden adapter=%s remote=%s reason=audit_scope_denied",
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
    endpoint_filter = (request.args.get("endpoint") or "").strip()
    verdict_filter = (request.args.get("verdict") or "").strip()
    target_host_filter = (request.args.get("target_host") or "").strip()
    if verdict_filter and verdict_filter not in {"allow", "deny", "gate_approved", "gate_denied", "gate_timeout"}:
        return _err("invalid verdict filter", 400)
    if endpoint_filter and endpoint_filter not in AUDIT_ENDPOINT_FILTERS:
        return _err("invalid endpoint filter", 400)
    if target_host_filter and not SSH_HOST_FINGERPRINT_RE.fullmatch(target_host_filter):
        return _err("invalid target_host filter", 400)

    where_clauses: list[str] = []
    query_params: list[str] = []
    allowed = adapter["allowed_keys"]
    list_all = adapter.get("can_list_all_keys", False)
    if not list_all:
        if allowed:
            placeholders = ",".join("?" * len(allowed))
            where_clauses.append(f"key_id IN ({placeholders})")
            query_params.extend(allowed)
        else:
            return jsonify({"events": []}), 200
    if key_id_filter:
        where_clauses.append("key_id = ?")
        query_params.append(key_id_filter)
    if endpoint_filter:
        where_clauses.append("endpoint = ?")
        query_params.append(endpoint_filter)
    if verdict_filter:
        where_clauses.append("verdict = ?")
        query_params.append(verdict_filter)
    if target_host_filter:
        where_clauses.append("target_host = ?")
        query_params.append(target_host_filter)
    where_sql = ""
    if where_clauses:
        where_sql = " WHERE " + " AND ".join(where_clauses)

    try:
        rows = _audit_conn.execute(
            f"""
            SELECT timestamp, adapter_id, endpoint, key_id, target_host, verdict, reason_code, remote
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
            "target_host": row[4],
            "verdict": row[5],
            "reason_code": row[6],
            "remote": row[7],
        }
        for row in rows
    ]

    return jsonify({
        "events": events,
        "count": len(events),
        "timestamp": _now_iso(),
    }), 200


@app.post("/audit")
def write_audit() -> tuple[Response, int]:
    remote = request.remote_addr or ""
    adapter_result = _resolve_adapter()
    if isinstance(adapter_result, _AdapterDenial):
        log.warning("audit_write: denied remote=%s reason=%s", remote, adapter_result.reason_code)
        return _denial_response(adapter_result)

    adapter = adapter_result
    if not adapter.get("can_write_audit", False):
        log.warning(
            "audit_write: forbidden adapter=%s remote=%s reason=audit_write_scope_denied",
            adapter["adapter_id"],
            remote,
        )
        return _err("forbidden", 403)

    if _audit_conn is None:
        log.warning("audit_write: unavailable remote=%s", remote)
        return _err("audit unavailable", 503)

    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        log.warning("audit_write: invalid_json adapter=%s remote=%s", adapter["adapter_id"], remote)
        return _err("invalid JSON body", 400)

    if not isinstance(payload, dict):
        log.warning("audit_write: invalid_payload adapter=%s remote=%s", adapter["adapter_id"], remote)
        return _err("invalid JSON body", 400)

    key_id = payload.get("key_id")
    endpoint = payload.get("endpoint")
    verdict = payload.get("verdict")
    reason_code = payload.get("reason_code")
    source_adapter_id = payload.get("adapter_id")
    target_host = payload.get("target_host")

    if (
        not isinstance(key_id, str)
        or not key_id
        or not isinstance(endpoint, str)
        or not endpoint
        or not isinstance(verdict, str)
        or verdict not in {"allow", "deny", "gate_approved", "gate_denied", "gate_timeout"}
        or not isinstance(reason_code, str)
        or not reason_code
        or not isinstance(source_adapter_id, str)
        or not source_adapter_id
        or (
            target_host is not None
            and (
                not isinstance(target_host, str)
                or not SSH_HOST_FINGERPRINT_RE.fullmatch(target_host)
            )
        )
    ):
        log.warning("audit_write: invalid_fields adapter=%s key_id=%s remote=%s", adapter["adapter_id"], key_id, remote)
        return _err("missing required fields", 400)

    nonce = request.headers.get("X-Subumbra-Nonce", "")
    valid, reason = _hmac_ok(adapter["adapter_id"], key_id, nonce)
    if not valid:
        status = 400 if reason == "subumbra_header_missing" else 401
        log.warning(
            "audit_write: rejected adapter=%s key_id=%s remote=%s reason=%s",
            adapter["adapter_id"],
            key_id,
            remote,
            reason,
        )
        return _err("bad request" if status == 400 else "unauthorized", status)

    valid, reason = _nonce_ok(key_id, nonce)
    if not valid:
        if reason == "nonce_reused":
            log.warning(
                "audit_write: rejected adapter=%s key_id=%s remote=%s reason=%s",
                adapter["adapter_id"],
                key_id,
                remote,
                reason,
            )
            return _err("unauthorized", 401)
        log.error("audit_write: nonce_store_failure adapter=%s key_id=%s reason=%s", adapter["adapter_id"], key_id, reason)
        return _err("internal error", 500)

    _record_audit(
        adapter_id=source_adapter_id,
        key_id=key_id,
        endpoint=endpoint,
        target_host=target_host,
        verdict=verdict,
        reason_code=reason_code,
        remote=remote,
    )
    log.info(
        "audit_write: recorded writer=%s source_adapter=%s key_id=%s endpoint=%s verdict=%s reason=%s",
        adapter["adapter_id"],
        source_adapter_id,
        key_id,
        endpoint,
        verdict,
        reason_code,
    )
    return Response(status=204)


@app.post("/internal/session-ssh-sign")
def reflect_session_ssh_sign() -> tuple[Response, int]:
    remote = request.remote_addr or ""
    adapter_result = _resolve_adapter()
    if isinstance(adapter_result, _AdapterDenial):
        log.warning("session_ssh_sign_reflect: denied remote=%s reason=%s", remote, adapter_result.reason_code)
        return _denial_response(adapter_result)

    adapter = adapter_result
    if not adapter.get("can_write_audit", False):
        log.warning(
            "session_ssh_sign_reflect: forbidden adapter=%s remote=%s reason=audit_write_scope_denied",
            adapter["adapter_id"],
            remote,
        )
        return _err("forbidden", 403)

    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        log.warning("session_ssh_sign_reflect: invalid_json adapter=%s remote=%s", adapter["adapter_id"], remote)
        return _err("invalid JSON body", 400)
    if not isinstance(payload, dict):
        log.warning("session_ssh_sign_reflect: invalid_payload adapter=%s remote=%s", adapter["adapter_id"], remote)
        return _err("invalid JSON body", 400)

    session_id = payload.get("session_id")
    ssh_sign_count = payload.get("ssh_sign_count")
    limit_reached = payload.get("limit_reached")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(ssh_sign_count, int)
        or ssh_sign_count < 0
        or not isinstance(limit_reached, bool)
    ):
        log.warning(
            "session_ssh_sign_reflect: invalid_fields adapter=%s session_id=%s remote=%s",
            adapter["adapter_id"],
            session_id,
            remote,
        )
        return _err("missing required fields", 400)

    nonce = request.headers.get("X-Subumbra-Nonce", "")
    valid, reason = _hmac_ok_for_subject(adapter["adapter_id"], session_id, nonce)
    if not valid:
        status = 400 if reason == "subumbra_header_missing" else 401
        log.warning(
            "session_ssh_sign_reflect: rejected adapter=%s session_id=%s remote=%s reason=%s",
            adapter["adapter_id"],
            session_id,
            remote,
            reason,
        )
        return _err("bad request" if status == 400 else "unauthorized", status)

    valid, reason = _nonce_ok(session_id, nonce)
    if not valid:
        if reason == "nonce_reused":
            log.warning(
                "session_ssh_sign_reflect: rejected adapter=%s session_id=%s remote=%s reason=%s",
                adapter["adapter_id"],
                session_id,
                remote,
                reason,
            )
            return _err("unauthorized", 401)
        log.error(
            "session_ssh_sign_reflect: nonce_store_failure adapter=%s session_id=%s reason=%s",
            adapter["adapter_id"],
            session_id,
            reason,
        )
        return _err("internal error", 500)

    reflected, result = _reflect_session_ssh_sign(session_id, ssh_sign_count, limit_reached)
    if not reflected:
        log.warning(
            "session_ssh_sign_reflect: unavailable adapter=%s session_id=%s result=%s",
            adapter["adapter_id"],
            session_id,
            result,
        )
        return _err("session unavailable", 503)
    if result == "session_missing":
        log.warning(
            "session_ssh_sign_reflect: noop adapter=%s session_id=%s reason=session_missing",
            adapter["adapter_id"],
            session_id,
        )
    elif result == "session_inactive":
        log.warning(
            "session_ssh_sign_reflect: noop adapter=%s session_id=%s reason=session_inactive",
            adapter["adapter_id"],
            session_id,
        )
    else:
        log.info(
            "session_ssh_sign_reflect: recorded adapter=%s session_id=%s ssh_sign_count=%s limit_reached=%s",
            adapter["adapter_id"],
            session_id,
            ssh_sign_count,
            str(limit_reached).lower(),
        )
    return Response(status=200)


@app.get("/sessions")
def sessions() -> tuple[Response, int]:
    remote = request.remote_addr or ""
    adapter_result = _resolve_adapter()
    if isinstance(adapter_result, _AdapterDenial):
        _record_audit(
            adapter_id=adapter_result.adapter_id,
            key_id=None,
            endpoint="sessions",
            verdict="deny",
            reason_code=adapter_result.reason_code,
            remote=remote,
        )
        return _denial_response(adapter_result)

    adapter = adapter_result
    if not adapter["can_read_stats"]:
        _record_audit(
            adapter_id=adapter["adapter_id"],
            key_id=None,
            endpoint="sessions",
            verdict="deny",
            reason_code="sessions_scope_denied",
            remote=remote,
        )
        return _err("forbidden", 403)

    try:
        active_sessions = [
            _session_row_to_dict(row)
            for row in _list_active_session_rows()
        ]
        recent_sessions = _list_recent_sessions(10)
    except (sqlite3.Error, ValueError) as exc:
        log.warning("session_read_error endpoint=sessions error=%s", exc)
        return _err("session unavailable", 503)

    return jsonify({
        "lockdown_enabled": _get_lockdown_enabled(),
        "active_sessions": active_sessions,
        "recent_sessions": recent_sessions,
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
