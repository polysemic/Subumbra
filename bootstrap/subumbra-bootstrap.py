#!/usr/bin/env python3
"""
subumbra-bootstrap — manifest-era V3 envelope encryption & deployment.

Usage (interactive — TTY, no .env.bootstrap):
  ./bootstrap.sh
  Requires subumbra.yaml (or subumbra.json) on the bootstrap mount; Cloudflare and provider secrets
  are prompted via hidden TTY reads (clear labels; no password echo) and held in RAM
  (including an
  in-process secret cache), not written to disk as plaintext.

Usage (automation / CI — requires .env.bootstrap with referenced secrets):
  ./bootstrap.sh

Single-key rotation (no Cloudflare interaction):
  ./bootstrap.sh --rotate

What it does (full bootstrap, in order):
  1. Detects mode: automation when CF token + account + manifest are all
     present in the environment; otherwise interactive manifest wizard (TTY) or
     structured errors when non-interactive
  2. Collects CF credentials + provider API keys (RAM only — never written to disk)
  3. Warns if keys.json already exists (rotation mode) and identifies any
     keys that will be removed because they are absent from this session
  4. Confirms with the operator before proceeding (interactive mode only)
  5. Generates NEW runtime auth tokens (per-adapter Subumbra tokens,
     SUBUMBRA_HMAC_KEY, transient SUBUMBRA_SETUP_TOKEN)
  6. Copies worker source to a temp dir and deploys via wrangler
  7. Calls CF-side one-shot /setup/keygen and receives the public key + fingerprint
  8. Writes public key to /app/data/public_key.pem (not sensitive)
  9. Encrypts each API key: per-key DEK -> AES-256-GCM (AAD bound), DEK -> RSA-OAEP wrap
 10. ONLY after remote deploy + remote keygen succeed: atomically writes keys.json
 11. Writes runtime tokens to /app/data/runtime.env (mode 0600)
 12. Zeroes sensitive memory and exits

ROTATION NOTE (full bootstrap):
  Every run generates a new RSA key pair in Cloudflare and new runtime tokens.
  ALL keys that should remain accessible must be re-entered — any key omitted
  from the wizard (or from .env.bootstrap in CI mode) will be removed from
  keys.json and become permanently inaccessible under the new key pair.

  After bootstrap completes:
    docker compose up -d --force-recreate

ROTATION NOTE (--rotate mode):
  Single-key rotation uses the existing public key on disk.  Only the
  targeted record changes.  No Cloudflare interaction or service restart
  needed.  subumbra-keys serves the new record on next request automatically.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import yaml
import re
import secrets
import shutil
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
from pathlib import Path
from typing import Any, Iterable, NoReturn

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
PUBLIC_KEY_FILE = DATA_DIR / "public_key.pem"
SYSTEM_INTEGRITY_FILE = DATA_DIR / "system-integrity.json"
HOST_ENV_FILE   = Path("/app/host-env")
WORKER_SRC      = Path("/app/worker")
MANIFEST_FILE   = Path("/app/manifest")
USER_TEMPLATES_DIR = Path("/app/user-templates")
KV_CONFIG_FILE = DATA_DIR / "kv-config.json"

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

STRUCTURED_KV_SCHEMA_VERSION = "1"

ADAPTER_SCOPE_VARS: dict[str, str] = {
    "subumbra-proxy": "PROXY_ALLOWED_KEYS",
    "subumbra-probe": "PROBE_ALLOWED_KEYS",
    "subumbra-ui": "UI_ALLOWED_KEYS",
}
BUILTIN_ADAPTER_IDS = tuple(ADAPTER_SCOPE_VARS.keys())
BUILTIN_TOKEN_SUFFIXES = {"PROXY", "UI", "PROBE"}

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


def _write_public_key_file(path: Path, public_key_pem: str) -> None:
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(public_key_pem.encode("utf-8"))
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
        fd = os.open(str(tmp_keys), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w") as fh:
            json.dump(keys_payload, fh, indent=2)
            fh.write("\n")
        os.replace(str(tmp_keys), str(KEYS_FILE))
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
    setup_token: str,
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
        "SUBUMBRA_SETUP_TOKEN": setup_token,
    }
    if "subumbra-probe" in allowed_keys_by_adapter:
        host_env_updates["PROBE_ALLOWED_KEYS"] = ",".join(allowed_keys_by_adapter["subumbra-probe"])
        host_env_updates["SUBUMBRA_TOKEN_PROBE"] = adapter_tokens["subumbra-probe"]
    for adapter_id in allowed_keys_by_adapter:
        if adapter_id not in BUILTIN_ADAPTER_IDS:
            host_env_updates[f"SUBUMBRA_TOKEN_{_normalize_adapter_id(adapter_id)}"] = adapter_tokens[adapter_id]
    return host_env_updates


def _write_runtime_env_file(runtime_env_lines: list[str]) -> None:
    runtime_env_content = "\n".join(runtime_env_lines) + "\n"
    try:
        fd = os.open(str(RUNTIME_ENV_OUT), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(runtime_env_content)
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


# Maps both Subumbra canonical env var names AND common standalone-app aliases
# to their provider_id. Both sides must be supported so that migration from a
# standard LiteLLM .env (which uses ANTHROPIC_API_KEY) and the CI path (which
# uses ANTHROPIC_KEY) both work.
IMPORT_PROVIDER_WHITELIST: dict[str, str] = {
    # Subumbra canonical secret refs retained for legacy import discovery
    "ANTHROPIC_KEY":        "anthropic",
    "OPENAI_KEY":           "openai",
    "GROQ_KEY":             "groq",
    "DEEPSEEK_KEY":         "deepseek",
    "CEREBRAS_API_KEY":     "cerebras",
    "GEMINI_API_KEY":       "gemini",
    "GOOGLE_API_KEY":       "gemini",
    "MISTRAL_API_KEY":      "mistral",
    "OPENROUTER_API_KEY":   "openrouter",
    "TOGETHER_AI_API_KEY":  "together",
    "XAI_API_KEY":          "xai",
    "GITHUB_KEY":           "github",
    "SLACK_KEY":            "slack",
    "SENDGRID_KEY":         "sendgrid",
    # Common standalone-app aliases (LiteLLM .env, OpenWebUI, etc.)
    # 7 providers have mismatched names vs. Subumbra canonical
    "ANTHROPIC_API_KEY":    "anthropic",
    "OPENAI_API_KEY":       "openai",
    "GROQ_API_KEY":         "groq",
    "DEEPSEEK_API_KEY":     "deepseek",
    "TOGETHER_API_KEY":     "together",
    "GITHUB_TOKEN":         "github",
    "GITHUB_REST_KEY":      "github_rest",
    "STRIPE_TEST_KEY":      "stripe_test",
    "SLACK_BOT_TOKEN":      "slack",
    "SENDGRID_API_KEY":     "sendgrid",
}

# Vars to explicitly skip — app-internal secrets that must never be imported
# as provider keys. If detected, skip silently (do not warn or shred).
IMPORT_EXCLUSION_LIST: frozenset[str] = frozenset({
    "LITELLM_MASTER_KEY",
    "LITELLM_SALT_KEY",
    "WEBUI_SECRET_KEY",
    "N8N_ENCRYPTION_KEY",
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "REDIS_URL",
    "SECRET_KEY",
    "JWT_SECRET",
})

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

    _CATALOG_CACHE = result
    return _CATALOG_CACHE


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
        warn(f"Local template {name!r} unreadable ({exc}); falling back to built-in catalog")
        return None
    except yaml.YAMLError as exc:
        warn(f"Local template {name!r} is invalid YAML ({exc}); falling back to built-in catalog")
        return None
    if not isinstance(data, dict):
        warn(f"Local template {name!r} top-level value is not an object; falling back to built-in catalog")
        return None
    return data


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

    required = {"key_id", "provider", "secret_ref", "adapters", "unique_vault"}
    missing = sorted(required - record.keys())
    if missing:
        _manifest_die(f"{source} missing required field(s): {', '.join(missing)}")

    has_template = "template" in record
    has_policy = "policy" in record
    if not has_template and not has_policy:
        _manifest_die(f"{source} must provide either 'template' or 'policy'")

    key_id = record.get("key_id")
    if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
        _manifest_die(f"{source}.key_id is invalid")

    provider = record.get("provider")
    if not isinstance(provider, str) or not provider:
        _manifest_die(f"{source}.provider must be a non-empty string")

    secret_ref = record.get("secret_ref")
    if not isinstance(secret_ref, str) or not secret_ref.strip():
        _manifest_die(f"{source}.secret_ref must be a non-empty string")

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


def _hash_worker_bundle(work_dir: Path) -> str:
    entrypoint = work_dir / "src" / "worker.js"
    if not entrypoint.exists():
        die(f"worker entrypoint missing from deploy bundle: {entrypoint}")
    return hashlib.sha256(entrypoint.read_bytes()).hexdigest()


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
        policy, _adapters = _require_fat_record_fields(record, key_id)
        _verify_embedded_policy_hash(record, key_id)
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
    for attempt in range(1, max_attempts + 1):
        parsed = _kv_get_json_value(cf_creds, namespace_id, key_name)
        if parsed is not None:
            return parsed
        if attempt < max_attempts:
            info(
                f"Structured KV key {key_name!r} not visible yet; "
                f"retrying ({attempt}/{max_attempts})"
            )
            time.sleep(delay_seconds)
    die(f"Structured KV key {key_name!r} did not become visible after publication")


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
    api_keys: dict[str, tuple[str, str, str, str, str]],
    allowed_keys_by_adapter: dict[str, list[str]],
) -> None:
    valid_key_ids = set(api_keys.keys())
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
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
        "subumbra-ui": {
            "token": adapter_tokens["subumbra-ui"],
            "allowed_keys": [],
            "can_list_keys": True,
            "can_read_stats": True,
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


def _get_push_registry_cf_creds() -> dict[str, str]:
    if _has_cf_credentials():
        return {
            "CF_API_TOKEN": os.environ["CF_API_TOKEN"].strip(),
            "CF_ACCOUNT_ID": os.environ["CF_ACCOUNT_ID"].strip(),
            "CF_WORKER_NAME": _resolved_cf_worker_name_from_operator_context(),
        }

    if not sys.stdin.isatty():
        die(
            "Missing Cloudflare credentials for day-2 management.\n"
            "  Set CF_API_TOKEN and CF_ACCOUNT_ID in the environment (or use interactive ./bootstrap.sh).\n"
            "  Set CF_WORKER_NAME in the repo .env (host mount), or set CF_WORKER_URL to a *.workers.dev URL "
            "so the worker name can be inferred — day-2 commands do not prompt for the worker name.\n"
            "  For non-interactive CI, inject CF_API_TOKEN, CF_ACCOUNT_ID, and CF_WORKER_NAME into the container."
        )

    cf_token = os.environ.get("CF_API_TOKEN", "").strip()
    if not cf_token:
        while True:
            cf_token = _prompt_hidden_line("Cloudflare API token")
            if cf_token:
                break
            print("  ✗  API token cannot be empty.\n")
    cf_account_id = os.environ.get("CF_ACCOUNT_ID", "").strip()
    if not cf_account_id:
        while True:
            cf_account_id = _prompt_hidden_line("Cloudflare account ID")
            if cf_account_id:
                break
            print("  ✗  Account ID cannot be empty.\n")
    cf_worker_name = _resolved_cf_worker_name_from_operator_context()
    if not cf_worker_name:
        die(
            "CF_WORKER_NAME could not be resolved from the environment, "
            f"{HOST_ENV_FILE}, or CF_WORKER_URL.\n"
            "  Add e.g. CF_WORKER_NAME=subumbra-proxy (or your deployed name) to the repo .env and retry."
        )
    info(f"Using Cloudflare Worker name {cf_worker_name!r} from .env / environment (not prompted).")
    return {
        "CF_API_TOKEN":   cf_token,
        "CF_ACCOUNT_ID":  cf_account_id,
        "CF_WORKER_NAME": cf_worker_name,
    }


def _load_kv_namespace_id() -> str:
    try:
        with KV_CONFIG_FILE.open() as fh:
            namespace_id = json.load(fh)["namespace_id"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        die(f"Provider registry KV not initialized at {KV_CONFIG_FILE}: {exc}")
    if not isinstance(namespace_id, str) or not namespace_id.strip():
        die(f"Provider registry KV not initialized at {KV_CONFIG_FILE}: invalid namespace_id")
    return namespace_id


# ─────────────────────────────────────────────────────────────────────────────
# Automation fallback (CI / headless mode)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_token_ttl_days(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        return 90
    try:
        token_ttl_days = int(raw)
    except ValueError:
        die("TOKEN_TTL_DAYS must be a positive integer")
    if token_ttl_days <= 0:
        die("TOKEN_TTL_DAYS must be a positive integer")
    return token_ttl_days


def _load_manifest_bootstrap() -> tuple[
    dict[str, tuple[str, str, str, str, str]],
    dict[str, str],
    dict[str, list[str]],
    dict[str, list[str]],
    int,
    dict[str, bool],
    dict[str, dict[str, Any]],
]:
    records = _load_manifest_records()

    cf_creds: dict[str, str] = {}
    missing_cf: list[str] = []
    for var in ("CF_API_TOKEN", "CF_ACCOUNT_ID"):
        val = os.environ.get(var, "").strip()
        if not val:
            missing_cf.append(var)
        else:
            cf_creds[var] = val
    if missing_cf:
        die(f"Missing required Cloudflare bootstrap credential(s): {', '.join(missing_cf)}")
    cf_creds["CF_WORKER_NAME"] = (
        _resolved_cf_worker_name_from_operator_context() or "subumbra-proxy"
    )

    declared_adapter_ids: list[str] = []
    seen_declared: set[str] = set()
    api_keys: dict[str, tuple[str, str, str, str, str]] = {}
    key_adapters_by_key_id: dict[str, list[str]] = {}
    policy_by_key_id: dict[str, dict[str, Any]] = {}
    unique_key_flags: dict[str, bool] = {}
    allowed_keys_by_adapter: dict[str, list[str]] = {
        "subumbra-proxy": [],
        "subumbra-ui": [],
    }

    for record in records:
        for adapter_id in record["adapters"]:
            if adapter_id in seen_declared:
                continue
            seen_declared.add(adapter_id)
            declared_adapter_ids.append(adapter_id)
            allowed_keys_by_adapter[adapter_id] = []

    for record in records:
        key_id = record["key_id"]
        api_keys[key_id] = (
            record["provider"],
            record["target_host"],
            record["auth_header"],
            record["auth_prefix"],
            record["secret_ref"],
        )
        policy_by_key_id[key_id] = record["policy"]
        unique_key_flags[key_id] = record["unique_vault"]
        _bind_key_to_adapters(
            key_id,
            record["adapters"],
            key_adapters_by_key_id=key_adapters_by_key_id,
            allowed_keys_by_adapter=allowed_keys_by_adapter,
        )

    token_ttl_days = _parse_token_ttl_days(os.environ.get("TOKEN_TTL_DAYS", ""))
    return (
        api_keys,
        cf_creds,
        allowed_keys_by_adapter,
        key_adapters_by_key_id,
        token_ttl_days,
        unique_key_flags,
        policy_by_key_id,
    )


# ── TOMBSTONED (R58): legacy env-only bootstrap ─────────────────────────────
# `_load_env_fallback` only calls `_automation_fail(...)`. `main()` still dispatches here when
# `subumbra.json` is missing in automation mode so operators get a structured error (manifest-only flow).
def _load_env_fallback(
    existing_keys: dict,
) -> tuple[
    dict[str, tuple[str, str, str, str, str]],
    dict[str, str],
    dict[str, list[str]],
    dict[str, list[str]],
    int,
]:
    """
    Legacy env-only bootstrap is no longer supported after provider catalog removal.
    cf_creds: {"CF_API_TOKEN": ..., "CF_ACCOUNT_ID": ..., "CF_WORKER_NAME": ...}
    """
    # retained for reference — not called in current flow (always _automation_fail).
    _automation_fail(
        "Legacy env-only bootstrap is no longer supported after provider catalog removal.\n"
        "  Author subumbra.yaml (or subumbra.json) with explicit policy.target.host and policy.auth settings,\n"
        "  then provide only the referenced secrets in .env.bootstrap."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Interactive wizard (manifest-era, RAM-only)
# ─────────────────────────────────────────────────────────────────────────────


def run_interactive_wizard(
    existing_keys: dict,
) -> tuple[
    dict[str, tuple[str, str, str, str, str]],
    dict[str, str],
    dict[str, list[str]],
    dict[str, list[str]],
    int,
    dict[str, bool],
    dict[str, dict[str, Any]],
    list[str],
]:
    """
    Interactive manifest bootstrap: collect Cloudflare credentials and per-key
    secrets in RAM, bind adapters, and return the same logical credential bundle
    as _load_manifest_bootstrap plus an empty shred_paths list.
    """
    global _WIZARD_SECRETS

    if existing_keys:
        info(
            f"Existing encrypted keys on disk: {len(existing_keys)} record(s) "
            "— rotation rules apply after this session."
        )

    _WIZARD_SECRETS.clear()

    step("Interactive manifest wizard — Cloudflare credentials")
    cf_token = os.environ.get("CF_API_TOKEN", "").strip()
    if not cf_token:
        while True:
            cf_token = _prompt_hidden_line("Cloudflare API token")
            if cf_token:
                break
            print("  ✗  API token cannot be empty.\n")
    cf_account_id = os.environ.get("CF_ACCOUNT_ID", "").strip()
    if not cf_account_id:
        while True:
            cf_account_id = _prompt_hidden_line("Cloudflare account ID")
            if cf_account_id:
                break
            print("  ✗  Account ID cannot be empty.\n")

    suggested = _resolved_cf_worker_name_from_operator_context()
    default_worker = suggested or "subumbra-proxy"
    if suggested:
        info(f"Current Worker name from .env / CF_WORKER_URL: {suggested!r}")
    else:
        info("No CF_WORKER_NAME or inferable CF_WORKER_URL in .env — default Worker name is subumbra-proxy")
    print(
        f"  Cloudflare Worker name [default: {default_worker}] — press Enter to use default, or type a new name:",
        flush=True,
    )
    cf_worker_raw = input("  > ").strip()
    cf_worker_name = cf_worker_raw or default_worker
    cf_creds = {
        "CF_API_TOKEN": cf_token,
        "CF_ACCOUNT_ID": cf_account_id,
        "CF_WORKER_NAME": cf_worker_name,
    }
    ok("Cloudflare credentials captured (values not printed)")

    step("Loading manifest records")
    records = _load_manifest_records()
    ok(f"Found {len(records)} manifest key record(s)")

    step("Per-key provider secrets (RAM only; not echoed)")
    accepted: list[dict[str, Any]] = []
    for record in records:
        key_id = record["key_id"]
        provider = record["provider"]
        secret_ref = record["secret_ref"]
        if os.environ.get(secret_ref, "").strip():
            ok(f"{key_id}: using existing bootstrap environment for {secret_ref!r}")
            accepted.append(record)
            continue
        print(f"\n  Key: {key_id!r}  provider={provider!r}  secret_ref={secret_ref!r}")
        choice = input("  Provision a secret for this key in this session? [Y/n]: ").strip().lower()
        if choice in ("n", "no"):
            info(f"Skipped {key_id!r} — no secret collected for this session.")
            continue
        while True:
            secret_a = _prompt_hidden_line(
                f"secret or API key for key_id {key_id!r} ({secret_ref!r})"
            )
            if not secret_a:
                print("  ✗  Secret cannot be empty.\n")
                continue
            secret_b = _prompt_hidden_line(
                f"same secret again to confirm for key_id {key_id!r}"
            )
            if secret_a != secret_b:
                print("  ✗  Secrets do not match. Try again.\n")
                continue
            _WIZARD_SECRETS[secret_ref] = secret_a
            accepted.append(record)
            ok(f"Secret captured for {key_id!r}")
            break

    if not accepted:
        die("No keys with resolvable secrets for this session. Aborted.")

    seen_declared: set[str] = set()
    api_keys: dict[str, tuple[str, str, str, str, str]] = {}
    key_adapters_by_key_id: dict[str, list[str]] = {}
    policy_by_key_id: dict[str, dict[str, Any]] = {}
    unique_key_flags: dict[str, bool] = {}
    allowed_keys_by_adapter: dict[str, list[str]] = {
        "subumbra-proxy": [],
        "subumbra-ui": [],
    }

    for rec in accepted:
        for adapter_id in rec["adapters"]:
            if adapter_id in seen_declared:
                continue
            seen_declared.add(adapter_id)
            allowed_keys_by_adapter[adapter_id] = []

    for rec in accepted:
        kid = rec["key_id"]
        api_keys[kid] = (
            rec["provider"],
            rec["target_host"],
            rec["auth_header"],
            rec["auth_prefix"],
            rec["secret_ref"],
        )
        policy_by_key_id[kid] = rec["policy"]
        unique_key_flags[kid] = rec["unique_vault"]
        _bind_key_to_adapters(
            kid,
            rec["adapters"],
            key_adapters_by_key_id=key_adapters_by_key_id,
            allowed_keys_by_adapter=allowed_keys_by_adapter,
        )

    token_ttl_days = _parse_token_ttl_days(os.environ.get("TOKEN_TTL_DAYS", ""))
    shred_paths: list[str] = []
    return (
        api_keys,
        cf_creds,
        allowed_keys_by_adapter,
        key_adapters_by_key_id,
        token_ttl_days,
        unique_key_flags,
        policy_by_key_id,
        shred_paths,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CF Worker deployment
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], *, cwd: Path, env: dict, input_text: str | None = None) -> str:
    """Run a subprocess, die with clear error on failure. Returns stdout."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(
            f"Command failed: {' '.join(cmd)}\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
    return result.stdout.strip()


def _persist_kv_namespace_config(namespace_id: str, title: str) -> str:
    with KV_CONFIG_FILE.open("w") as fh:
        json.dump({"namespace_id": namespace_id, "title": title}, fh, indent=2)
        fh.write("\n")
    return namespace_id


def _list_kv_namespaces(base_url: str, auth_headers: dict[str, str]) -> list[dict[str, Any]]:
    namespaces: list[dict[str, Any]] = []
    page = 1
    per_page = 1000

    while True:
        query = urllib.parse.urlencode({
            "page": page,
            "per_page": per_page,
            "order": "title",
            "direction": "asc",
        })
        list_req = urllib.request.Request(f"{base_url}?{query}", headers=auth_headers)
        try:
            with urllib.request.urlopen(list_req) as resp:
                list_result = json.loads(resp.read())
        except Exception as exc:
            die(f"Failed to list KV namespaces: {exc}")

        batch = list_result.get("result") or []
        if not isinstance(batch, list):
            die("Cloudflare KV list returned an invalid response payload")
        namespaces.extend(batch)

        result_info = list_result.get("result_info") or {}
        total_count = result_info.get("total_count")
        if isinstance(total_count, int):
            if len(namespaces) >= total_count:
                break
        elif len(batch) < per_page:
            break
        page += 1

    return namespaces


def _find_kv_namespace_by_title(
    base_url: str,
    auth_headers: dict[str, str],
    title: str,
) -> dict[str, Any] | None:
    for entry in _list_kv_namespaces(base_url, auth_headers):
        if entry.get("title") == title:
            return entry
    return None


def _create_or_reuse_kv_namespace(cf_creds: dict[str, str]) -> str:
    title = f"{cf_creds['CF_WORKER_NAME']}-PROVIDER_REGISTRY_KV"
    base_url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{cf_creds['CF_ACCOUNT_ID']}/storage/kv/namespaces"
    )
    auth_headers = {
        "Authorization": f"Bearer {cf_creds['CF_API_TOKEN']}",
        "Content-Type": "application/json",
    }

    # List existing namespaces and reuse if a matching title is found.
    existing = _list_kv_namespaces(base_url, auth_headers)
    saved_namespace_id = None
    if KV_CONFIG_FILE.exists():
        saved_namespace_id = _load_kv_namespace_id()
    if saved_namespace_id is not None:
        for entry in existing:
            if entry.get("id") == saved_namespace_id:
                return _persist_kv_namespace_config(saved_namespace_id, entry.get("title", title))
        warn(
            "Saved KV namespace ID missing from active Cloudflare account; falling back to title scan."
        )
    for entry in existing:
        if entry.get("title") == title:
            namespace_id = entry["id"]
            info(f"Reusing existing KV namespace: {title}")
            return _persist_kv_namespace_config(namespace_id, title)

    # No match found — create a new namespace.
    payload = json.dumps({"title": title}).encode()
    create_req = urllib.request.Request(
        base_url,
        data=payload,
        method="POST",
        headers=auth_headers,
    )

    try:
        with urllib.request.urlopen(create_req) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400:
            existing_entry = _find_kv_namespace_by_title(base_url, auth_headers, title)
            if existing_entry is not None:
                namespace_id = existing_entry["id"]
                info(f"Reusing existing KV namespace after create conflict: {title}")
                return _persist_kv_namespace_config(namespace_id, title)
        die(
            f"Failed to create provider-registry KV namespace: HTTP {exc.code}\n"
            f"--- response body ---\n{body}"
        )
    except Exception as exc:
        die(f"Failed to create provider-registry KV namespace: {exc}")

    if not result.get("success") or "result" not in result or "id" not in result["result"]:
        die("Failed to create provider-registry KV namespace")

    namespace_id = result["result"]["id"]
    return _persist_kv_namespace_config(namespace_id, title)


def _append_provider_registry_kv_binding(wrangler_toml: Path, namespace_id: str) -> None:
    with wrangler_toml.open("a") as fh:
        fh.write(
            "\n[[kv_namespaces]]\n"
            'binding = "PROVIDER_REGISTRY_KV"\n'
            f'id = "{namespace_id}"\n'
        )


def _wrangler_env(cf_creds: dict[str, str]) -> dict[str, str]:
    return {
        **os.environ,
        "CLOUDFLARE_API_TOKEN": cf_creds["CF_API_TOKEN"],
        "CLOUDFLARE_ACCOUNT_ID": cf_creds["CF_ACCOUNT_ID"],
        "CI": "true",
    }


def _build_worker_url(worker_name: str, deploy_out: str | None = None) -> str:
    worker_url = f"https://{worker_name}.workers.dev"
    if not deploy_out:
        return worker_url
    for line in deploy_out.splitlines():
        for token in line.split():
            if token.startswith("https://") and "workers.dev" in token:
                return token.rstrip(".,")
    return worker_url


def _delete_worker_secret(cf_creds: dict[str, str], secret_name: str, *, quiet_missing: bool = False) -> None:
    if not WORKER_SRC.exists():
        die(
            f"Worker source not found at {WORKER_SRC}.\n"
            "  Ensure ./worker is mounted into the bootstrap container\n"
            "  (check volumes in docker-compose.yml)."
        )

    worker_name = cf_creds["CF_WORKER_NAME"]
    env = _wrangler_env(cf_creds)

    with tempfile.TemporaryDirectory(prefix="subumbra-worker-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copytree(WORKER_SRC, tmp_dir / "worker")
        work_dir = tmp_dir / "worker"
        namespace_id = _create_or_reuse_kv_namespace(cf_creds)
        _append_provider_registry_kv_binding(work_dir / "wrangler.toml", namespace_id)

        result = subprocess.run(
            ["wrangler", "secret", "delete", secret_name, "--name", worker_name],
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ok(f"Deleted {secret_name} secret")
            return
        if quiet_missing:
            info(f"{secret_name} not present — already clean")
            return
        die(
            f"Command failed: wrangler secret delete {secret_name} --name {worker_name}\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )


def _put_worker_secret(cf_creds: dict[str, str], secret_name: str, secret_value: str) -> None:
    if not WORKER_SRC.exists():
        die(
            f"Worker source not found at {WORKER_SRC}.\n"
            "  Ensure ./worker is mounted into the bootstrap container\n"
            "  (check volumes in docker-compose.yml)."
        )

    worker_name = cf_creds["CF_WORKER_NAME"]
    env = _wrangler_env(cf_creds)

    with tempfile.TemporaryDirectory(prefix="subumbra-worker-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copytree(WORKER_SRC, tmp_dir / "worker")
        work_dir = tmp_dir / "worker"
        namespace_id = _create_or_reuse_kv_namespace(cf_creds)
        _append_provider_registry_kv_binding(work_dir / "wrangler.toml", namespace_id)

        _run(
            ["wrangler", "secret", "put", secret_name, "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=secret_value + "\n",
        )
    ok(f"{secret_name} pushed")


def call_setup_keygen(worker_url: str, setup_token: str, vault_instance: str) -> tuple[str, str, str]:
    last_http_error: urllib.error.HTTPError | None = None
    body = json.dumps({"vault_instance": vault_instance}, separators=(",", ":")).encode("utf-8")
    _MAX_KEYGEN_ATTEMPTS = 24
    for attempt in range(1, _MAX_KEYGEN_ATTEMPTS + 1):
        req = urllib.request.Request(
            f"{worker_url.rstrip('/')}/setup/keygen",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {setup_token}",
                "Content-Type": "application/json",
                "User-Agent": "curl/8.5.0",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            if exc.code in (401, 403, 503) and attempt < _MAX_KEYGEN_ATTEMPTS:
                info(
                    "Cloudflare setup token not visible yet; "
                    f"retrying /setup/keygen ({attempt}/{_MAX_KEYGEN_ATTEMPTS})"
                )
                time.sleep(5)
                continue
            body = exc.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare setup keygen failed: HTTP {exc.code}\n"
                f"--- response body ---\n{body}"
            )
        except Exception as exc:
            raise BootstrapFlowError(f"Cloudflare setup keygen failed: {exc}") from exc
    else:
        if last_http_error is not None:
            body = last_http_error.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare setup keygen failed after retry window: HTTP {last_http_error.code}\n"
                f"--- response body ---\n{body}"
            )
        raise BootstrapFlowError("Cloudflare setup keygen failed after retry window")

    public_key_pem = payload.get("public_key_pem")
    pub_key_fp = payload.get("pub_key_fp")
    created_at = payload.get("created_at")
    if not all(isinstance(value, str) and value for value in (public_key_pem, pub_key_fp, created_at)):
        raise BootstrapFlowError("Cloudflare setup keygen returned an invalid response payload")
    return public_key_pem, pub_key_fp, created_at


def _call_internal_vault_status(worker_url: str, setup_token: str, vault_instance: str) -> bool:
    body = json.dumps({"vault_instance": vault_instance}, separators=(",", ":")).encode("utf-8")
    last_http_error: urllib.error.HTTPError | None = None
    max_attempts = 24
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            f"{worker_url.rstrip('/')}/internal/vault-status",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {setup_token}",
                "Content-Type": "application/json",
                "User-Agent": "curl/8.5.0",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            if exc.code in (401, 403, 503) and attempt < max_attempts:
                info(
                    "Cloudflare status token not visible yet; "
                    f"retrying /internal/vault-status ({attempt}/{max_attempts})"
                )
                time.sleep(5)
                continue
            body_text = exc.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare vault status failed: HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            ) from exc
        except Exception as exc:
            raise BootstrapFlowError(f"Cloudflare vault status failed: {exc}") from exc
    else:
        if last_http_error is not None:
            body_text = last_http_error.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare vault status failed after retry window: HTTP {last_http_error.code}\n"
                f"--- response body ---\n{body_text}"
            )
        raise BootstrapFlowError("Cloudflare vault status failed after retry window")
    initialized = payload.get("initialized")
    if not isinstance(initialized, bool):
        raise BootstrapFlowError("Cloudflare vault status returned an invalid response payload")
    return initialized


def _call_internal_vault_reset(worker_url: str, setup_token: str, vault_instance: str) -> None:
    body = json.dumps({"vault_instance": vault_instance}, separators=(",", ":")).encode("utf-8")
    last_http_error: urllib.error.HTTPError | None = None
    max_attempts = 24
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            f"{worker_url.rstrip('/')}/internal/vault-reset",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {setup_token}",
                "Content-Type": "application/json",
                "User-Agent": "curl/8.5.0",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            if exc.code in (401, 403, 503) and attempt < max_attempts:
                info(
                    "Cloudflare reset token not visible yet; "
                    f"retrying /internal/vault-reset ({attempt}/{max_attempts})"
                )
                time.sleep(5)
                continue
            body_text = exc.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare vault reset failed: HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            ) from exc
        except Exception as exc:
            raise BootstrapFlowError(f"Cloudflare vault reset failed: {exc}") from exc
    else:
        if last_http_error is not None:
            body_text = last_http_error.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare vault reset failed after retry window: HTTP {last_http_error.code}\n"
                f"--- response body ---\n{body_text}"
            )
        raise BootstrapFlowError("Cloudflare vault reset failed after retry window")
    if payload.get("status") != "ok":
        raise BootstrapFlowError("Cloudflare vault reset returned an invalid response payload")


def _delete_kv_namespace_if_present(cf_creds: dict[str, str]) -> None:
    if not KV_CONFIG_FILE.exists():
        return
    try:
        payload = json.loads(KV_CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        payload = {}
    namespace_id = str(payload.get("namespace_id", "")).strip() if isinstance(payload, dict) else ""
    if not namespace_id:
        return

    base_url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{cf_creds['CF_ACCOUNT_ID']}/storage/kv/namespaces/{namespace_id}"
    )
    auth_headers = {
        "Authorization": f"Bearer {cf_creds['CF_API_TOKEN']}",
        "Content-Type": "application/json",
    }
    delete_req = urllib.request.Request(base_url, method="DELETE", headers=auth_headers)
    try:
        with urllib.request.urlopen(delete_req) as resp:
            result = json.loads(resp.read())
        if not result.get("success"):
            die("Failed to delete provider-registry KV namespace")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            body_text = exc.read().decode("utf-8", errors="replace")
            die(
                f"Failed to delete provider-registry KV namespace: HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            )
    except Exception as exc:
        die(f"Failed to delete provider-registry KV namespace: {exc}")

    _delete_file_if_present(KV_CONFIG_FILE)


def _publish_structured_kv(
    cf_creds: dict[str, str],
    keys_payload: dict[str, dict[str, Any]],
) -> None:
    namespace_id = _create_or_reuse_kv_namespace(cf_creds)
    env = _wrangler_env(cf_creds)
    existing_live_key_entries: dict[str, dict[str, Any]] = {}
    for key_id, record in sorted(keys_payload.items()):
        if _is_revoked_record(record):
            continue
        live_entry = _kv_get_json_value(cf_creds, namespace_id, f"key:{key_id}")
        if isinstance(live_entry, dict):
            existing_live_key_entries[key_id] = live_entry
    entries = _build_structured_kv_entries(keys_payload, existing_live_key_entries)
    if not entries:
        die("No structured KV entries compiled for publication")

    with tempfile.TemporaryDirectory(prefix="subumbra-structured-kv-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copytree(WORKER_SRC, tmp_dir / "worker")
        work_dir = tmp_dir / "worker"
        _append_provider_registry_kv_binding(work_dir / "wrangler.toml", namespace_id)

        payload_path = work_dir / "structured-kv.json"
        payload_path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")

        _run(
            [
                "wrangler", "kv", "bulk", "put",
                str(payload_path),
                "--namespace-id", namespace_id,
                "--remote",
            ],
            cwd=work_dir,
            env=env,
        )

        sample_key_entry = next(entry for entry in entries if entry["key"].startswith("key:"))
        sample_policy_entry = next(entry for entry in entries if entry["key"].startswith("policy:"))

        _kv_wait_for_json_value(cf_creds, namespace_id, sample_key_entry["key"])
        _kv_wait_for_json_value(cf_creds, namespace_id, sample_policy_entry["key"])

        _run(
            [
                "wrangler", "kv", "key", "put",
                "registry_version",
                STRUCTURED_KV_SCHEMA_VERSION,
                "--namespace-id", namespace_id,
                "--remote",
            ],
            cwd=work_dir,
            env=env,
        )


def deploy_worker(
    cf_creds: dict[str, str],
    adapter_tokens: dict[str, str],
    subumbra_hmac_key: str,
    management_token: str,
    setup_token: str,
    provider_id_filter: "set[str] | None" = None,
) -> str:
    """
    Deploy the CF Worker and push runtime/setup secrets. Returns the worker URL.

    Steps:
      1. Copy worker source to a temp dir (source mount is :ro)
      2. wrangler deploy --name <name>
      3. wrangler secret delete MASTER_DECRYPTION_KEY (V1 cleanup, best-effort)
      4. wrangler secret delete WORKER_PRIVATE_KEY (legacy cleanup, best-effort)
      5. wrangler secret delete WORKER_KEY_FINGERPRINT (legacy cleanup, best-effort)
      6. wrangler secret put SUBUMBRA_ADAPTER_TOKENS
      7. wrangler secret put SUBUMBRA_HMAC_KEY
      8. wrangler secret put SUBUMBRA_MANAGEMENT_TOKEN
      9. wrangler secret put SUBUMBRA_SETUP_TOKEN
    """
    if not WORKER_SRC.exists():
        die(
            f"Worker source not found at {WORKER_SRC}.\n"
            "  Ensure ./worker is mounted into the bootstrap container\n"
            "  (check volumes in docker-compose.yml)."
        )

    worker_name = cf_creds["CF_WORKER_NAME"]

    # Wrangler reads CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID from env
    env = _wrangler_env(cf_creds)

    with tempfile.TemporaryDirectory(prefix="subumbra-worker-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copytree(WORKER_SRC, tmp_dir / "worker")
        work_dir = tmp_dir / "worker"
        namespace_id = _create_or_reuse_kv_namespace(cf_creds)
        _append_provider_registry_kv_binding(work_dir / "wrangler.toml", namespace_id)
        bundle_sha256 = _hash_worker_bundle(work_dir)

        # ── deploy ────────────────────────────────────────────────────────────
        step(f"Deploying CF Worker '{worker_name}'")
        deploy_out = _run(
            ["wrangler", "deploy", "--name", worker_name],
            cwd=work_dir,
            env=env,
        )
        ok("Deployed")
        for line in deploy_out.splitlines():
            info(line)

        # ── delete stale V1 secret (best-effort) ─────────────────────────────
        step("Cleaning up stale MASTER_DECRYPTION_KEY (V1)")
        del_result = subprocess.run(
            ["wrangler", "secret", "delete", "MASTER_DECRYPTION_KEY",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if del_result.returncode == 0:
            ok("Deleted stale MASTER_DECRYPTION_KEY secret")
        else:
            info("MASTER_DECRYPTION_KEY not present — already clean")

        # ── delete legacy custody secrets (best-effort) ──────────────────────
        for secret_name in ("WORKER_PRIVATE_KEY", "WORKER_KEY_FINGERPRINT"):
            step(f"Removing legacy {secret_name} secret")
            del_result = subprocess.run(
                ["wrangler", "secret", "delete", secret_name, "--name", worker_name],
                cwd=work_dir,
                env=env,
                capture_output=True,
                text=True,
            )
            if del_result.returncode == 0:
                ok(f"Deleted stale {secret_name} secret")
            else:
                info(f"{secret_name} not present — already clean")

        # ── push SUBUMBRA_ADAPTER_TOKENS ─────────────────────────────────────
        step("Pushing SUBUMBRA_ADAPTER_TOKENS to CF Secrets")
        adapter_tokens_json = json.dumps(
            [{"id": k, "token": v} for k, v in adapter_tokens.items()],
            separators=(",", ":"),
        )
        _run(
            ["wrangler", "secret", "put", "SUBUMBRA_ADAPTER_TOKENS",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=adapter_tokens_json + "\n",
        )
        ok("SUBUMBRA_ADAPTER_TOKENS pushed")

        # ── push SUBUMBRA_HMAC_KEY ────────────────────────────────────────────
        step("Pushing SUBUMBRA_HMAC_KEY to CF Secrets")
        _run(
            ["wrangler", "secret", "put", "SUBUMBRA_HMAC_KEY",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=subumbra_hmac_key + "\n",
        )
        ok("SUBUMBRA_HMAC_KEY pushed")

        step("Pushing SUBUMBRA_MANAGEMENT_TOKEN to CF Secrets")
        _run(
            ["wrangler", "secret", "put", "SUBUMBRA_MANAGEMENT_TOKEN",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=management_token + "\n",
        )
        ok("SUBUMBRA_MANAGEMENT_TOKEN pushed")

        # ── push transient SUBUMBRA_SETUP_TOKEN ──────────────────────────────
        step("Pushing transient SUBUMBRA_SETUP_TOKEN to CF Secrets")
        _run(
            ["wrangler", "secret", "put", "SUBUMBRA_SETUP_TOKEN",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=setup_token + "\n",
        )
        ok("SUBUMBRA_SETUP_TOKEN pushed")


        worker_url = _build_worker_url(worker_name, deploy_out)
        _write_system_integrity(worker_name, worker_url, bundle_sha256)

    return worker_url


def run_push_registry() -> None:
    cf_creds = _get_push_registry_cf_creds()
    if not KEYS_FILE.exists():
        die("keys.json not found — cannot publish structured KV")

    try:
        keys_payload = json.loads(KEYS_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        die(f"Cannot read keys.json: {exc}")
    for key_id, record in keys_payload.items():
        target_host = record.get("target_host")
        if not isinstance(target_host, str) or not target_host:
            die(f"keys.json record {key_id!r} missing target_host")
        _require_fat_record_fields(record, key_id)
        _verify_embedded_policy_hash(record, key_id)

    step("Publishing structured KV entries to Cloudflare KV")
    try:
        _publish_structured_kv(cf_creds, keys_payload)
    except SystemExit:
        die("structured publish aborted before registry_version update")
    ok("Structured KV publication complete")


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


def _require_existing_active_record(keys_payload: dict[str, dict[str, Any]], key_id: str) -> dict[str, Any]:
    if key_id not in keys_payload:
        die(f"key_id {key_id!r} not found in keys.json")
    record = keys_payload[key_id]
    if not isinstance(record, dict):
        die(f"keys.json record {key_id!r} is malformed")
    if _is_revoked_record(record):
        die(f"key_id {key_id!r} is already revoked")
    if record.get("enc_version") != 3:
        die(f"{key_id!r} is not an existing V3 record. Re-run full bootstrap.")
    _require_fat_record_fields(record, key_id)
    _verify_embedded_policy_hash(record, key_id)
    return record


def _load_existing_public_key_for_record(key_id: str, record: dict[str, Any]) -> tuple[str, Any, str]:
    vault_instance = str(record.get("vault_instance", "")).strip()
    if not vault_instance:
        die(f"keys.json record {key_id!r} missing vault_instance")
    public_key_file = _public_key_file_for_key(key_id, vault_instance)
    if not public_key_file.exists():
        die(
            f"Public key file not found at {public_key_file}\n"
            f"  Re-run full bootstrap before mutating {key_id!r}."
        )
    try:
        pub_key = serialization.load_pem_public_key(public_key_file.read_bytes())
    except Exception as exc:
        die(f"Failed to load {public_key_file.name}: {exc}")
    pub_key_fp = public_key_fingerprint(pub_key)
    if pub_key_fp != record.get("pub_key_fp"):
        die(
            f"Public key fingerprint mismatch for key_id {key_id!r}\n"
            f"  stored:   {record.get('pub_key_fp')}\n"
            f"  computed: {pub_key_fp}"
        )
    return vault_instance, pub_key, pub_key_fp


def _rewrite_v3_record_from_plaintext(
    *,
    key_id: str,
    existing_record: dict[str, Any],
    raw_secret: str,
    policy: dict[str, Any],
    adapters: list[str],
) -> dict[str, Any]:
    provider = str(existing_record.get("provider", "")).strip()
    if not provider:
        die(f"keys.json record {key_id!r} missing provider")
    target_host = str(policy.get("target", {}).get("host", "")).strip()
    if not target_host:
        die(f"policy for {key_id!r} is missing target.host")
    vault_instance, pub_key, pub_key_fp = _load_existing_public_key_for_record(key_id, existing_record)
    policy_hash = compute_policy_hash(policy)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dek = os.urandom(32)
    ciphertext = encrypt_api_key_v3(dek, raw_secret, key_id, policy_hash)
    wrapped_dek = wrap_dek(pub_key, dek)
    del dek
    return _build_fat_record(
        key_id=key_id,
        provider=provider,
        target_host=target_host,
        pub_key_fp=pub_key_fp,
        wrapped_dek=wrapped_dek,
        ciphertext=ciphertext,
        policy=policy,
        policy_hash=policy_hash,
        adapters=adapters,
        vault_instance=vault_instance,
        created_at=now_iso,
        label=str(existing_record.get("label", key_id)),
        revoked=False,
    )


def _update_record_policy_without_reencrypt(
    *,
    key_id: str,
    existing_record: dict[str, Any],
    policy: dict[str, Any],
    adapters: list[str],
) -> dict[str, Any]:
    new_policy_hash = compute_policy_hash(policy)
    if new_policy_hash != existing_record.get("policy_hash"):
        die(
            f"--publish-policy baseline change detected for key_id {key_id!r}; "
            "re-encryption path required."
        )
    updated = dict(existing_record)
    updated["policy_id"] = policy["policy_id"]
    updated["policy_hash"] = new_policy_hash
    updated["policy"] = policy
    updated["adapters"] = list(adapters)
    updated["target_host"] = policy["target"]["host"]
    updated["revoked"] = False
    return updated


def _publish_after_local_record_update(cf_creds: dict[str, str], keys_payload: dict[str, dict[str, Any]]) -> None:
    step("Publishing structured KV entries")
    try:
        _publish_structured_kv(cf_creds, keys_payload)
    except SystemExit:
        die("structured publish aborted before registry_version update")
    ok("Structured KV publication complete")


def _load_management_manifest_authority(key_id: str, expected_provider: str | None = None) -> dict[str, Any]:
    authority = _load_manifest_repair_authority(key_id)
    provider = authority["provider"]
    if expected_provider and provider != expected_provider:
        die(
            f"Manifest provider mismatch for key_id {key_id!r}: expected {expected_provider!r}, "
            f"found {provider!r}."
        )
    return authority


def run_status() -> None:
    manifest_records = _load_manifest_records()
    keys_payload = _load_keys_payload_if_present()
    seen_manifest_key_ids: set[str] = set()
    found_problem = False

    for record in manifest_records:
        key_id = record["key_id"]
        seen_manifest_key_ids.add(key_id)
        manifest_hash = compute_policy_hash(record["policy"])
        stored = keys_payload.get(key_id)

        if not isinstance(stored, dict) or _is_revoked_record(stored):
            print(f"{key_id} NOT_DEPLOYED")
            found_problem = True
            continue

        _require_fat_record_fields(stored, key_id)
        _verify_embedded_policy_hash(stored, key_id)
        stored_hash = str(stored.get("policy_hash", "")).strip()
        if stored_hash == manifest_hash:
            print(f"{key_id} UP_TO_DATE")
        else:
            print(
                f"{key_id} POLICY_DRIFT "
                f"manifest_hash={manifest_hash} stored_hash={stored_hash}"
            )
            found_problem = True

    for key_id in sorted(keys_payload):
        if key_id in seen_manifest_key_ids:
            continue
        record = keys_payload[key_id]
        if not isinstance(record, dict):
            die(f"keys.json record {key_id!r} is malformed")
        print(f"{key_id} REVOKED")
        found_problem = True

    if found_problem:
        sys.exit(1)


def run_revoke_key(target_key_id: str) -> None:
    offline = "--offline" in sys.argv
    keys_payload = _load_keys_payload_or_die()
    if target_key_id not in keys_payload:
        die(f"key_id {target_key_id!r} not found in keys.json")
    stored = keys_payload[target_key_id]
    if not isinstance(stored, dict):
        die(f"keys.json record {target_key_id!r} is malformed")

    if _is_revoked_record(stored):
        if offline:
            die(
                f"key_id {target_key_id!r} is already revoked in keys.json.\n"
                "  Omit --offline and re-run with Cloudflare credentials to delete live KV entries only."
            )
        cf_creds = _get_push_registry_cf_creds()
        step(f"{target_key_id} already revoked locally — deleting Worker KV entries only")
        _delete_revoked_key_kv_entries(cf_creds, keys_payload, target_key_id, stored)
        ok("KV sync complete for revoked key")
        return

    record = _require_existing_active_record(keys_payload, target_key_id)
    revoked_record = dict(record)
    revoked_record["revoked"] = True
    keys_payload[target_key_id] = revoked_record

    step(f"Marking {target_key_id} as revoked in keys.json")
    _write_keys_payload(keys_payload)
    ok(f"Revocation marker persisted for {target_key_id}")

    if offline:
        warn(
            "Offline revoke: keys.json only. Worker KV may still list this key until you run the same "
            "command without --offline (Cloudflare credentials) to delete key:* / policy:* entries."
        )
        info("subumbra-keys will refuse fetches for this key_id while revoked=true is set.")
        return

    cf_creds = _get_push_registry_cf_creds()
    _delete_revoked_key_kv_entries(cf_creds, keys_payload, target_key_id, record)


def _delete_revoked_key_kv_entries(
    cf_creds: dict[str, str],
    keys_payload: dict[str, dict[str, Any]],
    target_key_id: str,
    record: dict[str, Any],
) -> None:
    namespace_id = _create_or_reuse_kv_namespace(cf_creds)
    step(f"Deleting live structured KV key:{target_key_id}")
    _kv_delete_key(cf_creds, namespace_id, f"key:{target_key_id}")
    ok(f"Deleted live key:{target_key_id}")

    policy_id = str(record.get("policy_id", "")).strip()
    if policy_id:
        orphaned = True
        for key_id, candidate in keys_payload.items():
            if key_id == target_key_id or _is_revoked_record(candidate):
                continue
            if candidate.get("policy_id") == policy_id:
                orphaned = False
                break
        if orphaned:
            step(f"Deleting orphaned structured KV policy:{policy_id}")
            _kv_delete_key(cf_creds, namespace_id, f"policy:{policy_id}")
            ok(f"Deleted orphaned policy:{policy_id}")


def _mutate_adapter_binding(target_key_id: str, adapter_id: str, *, add: bool) -> None:
    cf_creds = _get_push_registry_cf_creds()
    keys_payload = _load_keys_payload_or_die()
    existing_record = _require_existing_active_record(keys_payload, target_key_id)
    authority = _load_management_manifest_authority(target_key_id, str(existing_record.get("provider", "")))
    raw_secret = authority["raw_secret"]

    policy = json.loads(json.dumps(existing_record["policy"]))
    current_adapters = _policy_adapter_ids(policy)
    if add:
        if adapter_id not in current_adapters:
            current_adapters.append(adapter_id)
    else:
        if adapter_id not in current_adapters:
            die(f"adapter_id {adapter_id!r} is not currently bound to key_id {target_key_id!r}")
        current_adapters = [candidate for candidate in current_adapters if candidate != adapter_id]
        if not current_adapters:
            die(f"adapter mutation would leave key_id {target_key_id!r} with no allowed adapters")

    policy["allow"]["adapters"] = sorted(current_adapters)
    adapters = list(policy["allow"]["adapters"])
    step(
        f"{'Adding' if add else 'Revoking'} adapter binding via re-encryption path "
        f"for key_id {target_key_id}"
    )
    info("policy-hash-baseline mutation detected — re-encryption required")
    keys_payload[target_key_id] = _rewrite_v3_record_from_plaintext(
        key_id=target_key_id,
        existing_record=existing_record,
        raw_secret=raw_secret,
        policy=policy,
        adapters=adapters,
    )
    _write_keys_payload(keys_payload)
    ok(f"Updated {target_key_id} in keys.json")
    _publish_after_local_record_update(cf_creds, keys_payload)

    if sorted(authority["adapters"]) != sorted(adapters):
        _prompt_manifest_sync_after_adapter_mutation(target_key_id, adapters)


def run_add_adapter(target_key_id: str, adapter_id: str) -> None:
    _mutate_adapter_binding(target_key_id, adapter_id, add=True)


def run_revoke_adapter(target_key_id: str, adapter_id: str) -> None:
    _mutate_adapter_binding(target_key_id, adapter_id, add=False)


def run_publish_policy(target_key_id: str) -> None:
    cf_creds = _get_push_registry_cf_creds()
    keys_payload = _load_keys_payload_or_die()
    existing_record = _require_existing_active_record(keys_payload, target_key_id)
    authority = _load_management_manifest_authority(target_key_id, str(existing_record.get("provider", "")))
    new_policy = authority["policy"]
    new_adapters = authority["adapters"]
    new_policy_hash = compute_policy_hash(new_policy)
    old_policy_hash = str(existing_record.get("policy_hash", "")).strip()

    if new_policy_hash == old_policy_hash:
        step(f"Publishing non-baseline policy update for key_id {target_key_id}")
        info("publish-policy branch: non-baseline update path")
        keys_payload[target_key_id] = _update_record_policy_without_reencrypt(
            key_id=target_key_id,
            existing_record=existing_record,
            policy=new_policy,
            adapters=new_adapters,
        )
    else:
        step(f"Publishing baseline policy update for key_id {target_key_id}")
        info("publish-policy branch: baseline re-encryption path")
        keys_payload[target_key_id] = _rewrite_v3_record_from_plaintext(
            key_id=target_key_id,
            existing_record=existing_record,
            raw_secret=authority["raw_secret"],
            policy=new_policy,
            adapters=new_adapters,
        )

    _write_keys_payload(keys_payload)
    ok(f"Updated policy for {target_key_id} in keys.json")
    _publish_after_local_record_update(cf_creds, keys_payload)


# ─────────────────────────────────────────────────────────────────────────────
# Per-key rotation wizard (--rotate)
# ─────────────────────────────────────────────────────────────────────────────

def run_rotate_wizard() -> None:
    """
    Per-key rotation using the on-disk RSA public key.
    No Cloudflare interaction required.
    """
    print(BANNER, flush=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. Display info ──────────────────────────────────────────────────
    print("\n" + "═" * 70)
    print("  Subumbra — Per-Key Rotation")
    print("  Uses existing RSA public key — no Cloudflare interaction needed")
    print("═" * 70)

    # ── 2. Load existing keys ────────────────────────────────────────────
    if not KEYS_FILE.exists():
        die("keys.json not found — run a full bootstrap first.")

    try:
        existing_keys = json.loads(KEYS_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        die(f"Cannot read keys.json: {exc}")

    if not existing_keys:
        die("keys.json is empty — run a full bootstrap first.")

    key_ids = list(existing_keys.keys())
    print("\n  Existing keys:")
    for i, kid in enumerate(key_ids, 1):
        meta = existing_keys[kid]
        prov = meta.get("provider", "unknown")
        ver = meta.get("enc_version", 1)
        print(f"    {i}. {kid}  ({prov}, v{ver})")

    # ── 3. Select key to rotate ──────────────────────────────────────────
    provider = None
    target_host = None
    print()
    while True:
        choice = input("  Select existing key to rotate (number or key_id): ").strip()
        if not choice:
            print("  ✗  Selection required.\n")
            continue

        # Try as number
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(key_ids):
                key_id = key_ids[idx]
                provider = existing_keys[key_id].get("provider", "unknown")
                target_host = existing_keys[key_id].get("target_host")
                break
            print(f"  ✗  Enter a number between 1 and {len(key_ids)}.\n")
            continue
        except ValueError:
            pass

        # Try as existing key_id
        if choice in existing_keys:
            key_id = choice
            provider = existing_keys[key_id].get("provider", "unknown")
            target_host = existing_keys[key_id].get("target_host")
            break

        print(f"  ✗  '{choice}' is not an existing key selection.\n")

    print(f"\n  Rotating: {key_id} ({provider})")
    existing_record = existing_keys[key_id]
    if existing_record.get("enc_version") != 3:
        die(
            f"--rotate only supports existing V3 records. key_id {key_id!r} is enc_version="
            f"{existing_record.get('enc_version', 1)}.\n  Use full bootstrap for V2 migration."
        )
    existing_policy_id = existing_record.get("policy_id")
    existing_policy_hash = existing_record.get("policy_hash")
    if not isinstance(existing_policy_id, str) or not existing_policy_id.strip():
        die(f"--rotate requires an existing V3 policy_id for key_id {key_id!r}. Use full bootstrap.")
    if not isinstance(existing_policy_hash, str) or not existing_policy_hash.strip():
        die(f"--rotate requires an existing V3 policy_hash for key_id {key_id!r}. Use full bootstrap.")
    if not isinstance(target_host, str) or not target_host:
        die(f"--rotate requires target_host on the existing V3 record for key_id {key_id!r}.")
    policy, adapters = _require_fat_record_fields(existing_record, key_id)
    _verify_embedded_policy_hash(existing_record, key_id)
    vault_instance = existing_record.get("vault_instance", "vault")
    if not isinstance(vault_instance, str) or not vault_instance:
        die(f"--rotate requires vault_instance on the existing V3 record for key_id {key_id!r}.")

    public_key_file = _public_key_file_for_key(key_id, vault_instance)
    if not public_key_file.exists():
        die(
            f"Public key file not found at {public_key_file}\n"
            f"  Run a full bootstrap first to provision vault_instance {vault_instance!r}."
        )

    try:
        pub_key = serialization.load_pem_public_key(public_key_file.read_bytes())
    except Exception as exc:
        die(f"Failed to load {public_key_file.name}: {exc}\n  File may be corrupted — run a full bootstrap.")

    fp = public_key_fingerprint(pub_key)
    print(f"\n  Public key fingerprint: {fp}")

    # ── 4. Get new API key ───────────────────────────────────────────────
    while True:
        new_key = _prompt_hidden_line(f"new API key for {key_id!r}")
        if not new_key:
            print("  ✗  API key cannot be empty.")
            continue
        confirm_key = _prompt_hidden_line(f"same new API key again to confirm for {key_id!r}")
        if new_key != confirm_key:
            print("  ✗  Keys do not match. Try again.")
            continue
        break

    # ── 5. Encrypt with V3 envelope ──────────────────────────────────────
    step(f"Encrypting new key for {key_id}")
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dek = os.urandom(32)
    ciphertext = encrypt_api_key_v3(dek, new_key, key_id, existing_policy_hash)
    wrapped = wrap_dek(pub_key, dek)

    record = _build_fat_record(
        key_id=key_id,
        provider=provider,
        target_host=target_host,
        pub_key_fp=fp,
        wrapped_dek=wrapped,
        ciphertext=ciphertext,
        policy=policy,
        policy_hash=existing_policy_hash,
        adapters=adapters,
        vault_instance=vault_instance,
        created_at=now_iso,
        label=existing_record.get("label", key_id),
    )
    ok(f"Encrypted {provider:12s} → {key_id}")

    # ── 6. Zero sensitive values ─────────────────────────────────────────
    del dek
    new_key = "\x00" * len(new_key)
    del new_key
    del confirm_key
    gc.collect()

    # ── 7. Atomically update keys.json ───────────────────────────────────
    step(f"Updating {key_id} in keys.json")
    existing_keys[key_id] = record
    _write_keys_payload(existing_keys)

    ok(f"Updated {key_id} — only this record changed")
    info("All other records are untouched")
    info("No Cloudflare interaction, no runtime token changes")
    info("subumbra-keys will serve the new record on next request")
    print()


def run_provision_key(target_key_id: str) -> None:
    print(BANNER, flush=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        existing_keys = json.loads(KEYS_FILE.read_text()) if KEYS_FILE.exists() else {}
    except (json.JSONDecodeError, OSError) as exc:
        die(f"Cannot read keys.json: {exc}")
    if target_key_id in existing_keys:
        die(f"{target_key_id!r} already exists in keys.json — no targeted repair needed")

    cf_creds = _get_push_registry_cf_creds()
    worker_url = _read_env_file_value(HOST_ENV_FILE, "CF_WORKER_URL").strip()
    setup_token = _read_env_file_value(HOST_ENV_FILE, "SUBUMBRA_SETUP_TOKEN").strip()
    if not worker_url or not setup_token:
        die(
            "Cannot repair a missing key without a live Worker URL and setup token.\n"
            f"  Set CF_WORKER_URL and SUBUMBRA_SETUP_TOKEN in {HOST_ENV_FILE} (repo bind-mount).\n"
            "  These values come from the last bootstrap or your operator secrets store."
        )

    authority = _load_manifest_repair_authority(target_key_id)
    provider = authority["provider"]
    target_host = authority["target_host"]
    raw = authority["raw_secret"]
    vault_instance = authority["vault_instance"]
    policy = authority["policy"]
    adapters = authority["adapters"]

    public_key_file = _public_key_file_for_key(target_key_id, vault_instance)
    if not public_key_file.exists():
        die(
            f"Missing local public key for key_id {target_key_id!r}.\n"
            "  Ensure vault keygen completed (public_key*.pem on the data volume) "
            "or re-run full bootstrap."
        )
    step(f"Reading existing vault public key for {target_key_id} from {public_key_file.name}")
    public_key_pem = public_key_file.read_text()
    pub_key = _load_public_key_from_pem(public_key_pem)
    pub_key_fp = public_key_fingerprint(pub_key)

    _write_public_key_file(public_key_file, public_key_pem)

    policy_hash = compute_policy_hash(policy)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dek = os.urandom(32)
    ciphertext = encrypt_api_key_v3(dek, raw, target_key_id, policy_hash)
    wrapped_dek = wrap_dek(pub_key, dek)
    del dek

    existing_keys[target_key_id] = _build_fat_record(
        key_id=target_key_id,
        provider=provider,
        target_host=target_host,
        pub_key_fp=pub_key_fp,
        wrapped_dek=wrapped_dek,
        ciphertext=ciphertext,
        policy=policy,
        policy_hash=policy_hash,
        adapters=adapters,
        vault_instance=vault_instance,
        created_at=now_iso,
        label=target_key_id,
    )

    step(f"Updating {target_key_id} in keys.json")
    _write_keys_payload(existing_keys)
    ok(f"Added repaired record for {target_key_id}")

    for key_id, record in existing_keys.items():
        _require_fat_record_fields(record, key_id)
        _verify_embedded_policy_hash(record, key_id)

    step("Publishing structured KV entries after targeted repair")
    try:
        _publish_structured_kv(cf_creds, existing_keys)
    except SystemExit:
        die("structured publish aborted before registry_version update")
    ok("Structured KV publication complete")

    expected_key_ids = _load_manifest_key_ids_only()
    if expected_key_ids and expected_key_ids.issubset(set(existing_keys.keys())):
        step("Deleting transient SUBUMBRA_SETUP_TOKEN from CF Secrets")
        _delete_worker_secret(cf_creds, "SUBUMBRA_SETUP_TOKEN")
        _sync_host_env_file({"SUBUMBRA_SETUP_TOKEN": ""})
        ok("All manifest keys are present in keys.json — setup token cleared from CF and host env")
    else:
        missing = expected_key_ids - set(existing_keys.keys())
        warn(
            "Other manifest keys are still missing from keys.json; "
            f"SUBUMBRA_SETUP_TOKEN retained (missing: {', '.join(sorted(missing))})."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Main — Full bootstrap
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print(BANNER, flush=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing keys for rotation mode checks (needed before wizard)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing_keys: dict = {}
    is_rotation = KEYS_FILE.exists()
    if is_rotation:
        try:
            existing_keys = json.loads(KEYS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            existing_keys = {}
            warn("Could not parse existing keys.json — treating as fresh bootstrap")

    # ── Step 1: collect credentials ───────────────────────────────────────────
    use_wizard = _choose_bootstrap_mode()

    if not use_wizard:
        step("Automation mode — loading credentials from environment")
        manifest_mode = MANIFEST_FILE.exists()
        if manifest_mode:
            (
                api_keys,
                cf_creds,
                allowed_keys_by_adapter,
                key_adapters_by_key_id,
                token_ttl_days,
                unique_key_flags,
                policy_by_key_id,
            ) = _load_manifest_bootstrap()
            ok(f"Loaded {len(api_keys)} manifest key(s): {', '.join(api_keys.keys())}")
            ok("Cloudflare credentials present")
        else:
            # Automation without `subumbra.json`: `_load_env_fallback` is tombstoned (immediate `_automation_fail`).
            try:
                api_keys, cf_creds, allowed_keys_by_adapter, key_adapters_by_key_id, token_ttl_days = _load_env_fallback(existing_keys)
            except AutomationInputError as exc:
                use_wizard = _prompt_after_automation_error(str(exc))
            else:
                ok(f"Found {len(api_keys)} API key(s): {', '.join(api_keys.keys())}")
                ok("Cloudflare credentials present")
    else:
        if _has_env_credentials():
            step("Interactive wizard — RAM-only entry selected")
        else:
            step("Interactive wizard — no credentials found in environment")
    if use_wizard:
        # Interactive manifest wizard returns the same credential bundle shape as automation.
        try:
            (
                api_keys,
                cf_creds,
                allowed_keys_by_adapter,
                key_adapters_by_key_id,
                token_ttl_days,
                unique_key_flags,
                policy_by_key_id,
                shred_paths,
            ) = run_interactive_wizard(existing_keys)
        except KeyboardInterrupt:
            print("\n\nAborted. No changes written.", file=sys.stderr)
            sys.exit(0)
    if not use_wizard:
        shred_paths = []
        if not MANIFEST_FILE.exists():
            policy_index = _load_policy_index()
            policy_by_key_id = {}
            for key_id, (provider, target_host, _auth_header, _auth_prefix, _secret_ref) in api_keys.items():
                policy_by_key_id[key_id] = _resolve_policy_for_key(
                    key_id,
                    provider,
                    target_host,
                    policy_index,
                    key_adapters_by_key_id[key_id],
                )
            unique_key_flags = _load_unique_key_flags(list(api_keys.keys()))

    _validate_allowed_keys(api_keys, allowed_keys_by_adapter)

    # ── Step 2: rotation safety check ────────────────────────────────────
    # Every bootstrap run generates a NEW RSA key pair.  Any key omitted from
    # this session will be unreachable after this run.
    incoming_key_ids = set(api_keys.keys())
    existing_key_ids = set(existing_keys.keys())
    keys_to_remove   = existing_key_ids - incoming_key_ids

    if is_rotation:
        step("Existing keys.json found — ROTATION MODE")
        if any(record.get("enc_version", 1) == 2 for record in existing_keys.values()):
            warn("V2 records detected in keys.json — full bootstrap is required for V2 migration.")
        if keys_to_remove:
            warn("=" * 62)
            warn("WARNING: The following keys are in keys.json but NOT")
            warn("entered in this session.  They will be PERMANENTLY REMOVED")
            warn("because they cannot be re-encrypted under the new key pair:")
            for kid in sorted(keys_to_remove):
                ex_prov = existing_keys[kid].get("provider", "unknown")
                warn(f"  • {kid}  ({ex_prov})")
            warn("")
            warn("To keep them, re-run bootstrap and include those keys.")
            warn("=" * 62)
        else:
            ok("All existing keys are present in this session")
        warn("NEW RSA key pair and runtime tokens will be generated.")
        warn("Update your .env and restart ALL services after this run.")

    # ── Screen 3: confirmation (interactive path only) ────────────────────
    if use_wizard:
        print("\n" + "═" * 70)
        print("  Subumbra Bootstrap — Step 4 of 4: Confirm")
        print("═" * 70 + "\n")

        account_id = cf_creds["CF_ACCOUNT_ID"]
        masked = ("•" * max(0, len(account_id) - 4)) + account_id[-4:]
        print(f"  Cloudflare:")
        print(f"    Worker:  {cf_creds['CF_WORKER_NAME']}")
        print(f"    Account: {masked}\n")

        print("  Keys to encrypt:")
        for kid, (provider, _target_host, _auth_header, _auth_prefix, _secret_ref) in api_keys.items():
            print(f"    {kid:30s} → {provider:12s} → {_binding_label(key_adapters_by_key_id[kid])}")

        if keys_to_remove:
            print(f"\n  ⚠  WARNING — ROTATION MODE")
            print("  A new RSA key pair will be generated. The following existing")
            print("  keys were NOT re-entered and will be PERMANENTLY REMOVED:")
            for kid in sorted(keys_to_remove):
                ex_prov = existing_keys[kid].get("provider", "unknown")
                print(f"    • {kid}  ({ex_prov})")

        print()
        try:
            confirm = input("  Proceed? [y/N]: ").strip().lower()
        except KeyboardInterrupt:
            print("\n\nAborted. No changes written.", file=sys.stderr)
            sys.exit(0)
        if confirm != "y":
            print("\nAborted. No changes written.")
            sys.exit(0)

    had_prior_kv_state = KV_CONFIG_FILE.exists()
    # ── Pre-mutation gate: existing CF state check ────────────────────────
    # CRITICAL ORDER: This gate must run BEFORE token generation and deploy_worker().
    # If the operator aborts, no Cloudflare secrets or host .env have been modified.
    requested_nuke = "--nuke" in sys.argv
    candidate_vault_instances = sorted(
        {
            _vault_instance_for_key(key_id, unique_key_flags)
            for key_id in api_keys.keys()
        }
    )
    destructive_nuke = False

    if had_prior_kv_state:
        prompt_message = (
            "Existing Cloudflare state detected "
            f"(kv_namespace present at {KV_CONFIG_FILE})."
        )
        if requested_nuke:
            warn(prompt_message)
            destructive_nuke = True
        elif sys.stdin.isatty():
            print("\n" + "─" * 70)
            print(f"  {prompt_message}")
            try:
                confirm = input("  Nuke all detected Cloudflare state and continue? [y/N]: ").strip().lower()
            except KeyboardInterrupt:
                print("\n\nAborted. No changes written.", file=sys.stderr)
                sys.exit(0)
            if confirm != "y":
                print("\nAborted. No changes written.")
                sys.exit(0)
            destructive_nuke = True
        else:
            die(
                "Existing Cloudflare state detected, but no interactive confirmation path is available.\n"
                "  Re-run interactively or pass --nuke."
            )

    # ── Step 3: generate runtime auth tokens ─────────────────────────────
    # SECURITY: These are privileged bearer/HMAC secrets. Anyone who obtains
    # an adapter token can drive the Worker as a scoped decryption oracle.
    # Treat them with the same care as the API keys they protect.
    step("Generating runtime auth tokens")
    adapter_tokens = {
        "subumbra-proxy": secrets.token_hex(32),
        "subumbra-ui": secrets.token_hex(32),
    }
    if "subumbra-probe" in allowed_keys_by_adapter:
        adapter_tokens["subumbra-probe"] = secrets.token_hex(32)
    for adapter_id in allowed_keys_by_adapter:
        if adapter_id not in adapter_tokens:
            adapter_tokens[adapter_id] = secrets.token_hex(32)
    subumbra_hmac_key = secrets.token_hex(32)   # 64-char hex
    management_token = secrets.token_urlsafe(48)
    ok("SUBUMBRA_TOKEN_PROXY generated (proxy transport / compatibility mode)")
    ok("SUBUMBRA_TOKEN_UI generated")
    if "subumbra-probe" in adapter_tokens:
        ok("SUBUMBRA_TOKEN_PROBE generated")
    else:
        info("Probe provisioning skipped — optional diagnostic container not provisioned.")
    for adapter_id in allowed_keys_by_adapter:
        if adapter_id not in BUILTIN_ADAPTER_IDS:
            ok(f"SUBUMBRA_TOKEN_{_normalize_adapter_id(adapter_id)} generated")
    ok("SUBUMBRA_HMAC_KEY generated")
    ok("SUBUMBRA_MANAGEMENT_TOKEN generated")
    setup_token = secrets.token_urlsafe(48)
    ok("SUBUMBRA_SETUP_TOKEN generated")
    adapter_registry = _build_adapter_registry(
        adapter_tokens,
        allowed_keys_by_adapter,
        token_ttl_days=token_ttl_days,
    )
    # ── Step 4: Phase 1 — deploy worker + push secrets ───────────────────
    # CRITICAL ORDER: remote secrets are pushed BEFORE keys.json is written.
    # If the deploy fails here, keys.json still holds the old blobs that match
    # the old key pair — the system remains consistent.
    bootstrapped_providers = {v[0] for v in api_keys.values()}
    worker_url = deploy_worker(
        cf_creds,
        adapter_tokens, subumbra_hmac_key,
        management_token,
        setup_token,
        provider_id_filter=bootstrapped_providers,
    )
    ok(f"Worker URL: {worker_url}")
    host_env_updates = _build_host_env_updates(
        adapter_registry=adapter_registry,
        allowed_keys_by_adapter=allowed_keys_by_adapter,
        adapter_tokens=adapter_tokens,
        subumbra_hmac_key=subumbra_hmac_key,
        management_token=management_token,
        worker_url=worker_url,
        setup_token=setup_token,
    )
    _sync_host_env_file(host_env_updates)

    if destructive_nuke:
        step("Resetting detected Cloudflare state for fresh bootstrap")
        for vault_instance in candidate_vault_instances:
            try:
                _call_internal_vault_reset(worker_url, setup_token, vault_instance)
            except BootstrapFlowError as exc:
                die(str(exc))
            ok(f"Reset vault instance {vault_instance}")
        _delete_kv_namespace_if_present(cf_creds)
        ok("Deleted prior provider-registry KV namespace")
        for key_id in api_keys.keys():
            _delete_file_if_present(_public_key_file_for_key(key_id, _vault_instance_for_key(key_id, unique_key_flags)))
        step("Re-deploying Worker after KV namespace reset")
        worker_url = deploy_worker(
            cf_creds,
            adapter_tokens, subumbra_hmac_key,
            management_token,
            setup_token,
            provider_id_filter=bootstrapped_providers,
        )
        ok(f"Worker re-bound after reset: {worker_url}")
        host_env_updates["CF_WORKER_URL"] = worker_url
        _sync_host_env_file(host_env_updates)
        ok("Cleared local public-key artifacts after CF reset")

    # ── Step 5a: Phase 1 — /setup/keygen per vault instance (before secrets) ─
    step("Phase 1 — vault /setup/keygen (per vault instance)")
    phase1_failures: list[tuple[str, str]] = []
    public_keys_by_vault_instance: dict[str, Any] = {}

    for vault_instance in candidate_vault_instances:
        rep_key = _representative_key_id_for_vault_instance(api_keys.keys(), unique_key_flags, vault_instance)
        if rep_key is None:
            msg = "no manifest key maps to this vault_instance"
            phase1_failures.append((vault_instance, msg))
            warn(f"{vault_instance}: {msg}")
            continue
        public_key_file = _public_key_file_for_key(rep_key, vault_instance)
        try:
            if not destructive_nuke and public_key_file.exists():
                step(
                    f"Reusing existing vault public key for {vault_instance} "
                    f"from {public_key_file.name}"
                )
                public_key_pem = public_key_file.read_text()
                pub_key = _load_public_key_from_pem(public_key_pem)
            else:
                public_key_pem, pub_key_fp, _created_at = call_setup_keygen(
                    worker_url, setup_token, vault_instance
                )
                _write_public_key_file(public_key_file, public_key_pem)
                pub_key = _load_public_key_from_pem(public_key_pem)
                computed_fp = public_key_fingerprint(pub_key)
                if computed_fp != pub_key_fp:
                    die(
                        "Cloudflare setup keygen returned inconsistent fingerprint\n"
                        f"  returned: {pub_key_fp}\n"
                        f"  computed: {computed_fp}"
                    )
        except BootstrapFlowError as exc:
            phase1_failures.append((vault_instance, str(exc)))
            warn(f"{vault_instance}: vault keygen failed")
            continue
        except OSError as exc:
            phase1_failures.append((vault_instance, f"failed to read/write public key: {exc}"))
            warn(f"{vault_instance}: failed to read/write public key")
            continue

        public_keys_by_vault_instance[vault_instance] = pub_key
        info(f"{vault_instance}: fingerprint={public_key_fingerprint(pub_key)}")
        ok(f"Vault public key ready for {vault_instance}")

    phase1_failed_vaults = {vault_inst for vault_inst, _ in phase1_failures}

    # ── Step 5b: Phase 2 — per-key material from vault public keys ─────────
    step("Provisioning per-key vault public keys")
    phase2_material: dict[str, dict[str, str]] = {}
    phase2_failures: list[tuple[str, str]] = []
    forced_failure_key = os.environ.get("SUBUMBRA_FORCE_PROVISION_FAILURE_KEY", "").strip()

    for key_id, (provider, _target_host, _auth_header, _auth_prefix, _secret_ref) in api_keys.items():
        vault_instance = _vault_instance_for_key(key_id, unique_key_flags)
        if vault_instance in phase1_failed_vaults:
            warn(f"{key_id}: skipped — vault {vault_instance} failed during phase-1 keygen")
            continue
        if vault_instance not in public_keys_by_vault_instance:
            phase2_failures.append((key_id, f"vault {vault_instance} has no public key after phase 1"))
            warn(f"{key_id}: missing vault public key for {vault_instance}")
            continue

        public_key_file = _public_key_file_for_key(key_id, vault_instance)
        try:
            public_key_pem = public_key_file.read_text()
            pub_key_obj = _load_public_key_from_pem(public_key_pem)
        except OSError as exc:
            phase2_failures.append((key_id, f"failed to read public key: {exc}"))
            warn(f"{key_id}: failed to read public key")
            continue
        pub_key_fp = public_key_fingerprint(pub_key_obj)
        if public_key_fingerprint(public_keys_by_vault_instance[vault_instance]) != pub_key_fp:
            die(
                "Local public key does not match phase-1 vault key material\n"
                f"  key_id: {key_id}\n"
                f"  vault_instance: {vault_instance}"
            )
        if forced_failure_key == key_id:
            phase2_failures.append((key_id, "forced verification failure after vault provisioning"))
            warn(f"{key_id}: forced verification failure after vault provisioning")
            continue
        phase2_material[key_id] = {
            "vault_instance": vault_instance,
            "public_key_pem": public_key_pem,
            "pub_key_fp": pub_key_fp,
        }
        ok(f"Provisioned {provider:12s} → {key_id}  →  {vault_instance}")

    # ── Step 6: Phase 3 — encrypt successful keys ────────────────────────
    step("Encrypting API keys — V3 envelope (RSA-4096-OAEP + AES-256-GCM)")
    keys_payload: dict[str, dict] = {}
    raw_lengths: dict[str, int] = {}

    for key_id, (provider, target_host, _auth_header, _auth_prefix, secret_ref) in api_keys.items():
        if key_id not in phase2_material:
            continue
        raw = _resolve_manifest_secret(secret_ref)
        raw_lengths[key_id] = len(raw)
        phase2_entry = phase2_material[key_id]
        vault_instance = phase2_entry["vault_instance"]
        pub_key = public_keys_by_vault_instance[vault_instance]
        pub_key_fp = phase2_entry["pub_key_fp"]
        policy = policy_by_key_id[key_id]
        policy_hash = compute_policy_hash(policy)
        dek = os.urandom(32)
        ciphertext = encrypt_api_key_v3(dek, raw, key_id, policy_hash)
        wrapped_dek = wrap_dek(pub_key, dek)
        keys_payload[key_id] = _build_fat_record(
            key_id=key_id,
            provider=provider,
            target_host=target_host,
            pub_key_fp=pub_key_fp,
            wrapped_dek=wrapped_dek,
            ciphertext=ciphertext,
            policy=policy,
            policy_hash=policy_hash,
            adapters=key_adapters_by_key_id[key_id],
            vault_instance=vault_instance,
            created_at=now_iso,
            label=key_id,
        )
        del dek
        raw = "\x00" * raw_lengths[key_id]
        del raw
        ok(
            f"Encrypted {provider:12s} → {key_id}  →  "
            f"{_binding_label(key_adapters_by_key_id[key_id])}  →  {vault_instance}"
        )

    # ── Step 7: Phase 4 — write successful keys only ─────────────────────
    step(f"Atomically writing encrypted blobs → {KEYS_FILE}")
    _write_keys_payload(keys_payload)
    ok(f"Wrote {len(keys_payload)} key blob(s) — atomic rename complete")
    info("Blobs are useless without the CF private key — safe to store")

    # ── Step 8: publish structured KV ────────────────────────────────────
    step("Publishing structured KV entries to Cloudflare KV")
    try:
        _publish_structured_kv(cf_creds, keys_payload)
    except SystemExit:
        die("structured publish aborted before registry_version update")
    ok("structured publish complete")

    primary_pub_key_fp = next(
        (
            entry["pub_key_fp"]
            for entry in phase2_material.values()
            if entry["vault_instance"] == "vault"
        ),
        next(iter(phase2_material.values()), {}).get("pub_key_fp", ""),
    )

    # ── Step 9: write runtime env with restricted permissions ────────────
    # SECURITY: These tokens are privileged secrets.  Write with mode 0600
    # and do NOT print values to stdout (which may be captured in CI/CD logs).
    step(f"Writing runtime env → {RUNTIME_ENV_OUT}")
    runtime_env_lines = _build_runtime_env_lines(
        now_iso=now_iso,
        adapter_registry=adapter_registry,
        allowed_keys_by_adapter=allowed_keys_by_adapter,
        adapter_tokens=adapter_tokens,
        subumbra_hmac_key=subumbra_hmac_key,
        management_token=management_token,
        worker_url=worker_url,
        primary_pub_key_fp=primary_pub_key_fp,
    )
    _write_runtime_env_file(runtime_env_lines)
    _sync_host_env_file(host_env_updates)

    if not phase1_failures and not phase2_failures:
        step("Deleting transient SUBUMBRA_SETUP_TOKEN from CF Secrets")
        _delete_worker_secret(cf_creds, "SUBUMBRA_SETUP_TOKEN")
        _sync_host_env_file({"SUBUMBRA_SETUP_TOKEN": ""})
        ok("SUBUMBRA_SETUP_TOKEN zeroed in host .env (CF secret already deleted)")
        ok("Bootstrap cleanup complete (setup token removed from CF and zeroed in host .env)")

    # ── Step 10: zero sensitive memory ───────────────────────────────────
    step("Clearing sensitive values from memory")
    for adapter_id in list(adapter_tokens):
        adapter_tokens[adapter_id] = "\x00" * len(adapter_tokens[adapter_id])
    del adapter_tokens
    management_token = "\x00" * len(management_token)
    del management_token
    setup_token = "\x00" * len(setup_token)
    del setup_token
    # Zero resolved API key lengths where known; otherwise clear secret_ref slot length
    for k in list(api_keys):
        provider, target_host, auth_header, auth_prefix, secret_ref = api_keys[k]
        n = raw_lengths.get(k, len(secret_ref))
        api_keys[k] = (provider, target_host, auth_header, auth_prefix, "\x00" * n)
    del api_keys
    for _wk in list(_WIZARD_SECRETS):
        _wv = _WIZARD_SECRETS[_wk]
        _WIZARD_SECRETS[_wk] = "\x00" * len(_wv)
    _WIZARD_SECRETS.clear()
    del allowed_keys_by_adapter
    del cf_creds
    gc.collect()
    ok("Sensitive memory cleared (best-effort)")

    if phase1_failures or phase2_failures:
        print("\n" + "─" * 70)
        print("  Bootstrap completed with partial success")
        if phase1_failures:
            print("  Phase-1 vault keygen failures:")
            for vault_inst, message in phase1_failures:
                print(f"    • {vault_inst}: {message.splitlines()[0]}")
        if phase2_failures:
            print("  Successful records are live; failed keys were skipped:")
            for key_id, message in phase2_failures:
                print(f"    • {key_id}: {message.splitlines()[0]}")
        if phase2_failures:
            print("\n  Retry each failed key with:")
            for key_id, _message in phase2_failures:
                print(f"    ./bootstrap.sh --provision {key_id}")
        elif phase1_failures:
            print("\n  Re-run full bootstrap after fixing vault keygen for the instance(s) above.")
        print("─" * 70)
        sys.exit(1)

    if shred_paths:
        print("\n" + "─" * 70)
        print("  Shredding source .env files...")
        for shred_path in shred_paths:
            try:
                result = subprocess.run(
                    ["shred", "-u", shred_path],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    ok(f"Shredded: {shred_path}")
                else:
                    warn(f"shred failed for {shred_path}: {result.stderr.strip()}")
                    print(f"  ⚠  Manual deletion required: rm -P {shred_path}")
            except FileNotFoundError:
                warn(f"shred not found. Manual deletion required: rm -P {shred_path}")

    # ── Step 13: print summary (NO token values) ─────────────────────────
    rule = "═" * 68
    print(f"\n{rule}")
    print("  Bootstrap complete!")
    print(rule)
    print(textwrap.dedent(f"""
  New runtime tokens have been written to:
    {RUNTIME_ENV_OUT}

  Token values are NOT printed here (to avoid CI/CD log capture).
  Repo-local .env is updated automatically when /app/host-env is mounted.

  Next steps:
    1. Start/restart ALL services (new tokens generated):
       docker compose up -d --force-recreate
    2. Check all containers running:  docker compose ps
    3. Check worker health:           curl {worker_url}/health
    4. For any app-owned integration, set:
         api_base: http://subumbra-proxy:8090/t/<key_id>/...
         api_key:  <SUBUMBRA_TOKEN_YOUR_APP>   (adapter token from .env, NOT the key_id)
       See docs/adapter-contract.md for the canonical integration reference.

  V3 envelope encryption active:
    Shared key:    {PUBLIC_KEY_FILE}
    Fingerprint:   {primary_pub_key_fp or "(unique-vault only run)"}
    Per-key rotate: existing V3 records only via ./bootstrap.sh --rotate
    Pause/unpause: Worker management API via SUBUMBRA_MANAGEMENT_TOKEN
    Revoke key:    ./bootstrap.sh --revoke-key <key_id> [--offline]
                   (--offline: keys.json only; then re-run without --offline for KV delete)
    Adapter edit:  ./bootstrap.sh --add-adapter <key_id> <adapter_id>
                   ./bootstrap.sh --revoke-adapter <key_id> <adapter_id>
    Policy publish: ./bootstrap.sh --publish-policy <key_id>
    Targeted repair: ./bootstrap.sh --provision <key_id>
"""))


def print_help() -> None:
    print("""
Subumbra Bootstrap Utility

Usage: ./bootstrap.sh [OPTIONS]

Options:
  --help, -h                  Show this help message and exit
  --list-key-ids              List all key IDs defined in the manifest (subumbra.yaml)
  --list-adapters             List all unique adapters defined in the manifest (subumbra.yaml)
  --status                    Compare manifest authority to deployed record state
  --upgrade                   Rebuild images and recreate containers
  --nuke                      Destructive run: destroys existing Cloudflare Vault keypairs
                              and regenerates everything from scratch
  --rotate                    Rotate upstream keys for existing records
  --push-registry             Push keys.json state directly to Cloudflare KV
  --provision <key_id>        Targeted provisioning/repair for a single key
  --revoke-key <key_id>       Revoke a key (deletes from KV; --offline updates local keys.json only)
  --add-adapter <key_id>      Add an adapter binding to an existing key
  --revoke-adapter <key_id>   Revoke an adapter binding from an existing key
  --publish-policy <key_id>   Republish a key's policy/adapters to KV

For a full initial bootstrap, run without arguments.
""")


def print_key_ids() -> None:
    try:
        key_ids = _load_manifest_key_ids_only()
        for kid in sorted(key_ids):
            print(kid)
    except SystemExit:
        sys.exit(1)


def print_adapters() -> None:
    try:
        records = _load_manifest_records()
        adapters: set[str] = set()
        for r in records:
            adapters.update(r["effective_adapters"])
        for a in sorted(adapters):
            print(a)
    except SystemExit:
        sys.exit(1)


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        sys.exit(0)
    elif "--list-key-ids" in sys.argv:
        print_key_ids()
        sys.exit(0)
    elif "--list-adapters" in sys.argv:
        print_adapters()
        sys.exit(0)
    elif "--status" in sys.argv:
        run_status()
        sys.exit(0)

    if "--offline" in sys.argv and "--revoke-key" not in sys.argv:
        die("--offline is only supported together with --revoke-key")
    if "--rotate-policy" in sys.argv:
        die("--rotate-policy has been removed. Re-run full bootstrap for policy, routing, or adapter-binding changes.")
    mode_flags = (
        "--push-registry",
        "--rotate",
        "--provision",
        "--revoke-key",
        "--add-adapter",
        "--revoke-adapter",
        "--publish-policy",
        "--status",
    )
    selected_modes = sum(flag in sys.argv for flag in mode_flags)
    if selected_modes > 1:
        die(", ".join(mode_flags) + " are mutually exclusive")
    if "--nuke" in sys.argv and selected_modes > 0:
        die("--nuke is supported only for full bootstrap")
    if "--push-registry" in sys.argv:
        run_push_registry()
    elif "--revoke-key" in sys.argv:
        try:
            target_key_id = sys.argv[sys.argv.index("--revoke-key") + 1]
        except IndexError:
            die("--revoke-key requires <key_id>")
        run_revoke_key(target_key_id)
    elif "--add-adapter" in sys.argv:
        try:
            idx = sys.argv.index("--add-adapter")
            target_key_id = sys.argv[idx + 1]
            adapter_id = sys.argv[idx + 2]
        except IndexError:
            die("--add-adapter requires <key_id> <adapter_id>")
        run_add_adapter(target_key_id, adapter_id)
    elif "--revoke-adapter" in sys.argv:
        try:
            idx = sys.argv.index("--revoke-adapter")
            target_key_id = sys.argv[idx + 1]
            adapter_id = sys.argv[idx + 2]
        except IndexError:
            die("--revoke-adapter requires <key_id> <adapter_id>")
        run_revoke_adapter(target_key_id, adapter_id)
    elif "--publish-policy" in sys.argv:
        try:
            target_key_id = sys.argv[sys.argv.index("--publish-policy") + 1]
        except IndexError:
            die("--publish-policy requires <key_id>")
        run_publish_policy(target_key_id)
    elif "--provision" in sys.argv:
        try:
            target_key_id = sys.argv[sys.argv.index("--provision") + 1]
        except IndexError:
            die("--provision requires <key_id>")
        run_provision_key(target_key_id)
    elif "--rotate" in sys.argv:
        run_rotate_wizard()
    else:
        main()
