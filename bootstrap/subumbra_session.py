#!/usr/bin/env python3
"""Session management commands for Subumbra bootstrap."""

from __future__ import annotations

from subumbra_core import *
from subumbra_core import (
    _chown_to_subumbra,
    _is_revoked_record,
    _load_keys_payload_or_die,
    _read_runtime_credential_value,
    _secure_data_dir,
)
from subumbra_cf import _get_push_registry_cf_creds, _kv_delete_key, _kv_put_text_value, _load_kv_namespace_id

def _open_session_db() -> sqlite3.Connection:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _secure_data_dir()
        if SESSIONS_DB_FILE.exists():
            os.chmod(SESSIONS_DB_FILE, 0o640)
            _chown_to_subumbra(SESSIONS_DB_FILE)
        conn = sqlite3.connect(SESSIONS_DB_FILE, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id        TEXT PRIMARY KEY,
                name              TEXT,
                allowed_consumers  TEXT,
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
        if "allowed_adapters" in existing_columns and "allowed_consumers" not in existing_columns:
            conn.execute(
                "ALTER TABLE sessions RENAME COLUMN allowed_adapters TO allowed_consumers"
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
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN max_sign_ops INTEGER"
            )
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
        _secure_data_dir()
        return conn
    except (OSError, sqlite3.Error) as exc:
        die(f"Failed to open session database {SESSIONS_DB_FILE}: {exc}")


def _session_scope_to_db_value(values: list[str] | None) -> str | None:
    if values is None:
        return None
    if not values:
        die("session scope lists cannot be empty")
    return json.dumps(values, separators=(",", ":"))


def _session_scope_from_db_value(raw_value: object) -> list[str] | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, str) or not raw_value:
        die("session database contains invalid scope encoding")
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        die(f"session database contains invalid scope JSON: {exc}")
    if not isinstance(parsed, list) or not parsed or any(not isinstance(item, str) or not item for item in parsed):
        die("session database contains invalid scope list")
    return parsed


def _session_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "name": row["name"],
        "allowed_consumers": _session_scope_from_db_value(row["allowed_consumers"]),
        "allowed_keys": _session_scope_from_db_value(row["allowed_keys"]),
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


def _expire_active_sessions(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE sessions
        SET status = 'expired'
        WHERE status = 'active' AND datetime(expires_at) < datetime('now')
        """
    )
    conn.commit()


def _list_active_session_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    _expire_active_sessions(conn)
    return conn.execute(
        """
        SELECT session_id, name, allowed_consumers, allowed_keys, max_queries,
               max_sign_ops, queries_used, ssh_sign_count, created_at, expires_at,
               status, owner_id, session_type
        FROM sessions
        WHERE status = 'active'
        ORDER BY created_at DESC
        """
    ).fetchall()


def _get_session_row_by_id(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    _expire_active_sessions(conn)
    return conn.execute(
        """
        SELECT session_id, name, allowed_consumers, allowed_keys, max_queries,
               max_sign_ops, queries_used, ssh_sign_count, created_at, expires_at,
               status, owner_id, session_type
        FROM sessions
        WHERE session_id = ?
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()


def _load_runtime_consumer_registry() -> dict[str, dict[str, Any]]:
    raw = _read_runtime_credential_value("SUBUMBRA_CONSUMER_REGISTRY")
    if not raw:
        die("SUBUMBRA_CONSUMER_REGISTRY missing from runtime environment / host .env")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"SUBUMBRA_CONSUMER_REGISTRY invalid JSON: {exc}")
    if not isinstance(parsed, dict) or not parsed:
        die("SUBUMBRA_CONSUMER_REGISTRY must be a non-empty JSON object")
    for consumer_id, config in parsed.items():
        if not isinstance(config, dict):
            die(f"SUBUMBRA_CONSUMER_REGISTRY[{consumer_id!r}] must be an object")
        can_write_audit = config.get("can_write_audit", False)
        if not isinstance(can_write_audit, bool):
            die(f"SUBUMBRA_CONSUMER_REGISTRY[{consumer_id!r}].can_write_audit must be true/false")
    return parsed


def _load_active_session_static_consumers() -> dict[str, dict[str, Any]]:
    registry = _load_runtime_consumer_registry()
    active_consumers: dict[str, dict[str, Any]] = {}
    for consumer_id, config in registry.items():
        if not isinstance(consumer_id, str) or not consumer_id:
            continue
        if not isinstance(config, dict):
            continue
        allowed_keys = config.get("allowed_keys")
        if isinstance(allowed_keys, list) and allowed_keys:
            active_consumers[consumer_id] = config
    if not active_consumers:
        die("No static key-fetch-capable consumers found in SUBUMBRA_CONSUMER_REGISTRY")
    return active_consumers


def _load_live_key_ids() -> list[str]:
    keys_payload = _load_keys_payload_or_die()
    key_ids = [key_id for key_id, record in keys_payload.items() if not _is_revoked_record(record)]
    if not key_ids:
        die("No live key IDs found in endpoint.json")
    return sorted(key_ids)


def _parse_session_duration_seconds(raw: str) -> int:
    value = raw.strip().lower()
    if not value:
        die("--ttl requires a non-empty duration")
    match = re.fullmatch(r"(\d+)([smhd])", value)
    if not match:
        die("--ttl must use one of the forms <int>s, <int>m, <int>h, or <int>d")
    amount = int(match.group(1))
    unit = match.group(2)
    if amount <= 0:
        die("--ttl must be greater than zero")
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return amount * multipliers[unit]


def _parse_session_csv_arg(raw: str, *, field_name: str) -> list[str] | None:
    value = raw.strip()
    if value.lower() == "all":
        return None
    parsed = [item.strip() for item in value.split(",") if item.strip()]
    if not parsed:
        die(f"{field_name} cannot be empty")
    return parsed


def _session_args(flag_name: str) -> list[str]:
    if "--session" not in sys.argv:
        return []
    idx = sys.argv.index("--session")
    return sys.argv[idx + 1 :]


def _session_arg_value(args: list[str], flag_name: str) -> str | None:
    if flag_name not in args:
        return None
    idx = args.index(flag_name)
    try:
        value = args[idx + 1]
    except IndexError:
        die(f"{flag_name} requires a value")
    if value.startswith("--"):
        die(f"{flag_name} requires a value")
    return value

def _resolve_session_consumer_scope(raw_consumers: str) -> tuple[list[str], list[str] | None]:
    static_consumers = _load_active_session_static_consumers()
    selected = _parse_session_csv_arg(raw_consumers, field_name="--consumers")
    if selected is None:
        concrete = sorted(static_consumers.keys())
        return concrete, None

    resolved: list[str] = []
    for consumer_id in selected:
        if consumer_id not in static_consumers:
            die(f"Unknown or non-key-fetch-capable consumer_id {consumer_id!r} for --consumers")
        resolved.append(consumer_id)
    return sorted(dict.fromkeys(resolved)), sorted(dict.fromkeys(resolved))


def _resolve_session_key_scope(raw_keys: str | None) -> list[str] | None:
    selected = _parse_session_csv_arg(raw_keys or "all", field_name="--keys")
    if selected is None:
        return None

    live_key_ids = set(_load_live_key_ids())
    resolved: list[str] = []
    for key_id in selected:
        if key_id not in live_key_ids:
            die(f"Unknown key_id {key_id!r} for --keys")
        resolved.append(key_id)
    return sorted(dict.fromkeys(resolved))


def _format_session_scope(values: list[str] | None, *, empty_label: str = "all") -> str:
    if values is None:
        return empty_label
    return ",".join(values)


def _effective_session_consumer_ids(session_dict: dict[str, Any]) -> list[str]:
    allowed_consumers = session_dict["allowed_consumers"]
    if allowed_consumers is None:
        return sorted(_load_active_session_static_consumers().keys())
    return sorted(dict.fromkeys(str(consumer_id) for consumer_id in allowed_consumers))


def _effective_session_key_ids(session_dict: dict[str, Any]) -> list[str]:
    allowed_keys = session_dict["allowed_keys"]
    if allowed_keys is None:
        return _load_live_key_ids()
    return sorted(dict.fromkeys(str(key_id) for key_id in allowed_keys))


def _ssh_session_scope_key(consumer_id: str, key_id: str) -> str:
    return f"ssh_session_scope:{consumer_id}:{key_id}"


def _ssh_session_scope_pairs(session_dict: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (consumer_id, key_id)
        for consumer_id in _effective_session_consumer_ids(session_dict)
        for key_id in _effective_session_key_ids(session_dict)
    ]


def _session_remaining_ttl_seconds(session_dict: dict[str, Any], now: datetime | None = None) -> int:
    current_time = now or datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(str(session_dict["expires_at"]).replace("Z", "+00:00"))
    return max(0, int((expires_at - current_time).total_seconds()))


def _reconcile_active_consumer_gate(
    cf_creds: dict[str, str],
    namespace_id: str,
    consumer_id: str,
    active_sessions: Iterable[dict[str, Any]],
) -> None:
    max_remaining_ttl = 0
    now = datetime.now(timezone.utc)
    for session_dict in active_sessions:
        if consumer_id not in _effective_session_consumer_ids(session_dict):
            continue
        max_remaining_ttl = max(
            max_remaining_ttl,
            _session_remaining_ttl_seconds(session_dict, now),
        )
    janus_key = f"active_consumer:{consumer_id}"
    if max_remaining_ttl > 0:
        _kv_put_text_value(
            cf_creds,
            namespace_id,
            janus_key,
            "1",
            expiration_ttl=max_remaining_ttl,
        )
        return
    _kv_delete_key(cf_creds, namespace_id, janus_key)


def _reconcile_active_consumer_gates(
    cf_creds: dict[str, str],
    namespace_id: str,
    consumer_ids: Iterable[str],
    active_sessions: Iterable[dict[str, Any]],
) -> None:
    active_session_list = list(active_sessions)
    for consumer_id in sorted(dict.fromkeys(consumer_ids)):
        _reconcile_active_consumer_gate(cf_creds, namespace_id, consumer_id, active_session_list)


def _ensure_session_start_has_no_overlap(
    candidate_session: dict[str, Any],
    active_sessions: Iterable[dict[str, Any]],
) -> None:
    candidate_consumer_ids = set(_effective_session_consumer_ids(candidate_session))
    candidate_key_ids = set(_effective_session_key_ids(candidate_session))
    for active_session in active_sessions:
        overlapping_consumers = sorted(
            candidate_consumer_ids & set(_effective_session_consumer_ids(active_session))
        )
        if not overlapping_consumers:
            continue
        overlapping_keys = sorted(
            candidate_key_ids & set(_effective_session_key_ids(active_session))
        )
        if not overlapping_keys:
            continue
        die(
            "session_start_overlap_denied "
            f"conflicting_session_id={active_session['session_id']} "
            f"consumer_id={overlapping_consumers[0]} "
            f"key_id={overlapping_keys[0]}"
        )


def _fmt_utc_ts(iso_str: str) -> str:
    """Format an ISO-8601 UTC timestamp as a readable string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return iso_str


def _fmt_duration(seconds: int) -> str:
    """Format a duration in seconds as Xh Ym Zs."""
    if seconds <= 0:
        return "0s (expired)"
    parts = []
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


def _print_session_row(prefix: str, row_dict: dict[str, Any]) -> None:
    print(f"{prefix}session_id={row_dict['session_id']}")
    print(f"{prefix}name={row_dict['name'] or '(none)'}")
    print(f"{prefix}status={row_dict['status']}")
    print(f"{prefix}allowed_consumers={_format_session_scope(row_dict['allowed_consumers'])}")
    print(f"{prefix}allowed_keys={_format_session_scope(row_dict['allowed_keys'])}")
    print(f"{prefix}max_queries={row_dict['max_queries'] if row_dict['max_queries'] is not None else 'unlimited'}")
    print(f"{prefix}max_sign_ops={row_dict['max_sign_ops'] if row_dict['max_sign_ops'] is not None else 'unlimited'}")
    print(f"{prefix}queries_used={row_dict['queries_used']}")
    print(f"{prefix}ssh_sign_count={row_dict['ssh_sign_count']}")
    print(f"{prefix}created_at={_fmt_utc_ts(str(row_dict['created_at']))}")
    print(f"{prefix}expires_at={_fmt_utc_ts(str(row_dict['expires_at']))}")


def _session_wizard(
    args: list[str],
    static_consumers: dict[str, dict[str, Any]],
    live_key_ids: list[str],
) -> tuple[str, str, str | None, str | None, str | None, str | None]:
    """Interactive wizard for --session start when args are missing.

    Returns (ttl_raw, consumers_raw, keys_raw, name, max_queries_raw, max_sign_ops_raw).
    Entry modes:
      - consumer-first: --consumers provided, only keys for those consumers shown
      - key-first:     --keys provided, only consumers that include those keys shown
      - bare:          show all consumers, then filter keys to selected consumers
    """
    if not sys.stdin.isatty():
        die("--session start: --ttl and --consumers are required in non-interactive mode")

    consumer_key_map: dict[str, list[str]] = {}
    for consumer_id, config in static_consumers.items():
        allowed = config.get("allowed_keys")
        consumer_key_map[consumer_id] = allowed if isinstance(allowed, list) else list(live_key_ids)

    key_consumer_map: dict[str, list[str]] = {}
    for consumer_id, keys in consumer_key_map.items():
        for k in keys:
            key_consumer_map.setdefault(k, []).append(consumer_id)

    ttl_raw = _session_arg_value(args, "--ttl")
    consumers_raw = _session_arg_value(args, "--consumers")
    keys_raw = _session_arg_value(args, "--keys")
    name = (_session_arg_value(args, "--name") or "").strip() or None
    max_queries_raw = _session_arg_value(args, "--max-queries")
    max_sign_ops_raw = _session_arg_value(args, "--max-sign-ops")

    print("\n=== Subumbra session wizard ===")

    if consumers_raw is not None:
        # consumer-first: show only keys available for requested consumers
        selected_consumers_list = [a.strip() for a in consumers_raw.split(",") if a.strip()]
        visible_keys = sorted({k for a in selected_consumers_list for k in consumer_key_map.get(a, [])})
        if keys_raw is None:
            if visible_keys:
                print(f"\nKeys accessible by {', '.join(selected_consumers_list)}:")
                for i, k in enumerate(visible_keys, 1):
                    print(f"  {i}. {k}")
                raw = input("Keys to allow [enter = all, or comma-separated numbers/names]: ").strip()
                if raw:
                    chosen: list[str] = []
                    for token in raw.split(","):
                        token = token.strip()
                        if token.isdigit() and 1 <= int(token) <= len(visible_keys):
                            chosen.append(visible_keys[int(token) - 1])
                        elif token in visible_keys:
                            chosen.append(token)
                        else:
                            die(f"Unknown selection {token!r}")
                    keys_raw = ",".join(chosen)

    elif keys_raw is not None:
        # key-first: show only consumers that include the requested keys
        selected_keys_list = [k.strip() for k in keys_raw.split(",") if k.strip()]
        visible_consumers = sorted({a for k in selected_keys_list for a in key_consumer_map.get(k, [])})
        if not visible_consumers:
            die(f"No consumers are authorized for keys: {', '.join(selected_keys_list)}")
        if consumers_raw is None:
            print(f"\nConsumers authorized for {', '.join(selected_keys_list)}:")
            for i, a in enumerate(visible_consumers, 1):
                print(f"  {i}. {a}")
            raw = input("Consumers to open [enter = all shown, or comma-separated numbers/names]: ").strip()
            if raw:
                chosen_consumers: list[str] = []
                for token in raw.split(","):
                    token = token.strip()
                    if token.isdigit() and 1 <= int(token) <= len(visible_consumers):
                        chosen_consumers.append(visible_consumers[int(token) - 1])
                    elif token in visible_consumers:
                        chosen_consumers.append(token)
                    else:
                        die(f"Unknown consumer {token!r}")
                consumers_raw = ",".join(chosen_consumers)
            else:
                consumers_raw = ",".join(visible_consumers)

    else:
        # bare: show all consumers, then filter keys
        all_consumer_ids = sorted(static_consumers.keys())
        print("\nAvailable consumers:")
        for i, a in enumerate(all_consumer_ids, 1):
            key_count = len(consumer_key_map.get(a, []))
            print(f"  {i}. {a}  ({key_count} key(s))")
        raw = input("Consumers to open [enter = all, or comma-separated numbers/names]: ").strip()
        if raw:
            chosen_consumers: list[str] = []
            for token in raw.split(","):
                token = token.strip()
                if token.isdigit() and 1 <= int(token) <= len(all_consumer_ids):
                    chosen_consumers.append(all_consumer_ids[int(token) - 1])
                elif token in all_consumer_ids:
                    chosen_consumers.append(token)
                else:
                    die(f"Unknown consumer {token!r}")
            consumers_raw = ",".join(chosen_consumers)
            selected_consumers_for_keys = chosen_consumers
        else:
            consumers_raw = "all"
            selected_consumers_for_keys = all_consumer_ids

        # filter keys to those accessible by the selected consumers
        visible_keys2 = sorted({k for a in selected_consumers_for_keys for k in consumer_key_map.get(a, [])})
        if visible_keys2:
            print(f"\nKeys accessible by selected consumers:")
            for i, k in enumerate(visible_keys2, 1):
                print(f"  {i}. {k}")
            raw = input("Keys to allow [enter = all, or comma-separated numbers/names]: ").strip()
            if raw:
                chosen_k: list[str] = []
                for token in raw.split(","):
                    token = token.strip()
                    if token.isdigit() and 1 <= int(token) <= len(visible_keys2):
                        chosen_k.append(visible_keys2[int(token) - 1])
                    elif token in visible_keys2:
                        chosen_k.append(token)
                    else:
                        die(f"Unknown key {token!r}")
                keys_raw = ",".join(chosen_k)

    # name (optional)
    if name is None:
        raw_name = input("\nSession name (optional, enter to skip): ").strip()
        if raw_name:
            name = raw_name

    # TTL
    if ttl_raw is None:
        print("\nTTL — how long this session stays open.")
        print("  Format: <number><unit> where unit is s=seconds, m=minutes, h=hours, d=days")
        print("  Examples: 30m  2h  8h  1d")
        raw_ttl = input("TTL [default: 1h]: ").strip()
        ttl_raw = raw_ttl if raw_ttl else "1h"

    # max-queries (optional)
    if max_queries_raw is None:
        raw_mq = input("\nMax queries (optional, enter for unlimited): ").strip()
        if raw_mq:
            max_queries_raw = raw_mq

    if max_sign_ops_raw is None:
        raw_mso = input("Max SSH signs (optional, enter for unlimited): ").strip()
        if raw_mso:
            max_sign_ops_raw = raw_mso

    print()
    return ttl_raw, consumers_raw or "all", keys_raw, name, max_queries_raw, max_sign_ops_raw


def run_session_start() -> None:
    args = _session_args("--session")
    if not args or args[0] != "start":
        die("--session start requires the 'start' subcommand immediately after --session")

    ttl_raw = _session_arg_value(args, "--ttl")
    consumers_raw = _session_arg_value(args, "--consumers")
    keys_raw = _session_arg_value(args, "--keys")
    name = (_session_arg_value(args, "--name") or "").strip() or None
    max_queries_raw = _session_arg_value(args, "--max-queries")
    max_sign_ops_raw = _session_arg_value(args, "--max-sign-ops")

    wizard_needed = ttl_raw is None or consumers_raw is None
    if wizard_needed:
        static_consumers = _load_active_session_static_consumers()
        live_key_ids = _load_live_key_ids()
        ttl_raw, consumers_raw, keys_raw, name, max_queries_raw, max_sign_ops_raw = _session_wizard(
            args, static_consumers, live_key_ids
        )
    elif ttl_raw is None:
        die("--session start requires --ttl <duration>")

    if consumers_raw is None:
        die("--session start requires --consumers <csv|all>")
    ttl_seconds = _parse_session_duration_seconds(ttl_raw)
    _resolved_consumer_ids, stored_consumer_scope = _resolve_session_consumer_scope(consumers_raw)
    stored_key_scope = _resolve_session_key_scope(keys_raw)
    max_queries: int | None = None
    if max_queries_raw is not None:
        try:
            max_queries = int(max_queries_raw)
        except ValueError:
            die("--max-queries must be an integer")
        if max_queries <= 0:
            die("--max-queries must be greater than zero")
    max_sign_ops: int | None = None
    if max_sign_ops_raw is not None:
        try:
            max_sign_ops = int(max_sign_ops_raw)
        except ValueError:
            die("--max-sign-ops must be an integer")
        if max_sign_ops <= 0:
            die("--max-sign-ops must be greater than zero")

    conn = _open_session_db()
    now = datetime.now(timezone.utc)
    created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_at = (now + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    session_id = secrets.token_hex(16)
    owner_id = "operator"
    session_type = "operator"
    candidate_session = {
        "session_id": session_id,
        "name": name,
        "allowed_consumers": stored_consumer_scope,
        "allowed_keys": stored_key_scope,
        "max_queries": max_queries,
        "max_sign_ops": max_sign_ops,
        "queries_used": 0,
        "ssh_sign_count": 0,
        "created_at": created_at,
        "expires_at": expires_at,
        "status": "pending",
        "owner_id": owner_id,
        "session_type": session_type,
    }
    active_sessions = [
        _session_row_to_dict(row)
        for row in _list_active_session_rows(conn)
    ]
    _ensure_session_start_has_no_overlap(candidate_session, active_sessions)
    effective_consumer_ids = _effective_session_consumer_ids(candidate_session)

    cf_creds = _get_push_registry_cf_creds()
    namespace_id = _load_kv_namespace_id()

    conn.execute(
        """
        INSERT INTO sessions (
            session_id, name, allowed_consumers, allowed_keys, max_queries,
            max_sign_ops, queries_used, ssh_sign_count, created_at, expires_at,
            status, owner_id, session_type
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 'pending', ?, ?)
        """,
        (
            session_id,
            name,
            _session_scope_to_db_value(stored_consumer_scope),
            _session_scope_to_db_value(stored_key_scope),
            max_queries,
            max_sign_ops,
            created_at,
            expires_at,
            owner_id,
            session_type,
        ),
    )
    conn.commit()

    written_shadow_keys: list[str] = []
    written_scope_keys: list[str] = []
    try:
        for consumer_id, key_id in _ssh_session_scope_pairs(candidate_session):
            _kv_delete_key(
                cf_creds,
                namespace_id,
                _ssh_session_scope_key(consumer_id, key_id),
            )
        for consumer_id in effective_consumer_ids:
            kv_key = f"session_token:{session_id}:{consumer_id}"
            _kv_put_text_value(
                cf_creds,
                namespace_id,
                kv_key,
                "1",
                expiration_ttl=ttl_seconds,
            )
            written_shadow_keys.append(kv_key)
        if max_sign_ops is not None:
            for consumer_id, key_id in _ssh_session_scope_pairs(candidate_session):
                scope_key = _ssh_session_scope_key(consumer_id, key_id)
                _kv_put_text_value(
                    cf_creds,
                    namespace_id,
                    scope_key,
                    json.dumps(
                        {
                            "session_id": session_id,
                            "consumer_id": consumer_id,
                            "key_id": key_id,
                            "expires_at": expires_at,
                            "max_sign_ops": max_sign_ops,
                        },
                        separators=(",", ":"),
                    ),
                    expiration_ttl=ttl_seconds,
                )
                written_scope_keys.append(scope_key)

        conn.execute(
            "UPDATE sessions SET status = 'active' WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
        active_sessions = [
            _session_row_to_dict(row)
            for row in _list_active_session_rows(conn)
        ]
        _reconcile_active_consumer_gates(
            cf_creds,
            namespace_id,
            effective_consumer_ids,
            active_sessions,
        )
    except SystemExit:
        for kv_key in written_shadow_keys:
            try:
                _kv_delete_key(cf_creds, namespace_id, kv_key)
            except SystemExit:
                pass
        for scope_key in written_scope_keys:
            try:
                _kv_delete_key(cf_creds, namespace_id, scope_key)
            except SystemExit:
                pass
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()
        remaining_active_sessions = [
            _session_row_to_dict(row)
            for row in _list_active_session_rows(conn)
        ]
        try:
            _reconcile_active_consumer_gates(
                cf_creds,
                namespace_id,
                effective_consumer_ids,
                remaining_active_sessions,
            )
        except SystemExit:
            pass
        raise

    ok(f"Started session {session_id}")
    info(f"expires_at={expires_at}")
    info(f"allowed_consumers={_format_session_scope(stored_consumer_scope)}")
    info(f"allowed_keys={_format_session_scope(stored_key_scope)}")
    if max_queries is not None:
        info(f"max_queries={max_queries}")
    if max_sign_ops is not None:
        info(f"max_sign_ops={max_sign_ops}")


def run_session_end() -> None:
    args = _session_args("--session")
    if not args or args[0] != "end":
        die("--session end requires the 'end' subcommand immediately after --session")

    target_session_id = None
    if len(args) >= 2 and not args[1].startswith("--"):
        target_session_id = args[1]
    end_all = "--all" in args[1:]
    if target_session_id is not None and end_all:
        die("--session end <session_id> and --all are mutually exclusive")
    for token in args[1:]:
        if token == "--all":
            continue
        if target_session_id is not None and token == target_session_id:
            continue
        die(f"Unknown --session end argument: {token}")

    conn = _open_session_db()
    active_rows = _list_active_session_rows(conn)
    if not active_rows:
        die("No active session exists.")
    active_sessions = [_session_row_to_dict(row) for row in active_rows]
    cf_creds = _get_push_registry_cf_creds()
    namespace_id = _load_kv_namespace_id()

    target_sessions: list[dict[str, Any]]
    if end_all:
        target_sessions = list(active_sessions)
    elif target_session_id is not None:
        target_row = _get_session_row_by_id(conn, target_session_id)
        if target_row is None or str(target_row["status"]) != "active":
            die(f"Active session {target_session_id!r} not found")
        target_sessions = [_session_row_to_dict(target_row)]
    elif len(active_sessions) == 1:
        target_sessions = [active_sessions[0]]
    elif sys.stdin.isatty():
        print("Multiple active sessions:")
        for idx, session_dict in enumerate(active_sessions, start=1):
            print(f"[{idx}] {session_dict['session_id']} {session_dict['name'] or '(none)'}")
        selection = input("Close which session? [number]: ").strip()
        if not selection.isdigit():
            die("Expected a session number")
        selected_index = int(selection)
        if selected_index < 1 or selected_index > len(active_sessions):
            die("Selected session number is out of range")
        target_sessions = [active_sessions[selected_index - 1]]
    else:
        active_ids = ", ".join(session_dict["session_id"] for session_dict in active_sessions)
        die(
            "Multiple active sessions exist; use ./bootstrap.sh --session end <session_id> "
            f"or --all. active_session_ids={active_ids}"
        )

    current_active_sessions = list(active_sessions)
    for session_dict in target_sessions:
        consumer_ids = _effective_session_consumer_ids(session_dict)
        remaining_sessions = [
            active_session
            for active_session in current_active_sessions
            if active_session["session_id"] != session_dict["session_id"]
        ]
        _reconcile_active_consumer_gates(
            cf_creds,
            namespace_id,
            consumer_ids,
            remaining_sessions,
        )
        for consumer_id in consumer_ids:
            _kv_delete_key(
                cf_creds,
                namespace_id,
                f"session_token:{session_dict['session_id']}:{consumer_id}",
            )
        if session_dict["max_sign_ops"] is not None:
            for consumer_id, key_id in _ssh_session_scope_pairs(session_dict):
                _kv_delete_key(
                    cf_creds,
                    namespace_id,
                    _ssh_session_scope_key(consumer_id, key_id),
                )
        conn.execute(
            "UPDATE sessions SET status = 'closed' WHERE session_id = ? AND status = 'active'",
            (session_dict["session_id"],),
        )
        conn.commit()
        current_active_sessions = remaining_sessions
        ok(f"Closed session {session_dict['session_id']}")


def run_session_status() -> None:
    args = _session_args("--session")
    if not args or args[0] != "status":
        die("--session status requires the 'status' subcommand immediately after --session")

    conn = _open_session_db()
    row = conn.execute("SELECT lockdown_enabled FROM lockdown_config WHERE id = 1").fetchone()
    lockdown_enabled = True if row is None else bool(row["lockdown_enabled"])
    active_sessions = [
        _session_row_to_dict(active_row)
        for active_row in _list_active_session_rows(conn)
    ]

    print(f"lockdown_enabled={str(lockdown_enabled).lower()}")
    if not active_sessions:
        print("active_sessions=0")
        return

    print(f"active_sessions={len(active_sessions)}")
    for idx, session_dict in enumerate(active_sessions, start=1):
        print(f"[{idx}]")
        _print_session_row("  ", session_dict)
        print(f"  ttl_remaining={_fmt_duration(_session_remaining_ttl_seconds(session_dict))}")


def run_session_list() -> None:
    args = _session_args("--session")
    if not args or args[0] != "list":
        die("--session list requires the 'list' subcommand immediately after --session")

    conn = _open_session_db()
    rows = conn.execute(
        """
        SELECT session_id, name, allowed_consumers, allowed_keys, max_queries,
               max_sign_ops, queries_used, ssh_sign_count, created_at, expires_at,
               status, owner_id, session_type
        FROM sessions
        ORDER BY created_at DESC
        LIMIT 20
        """
    ).fetchall()
    if not rows:
        print("no_sessions")
        return
    for idx, row in enumerate(rows, start=1):
        print(f"[{idx}]")
        _print_session_row("  ", _session_row_to_dict(row))
