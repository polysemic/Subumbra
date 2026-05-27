#!/usr/bin/env python3
"""Shared bootstrap constants and helpers for Subumbra."""

from __future__ import annotations

import gc
import hashlib
import json
import os
import yaml
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import termios
import tty
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Iterable, NoReturn

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from subumbra_ssh import (
    SshBootstrapError,
    build_ssh_policy,
    operator_ssh_auth_sock,
    provision_generated_ssh_key,
    provision_imported_ssh_key,
    resolve_allowed_host_fingerprints,
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║       Subumbra Bootstrap — manifest-era V3 encryption            ║
║  API keys exist in RAM only.  Nothing sensitive is written.      ║
║                                                                  ║
║  Full / nuke:   ./bootstrap.sh   (host uses -it when interactive)║
║  Rotate:        ./bootstrap.sh --rotate                          ║
║  CI / auto:   ./bootstrap.sh + host .env.bootstrap secrets       ║
║  Day-2 / KV:   ./bootstrap.sh --push-registry, --provision, …    ║
║  Image update:  ./bootstrap.sh --upgrade                         ║
╚══════════════════════════════════════════════════════════════════╝
"""

# key_id validation: lowercase alphanumeric + underscores + hyphens, 3-64 chars
KEY_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{2,63}$')
ADAPTER_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,61}[a-z0-9]$')

DATA_DIR        = Path(os.environ.get("DATA_DIR", "/app/data"))
KEYS_FILE       = DATA_DIR / "keys.json"
RUNTIME_ENV_OUT = DATA_DIR / "runtime.env"
CF_RESOURCES_FILE = DATA_DIR / "cf-resources.json"
PUBLIC_KEY_FILE = DATA_DIR / "public_key.pem"
SYSTEM_INTEGRITY_FILE = DATA_DIR / "system-integrity.json"
HOST_ENV_FILE   = Path("/app/host-env")
WORKER_SRC      = Path("/app/worker")
MANIFEST_FILE   = Path("/app/manifest")
USER_TEMPLATES_DIR = Path("/app/user-templates")
KV_CONFIG_FILE = DATA_DIR / "kv-config.json"
SESSIONS_DB_FILE = DATA_DIR / "sessions.db"

# RAM-only secrets collected in interactive wizard mode (never written to disk here).
_WIZARD_SECRETS: dict[str, str] = {}

CATALOG_DIR = Path("/app/templates")
CATALOG_JSON_FILE = CATALOG_DIR / "catalog.json"
CATALOG_SIG_FILE = CATALOG_DIR / "catalog.sig"
# 64-char hex encoding of the 32-byte Ed25519 release public key.
# Update when rotating the release key; rebuild bootstrap image.
CATALOG_RELEASE_PUBKEY_HEX: str = (
    "596369765b3ed21312a1df175cc1ebe822e7f155ef2ddf27a1032d6b8dc89373"
)

_CATALOG_CACHE: dict[str, dict] | None = None
_ADAPTER_CATALOG_CACHE: dict[str, dict] | None = None

STRUCTURED_KV_SCHEMA_VERSION = "1"

ADAPTER_SCOPE_VARS: dict[str, str] = {
    "subumbra-proxy": "PROXY_ALLOWED_KEYS",
    "subumbra-probe": "PROBE_ALLOWED_KEYS",
    "subumbra-ui": "UI_ALLOWED_KEYS",
}
BUILTIN_ADAPTER_IDS = tuple(ADAPTER_SCOPE_VARS.keys())
BUILTIN_TOKEN_SUFFIXES = {"PROXY", "UI", "PROBE"}

POLICY_PROTOCOLS = {"openai_compatible", "http_rest"}
POLICY_CAPABILITY_CLASSES = {
    "llm",
    "payments_read",
    "payments_write",
    "source_control_read",
    "source_control_write",
    "email_send",
    "webhook_verify",
    "custom_rest",
}
POLICY_SOURCES = {"env", "import_path"}
POLICY_AUTH_SCHEMES = {"bearer", "basic", "header", "query"}
POLICY_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# ─────────────────────────────────────────────────────────────────────────────
# Logging helpers
# ─────────────────────────────────────────────────────────────────────────────

def step(msg: str) -> None:
    print(f"\n▶  {msg}", flush=True)

def ok(msg: str) -> None:
    print(f"   ✓  {msg}", flush=True)

def info(msg: str) -> None:
    print(f"   ·  {msg}", flush=True)

def warn(msg: str) -> None:
    print(f"   ⚠  {msg}", flush=True)

def die(msg: str) -> NoReturn:
    print(f"\n✗  ERROR: {msg}\n", file=sys.stderr, flush=True)
    sys.exit(1)


def _prompt_hidden_line(what: str) -> str:
    """Read one sensitive line without local echo using /dev/tty + termios.

    Prints ``Please enter your {what}:`` before reading (no generic password warning).
    """
    print(f"  Please enter your {what}:", flush=True)
    try:
        tty_in = open("/dev/tty", "rb", buffering=0)
    except OSError:
        die(
            "Cannot open /dev/tty for hidden input. Run with a TTY, e.g. "
            "`./bootstrap.sh` from an interactive shell (the host wrapper uses `docker compose run -it`)."
        )

    fd = tty_in.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        data = bytearray()
        while True:
            ch = tty_in.read(1)
            if not ch:
                break
            b = ch[0]
            if b in (10, 13):
                break
            if b in (8, 127):
                if data:
                    data.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if b == 3:
                raise KeyboardInterrupt
            if 32 <= b <= 126 or b >= 128:
                data.append(b)
                sys.stdout.write("*")
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        tty_in.close()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return data.decode("utf-8", errors="replace").strip()


class BootstrapFlowError(RuntimeError):
    """Raised for recoverable per-key bootstrap failures."""


class AutomationInputError(RuntimeError):
    """Raised when interactive operators should be offered a fallback path."""


def _automation_fail(msg: str) -> NoReturn:
    if sys.stdin.isatty():
        raise AutomationInputError(msg)
    die(msg)


def _parse_bool_flag(raw: str, *, flag_name: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    die(f"{flag_name} must be true/false")


def _unique_key_env_var_name(key_id: str) -> str:
    return f"UNIQUE_KEY_{key_id}"


def _load_unique_key_flags(key_ids: list[str]) -> dict[str, bool]:
    return {
        key_id: _parse_bool_flag(
            os.environ.get(_unique_key_env_var_name(key_id), ""),
            flag_name=_unique_key_env_var_name(key_id),
        )
        for key_id in key_ids
    }


def _manifest_die(message: str) -> NoReturn:
    die(f"manifest: {message}")


def _vault_instance_for_key(key_id: str, unique_key_flags: dict[str, bool]) -> str:
    if unique_key_flags.get(key_id, False):
        return f"vault-{key_id}"
    return "vault"


def _public_key_file_for_key(key_id: str, vault_instance: str) -> Path:
    if vault_instance == "vault":
        return PUBLIC_KEY_FILE
    return DATA_DIR / f"public_key_{key_id}.pem"


def _representative_key_id_for_vault_instance(
    key_ids: Iterable[str],
    unique_key_flags: dict[str, bool],
    vault_instance: str,
) -> str | None:
    for key_id in sorted(key_ids):
        if _vault_instance_for_key(key_id, unique_key_flags) == vault_instance:
            return key_id
    return None


def _chown_to_subumbra(path: Path) -> None:
    try:
        os.chown(path, 1000, 1000)
    except OSError:
        pass


def _secure_data_dir() -> None:
    """Ensure DATA_DIR and all files in it have secure permissions (0o750/0o640) and correct ownership (1000:1000)"""
    try:
        if DATA_DIR.exists():
            os.chmod(DATA_DIR, 0o750)
            _chown_to_subumbra(DATA_DIR)
            
            for item in DATA_DIR.rglob("*"):
                try:
                    if item.is_dir():
                        os.chmod(item, 0o750)
                    else:
                        current_mode = os.stat(item).st_mode & 0o777
                        if current_mode == 0o600:
                            os.chmod(item, 0o600)
                        else:
                            os.chmod(item, 0o640)
                    _chown_to_subumbra(item)
                except OSError:
                    pass
    except Exception as exc:
        print(f"Warning: failed to secure data directory: {exc}")


def _write_public_key_file(path: Path, public_key_pem: str) -> None:
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "wb") as fh:
            fh.write(public_key_pem.encode("utf-8"))
        _chown_to_subumbra(path)
        _secure_data_dir()
    except OSError as exc:
        die(f"Failed to write {path.name}: {exc}")


def _delete_file_if_present(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        die(f"Failed to delete {path}: {exc}")


def _load_public_key_from_pem(public_key_pem: str):
    try:
        return serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except Exception as exc:
        die(f"Failed to load returned public key: {exc}")


def _write_keys_payload(keys_payload: dict[str, Any]) -> None:
    tmp_keys = KEYS_FILE.with_suffix(".json.tmp")
    try:
        fd = os.open(str(tmp_keys), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as fh:
            json.dump(keys_payload, fh, indent=2)
            fh.write("\n")
        _chown_to_subumbra(tmp_keys)
        os.replace(str(tmp_keys), str(KEYS_FILE))
        _secure_data_dir()
    except OSError as exc:
        die(f"Failed to write keys.json: {exc}")


def _build_runtime_env_lines(
    *,
    now_iso: str,
    adapter_registry: dict[str, dict[str, Any]],
    allowed_keys_by_adapter: dict[str, list[str]],
    adapter_tokens: dict[str, str],
    subumbra_hmac_key: str,
    management_token: str,
    worker_url: str,
    primary_pub_key_fp: str,
) -> list[str]:
    runtime_env_lines = [
        f"# Generated by subumbra-bootstrap on {now_iso}",
        "# PRIVILEGED — treat like an API key; restrict access to this file",
        f"SUBUMBRA_ADAPTER_REGISTRY={json.dumps(adapter_registry, separators=(',', ':'))}",
        f"PROXY_ALLOWED_KEYS={','.join(allowed_keys_by_adapter['subumbra-proxy'])}",
        f"UI_ALLOWED_KEYS={','.join(allowed_keys_by_adapter['subumbra-ui'])}",
        f"SUBUMBRA_TOKEN_PROXY={adapter_tokens['subumbra-proxy']}",
        f"SUBUMBRA_TOKEN_UI={adapter_tokens['subumbra-ui']}",
        f"SUBUMBRA_MANAGEMENT_TOKEN={management_token}",
    ]
    if "subumbra-probe" in allowed_keys_by_adapter:
        runtime_env_lines.append(
            f"PROBE_ALLOWED_KEYS={','.join(allowed_keys_by_adapter['subumbra-probe'])}"
        )
        runtime_env_lines.append(
            f"SUBUMBRA_TOKEN_PROBE={adapter_tokens['subumbra-probe']}"
        )
    for adapter_id in allowed_keys_by_adapter:
        if adapter_id not in BUILTIN_ADAPTER_IDS:
            runtime_env_lines.append(
                f"SUBUMBRA_TOKEN_{_normalize_adapter_id(adapter_id)}={adapter_tokens[adapter_id]}"
            )
    runtime_env_lines.extend(
        [
            f"SUBUMBRA_HMAC_KEY={subumbra_hmac_key}",
            f"CF_WORKER_URL={worker_url}",
            "# Public key fingerprint (audit trail — not sensitive)",
            f"WORKER_KEY_FINGERPRINT={primary_pub_key_fp}",
        ]
    )
    return runtime_env_lines


def _build_host_env_updates(
    *,
    adapter_registry: dict[str, dict[str, Any]],
    allowed_keys_by_adapter: dict[str, list[str]],
    adapter_tokens: dict[str, str],
    subumbra_hmac_key: str,
    management_token: str,
    worker_url: str,
    worker_name: str,
    setup_token: str,
    cf_runtime_creds: dict[str, str] | None = None,
) -> dict[str, str]:
    host_env_updates = {
        "SUBUMBRA_ADAPTER_REGISTRY": json.dumps(adapter_registry, separators=(",", ":")),
        "PROXY_ALLOWED_KEYS": ",".join(allowed_keys_by_adapter["subumbra-proxy"]),
        "UI_ALLOWED_KEYS": ",".join(allowed_keys_by_adapter["subumbra-ui"]),
        "SUBUMBRA_TOKEN_PROXY": adapter_tokens["subumbra-proxy"],
        "SUBUMBRA_TOKEN_UI": adapter_tokens["subumbra-ui"],
        "SUBUMBRA_MANAGEMENT_TOKEN": management_token,
        "SUBUMBRA_HMAC_KEY": subumbra_hmac_key,
        "CF_WORKER_URL": worker_url,
        "CF_WORKER_NAME": worker_name,
        "SUBUMBRA_SETUP_TOKEN": setup_token,
    }
    if "subumbra-probe" in allowed_keys_by_adapter:
        host_env_updates["PROBE_ALLOWED_KEYS"] = ",".join(allowed_keys_by_adapter["subumbra-probe"])
        host_env_updates["SUBUMBRA_TOKEN_PROBE"] = adapter_tokens["subumbra-probe"]
    for adapter_id in allowed_keys_by_adapter:
        if adapter_id not in BUILTIN_ADAPTER_IDS:
            host_env_updates[f"SUBUMBRA_TOKEN_{_normalize_adapter_id(adapter_id)}"] = adapter_tokens[adapter_id]
    if cf_runtime_creds:
        for key in ("TUNNEL_TOKEN", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"):
            val = cf_runtime_creds.get(key, "").strip()
            if val:
                host_env_updates[key] = val
    return host_env_updates


def _write_runtime_env_file(runtime_env_lines: list[str]) -> None:
    runtime_env_content = "\n".join(runtime_env_lines) + "\n"
    try:
        fd = os.open(str(RUNTIME_ENV_OUT), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(runtime_env_content)
        _chown_to_subumbra(RUNTIME_ENV_OUT)
        _secure_data_dir()
    except OSError as exc:
        die(f"Failed to write runtime.env: {exc}")
    ok("Runtime env written with mode 0600")


def _sync_host_env_file(host_env_updates: dict[str, str]) -> None:
    if HOST_ENV_FILE.is_file():
        try:
            _upsert_env_file(HOST_ENV_FILE, host_env_updates)
            os.chmod(HOST_ENV_FILE, 0o600)
        except OSError as exc:
            die(f"Failed to update host env file {HOST_ENV_FILE}: {exc}")
        ok(f"Repo-local env updated via {HOST_ENV_FILE}")
    else:
        die(
            f"Host env file {HOST_ENV_FILE} is missing or not a regular file.\n"
            "  Bootstrap must bind-mount the repo-root .env at that path so runtime tokens can be written."
        )


def _read_env_file_value(path: Path, key: str) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            continue
        lhs, rhs = raw.split("=", 1)
        if lhs.strip() != key:
            continue
        value = rhs.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return ""


def _read_runtime_credential_value(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if value:
        return value
    return _read_env_file_value(HOST_ENV_FILE, key).strip()



def _load_policy_path_from_env() -> str:
    return os.environ.get("SUBUMBRA_POLICY_PATH", "").strip()


def _policy_die(source: str, message: str) -> NoReturn:
    die(f"{source}: {message}")


def _policy_require_string(
    value: Any,
    source: str,
    field_name: str,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        _policy_die(source, f"{field_name} must be a string")
    if not allow_empty and not value:
        _policy_die(source, f"{field_name} must be a non-empty string")
    return value


def _is_safe_literal_pattern(body: str) -> bool:
    if not body:
        return False
    return re.fullmatch(r"[A-Za-z0-9_./:@%+=, -]+", body) is not None


def _validate_safe_pattern(value: Any, source: str, field_name: str) -> None:
    if not isinstance(value, str):
        _policy_die(source, f"{field_name} must be a string")
    if _is_safe_literal_pattern(value):
        return
    _policy_die(
        source,
        f'{field_name} must be a bare safe substring like "api_key"'
    )


def _normalize_policy_doc(doc: dict[str, Any], source: str) -> dict[str, Any]:
    required_top = {"key_id", "policy_id", "protocol", "capability_class", "source", "target", "auth", "allow"}
    missing = sorted(required_top - doc.keys())
    if missing:
        _policy_die(source, f"missing required field(s): {', '.join(missing)}")

    key_id = _policy_require_string(doc.get("key_id"), source, "key_id")
    if not KEY_ID_RE.fullmatch(key_id):
        _policy_die(source, f"key_id {key_id!r} is invalid")

    policy_id = _policy_require_string(doc.get("policy_id"), source, "policy_id")
    protocol = _policy_require_string(doc.get("protocol"), source, "protocol")
    if protocol not in POLICY_PROTOCOLS:
        _policy_die(source, f"protocol {protocol!r} is invalid")

    capability_class = _policy_require_string(doc.get("capability_class"), source, "capability_class")
    if capability_class not in POLICY_CAPABILITY_CLASSES:
        _policy_die(source, f"capability_class {capability_class!r} is invalid")

    policy_source = _policy_require_string(doc.get("source"), source, "source")
    if policy_source not in POLICY_SOURCES:
        _policy_die(source, f"source {policy_source!r} is invalid")

    target = doc.get("target")
    if not isinstance(target, dict):
        _policy_die(source, "target must be an object")
    target_host = _policy_require_string(target.get("host"), source, "target.host")
    if target_host == "*" or "*" in target_host:
        _policy_die(source, "target.host cannot contain wildcard '*'")
    parsed_host = urllib.parse.urlsplit(target_host)
    if parsed_host.scheme or parsed_host.netloc or "/" in target_host or "?" in target_host or "#" in target_host:
        _policy_die(source, "target.host must be an exact host with no scheme, path, query, or fragment")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", target_host):
        _policy_die(source, f"target.host {target_host!r} is invalid")

    base_path = target.get("base_path")
    if base_path is not None:
        base_path = _policy_require_string(base_path, source, "target.base_path")
        if not base_path.startswith("/"):
            _policy_die(source, "target.base_path must start with '/'")

    auth = doc.get("auth")
    if not isinstance(auth, dict):
        _policy_die(source, "auth must be an object")
    auth_scheme = _policy_require_string(auth.get("scheme"), source, "auth.scheme")
    if auth_scheme not in POLICY_AUTH_SCHEMES:
        _policy_die(source, f"auth.scheme {auth_scheme!r} is invalid")
    if auth_scheme == "header":
        _policy_require_string(auth.get("header_name"), source, "auth.header_name")
    if auth_scheme == "query":
        _policy_require_string(auth.get("query_param"), source, "auth.query_param")
        if auth.get("allow_query") is not True:
            _policy_die(source, "auth.scheme 'query' requires auth.allow_query: true")

    allow = doc.get("allow")
    if not isinstance(allow, dict):
        _policy_die(source, "allow must be an object")
    adapters = allow.get("adapters")
    if not isinstance(adapters, list) or not adapters:
        _policy_die(source, "allow.adapters must be a non-empty array")
    for idx, adapter in enumerate(adapters):
        adapter = _policy_require_string(adapter, source, f"allow.adapters[{idx}]")
        if not ADAPTER_ID_RE.fullmatch(adapter):
            _policy_die(source, f"allow.adapters[{idx}] {adapter!r} is invalid")
    methods = allow.get("methods")
    if not isinstance(methods, list) or not methods:
        _policy_die(source, "allow.methods must be a non-empty array")
    for idx, method in enumerate(methods):
        method = _policy_require_string(method, source, f"allow.methods[{idx}]")
        if method not in POLICY_ALLOWED_METHODS:
            _policy_die(source, f"allow.methods[{idx}] {method!r} is invalid")
    path_prefixes = allow.get("path_prefixes")
    if not isinstance(path_prefixes, list) or not path_prefixes:
        _policy_die(source, "allow.path_prefixes must be a non-empty array")
    for idx, path_prefix in enumerate(path_prefixes):
        path_prefix = _policy_require_string(path_prefix, source, f"allow.path_prefixes[{idx}]")
        if path_prefix in {"", "*", "/"}:
            _policy_die(source, f"allow.path_prefixes[{idx}] {path_prefix!r} is rejected")
        if "*" in path_prefix:
            _policy_die(source, f"allow.path_prefixes[{idx}] cannot contain '*'")
        if not path_prefix.startswith("/"):
            _policy_die(source, f"allow.path_prefixes[{idx}] must start with '/'")
    content_types = allow.get("content_types")
    if not isinstance(content_types, list) or not content_types:
        _policy_die(source, "allow.content_types must be a non-empty array")
    for idx, content_type in enumerate(content_types):
        _policy_require_string(content_type, source, f"allow.content_types[{idx}]")
    request_headers = allow.get("request_headers")
    if request_headers is not None:
        if not isinstance(request_headers, list):
            _policy_die(source, "allow.request_headers must be an array")
        for idx, header_name in enumerate(request_headers):
            _policy_require_string(header_name, source, f"allow.request_headers[{idx}]")
    max_body_bytes = allow.get("max_body_bytes")
    if not isinstance(max_body_bytes, int) or isinstance(max_body_bytes, bool) or max_body_bytes <= 0:
        _policy_die(source, "allow.max_body_bytes must be a positive integer")

    deny = doc.get("deny")
    if deny is not None:
        if not isinstance(deny, dict):
            _policy_die(source, "deny must be an object")
        deny_prefixes = deny.get("path_prefixes")
        if deny_prefixes is not None:
            if not isinstance(deny_prefixes, list):
                _policy_die(source, "deny.path_prefixes must be an array")
            for idx, path_prefix in enumerate(deny_prefixes):
                path_prefix = _policy_require_string(path_prefix, source, f"deny.path_prefixes[{idx}]")
                if not path_prefix.startswith("/"):
                    _policy_die(source, f"deny.path_prefixes[{idx}] must start with '/'")

    intent = doc.get("intent")
    if intent is not None:
        if not isinstance(intent, dict):
            _policy_die(source, "intent must be an object")
        policy_match = intent.get("policy_match")
        if policy_match is not None:
            _validate_safe_pattern(policy_match, source, "intent.policy_match")
        trust = intent.get("trust")
        if trust is not None:
            if not isinstance(trust, dict):
                _policy_die(source, "intent.trust must be an object")
            for field_name in ("allowed_initiators", "allowed_content_sources"):
                field_value = trust.get(field_name)
                if field_value is None:
                    continue
                if not isinstance(field_value, list):
                    _policy_die(source, f"intent.trust.{field_name} must be an array")
                for idx, entry in enumerate(field_value):
                    _policy_require_string(
                        entry,
                        source,
                        f"intent.trust.{field_name}[{idx}]",
                    )

    response = doc.get("response")
    if response is not None:
        if not isinstance(response, dict):
            _policy_die(source, "response must be an object")
        allow_headers = response.get("allow_headers")
        if allow_headers is not None:
            if not isinstance(allow_headers, list):
                _policy_die(source, "response.allow_headers must be an array")
            for idx, header_name in enumerate(allow_headers):
                _policy_require_string(header_name, source, f"response.allow_headers[{idx}]")
        deny_patterns = response.get("deny_patterns")
        if deny_patterns is not None:
            if not isinstance(deny_patterns, list):
                _policy_die(source, "response.deny_patterns must be an array")
            for idx, pattern in enumerate(deny_patterns):
                _validate_safe_pattern(pattern, source, f"response.deny_patterns[{idx}]")

    velocity = doc.get("velocity")
    if velocity is not None:
        if not isinstance(velocity, dict):
            _policy_die(source, "velocity must be an object")
        _velocity_fields = {"adapter_rpm", "key_rpm", "breaker_failures", "breaker_cooldown_seconds"}
        for _vk, _vv in velocity.items():
            if _vk not in _velocity_fields:
                _policy_die(source, f"velocity.{_vk} is not a recognized field")
            if not isinstance(_vv, int) or _vv <= 0:
                _policy_die(source, f"velocity.{_vk} must be a positive integer")

    return doc


def _load_policy_index() -> dict[str, dict[str, Any]]:
    policy_path = _load_policy_path_from_env()
    if not policy_path:
        return {}
    try:
        with open(policy_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except OSError as exc:
        die(f"SUBUMBRA_POLICY_PATH unreadable: {exc}")
    except json.JSONDecodeError as exc:
        die(f"SUBUMBRA_POLICY_PATH invalid JSON: {exc}")
    if not isinstance(payload, list):
        die("SUBUMBRA_POLICY_PATH must contain a top-level JSON array")
    index: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(payload):
        source = f"SUBUMBRA_POLICY_PATH[{idx}]"
        if not isinstance(item, dict):
            _policy_die(source, "policy document must be an object")
        normalized = _normalize_policy_doc(item, source)
        key_id = normalized["key_id"]
        if key_id in index:
            die(f"SUBUMBRA_POLICY_PATH duplicate key_id {key_id!r}")
        index[key_id] = normalized
    return index


def _resolve_manifest_secret(secret_ref: str) -> str:
    if not isinstance(secret_ref, str) or not secret_ref.strip():
        _manifest_die("secret_ref must be a non-empty string")
    cached = _WIZARD_SECRETS.get(secret_ref, "").strip()
    if cached:
        return cached
    resolved = os.environ.get(secret_ref, "").strip()
    if resolved:
        return resolved
    file_val = _read_env_file_value(HOST_ENV_FILE, secret_ref).strip()
    if file_val:
        return file_val
    if sys.stdin.isatty():
        warn(
            f"secret_ref {secret_ref!r} is not in the process environment or repo .env — "
            "enter the provider secret once for this command (RAM only; bootstrap does not write it to disk)."
        )
        value = _prompt_hidden_line(
            f"provider secret / API key for manifest secret_ref {secret_ref!r}"
        )
        if not value:
            _manifest_die(f"secret_ref {secret_ref!r} cannot be empty")
        _WIZARD_SECRETS[secret_ref] = value
        return value
    _manifest_die(
        f"secret_ref {secret_ref!r} is missing or empty — set {secret_ref} in the environment "
        f"(e.g. `.env.bootstrap` loaded by docker compose), add {secret_ref}=... to the repo `.env` "
        "host mount, or run `./bootstrap.sh` day-2 commands from an interactive terminal so bootstrap can prompt."
    )


def _effective_manifest_adapters(adapters: list[str]) -> list[str]:
    return list(adapters) if adapters else ["subumbra-proxy"]


def _load_and_verify_catalog() -> dict[str, dict]:
    """Load catalog.json, verify Ed25519 signature and per-template SHA-256.
    Returns dict mapping provider name → template dict. Fail-closed on any error."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    if not CATALOG_JSON_FILE.exists():
        die("Template catalog missing: /app/templates/catalog.json")
    if not CATALOG_SIG_FILE.exists():
        die("Template catalog signature missing: /app/templates/catalog.sig")

    catalog_bytes = CATALOG_JSON_FILE.read_bytes()
    sig_bytes = CATALOG_SIG_FILE.read_bytes()

    try:
        pub_raw = bytes.fromhex(CATALOG_RELEASE_PUBKEY_HEX)
    except ValueError:
        die("CATALOG_RELEASE_PUBKEY_HEX is not valid hex")
    if len(pub_raw) != 32:
        die("CATALOG_RELEASE_PUBKEY_HEX must encode exactly 32 bytes")

    pub = Ed25519PublicKey.from_public_bytes(pub_raw)
    try:
        pub.verify(sig_bytes, catalog_bytes)
    except Exception:
        die("Template catalog signature verification failed")

    try:
        catalog_doc = json.loads(catalog_bytes)
    except json.JSONDecodeError as exc:
        die(f"Template catalog JSON is invalid: {exc}")

    result: dict[str, dict] = {}

    for entry in catalog_doc.get("providers", []):
        name: str = entry["name"]
        file_path = CATALOG_DIR / entry["file"]
        expected_sha256: str = entry["sha256"]
        if not file_path.exists():
            die(f"Template file missing: {entry['file']}")
        template_bytes = file_path.read_bytes()
        if hashlib.sha256(template_bytes).hexdigest() != expected_sha256:
            die(f"Template SHA-256 mismatch: {name}")
        try:
            template_doc = yaml.safe_load(template_bytes)
        except yaml.YAMLError as exc:
            die(f"Template {name!r} YAML is invalid: {exc}")
        if not isinstance(template_doc, dict):
            die(f"Template {name!r} top-level YAML value must be an object")
        result[name] = template_doc

    adapter_result: dict[str, dict] = {}
    for entry in catalog_doc.get("adapters", []):
        name = entry["name"]
        file_path = CATALOG_DIR / entry["file"]
        expected_sha256 = entry["sha256"]
        if not file_path.exists():
            die(f"Adapter template file missing: {entry['file']}")
        template_bytes = file_path.read_bytes()
        if hashlib.sha256(template_bytes).hexdigest() != expected_sha256:
            die(f"Template SHA-256 mismatch: adapter:{name}")
        try:
            template_doc = yaml.safe_load(template_bytes)
        except yaml.YAMLError as exc:
            die(f"Adapter template {name!r} YAML is invalid: {exc}")
        if not isinstance(template_doc, dict):
            die(f"Adapter template {name!r} top-level YAML value must be an object")
        adapter_result[name] = template_doc

    global _ADAPTER_CATALOG_CACHE
    _ADAPTER_CATALOG_CACHE = adapter_result
    _CATALOG_CACHE = result
    return _CATALOG_CACHE


def _load_adapter_catalog() -> dict[str, dict]:
    """Return adapter name → adapter template dict.
    Calls _load_and_verify_catalog() first so signature and SHA-256 are always verified."""
    global _ADAPTER_CATALOG_CACHE
    if _ADAPTER_CATALOG_CACHE is None:
        _load_and_verify_catalog()
    return _ADAPTER_CATALOG_CACHE or {}


def _expand_template_into_policy(
    template: dict[str, Any],
    key_id: str,
    policy_id: str,
    effective_adapters: list[str],
    operator_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge a verified provider template with operator-supplied fields.
    Returns a policy dict ready for _normalize_policy_doc()."""
    policy: dict[str, Any] = {}

    for field in ("protocol", "capability_class"):
        if field in template:
            policy[field] = template[field]
    if "target" in template:
        policy["target"] = dict(template["target"])
    if "auth" in template:
        policy["auth"] = dict(template["auth"])

    allow: dict[str, Any] = {}
    if "allow" in template:
        allow.update(template["allow"])
    allow["adapters"] = effective_adapters
    policy["allow"] = allow

    for opt in ("response", "intent", "velocity", "deny"):
        if opt in template:
            policy[opt] = template[opt]

    policy["key_id"] = key_id
    policy["policy_id"] = policy_id
    policy["source"] = "env"

    if operator_overrides:
        for k, v in operator_overrides.items():
            if k in ("key_id", "source"):
                continue
            if k == "allow" and isinstance(v, dict):
                for ak, av in v.items():
                    if ak != "adapters":
                        policy["allow"][ak] = av
            else:
                policy[k] = v
        policy["key_id"] = key_id
        policy["source"] = "env"
        policy["allow"]["adapters"] = effective_adapters

    return policy


def _auth_metadata_from_policy(policy: dict[str, Any], source: str) -> tuple[str, str]:
    auth = policy.get("auth")
    if not isinstance(auth, dict):
        _policy_die(source, "auth must be an object")
    scheme = auth.get("scheme")
    if scheme == "bearer":
        return "authorization", "Bearer "
    if scheme == "basic":
        return "authorization", "Basic "
    if scheme == "header":
        header_name = auth.get("header_name")
        if not isinstance(header_name, str) or not header_name:
            _policy_die(source, "auth.header_name must be a non-empty string")
        return header_name, ""
    if scheme == "query":
        return "", ""
    _policy_die(source, f"auth.scheme {scheme!r} is invalid")


def _load_local_template(name: str) -> dict | None:
    """Return the parsed template dict from USER_TEMPLATES_DIR if present, else None.

    Logs a warning and returns None (falls back to built-in catalog) if the
    file exists but cannot be read or parsed — never silently discards errors.
    """
    if not USER_TEMPLATES_DIR.is_dir():
        return None
    candidate = USER_TEMPLATES_DIR / f"{name}.yaml"
    if not candidate.is_file():
        return None
    try:
        data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except OSError as exc:
        warn(
            f"Local template {name!r} at {candidate} unreadable ({exc}); "
            "falling back to built-in catalog"
        )
        return None
    except yaml.YAMLError as exc:
        warn(
            f"Local template {name!r} at {candidate} is invalid YAML ({exc}); "
            "falling back to built-in catalog"
        )
        return None
    if not isinstance(data, dict):
        warn(
            f"Local template {name!r} at {candidate} top-level value is not an object; "
            "falling back to built-in catalog"
        )
        return None
    return data


def _load_keys_payload_or_die() -> dict[str, dict[str, Any]]:
    if not KEYS_FILE.exists():
        die("keys.json not found — run a full bootstrap first.")
    try:
        payload = json.loads(KEYS_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        die(f"Cannot read keys.json: {exc}")
    if not isinstance(payload, dict):
        die("keys.json is malformed")
    return payload


def _load_keys_payload_if_present() -> dict[str, dict[str, Any]]:
    if not KEYS_FILE.exists():
        return {}
    return _load_keys_payload_or_die()


def _format_adapter_line(adapter_ids: Iterable[str]) -> str:
    return "[" + ", ".join(adapter_ids) + "]"


def _rewrite_manifest_adapters_line(target_key_id: str, adapter_ids: list[str]) -> tuple[bool, str]:
    """Best-effort manifest sync for canonical single-line YAML adapter lists only."""
    try:
        manifest_text = MANIFEST_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"manifest unreadable ({exc})"

    stanza_pattern = re.compile(
        rf"(?ms)^([ \t]*)-\s+key_id:\s*{re.escape(target_key_id)}\s*$"
        rf"(.*?)(?=^[ \t]*-\s+key_id:\s*|\Z)"
    )
    stanza_match = stanza_pattern.search(manifest_text)
    if stanza_match is None:
        return False, "target key stanza not found in canonical YAML item form"

    stanza_text = stanza_match.group(0)
    adapters_pattern = re.compile(r"(?m)^([ \t]*adapters:\s*)\[[^\n]*\]\s*$")
    adapters_match = adapters_pattern.search(stanza_text)
    if adapters_match is None:
        return False, "adapters line is not in canonical single-line form"

    replacement = adapters_match.group(1) + _format_adapter_line(adapter_ids)
    rewritten_stanza = adapters_pattern.sub(replacement, stanza_text, count=1)
    rewritten_manifest = (
        manifest_text[:stanza_match.start()] +
        rewritten_stanza +
        manifest_text[stanza_match.end():]
    )

    try:
        MANIFEST_FILE.write_text(rewritten_manifest, encoding="utf-8")
    except OSError as exc:
        return False, f"manifest write failed ({exc})"
    return True, "updated"


def _prompt_manifest_sync_after_adapter_mutation(target_key_id: str, adapters: list[str]) -> None:
    prompt = (
        f"  Deployed record for {target_key_id!r} changed. Also update subumbra.yaml "
        f"adapters line to {_format_adapter_line(adapters)}? [y/N]: "
    )
    if not sys.stdin.isatty():
        warn("Manifest sync prompt unavailable without a TTY; manual manifest update required.")
        warn("A later --publish-policy will restore manifest authority until the manifest is updated.")
        return

    try:
        choice = input(prompt).strip().lower()
    except EOFError:
        warn("Manifest sync prompt unavailable; manual manifest update required.")
        warn("A later --publish-policy will restore manifest authority until the manifest is updated.")
        return

    if choice != "y":
        warn("Manifest left unchanged. A later --publish-policy will restore manifest authority.")
        return

    synced, reason = _rewrite_manifest_adapters_line(target_key_id, adapters)
    if not synced:
        warn(
            "Manifest auto-sync skipped; manual manifest update required "
            f"({reason})."
        )
        return
    ok(f"Updated manifest adapters line for {target_key_id}")


def _normalize_manifest_record(record: Any, idx: int) -> dict[str, Any]:
    source = f"manifest.keys[{idx}]"
    if not isinstance(record, dict):
        _manifest_die(f"{source} must be an object")

    key_id = record.get("key_id")
    if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
        _manifest_die(f"{source}.key_id is invalid")

    record_type = record.get("type", "api_key")
    if not isinstance(record_type, str) or record_type not in {"api_key", "ssh_key"}:
        _manifest_die(f"{source}.type must be 'api_key' or 'ssh_key'")

    adapters = record.get("adapters")
    if not isinstance(adapters, list):
        _manifest_die(f"{source}.adapters must be an array")
    normalized_adapters: list[str] = []
    seen_adapters: set[str] = set()
    for adapter_idx, adapter_id in enumerate(adapters):
        if not isinstance(adapter_id, str) or not ADAPTER_ID_RE.fullmatch(adapter_id):
            _manifest_die(f"{source}.adapters[{adapter_idx}] is invalid")
        if adapter_id in BUILTIN_ADAPTER_IDS:
            _manifest_die(f"{source}.adapters[{adapter_idx}] {adapter_id!r} is reserved")
        if adapter_id in seen_adapters:
            continue
        seen_adapters.add(adapter_id)
        normalized_adapters.append(adapter_id)
    effective_adapters = _effective_manifest_adapters(normalized_adapters)

    unique_vault = record.get("unique_vault")
    if not isinstance(unique_vault, bool):
        _manifest_die(f"{source}.unique_vault must be true or false")

    if record_type == "ssh_key":
        key_source = record.get("key_source")
        if not isinstance(key_source, str) or key_source not in {"generated", "provided"}:
            _manifest_die(f"{source}.key_source must be 'generated' or 'provided'")
        secret_ref = record.get("secret_ref")
        if key_source == "provided":
            if not isinstance(secret_ref, str) or not secret_ref.strip():
                _manifest_die(f"{source}.secret_ref must be a non-empty string when key_source is 'provided'")
        else:
            secret_ref = None
        raw_allow = record.get("allow", {})
        if raw_allow is None:
            raw_allow = {}
        if not isinstance(raw_allow, dict):
            _manifest_die(f"{source}.allow must be an object when provided")
        raw_hosts = raw_allow.get("hosts")
        if raw_hosts is not None and not isinstance(raw_hosts, list):
            _manifest_die(f"{source}.allow.hosts must be an array when provided")
        requested_hosts: list[str] = []
        if isinstance(raw_hosts, list):
            for host_idx, host in enumerate(raw_hosts):
                if not isinstance(host, str) or not host.strip():
                    _manifest_die(f"{source}.allow.hosts[{host_idx}] must be a non-empty string")
                requested_hosts.append(host.strip())
        try:
            allowed_host_fingerprints = resolve_allowed_host_fingerprints(requested_hosts)
        except SshBootstrapError as exc:
            _manifest_die(f"{source}.allow.hosts could not be resolved: {exc}")

        policy = build_ssh_policy(
            key_id=key_id,
            adapters=effective_adapters,
            allowed_host_fingerprints=allowed_host_fingerprints,
        )
        return {
            "key_id": key_id,
            "type": "ssh_key",
            "provider": "ssh",
            "secret_ref": secret_ref,
            "key_source": key_source,
            "adapters": normalized_adapters,
            "effective_adapters": effective_adapters,
            "unique_vault": unique_vault,
            "policy": policy,
            "requested_allow_hosts": requested_hosts,
        }

    required = {"key_id", "provider", "secret_ref", "adapters", "unique_vault"}
    missing = sorted(required - record.keys())
    if missing:
        _manifest_die(f"{source} missing required field(s): {', '.join(missing)}")

    has_template = "template" in record
    has_policy = "policy" in record
    if not has_template and not has_policy:
        _manifest_die(f"{source} must provide either 'template' or 'policy'")

    provider = record.get("provider")
    if not isinstance(provider, str) or not provider:
        _manifest_die(f"{source}.provider must be a non-empty string")

    secret_ref = record.get("secret_ref")
    if not isinstance(secret_ref, str) or not secret_ref.strip():
        _manifest_die(f"{source}.secret_ref must be a non-empty string")

    template_name = record.get("template")
    if template_name is not None:
        if not isinstance(template_name, str) or not template_name:
            _manifest_die(f"{source}.template must be a non-empty string")
        template_data = _load_local_template(template_name)
        if template_data is None:
            catalog = _load_and_verify_catalog()
            if template_name not in catalog:
                _manifest_die(f"{source} template {template_name!r} not found in user-templates or built-in catalog")
            template_data = catalog[template_name]
        else:
            catalog = _load_and_verify_catalog()
            if template_name in catalog:
                warn(
                    f"Local template {template_name!r} shadows signed built-in catalog entry; "
                    "using local version (not signature-verified)"
                )
            else:
                info(f"Using local template for {template_name!r} from user templates directory")
        operator_overrides = record.get("policy") if isinstance(record.get("policy"), dict) else None
        policy_raw = _expand_template_into_policy(
            template=template_data,
            key_id=key_id,
            policy_id=f"{template_name}-{key_id}",
            effective_adapters=effective_adapters,
            operator_overrides=operator_overrides,
        )
    else:
        policy_raw = record.get("policy")
        if not isinstance(policy_raw, dict):
            _manifest_die(f"{source}.policy must be an object")
    normalized_policy = _normalize_policy_doc(
        policy_raw,
        f"{source}.policy (expanded from template {template_name!r})" if template_name is not None else f"{source}.policy",
    )
    if normalized_policy["key_id"] != key_id:
        _manifest_die(
            f"{source}.policy.key_id {normalized_policy['key_id']!r} does not match record key_id {key_id!r}"
        )
    if normalized_policy.get("source") != "env":
        _manifest_die(f"{source}.policy.source must be 'env' for direct secret bootstrap")
    if sorted(_policy_adapter_ids(normalized_policy)) != sorted(effective_adapters):
        _manifest_die(
            f"{source}.policy.allow.adapters does not match adapters for key_id {key_id!r}"
        )
    auth_header, auth_prefix = _auth_metadata_from_policy(normalized_policy, f"{source}.policy")

    return {
        "key_id": key_id,
        "type": "api_key",
        "provider": provider,
        "secret_ref": secret_ref,
        "adapters": normalized_adapters,
        "effective_adapters": effective_adapters,
        "unique_vault": unique_vault,
        "policy": normalized_policy,
        "target_host": normalized_policy["target"]["host"],
        "auth_header": auth_header,
        "auth_prefix": auth_prefix,
    }


def _load_manifest_records() -> list[dict[str, Any]]:
    if not MANIFEST_FILE.exists():
        _manifest_die(f"required manifest file is missing at {MANIFEST_FILE}")
    if not MANIFEST_FILE.is_file():
        _manifest_die(f"{MANIFEST_FILE} is not a regular file")

    try:
        payload = yaml.safe_load(MANIFEST_FILE.read_text(encoding="utf-8"))
    except OSError as exc:
        _manifest_die(f"unreadable manifest: {exc}")
    except yaml.YAMLError as exc:
        _manifest_die(f"invalid YAML/JSON: {exc}")

    if not isinstance(payload, dict):
        _manifest_die("top-level value must be an object")
    records = payload.get("keys")
    if not isinstance(records, list) or not records:
        _manifest_die("top-level 'keys' must be a non-empty array")

    normalized_records: list[dict[str, Any]] = []
    seen_key_ids: set[str] = set()
    seen_providers: set[str] = set()
    for idx, record in enumerate(records):
        normalized = _normalize_manifest_record(record, idx)
        key_id = normalized["key_id"]
        if key_id in seen_key_ids:
            _manifest_die(f"duplicate key_id {key_id!r}")
        seen_key_ids.add(key_id)
        if normalized.get("type") == "ssh_key":
            normalized_records.append(normalized)
            continue
        provider = normalized["provider"]
        if provider in seen_providers:
            warn(f"duplicate provider label {provider!r} — each key's provider should be a unique display name")
        seen_providers.add(provider)
        normalized_records.append(normalized)
    return normalized_records


def _load_manifest_key_ids_only() -> set[str]:
    if not MANIFEST_FILE.exists():
        _manifest_die(f"required manifest file is missing at {MANIFEST_FILE}")
    if not MANIFEST_FILE.is_file():
        _manifest_die(f"{MANIFEST_FILE} is not a regular file")

    try:
        payload = yaml.safe_load(MANIFEST_FILE.read_text(encoding="utf-8"))
    except OSError as exc:
        _manifest_die(f"unreadable manifest: {exc}")
    except yaml.YAMLError as exc:
        _manifest_die(f"invalid YAML/JSON: {exc}")

    if not isinstance(payload, dict):
        _manifest_die("top-level value must be an object")
    records = payload.get("keys")
    if not isinstance(records, list) or not records:
        _manifest_die("top-level 'keys' must be a non-empty array")

    key_ids: set[str] = set()
    for idx, record in enumerate(records):
        source = f"manifest.keys[{idx}]"
        if not isinstance(record, dict):
            _manifest_die(f"{source} must be an object")
        key_id = record.get("key_id")
        if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
            _manifest_die(f"{source}.key_id is invalid")
        if key_id in key_ids:
            _manifest_die(f"duplicate key_id {key_id!r}")
        key_ids.add(key_id)
    return key_ids


def _binding_policy_id(key_id: str, allowed_adapters: list[str]) -> str:
    if "subumbra-proxy" in allowed_adapters:
        return f"auto-compat-{key_id}"
    return f"auto-app-{key_id}"


def _write_system_integrity(worker_name: str, worker_url: str, bundle_sha256: str) -> None:
    # scripts/subumbra-verify-deploy reports integrity drift detected on mismatch.
    payload = {
        "worker_name": worker_name,
        "worker_url": worker_url,
        "bundle_sha256": bundle_sha256,
        "hash_algorithm": "sha256",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    SYSTEM_INTEGRITY_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _resolve_policy_for_key(
    key_id: str,
    provider: str,
    target_host: str,
    policy_index: dict[str, dict[str, Any]],
    allowed_adapters: list[str],
) -> dict[str, Any]:
    policy = policy_index.get(key_id)
    if policy is None:
        die(
            f"No policy found for key_id {key_id!r}.\n"
            "  Manifest-owned routing and policy authority is required after provider catalog removal."
        )
    if policy["target"]["host"] != target_host:
        die(
            f"Policy host conflict for key_id {key_id!r}: "
            f"policy target.host={policy['target']['host']!r} "
            f"does not match bootstrap target_host={target_host!r}"
        )
    if sorted(_policy_adapter_ids(policy)) != sorted(allowed_adapters):
        die(
            f"Policy adapter conflict for key_id {key_id!r}: "
            f"policy adapters={', '.join(_policy_adapter_ids(policy))} "
            f"do not match bootstrap adapters={', '.join(allowed_adapters)}"
        )
    return policy


def _require_fat_record_fields(record: dict[str, Any], key_id: str) -> tuple[dict[str, Any], list[str]]:
    policy = record.get("policy")
    if not isinstance(policy, dict):
        die(
            f"keys.json record {key_id!r} is missing embedded policy authority.\n"
            "  Repair the record or re-run full bootstrap."
        )
    adapters = record.get("adapters")
    if not isinstance(adapters, list) or not adapters or not all(isinstance(adapter_id, str) and adapter_id for adapter_id in adapters):
        die(
            f"keys.json record {key_id!r} is missing embedded adapter authority.\n"
            "  Repair the record or re-run full bootstrap."
        )
    return policy, list(adapters)


def _verify_embedded_policy_hash(record: dict[str, Any], key_id: str) -> None:
    policy, _adapters = _require_fat_record_fields(record, key_id)
    stored_policy_id = record.get("policy_id")
    if not isinstance(stored_policy_id, str) or not stored_policy_id.strip():
        die(
            f"keys.json record {key_id!r} is missing policy_id.\n"
            "  Repair the record or re-run full bootstrap."
        )
    if policy.get("policy_id") != stored_policy_id:
        die(
            f"Embedded policy mismatch for key_id {key_id!r}: stored policy_id does not match embedded policy.\n"
            "  Repair the record or re-run full bootstrap."
        )
    stored_policy_hash = record.get("policy_hash")
    if not isinstance(stored_policy_hash, str) or not stored_policy_hash.strip():
        die(
            f"keys.json record {key_id!r} is missing policy_hash.\n"
            "  Repair the record or re-run full bootstrap."
        )
    computed_policy_hash = compute_policy_hash(policy)
    if computed_policy_hash != stored_policy_hash:
        die(
            f"Embedded policy mismatch for key_id {key_id!r}: stored policy_hash does not match embedded policy.\n"
            "  Repair the record or re-run full bootstrap."
        )


def _load_manifest_repair_authority(target_key_id: str) -> dict[str, Any]:
    if not MANIFEST_FILE.exists():
        die(
            f"Cannot repair key_id {target_key_id!r}: manifest is unavailable.\n"
            "  Re-run full bootstrap with a manifest that declares this key."
        )
    for record in _load_manifest_records():
        if record["key_id"] == target_key_id:
            if record.get("type") == "ssh_key":
                return {
                    "provider": record["provider"],
                    "policy": record["policy"],
                    "adapters": list(record["effective_adapters"]),
                    "key_source": record["key_source"],
                    "secret_ref": record.get("secret_ref"),
                }
            return {
                "provider": record["provider"],
                "target_host": record["target_host"],
                "raw_secret": _resolve_manifest_secret(record["secret_ref"]),
                "vault_instance": _vault_instance_for_key(target_key_id, {target_key_id: record["unique_vault"]}),
                "policy": record["policy"],
                "adapters": list(record["adapters"]),
                "auth_header": record["auth_header"],
                "auth_prefix": record["auth_prefix"],
                "template_name": record.get("template"),
            }
    die(
        f"Cannot repair key_id {target_key_id!r}: manifest does not declare that key.\n"
        "  Re-run full bootstrap or add the key to the manifest."
    )


def _is_revoked_record(record: dict[str, Any]) -> bool:
    return record.get("revoked") is True


def _build_structured_kv_entries(
    keys_payload: dict[str, dict[str, Any]],
    existing_live_key_entries: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    published_policy_ids: set[str] = set()

    for key_id, record in sorted(keys_payload.items()):
        if _is_revoked_record(record):
            info(f"Skipping revoked record during structured publish: {key_id}")
            continue
        policy, adapters = _require_fat_record_fields(record, key_id)
        _verify_embedded_policy_hash(record, key_id)
        if record.get("type") == "ssh_key":
            key_entry = {
                "key_id": key_id,
                "type": "ssh_key",
                "key_source": record["key_source"],
                "algorithm": record["algorithm"],
                "public_key": record["public_key"],
                "vault_instance": record["vault_instance"],
                "policy_id": record["policy_id"],
                "policy_hash": record["policy_hash"],
                "policy": policy,
                "adapters": adapters,
                "created_at": record["created_at"],
                "status": record.get("status", "active"),
                "label": record["label"],
            }
            existing_live_entry = (existing_live_key_entries or {}).get(key_id)
            if isinstance(existing_live_entry, dict) and existing_live_entry.get("paused") is True:
                key_entry["paused"] = True
                info(f"Preserving paused flag during structured publish: {key_id}")
            entries.append({"key": f"key:{key_id}", "value": json.dumps(key_entry, separators=(",", ":"))})

            policy_id = policy["policy_id"]
            if policy_id not in published_policy_ids:
                entries.append(
                    {
                        "key": f"policy:{policy_id}",
                        "value": json.dumps(policy, separators=(",", ":")),
                    }
                )
                published_policy_ids.add(policy_id)
            continue

        provider_id = record["provider"]

        key_entry = {
            "key_id": key_id,
            "enc_version": record["enc_version"],
            "pub_key_fp": record["pub_key_fp"],
            "wrapped_dek": record["wrapped_dek"],
            "ciphertext": record["ciphertext"],
            "provider": provider_id,
            "target_host": record["target_host"],
            "policy_id": record["policy_id"],
            "policy_hash": record["policy_hash"],
            "created_at": record["created_at"],
            "label": record["label"],
        }
        existing_live_entry = (existing_live_key_entries or {}).get(key_id)
        if isinstance(existing_live_entry, dict) and existing_live_entry.get("paused") is True:
            key_entry["paused"] = True
            info(f"Preserving paused flag during structured publish: {key_id}")
        entries.append({"key": f"key:{key_id}", "value": json.dumps(key_entry, separators=(",", ":"))})

        policy_id = policy["policy_id"]
        if policy_id not in published_policy_ids:
            entries.append(
                {
                    "key": f"policy:{policy_id}",
                    "value": json.dumps(policy, separators=(",", ":")),
                }
            )
            published_policy_ids.add(policy_id)

    return entries


def _kv_value_url(cf_creds: dict[str, str], namespace_id: str, key_name: str) -> str:
    quoted_key = urllib.parse.quote(key_name, safe="")
    return (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{cf_creds['CF_ACCOUNT_ID']}/storage/kv/namespaces/{namespace_id}/values/{quoted_key}"
    )


def _kv_auth_headers(cf_creds: dict[str, str]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cf_creds['CF_API_TOKEN']}",
        "Content-Type": "application/json",
    }


def _kv_get_json_value(cf_creds: dict[str, str], namespace_id: str, key_name: str) -> dict[str, Any] | None:
    request = urllib.request.Request(_kv_value_url(cf_creds, namespace_id, key_name), headers=_kv_auth_headers(cf_creds))
    try:
        with urllib.request.urlopen(request) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        body_text = exc.read().decode("utf-8", errors="replace")
        die(
            f"Failed to read structured KV key {key_name!r}: HTTP {exc.code}\n"
            f"--- response body ---\n{body_text}"
        )
    except Exception as exc:
        die(f"Failed to read structured KV key {key_name!r}: {exc}")

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        die(f"Structured KV key {key_name!r} returned invalid JSON: {exc}")
    if not isinstance(parsed, dict):
        die(f"Structured KV key {key_name!r} returned invalid schema")
    return parsed


def _kv_wait_for_json_value(
    cf_creds: dict[str, str],
    namespace_id: str,
    key_name: str,
    *,
    max_attempts: int = 18,
    delay_seconds: int = 5,
) -> dict[str, Any]:
    info(
        f"Checking Cloudflare KV propagation for {key_name!r} "
        f"(eventual consistency; may take up to {max_attempts} attempts)"
    )
    for attempt in range(1, max_attempts + 1):
        parsed = _kv_get_json_value(cf_creds, namespace_id, key_name)
        if parsed is not None:
            return parsed
        if attempt < max_attempts:
            info(
                f"Cloudflare KV propagation delay for {key_name!r} is still normal; "
                f"rechecking consistency ({attempt}/{max_attempts})"
            )
            time.sleep(delay_seconds)
    die(
        f"Cloudflare KV key {key_name!r} did not become readable after publication.\n"
        f"  Consistency check exhausted {max_attempts} attempts."
    )


def _kv_put_value(cf_creds: dict[str, str], namespace_id: str, key_name: str, value: str) -> None:
    """Write a value to CF KV via the management API. Immediately consistent."""
    request = urllib.request.Request(
        _kv_value_url(cf_creds, namespace_id, key_name),
        method="PUT",
        data=value.encode("utf-8"),
        headers={**_kv_auth_headers(cf_creds), "Content-Type": "text/plain"},
    )
    try:
        with urllib.request.urlopen(request) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        die(
            f"Failed to write structured KV key {key_name!r}: HTTP {exc.code}\n"
            f"--- response body ---\n{body_text}"
        )
    except Exception as exc:
        die(f"Failed to write structured KV key {key_name!r}: {exc}")
    if not body.get("success"):
        die(f"Failed to write structured KV key {key_name!r}: {body}")


def _kv_delete_key(cf_creds: dict[str, str], namespace_id: str, key_name: str) -> None:
    request = urllib.request.Request(
        _kv_value_url(cf_creds, namespace_id, key_name),
        method="DELETE",
        headers=_kv_auth_headers(cf_creds),
    )
    try:
        with urllib.request.urlopen(request) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return
        body_text = exc.read().decode("utf-8", errors="replace")
        die(
            f"Failed to delete structured KV key {key_name!r}: HTTP {exc.code}\n"
            f"--- response body ---\n{body_text}"
        )
    except Exception as exc:
        die(f"Failed to delete structured KV key {key_name!r}: {exc}")
    if not body.get("success"):
        die(f"Failed to delete structured KV key {key_name!r}")


def _parse_allowed_keys_csv(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_key_id(provider: str) -> str:
    return f"{provider}_prod"


def _parse_env_file(path: str) -> list[tuple[str, str, str]]:
    """
    Parse a .env file and return detected provider key entries.

    Returns a list of (env_var_name, provider_id, raw_value) tuples.
    Only includes vars that appear in IMPORT_PROVIDER_WHITELIST.
    Skips blank lines, comments, and IMPORT_EXCLUSION_LIST vars.
    Returns empty list if file does not exist or cannot be read.

    Rules:
    - If zero entries are detected, the file must NOT be added to the shred queue.
    - Duplicate env var names: last occurrence wins (standard .env behavior).
    - Values may be quoted (single or double); quotes are stripped.
    """
    results: dict[str, tuple[str, str, str]] = {}

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()

                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]

                if not value:
                    continue
                if key in IMPORT_EXCLUSION_LIST:
                    continue
                if key in IMPORT_PROVIDER_WHITELIST:
                    provider_id = IMPORT_PROVIDER_WHITELIST[key]
                    results[key] = (key, provider_id, value)
    except OSError:
        return []

    return list(results.values())


def _load_simple_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        die(f"Cannot read env file {path}: {exc}")

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, _sep, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _append_unique_adapter_binding(
    key_adapters_by_key_id: dict[str, list[str]],
    key_id: str,
    adapter_id: str,
) -> None:
    bindings = key_adapters_by_key_id.setdefault(key_id, [])
    if adapter_id not in bindings:
        bindings.append(adapter_id)


def _prompt_app_label(prompt: str = "  App/label for this key: ") -> str:
    while True:
        app_label = input(prompt).strip().lower()
        if ADAPTER_ID_RE.fullmatch(app_label):
            return app_label
        print("  ✗  App/label must be lowercase letters, numbers, hyphens, or underscores.")
        print("     It must start and end with a letter or number. Examples: litellm, open-webui, myapp1\n")


def _prompt_declared_adapter_ids() -> list[str]:
    print("\n" + "═" * 70)
    print("  Subumbra Bootstrap — Step 2 of 4: App Adapters")
    print("═" * 70)
    print("  Declare the app adapter IDs that should receive per-app Subumbra tokens.")
    print("  Example: litellm,openwebui")
    print("  Leave blank only if you intentionally want compatibility/simple mode.\n")

    while True:
        raw = input("  Declared app adapter IDs (comma-separated, blank = none): ").strip()
        try:
            return _parse_adapter_ids(raw)
        except SystemExit:
            print("  ✗  Invalid adapter declaration. Please try again.\n")


def _prompt_key_adapter_ids(key_id: str, declared_adapter_ids: list[str]) -> list[str]:
    if not declared_adapter_ids:
        info(f"{key_id} will use compatibility/simple mode because no app adapters were declared.")
        return []

    print(f"  Declared app adapters: {', '.join(declared_adapter_ids)}")
    print("  Enter comma-separated adapter IDs, 'all' for every declared app, or blank for compatibility/simple mode.")

    while True:
        raw = input(f"  Adapters for {key_id}: ").strip()
        if raw.lower() == "all":
            return list(declared_adapter_ids)
        try:
            return _parse_key_adapter_ids(
                raw,
                source=f"Interactive binding for {key_id}",
                declared_adapter_ids=set(declared_adapter_ids),
            )
        except SystemExit:
            print("  ✗  Invalid adapter selection. Please try again.\n")


def _upsert_env_file(path: Path, updates: dict[str, str]) -> None:
    existing_lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)
    rewritten: list[str] = []

    for line in existing_lines:
        if line.startswith("#") or "=" not in line:
            rewritten.append(line)
            continue
        key, _sep, _value = line.partition("=")
        if key in remaining:
            rewritten.append(f"{key}={remaining.pop(key)}")
        else:
            rewritten.append(line)

    if remaining:
        if rewritten and rewritten[-1] != "":
            rewritten.append("")
        for key, value in remaining.items():
            rewritten.append(f"{key}={value}")

    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def _next_generated_key_id(
    provider: str,
    app_id: str,
    api_keys: dict[str, tuple[str, str, str, str, str]],
    existing_keys: dict,
) -> str:
    ordinal = 1
    while True:
        candidate = f"{provider}_{app_id}_{ordinal}"
        if candidate not in api_keys and candidate not in existing_keys:
            return candidate
        ordinal += 1


def _find_duplicate_secret_key_id(
    api_keys: dict[str, tuple[str, str, str, str, str]],
    provider: str,
    raw_value: str,
) -> str | None:
    for key_id, (existing_provider, _target_host, _auth_header, _auth_prefix, existing_value) in api_keys.items():
        if existing_provider == provider and existing_value == raw_value:
            return key_id
    return None


def _prompt_duplicate_secret_action(provider: str, existing_key_id: str) -> bool:
    print(f"\n  ⚠  WARNING: duplicate {provider} secret detected; existing record is {existing_key_id}.")
    while True:
        choice = input("     Reuse existing record? [Y/n]: ").strip().lower()
        if choice in {"", "y", "yes"}:
            return False
        if choice in {"n", "no"}:
            return True
        print("     Please answer 'y' to reuse or 'n' to create a new record.")


def _key_id_env_var_name(secret_env_var: str) -> str:
    if secret_env_var.endswith("_API_KEY"):
        return f"{secret_env_var[:-8]}_KEY_ID"
    if secret_env_var.endswith("_KEY"):
        return f"{secret_env_var[:-4]}_KEY_ID"
    return f"{secret_env_var}_KEY_ID"


def _adapter_binding_env_var_name(secret_env_var: str) -> str:
    return f"{secret_env_var}_ADAPTERS"


def _resolve_env_key_id(provider: str, secret_env_var: str) -> tuple[str, str]:
    key_id_var = _key_id_env_var_name(secret_env_var)
    key_id = os.environ.get(key_id_var, "").strip() or _default_key_id(provider)
    if not KEY_ID_RE.fullmatch(key_id):
        die(
            f"Automation mode: invalid key_id {key_id!r} from {key_id_var}\n"
            f"  Must match ^[a-z0-9][a-z0-9_-]{{2,63}}$"
        )
    return key_id, key_id_var


def _normalize_adapter_id(adapter_id: str) -> str:
    return adapter_id.upper().replace("-", "_")


def _parse_adapter_ids(raw: str) -> list[str]:
    adapter_ids: list[str] = []
    seen_normalized: dict[str, str] = {}
    for adapter_id in (item.strip() for item in raw.split(",")):
        if not adapter_id:
            continue
        if not ADAPTER_ID_RE.fullmatch(adapter_id):
            die(f"Invalid adapter_id '{adapter_id}'")
        if adapter_id in BUILTIN_ADAPTER_IDS:
            die(f"Reserved built-in adapter_id '{adapter_id}'")
        normalized = _normalize_adapter_id(adapter_id)
        if normalized in BUILTIN_TOKEN_SUFFIXES:
            die(f"Reserved built-in adapter token suffix '{normalized}' for adapter_id '{adapter_id}'")
        if normalized in seen_normalized:
            die(
                f"Duplicate normalized adapter token suffix '{normalized}' for adapter_ids "
                f"'{seen_normalized[normalized]}' and '{adapter_id}'"
            )
        seen_normalized[normalized] = adapter_id
        adapter_ids.append(adapter_id)
    return adapter_ids


def _build_custom_adapter_scope_vars(adapter_ids: list[str]) -> dict[str, str]:
    return {
        adapter_id: f"{_normalize_adapter_id(adapter_id)}_ALLOWED_KEYS"
        for adapter_id in adapter_ids
    }


def _parse_key_adapter_ids(
    raw: str,
    *,
    source: str,
    declared_adapter_ids: set[str],
) -> list[str]:
    adapter_ids: list[str] = []
    seen: set[str] = set()
    for adapter_id in (item.strip() for item in raw.split(",")):
        if not adapter_id:
            continue
        if not ADAPTER_ID_RE.fullmatch(adapter_id):
            die(
                f"{source}: invalid adapter_id {adapter_id!r}\n"
                "  App adapters must match ^[a-z0-9][a-z0-9_-]{0,61}[a-z0-9]$"
            )
        if adapter_id in BUILTIN_ADAPTER_IDS:
            die(f"{source}: built-in adapter_id {adapter_id!r} is reserved")
        if adapter_id not in declared_adapter_ids:
            die(
                f"{source}: adapter_id {adapter_id!r} was not declared\n"
                f"  Declared adapters: {', '.join(sorted(declared_adapter_ids)) or '(none)'}"
            )
        if adapter_id in seen:
            continue
        seen.add(adapter_id)
        adapter_ids.append(adapter_id)
    return adapter_ids


def _binding_label(allowed_adapters: list[str]) -> str:
    if allowed_adapters == ["subumbra-proxy"]:
        return "compat/simple"
    return ",".join(allowed_adapters)


def _bind_key_to_adapters(
    key_id: str,
    selected_adapter_ids: list[str],
    *,
    key_adapters_by_key_id: dict[str, list[str]],
    allowed_keys_by_adapter: dict[str, list[str]],
) -> None:
    effective_adapters = selected_adapter_ids or ["subumbra-proxy"]
    key_adapters_by_key_id[key_id] = list(effective_adapters)
    for adapter_id in effective_adapters:
        allowed_keys_by_adapter.setdefault(adapter_id, []).append(key_id)


def _policy_adapter_ids(policy: dict[str, Any]) -> list[str]:
    adapters = policy.get("allow", {}).get("adapters")
    if not isinstance(adapters, list) or not adapters:
        die(f"Policy {policy.get('policy_id', '<unknown>')} missing allow.adapters")
    return [str(adapter) for adapter in adapters]


def _validate_allowed_keys(
    valid_key_map: dict[str, Any],
    allowed_keys_by_adapter: dict[str, list[str]],
) -> None:
    valid_key_ids = set(valid_key_map.keys())
    for adapter_id, allowed_keys in allowed_keys_by_adapter.items():
        missing = [key_id for key_id in allowed_keys if key_id not in valid_key_ids]
        if missing:
            die(
                f"{adapter_id} requested unknown allowed key_id(s): {', '.join(sorted(missing))}\n"
                f"  Valid key_ids for this bootstrap run: {', '.join(sorted(valid_key_ids))}"
            )


def _build_adapter_registry(
    adapter_tokens: dict[str, str],
    allowed_keys_by_adapter: dict[str, list[str]],
    *,
    token_ttl_days: int,
) -> dict[str, dict]:
    issued_at_dt = datetime.now(timezone.utc)
    expires_at_dt = issued_at_dt + timedelta(days=token_ttl_days)
    issued_at = issued_at_dt.isoformat(timespec="seconds")
    expires_at = expires_at_dt.isoformat(timespec="seconds")
    registry = {
        "subumbra-proxy": {
            "token": adapter_tokens["subumbra-proxy"],
            "allowed_keys": allowed_keys_by_adapter["subumbra-proxy"],
            "can_list_keys": False,
            "can_read_stats": False,
            "can_write_audit": True,
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
        "subumbra-ui": {
            "token": adapter_tokens["subumbra-ui"],
            "allowed_keys": [],
            "can_list_keys": True,
            "can_read_stats": True,
            "can_list_all_keys": True,
            "can_write_audit": False,
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
    }
    if "subumbra-probe" in adapter_tokens and "subumbra-probe" in allowed_keys_by_adapter:
        registry["subumbra-probe"] = {
            "token": adapter_tokens["subumbra-probe"],
            "allowed_keys": allowed_keys_by_adapter["subumbra-probe"],
            "can_list_keys": False,
            "can_read_stats": False,
            "can_write_audit": False,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
    for adapter_id, token in adapter_tokens.items():
        if adapter_id in BUILTIN_ADAPTER_IDS:
            continue
        registry[adapter_id] = {
            "token": token,
            "allowed_keys": allowed_keys_by_adapter[adapter_id],
            "can_list_keys": False,
            "can_read_stats": False,
            "can_write_audit": False,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
    return registry


def _prompt_allowed_keys(adapter_label: str, available_key_ids: list[str]) -> list[str]:
    while True:
        print(f"\n  {adapter_label} — select allowed key_ids:")
        for i, kid in enumerate(available_key_ids, 1):
            print(f"    {i}. {kid}")
        print(f"  Enter space-separated numbers (e.g. 1 3), blank = all, 'none' = none: ", end="")
        raw = input("").strip().lower()
        if raw == "" or raw == "all":
            return list(available_key_ids)
        if raw == "none":
            return []

        parts = raw.split()
        result = []
        invalid = []
        for part in parts:
            if part.isdigit() and 1 <= int(part) <= len(available_key_ids):
                result.append(available_key_ids[int(part) - 1])
            else:
                invalid.append(part)
        if invalid:
            print(
                f"  ✗  Invalid selection(s): {', '.join(invalid)}. "
                f"Enter numbers 1-{len(available_key_ids)}, blank, or 'none'.\n"
            )
            continue
        return result


# ─────────────────────────────────────────────────────────────────────────────
# V2 Crypto — RSA-4096-OAEP + AES-256-GCM with AAD
# ─────────────────────────────────────────────────────────────────────────────


def public_key_fingerprint(pub_key) -> str:
    """SHA-256 fingerprint of the DER-encoded SubjectPublicKeyInfo."""
    der = pub_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return "sha256:" + hashlib.sha256(der).hexdigest()


def wrap_dek(pub_key, dek_bytes: bytes) -> str:
    """Wrap a 32-byte DEK with RSA-4096-OAEP-SHA256. Returns base64 string."""
    wrapped = pub_key.encrypt(
        dek_bytes,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return b64encode(wrapped).decode("ascii")


def encrypt_api_key_v2(dek_bytes: bytes, plaintext: str, key_id: str) -> str:
    """
    Encrypt a plaintext API key with AES-256-GCM, AAD bound to key_id.

    Wire format (base64-encoded):
        nonce[12] || ciphertext[n] || GCM-tag[16]

    AAD: "subumbra:v2:<key_id>" — binds ciphertext to this specific record.
    """
    nonce = os.urandom(12)
    aesgcm = AESGCM(dek_bytes)
    aad = f"subumbra:v2:{key_id}".encode("utf-8")
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
    return b64encode(nonce + ct).decode("ascii")


def encrypt_api_key_v3(dek_bytes: bytes, plaintext: str, key_id: str, policy_hash: str) -> str:
    """Encrypt a plaintext API key with V3 AAD bound to key_id + policy_hash."""
    nonce = os.urandom(12)
    aesgcm = AESGCM(dek_bytes)
    aad = f"subumbra:v3:{key_id}:{policy_hash}".encode("utf-8")
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
    return b64encode(nonce + ct).decode("ascii")


def compute_policy_hash(policy_doc: dict[str, Any]) -> str:
    """Return the lowercase hex SHA-256 of the baseline-bound policy object."""
    if policy_doc.get("type") == "ssh_key":
        allow = policy_doc["allow"]
        baseline_allow: dict[str, Any] = {
            "adapters": sorted(allow["adapters"]),
        }
        hosts = allow.get("hosts")
        if isinstance(hosts, list) and hosts:
            baseline_allow["hosts"] = sorted(hosts)
        baseline_obj: dict[str, Any] = {
            "type": "ssh_key",
            "key_id": policy_doc["key_id"],
            "algorithm": policy_doc["algorithm"],
            "allow": baseline_allow,
        }
        canonical = json.dumps(baseline_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    auth = policy_doc["auth"]
    allow = policy_doc["allow"]
    baseline_obj: dict[str, Any] = {
        "key_id": policy_doc["key_id"],
        "target": {
            "host": policy_doc["target"]["host"],
        },
        "auth": {
            "scheme": auth["scheme"],
        },
        "allow": {
            "adapters": sorted(allow["adapters"]),
            "methods": sorted(allow["methods"]),
            "path_prefixes": sorted(allow["path_prefixes"]),
            "content_types": sorted(allow["content_types"]),
            "max_body_bytes": allow["max_body_bytes"],
        },
    }
    if "request_headers" in allow:
        baseline_obj["allow"]["request_headers"] = sorted(allow["request_headers"])
    if "header_name" in auth:
        baseline_obj["auth"]["header_name"] = auth["header_name"]
    if "query_param" in auth:
        baseline_obj["auth"]["query_param"] = auth["query_param"]
    if "allow_query" in auth:
        baseline_obj["auth"]["allow_query"] = auth["allow_query"]
    canonical = json.dumps(baseline_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _build_fat_record(
    *,
    key_id: str,
    provider: str,
    target_host: str,
    pub_key_fp: str,
    wrapped_dek: str,
    ciphertext: str,
    policy: dict[str, Any],
    policy_hash: str,
    adapters: list[str],
    vault_instance: str,
    created_at: str,
    label: str,
    revoked: bool = False,
) -> dict[str, Any]:
    return {
        "key_id": key_id,
        "enc_version": 3,
        "pub_key_fp": pub_key_fp,
        "wrapped_dek": wrapped_dek,
        "ciphertext": ciphertext,
        "provider": provider,
        "target_host": target_host,
        "policy_id": policy["policy_id"],
        "policy_hash": policy_hash,
        "policy": policy,
        "adapters": list(adapters),
        "vault_instance": vault_instance,
        "created_at": created_at,
        "label": label,
        "revoked": revoked,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Mode detection
# ─────────────────────────────────────────────────────────────────────────────

def _has_env_credentials() -> bool:
    """
    Return True if the environment contains all required credentials for
    unattended manifest-era bootstrap:
      - CF_API_TOKEN     (non-empty)
      - CF_ACCOUNT_ID    (non-empty)
      - manifest exists at /app/manifest

    Comment-only, whitespace-only, or REPLACE_ME placeholder values do NOT
    satisfy this check — only real non-empty values count.
    """
    cf_token = os.environ.get("CF_API_TOKEN", "").strip()
    cf_account = os.environ.get("CF_ACCOUNT_ID", "").strip()
    if not cf_token or not cf_account:
        return False
    # Reject obvious placeholders
    for placeholder in ("REPLACE_ME", "YOUR_TOKEN_HERE", "CHANGEME"):
        if placeholder in cf_token.upper() or placeholder in cf_account.upper():
            return False
    return MANIFEST_FILE.exists()


def _infer_cf_worker_name_from_worker_url(url: str) -> str:
    """Best-effort worker script name from a Workers *.workers.dev URL."""
    url = url.strip()
    if not url:
        return ""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").strip().lower()
    except Exception:
        return ""
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) >= 2 and labels[-2] == "workers" and labels[-1] == "dev":
        return labels[0] or ""
    return ""


def _resolved_cf_worker_name_from_operator_context() -> str:
    """Resolve Worker script name for day-2 / defaults: env, then host .env, then CF_WORKER_URL."""
    w = os.environ.get("CF_WORKER_NAME", "").strip()
    if w:
        return w
    w = _read_env_file_value(HOST_ENV_FILE, "CF_WORKER_NAME").strip()
    if w:
        return w
    url = _read_env_file_value(HOST_ENV_FILE, "CF_WORKER_URL").strip()
    return _infer_cf_worker_name_from_worker_url(url)


def _has_cf_credentials() -> bool:
    required = ("CF_API_TOKEN", "CF_ACCOUNT_ID")
    if not all(os.environ.get(name, "").strip() for name in required):
        return False
    if not _resolved_cf_worker_name_from_operator_context():
        return False
    return True


def _choose_bootstrap_mode() -> bool:
    """
    Return True when bootstrap should continue into the interactive wizard.

    Headless runs keep the existing automation behavior. In a real TTY, if
    environment credentials are already present, let the operator choose
    between RAM-only interactive entry and automated environment processing.
    """
    if not _has_env_credentials():
        return True
    if not sys.stdin.isatty():
        return False

    print("\n▶  Environment credentials detected")
    print("   This usually means Docker loaded values from .env.bootstrap.")
    print("   Choose bootstrap mode:\n")
    print("   1. Interactive RAM-only setup")
    print("   2. Automated setup from detected environment credentials\n")

    while True:
        choice = input("  Enter 1 or 2: ").strip()
        if choice == "1":
            return True
        if choice == "2":
            return False
        print("  ✗  Enter 1 for interactive mode or 2 for automated mode.\n")


def _prompt_after_automation_error(message: str) -> bool:
    warn(message)
    print("   Choose next step:\n")
    print("   1. Continue with interactive RAM-only setup")
    print("   2. Abort and fix automated input\n")

    while True:
        choice = input("  Enter 1 or 2: ").strip()
        if choice == "1":
            return True
        if choice == "2":
            die("Aborted so you can fix automated input and rerun bootstrap.")
        print("  ✗  Enter 1 to continue interactively or 2 to abort.\n")



def _parse_token_ttl_days(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        return 365
    try:
        token_ttl_days = int(raw)
    except ValueError:
        die("TOKEN_TTL_DAYS must be a positive integer")
    if token_ttl_days <= 0:
        die("TOKEN_TTL_DAYS must be a positive integer")
    return token_ttl_days


