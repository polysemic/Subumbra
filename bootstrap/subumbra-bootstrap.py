#!/usr/bin/env python3
"""
subumbra-bootstrap — V2 Asymmetric Envelope Encryption & Deployment.

Usage (interactive — primary):
    ./bootstrap.sh

Usage (automation / CI — requires .env.bootstrap with all credentials):
    ./bootstrap.sh

Single-key rotation (no Cloudflare interaction):
    docker compose --profile bootstrap run --rm -it bootstrap --rotate

What it does (full bootstrap, in order):
  1. Detects mode: headless env-var fallback (CI) or, in a TTY, prompts when
     environment credentials are already present
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
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NoReturn

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║       Subumbra Bootstrap — V2 Envelope Encryption                ║
║  API keys exist in RAM only.  Nothing sensitive is written.      ║
║                                                                  ║
║  Full:     docker compose --profile bootstrap run --rm -it       ║
║  Rotate:   ... run --rm -it bootstrap --rotate                   ║
║  CI/auto:  docker compose --profile bootstrap run --rm           ║
╚══════════════════════════════════════════════════════════════════╝
"""

# Known providers for the numbered wizard menu.
# Format: (provider_label, env_var_name_for_CI_fallback)
# This is derived from the shared built-in provider registry.

# key_id validation: lowercase alphanumeric + underscores + hyphens, 3-64 chars
KEY_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{2,63}$')
ADAPTER_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,61}[a-z0-9]$')

DATA_DIR        = Path(os.environ.get("DATA_DIR", "/app/data"))
KEYS_FILE       = DATA_DIR / "keys.json"
RUNTIME_ENV_OUT = DATA_DIR / "runtime.env"
PUBLIC_KEY_FILE = DATA_DIR / "public_key.pem"
CHECKPOINT_FILE = DATA_DIR / "bootstrap-checkpoint.json"
SYSTEM_INTEGRITY_FILE = DATA_DIR / "system-integrity.json"
HOST_ENV_FILE   = Path("/app/host-env")
WORKER_SRC      = Path("/app/worker")
PROVIDERS_FILE  = Path("/app/providers.json")
CUSTOM_PROVIDER_REGISTRY_FILE = DATA_DIR / "custom-providers.json"
KV_CONFIG_FILE = DATA_DIR / "kv-config.json"
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


def _vault_instance_for_key(key_id: str, unique_key_flags: dict[str, bool]) -> str:
    if unique_key_flags.get(key_id, False):
        return f"vault-{key_id}"
    return "vault"


def _public_key_file_for_key(key_id: str, vault_instance: str) -> Path:
    if vault_instance == "vault":
        return PUBLIC_KEY_FILE
    return DATA_DIR / f"public_key_{key_id}.pem"


def _load_bootstrap_checkpoint() -> dict[str, Any]:
    if not CHECKPOINT_FILE.exists():
        return {"worker_url": "", "setup_token": "", "keys": {}, "host_env_updates": {}}
    try:
        checkpoint = json.loads(CHECKPOINT_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        die(f"Cannot read bootstrap checkpoint: {exc}")
    if not isinstance(checkpoint, dict):
        die("bootstrap checkpoint is malformed")
    checkpoint.setdefault("worker_url", "")
    checkpoint.setdefault("setup_token", "")
    checkpoint.setdefault("keys", {})
    checkpoint.setdefault("host_env_updates", {})
    if not isinstance(checkpoint["keys"], dict):
        die("bootstrap checkpoint keys section is malformed")
    if not isinstance(checkpoint["host_env_updates"], dict):
        die("bootstrap checkpoint host_env_updates section is malformed")
    return checkpoint


def _write_bootstrap_checkpoint(checkpoint: dict[str, Any]) -> None:
    tmp_file = CHECKPOINT_FILE.with_suffix(".json.tmp")
    try:
        fd = os.open(str(tmp_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(checkpoint, fh, indent=2)
            fh.write("\n")
        os.replace(str(tmp_file), str(CHECKPOINT_FILE))
    except OSError as exc:
        die(f"Failed to write bootstrap checkpoint: {exc}")


def _delete_bootstrap_checkpoint() -> None:
    try:
        CHECKPOINT_FILE.unlink(missing_ok=True)
    except OSError as exc:
        die(f"Failed to delete bootstrap checkpoint: {exc}")


def _checkpoint_entry_by_vault_instance(checkpoint: dict[str, Any], vault_instance: str) -> dict[str, Any] | None:
    for entry in checkpoint.get("keys", {}).values():
        if isinstance(entry, dict) and entry.get("vault_instance") == vault_instance:
            return entry
    return None


def _store_checkpoint_entry(
    checkpoint: dict[str, Any],
    key_id: str,
    *,
    vault_instance: str,
    public_key_pem: str,
    pub_key_fp: str,
) -> None:
    checkpoint.setdefault("keys", {})
    checkpoint["keys"][key_id] = {
        "vault_instance": vault_instance,
        "public_key_pem": public_key_pem,
        "pub_key_fp": pub_key_fp,
    }
    _write_bootstrap_checkpoint(checkpoint)


def _write_public_key_file(path: Path, public_key_pem: str) -> None:
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(public_key_pem.encode("utf-8"))
    except OSError as exc:
        die(f"Failed to write {path.name}: {exc}")


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
    worker_url: str,
    setup_token: str,
) -> dict[str, str]:
    host_env_updates = {
        "SUBUMBRA_ADAPTER_REGISTRY": json.dumps(adapter_registry, separators=(",", ":")),
        "PROXY_ALLOWED_KEYS": ",".join(allowed_keys_by_adapter["subumbra-proxy"]),
        "UI_ALLOWED_KEYS": ",".join(allowed_keys_by_adapter["subumbra-ui"]),
        "SUBUMBRA_TOKEN_PROXY": adapter_tokens["subumbra-proxy"],
        "SUBUMBRA_TOKEN_UI": adapter_tokens["subumbra-ui"],
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
        info(f"Host env sync skipped — {HOST_ENV_FILE} is unavailable")


def _load_provider_registry() -> list[dict]:
    """Load and validate the shared built-in provider registry."""
    reg_path = PROVIDERS_FILE
    try:
        with reg_path.open() as fh:
            entries = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        die(f"Cannot load provider registry at {reg_path}: {exc}")

    if not isinstance(entries, list):
        die("providers.json: top-level value must be a JSON array")

    required = {"provider_id", "target_host", "auth_header", "auth_prefix", "env_var"}
    seen_ids: set[str] = set()
    seen_hosts: set[str] = set()
    seen_vars: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            die(f"providers.json: each entry must be an object: {entry!r}")

        missing = required - entry.keys()
        if missing:
            die(f"providers.json: entry missing fields {sorted(missing)}: {entry}")

        if not all(isinstance(entry[field], str) for field in required):
            die(f"providers.json: all fields must be strings: {entry}")

        provider_id = entry["provider_id"]
        target_host = entry["target_host"]
        env_var = entry["env_var"]

        if provider_id in seen_ids:
            die(f"providers.json: duplicate provider_id '{provider_id}'")
        if target_host in seen_hosts:
            die(f"providers.json: duplicate target_host '{target_host}'")
        if not env_var:
            die(f"providers.json: 'env_var' must be a non-empty string: {entry}")
        if env_var in seen_vars:
            die(f"providers.json: duplicate env_var '{env_var}'")

        seen_ids.add(provider_id)
        seen_hosts.add(target_host)
        seen_vars.add(env_var)

    return entries


_REGISTRY = _load_provider_registry()
BUILTIN_PROVIDER_BY_ID = {
    entry["provider_id"]: entry
    for entry in _REGISTRY
}
PROVIDER_HOSTS: dict[str, str] = {
    entry["provider_id"]: entry["target_host"]
    for entry in _REGISTRY
}
KNOWN_PROVIDERS: list[tuple[str, str]] = [
    (entry["provider_id"], entry["env_var"])
    for entry in _REGISTRY
]

# Maps both Subumbra canonical env var names AND common standalone-app aliases
# to their provider_id. Both sides must be supported so that migration from a
# standard LiteLLM .env (which uses ANTHROPIC_API_KEY) and the CI path (which
# uses ANTHROPIC_KEY) both work.
IMPORT_PROVIDER_WHITELIST: dict[str, str] = {
    # Subumbra canonical names (from providers.json env_var field)
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
NON_LLM_BUILTIN_PROVIDERS = {"slack", "sendgrid"}
OPENAI_COMPATIBLE_BUILTIN_PROVIDERS = {
    "openai",
    "groq",
    "deepseek",
    "cerebras",
    "gemini",
    "mistral",
    "openrouter",
    "together",
    "xai",
    "github",
}


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
    if not (value.startswith("^") and value.endswith("$")):
        _policy_die(source, f"{field_name} must be anchored with ^...$")
    body = value[1:-1]
    if _is_safe_literal_pattern(body):
        return
    if body.startswith("(") and body.endswith(")") and body.count("(") == 1 and body.count(")") == 1:
        alternatives = body[1:-1].split("|")
        if len(alternatives) >= 2 and all(_is_safe_literal_pattern(item) for item in alternatives):
            return
    _policy_die(source, f"{field_name} uses a pattern outside the safe vocabulary")


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
    if velocity is not None and not isinstance(velocity, dict):
        _policy_die(source, "velocity must be an object")

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


def _auth_scheme_from_provider_entry(provider_entry: dict[str, str]) -> str:
    auth_header = provider_entry["auth_header"].lower()
    auth_prefix = provider_entry["auth_prefix"]
    if auth_header == "authorization" and auth_prefix == "Bearer ":
        return "bearer"
    if auth_header == "authorization" and auth_prefix == "Basic ":
        return "basic"
    return "header"

def _binding_policy_id(key_id: str, allowed_adapters: list[str]) -> str:
    if "subumbra-proxy" in allowed_adapters:
        return f"auto-compat-{key_id}"
    return f"auto-app-{key_id}"


def _synthesize_builtin_policy(
    key_id: str,
    provider: str,
    allowed_adapters: list[str],
) -> dict[str, Any] | None:
    if provider not in BUILTIN_PROVIDER_BY_ID:
        return None
    provider_entry = BUILTIN_PROVIDER_BY_ID[provider]
    capability_class = "custom_rest" if provider in NON_LLM_BUILTIN_PROVIDERS else "llm"
    protocol = "openai_compatible" if provider in OPENAI_COMPATIBLE_BUILTIN_PROVIDERS else "http_rest"
    auth: dict[str, Any] = {"scheme": _auth_scheme_from_provider_entry(provider_entry)}
    if auth["scheme"] == "header":
        auth["header_name"] = provider_entry["auth_header"]
    return {
        "key_id": key_id,
        "policy_id": _binding_policy_id(key_id, allowed_adapters),
        "protocol": protocol,
        "capability_class": capability_class,
        "source": "env",
        "target": {"host": provider_entry["target_host"]},
        "auth": auth,
        "allow": {
            "adapters": allowed_adapters,
            "methods": ["GET", "POST"],
            "path_prefixes": [provider_entry.get("api_base_path") or "/v1/chat/completions"],
            "content_types": ["application/json"],
            "max_body_bytes": 1048576,
        },
    }


def _resolve_policy_for_direct_secret(
    key_id: str,
    provider: str,
    policy_index: dict[str, dict[str, Any]],
    allowed_adapters: list[str],
) -> dict[str, Any]:
    policy = policy_index.get(key_id)
    if policy is not None:
        if policy.get("source") != "env":
            _automation_fail(
                f"Automation mode: key_id {key_id} policy source must be 'env' for direct secret ingestion"
            )
        if sorted(_policy_adapter_ids(policy)) != sorted(allowed_adapters):
            _automation_fail(
                f"Automation mode: adapter binding for {key_id} does not match SUBUMBRA_POLICY_PATH\n"
                f"  requested: {', '.join(allowed_adapters)}\n"
                f"  policy   : {', '.join(_policy_adapter_ids(policy))}"
            )
        return policy
    synthesized = _synthesize_builtin_policy(key_id, provider, allowed_adapters)
    if synthesized is not None:
        return synthesized
    _automation_fail(
        f"Automation mode: refusing policy-less secret for key_id {key_id} provider {provider}"
    )


def _require_import_policy(key_id: str, policy_index: dict[str, dict[str, Any]], import_path: str) -> dict[str, Any]:
    policy = policy_index.get(key_id)
    if policy is None or policy.get("source") != "import_path":
        _automation_fail(
            f"Automation mode: import path {import_path} requires matching policy with source 'import_path' for key_id {key_id}"
        )
    return policy


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


def _validate_live_provider_registry(entries: list[dict], source: str) -> list[dict]:
    required = {"provider_id", "target_host", "auth_header", "auth_prefix"}
    if not isinstance(entries, list):
        die(f"{source}: top-level value must be a JSON array")

    seen_ids: set[str] = set()
    seen_hosts: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            die(f"{source}: each entry must be an object: {entry!r}")
        missing = required - entry.keys()
        if missing:
            die(f"{source}: entry missing fields {sorted(missing)}: {entry}")
        if not all(isinstance(entry[field], str) for field in required):
            die(f"{source}: all fields must be strings: {entry}")
        provider_id = entry["provider_id"]
        target_host = entry["target_host"]
        if provider_id in seen_ids:
            die(f"{source}: duplicate provider_id '{provider_id}'")
        if target_host in seen_hosts:
            die(f"{source}: duplicate target_host '{target_host}'")
        seen_ids.add(provider_id)
        seen_hosts.add(target_host)
    return entries


def _load_custom_provider_registry() -> list[dict]:
    if not CUSTOM_PROVIDER_REGISTRY_FILE.exists():
        return []
    try:
        with CUSTOM_PROVIDER_REGISTRY_FILE.open() as fh:
            entries = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        die(f"Cannot load custom provider registry at {CUSTOM_PROVIDER_REGISTRY_FILE}: {exc}")
    return _validate_live_provider_registry(entries, "custom-providers.json")


def _project_builtin_provider_registry(entries: list[dict]) -> list[dict]:
    projected = [
        {
            "provider_id": entry["provider_id"],
            "target_host": entry["target_host"],
            "auth_header": entry["auth_header"],
            "auth_prefix": entry["auth_prefix"],
        }
        for entry in entries
    ]
    return _validate_live_provider_registry(projected, "projected built-in provider registry")


def _build_live_provider_registry_json(
    provider_id_filter: "set[str] | None" = None,
) -> str:
    """Build the JSON blob pushed to Cloudflare KV.

    provider_id_filter: when set, only built-in entries whose provider_id is in
    the filter are included.  Custom provider entries are always included.
    Pass None (default) to include all built-ins, e.g. for --push-registry.
    """
    builtins = _project_builtin_provider_registry(_load_provider_registry())
    if provider_id_filter is not None:
        builtins = [e for e in builtins if e["provider_id"] in provider_id_filter]
    custom = _load_custom_provider_registry()
    merged = builtins + custom
    _validate_live_provider_registry(merged, "merged live provider registry")
    return json.dumps(merged, separators=(",", ":"))


def _load_template_registry() -> dict[str, dict[str, str]]:
    templates: dict[str, dict[str, str]] = {}
    for entry in _load_provider_registry():
        templates[entry["provider_id"]] = {
            "provider_id": entry["provider_id"],
            "target_host": entry["target_host"],
            "auth_header": entry["auth_header"],
            "auth_prefix": entry["auth_prefix"],
            "api_base_path": entry.get("api_base_path", ""),
        }
    for entry in _load_custom_provider_registry():
        templates.setdefault(
            entry["provider_id"],
            {
                "provider_id": entry["provider_id"],
                "target_host": entry["target_host"],
                "auth_header": entry["auth_header"],
                "auth_prefix": entry["auth_prefix"],
                "api_base_path": "",
            },
        )
    return templates


def _resolve_policy_for_key(
    key_id: str,
    provider: str,
    target_host: str,
    policy_index: dict[str, dict[str, Any]],
    allowed_adapters: list[str],
) -> dict[str, Any]:
    policy = policy_index.get(key_id)
    if policy is None:
        policy = _synthesize_builtin_policy(key_id, provider, allowed_adapters)
    if policy is None:
        die(f"No policy found for key_id {key_id!r}")
    if policy["target"]["host"] != target_host:
        die(
            f"Policy host conflict for key_id {key_id!r}: "
            f"policy target.host={policy['target']['host']!r} "
            f"does not match bootstrap target_host={target_host!r}"
        )
    return policy


def _build_structured_kv_entries(
    keys_payload: dict[str, dict[str, Any]],
    policy_by_key_id: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    template_registry = _load_template_registry()
    entries: list[dict[str, str]] = []
    published_policy_ids: set[str] = set()
    published_templates: set[str] = set()

    for key_id, record in sorted(keys_payload.items()):
        policy = policy_by_key_id[key_id]
        provider_id = record["provider"]
        template = template_registry.get(provider_id)
        if template is None:
            die(f"No template routing defaults found for provider {provider_id!r}")

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
            "auth_header": template["auth_header"],
            "auth_prefix": template["auth_prefix"],
            "template_name": template["provider_id"],
        }
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

        template_name = template["provider_id"]
        if template_name not in published_templates:
            entries.append(
                {
                    "key": f"template:{template_name}",
                    "value": json.dumps(template, separators=(",", ":")),
                }
            )
            published_templates.add(template_name)

    return entries


def _upsert_custom_provider_registry_entry(
    provider_id: str,
    target_host: str,
    auth_header: str,
    auth_prefix: str,
) -> None:
    entries = _load_custom_provider_registry()
    replacement = {
        "provider_id": provider_id,
        "target_host": target_host,
        "auth_header": auth_header,
        "auth_prefix": auth_prefix,
    }

    for entry in entries:
        same_id = entry["provider_id"] == provider_id
        same_host = entry["target_host"] == target_host
        if same_id or same_host:
            if entry != replacement:
                die(
                    "custom provider metadata conflict for "
                    f"provider_id='{provider_id}' or target_host='{target_host}'"
                )
            return

    entries.append(replacement)
    with CUSTOM_PROVIDER_REGISTRY_FILE.open("w") as fh:
        json.dump(entries, fh, indent=2)
        fh.write("\n")


def _resolve_target_host(provider: str, *, prompt_if_missing: bool) -> str:
    target_host = PROVIDER_HOSTS.get(provider)
    if target_host:
        return target_host
    if prompt_if_missing:
        while True:
            host = input("  Custom target host (e.g. api.example.com): ").strip().lower()
            if host:
                return host
            print("  ✗  Target host cannot be empty.\n")
    die(
        f"No target_host mapping for provider '{provider}' in automation mode.\n"
        "  Automation mode supports only built-in providers in the registry.\n"
        "  Re-run interactively to add a custom provider:\n"
        "    docker compose --profile bootstrap run --rm -it bootstrap"
    )


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


def _load_persisted_key_adapter_bindings() -> dict[str, list[str]]:
    raw_registry = os.environ.get("SUBUMBRA_ADAPTER_REGISTRY", "").strip()
    if not raw_registry and HOST_ENV_FILE.is_file():
        env_values = _load_simple_env_file(HOST_ENV_FILE)
        raw_registry = env_values.get("SUBUMBRA_ADAPTER_REGISTRY", "").strip()
    if not raw_registry and not HOST_ENV_FILE.is_file():
        die(
            "Cannot reconstruct app bindings for existing keys because SUBUMBRA_ADAPTER_REGISTRY is not available "
            "in the environment and /app/host-env is unavailable.\n"
            "  Re-run full bootstrap from this repo checkout or provide explicit SUBUMBRA_POLICY_PATH entries."
        )
    if not raw_registry:
        die(
            "Cannot reconstruct app bindings for existing keys because SUBUMBRA_ADAPTER_REGISTRY is missing from "
            "the available bootstrap inputs.\n  Re-run full bootstrap from this repo checkout or provide explicit SUBUMBRA_POLICY_PATH entries."
        )
    try:
        registry = json.loads(raw_registry)
    except json.JSONDecodeError as exc:
        die(f"SUBUMBRA_ADAPTER_REGISTRY in {HOST_ENV_FILE} is invalid JSON: {exc}")
    if not isinstance(registry, dict):
        die(f"SUBUMBRA_ADAPTER_REGISTRY in {HOST_ENV_FILE} must be a JSON object")

    key_adapters_by_key_id: dict[str, list[str]] = {}
    for adapter_id, entry in registry.items():
        if adapter_id in {"subumbra-ui", "subumbra-probe"}:
            continue
        if not isinstance(entry, dict):
            die(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id}] must be an object")
        allowed_keys = entry.get("allowed_keys")
        if not isinstance(allowed_keys, list):
            die(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id}].allowed_keys must be an array")
        for raw_key_id in allowed_keys:
            if not isinstance(raw_key_id, str) or not raw_key_id:
                die(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id}].allowed_keys entries must be non-empty strings")
            if adapter_id == "subumbra-proxy" or adapter_id not in BUILTIN_ADAPTER_IDS:
                _append_unique_adapter_binding(key_adapters_by_key_id, raw_key_id, adapter_id)
    return key_adapters_by_key_id


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


def _collect_automation_imports() -> list[tuple[str, str]]:
    imports: list[tuple[str, str]] = []
    indices: list[int] = []
    for key in os.environ:
        match = re.fullmatch(r"IMPORT_PATH_(\d+)", key)
        if match:
            indices.append(int(match.group(1)))

    for idx in sorted(set(indices)):
        path = os.environ.get(f"IMPORT_PATH_{idx}", "").strip()
        if not path:
            continue
        label = os.environ.get(f"IMPORT_APP_LABEL_{idx}", "").strip().lower()
        if not label:
            _automation_fail(
                f"Automation mode: IMPORT_APP_LABEL_{idx} is required when IMPORT_PATH_{idx} is set"
            )
        if not ADAPTER_ID_RE.fullmatch(label):
            _automation_fail(
                f"Automation mode: invalid IMPORT_APP_LABEL_{idx} value {label!r}\n"
                "  App/label must match ^[a-z0-9][a-z0-9_-]{0,61}[a-z0-9]$"
            )
        imports.append((path, label))

    return imports


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


def _run_import_screen(
    api_keys: dict[str, tuple[str, str, str, str, str]],
    existing_keys: dict,
) -> tuple[dict[str, tuple[str, str, str, str, str]], list[str]]:
    """
    Interactive import loop: operator specifies one or more .env file paths,
    wizard detects provider keys, operator confirms each, keys are added to
    api_keys. Operator may re-run the loop for multiple files.

    Returns updated api_keys plus shred_paths.
    """
    shred_paths: list[str] = []
    policy_index = _load_policy_index()

    while True:
        print("\n" + "─" * 70)
        print("  Import from .env file")
        print("  (In-container path — mount host files with -v /opt/...:/host_...:ro)")
        print("─" * 70)
        path = input("  Path to .env file (or Enter to skip): ").strip()
        if not path:
            break

        detected = _parse_env_file(path)

        if not detected:
            print(f"  ✗  No recognised provider keys found in {path}.")
            print("     (App-internal secrets like LITELLM_MASTER_KEY are excluded by design.)")
            print("     File will NOT be shredded. Add keys manually below if needed.")
            another = input("\n  Import from another file? [y/N]: ").strip().lower()
            if another != "y":
                break
            continue

        print(f"\n  Detected {len(detected)} provider key(s):")
        for env_var, provider_id, raw_value in detected:
            print(f"    {env_var:22s} → {provider_id:12s} ({len(raw_value)} chars)")

        confirm = input("\n  Import these keys? [y/N]: ").strip().lower()
        if confirm != "y":
            print("  Skipped. File will NOT be shredded.")
            another = input("\n  Import from another file? [y/N]: ").strip().lower()
            if another != "y":
                break
            continue

        app_label = _prompt_app_label("\n  App/label for keys from this file: ")
        for env_var, provider_id, raw_value in detected:
            target_host = _resolve_target_host(provider_id, prompt_if_missing=False)
            provider_entry = BUILTIN_PROVIDER_BY_ID[provider_id]
            auth_header = provider_entry["auth_header"]
            auth_prefix = provider_entry["auth_prefix"]
            duplicate_key_id = _find_duplicate_secret_key_id(api_keys, provider_id, raw_value)
            if duplicate_key_id is not None:
                create_new = _prompt_duplicate_secret_action(provider_id, duplicate_key_id)
                if not create_new:
                    ok(f"{provider_id:12s}  →  reusing {duplicate_key_id}  (from {env_var}, key hidden)")
                    continue

            key_id = _next_generated_key_id(provider_id, app_label, api_keys, existing_keys)
            try:
                _require_import_policy(key_id, policy_index, path)
            except AutomationInputError as exc:
                print(f"  ✗  {exc}")
                print("     File will NOT be shredded and no record will be created.")
                another = input("\n  Import from another file? [y/N]: ").strip().lower()
                if another != "y":
                    return api_keys, shred_paths
                break
            api_keys[key_id] = (provider_id, target_host, auth_header, auth_prefix, raw_value)
            ok(f"{provider_id:12s}  →  {key_id}  (from {env_var}, key hidden)")
        else:
            shred_confirm = input(
                f"\n  Shred source file {path} after bootstrap completes? [y/N]: "
            ).strip().lower()
            if shred_confirm == "y":
                shred_paths.append(path)
                print(f"  ✓ {path} queued for shredding after successful bootstrap.")
            else:
                print(f"  Skipped shredding. Raw keys remain in {path}.")

            another = input("\n  Import from another file? [y/N]: ").strip().lower()
            if another != "y":
                break

    return api_keys, shred_paths


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


# ─────────────────────────────────────────────────────────────────────────────
# Mode detection
# ─────────────────────────────────────────────────────────────────────────────

def _has_env_credentials() -> bool:
    """
    Return True if the environment contains all required credentials for
    unattended (CI/automation) mode:
      - CF_API_TOKEN     (non-empty)
      - CF_ACCOUNT_ID    (non-empty)
      - at least one provider key from the built-in provider registry (non-empty)

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
    has_provider = any(
        os.environ.get(env_var, "").strip() and
        "REPLACE_ME" not in os.environ.get(env_var, "").upper()
        for _, env_var in KNOWN_PROVIDERS
    )
    return has_provider


def _has_cf_credentials() -> bool:
    required = ("CF_API_TOKEN", "CF_ACCOUNT_ID", "CF_WORKER_NAME")
    return all(os.environ.get(name, "").strip() for name in required)


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
            "CF_WORKER_NAME": os.environ["CF_WORKER_NAME"].strip(),
        }

    if not sys.stdin.isatty():
        die("Missing CF_API_TOKEN / CF_ACCOUNT_ID / CF_WORKER_NAME for --push-registry")

    while True:
        cf_token = getpass.getpass("  Cloudflare API token: ").strip()
        if cf_token:
            break
        print("  ✗  API token cannot be empty.\n")
    while True:
        cf_account_id = input("  Cloudflare account ID: ").strip()
        if cf_account_id:
            break
        print("  ✗  Account ID cannot be empty.\n")
    while True:
        cf_worker_name = input("  Cloudflare Worker name: ").strip()
        if cf_worker_name:
            break
        print("  ✗  Worker name cannot be empty.\n")
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
    Load credentials from environment variables.
    Returns (api_keys, cf_creds, allowed_keys_by_adapter, key_adapters_by_key_id, token_ttl_days) in the same shape as
    run_interactive_wizard().

    api_keys: {key_id: (provider, target_host, auth_header, auth_prefix, raw_secret)}
    cf_creds: {"CF_API_TOKEN": ..., "CF_ACCOUNT_ID": ..., "CF_WORKER_NAME": ...}
    """
    missing: list[str] = []

    cf_creds: dict[str, str] = {}
    for var in ("CF_API_TOKEN", "CF_ACCOUNT_ID"):
        val = os.environ.get(var, "").strip()
        if not val:
            missing.append(var)
        else:
            cf_creds[var] = val

    # CF_WORKER_NAME may default
    cf_creds["CF_WORKER_NAME"] = (
        os.environ.get("CF_WORKER_NAME", "").strip() or "subumbra-proxy"
    )
    policy_index = _load_policy_index()

    api_keys: dict[str, tuple[str, str, str, str, str]] = {}
    key_adapters_by_key_id: dict[str, list[str]] = {}
    missing_key_ids: list[tuple[str, str]] = []
    custom_adapter_ids = _parse_adapter_ids(os.environ.get("ADAPTER_IDS", ""))
    declared_adapter_ids: set[str] = set(custom_adapter_ids)
    allowed_keys_by_adapter: dict[str, list[str]] = {
        "subumbra-proxy": [],
        "subumbra-ui": [],
    }
    for adapter_id in custom_adapter_ids:
        allowed_keys_by_adapter[adapter_id] = []
    for provider, env_var in KNOWN_PROVIDERS:
        base_key_id_var = _key_id_env_var_name(env_var)
        provider_entry = BUILTIN_PROVIDER_BY_ID[provider]
        for slot_idx in range(1, 10):
            slot_env_var = env_var if slot_idx == 1 else f"{env_var}_{slot_idx}"
            slot_key_id_var = base_key_id_var if slot_idx == 1 else f"{base_key_id_var}_{slot_idx}"
            slot_adapter_var = _adapter_binding_env_var_name(slot_env_var)
            val = os.environ.get(slot_env_var, "").strip()
            if not val:
                continue
            key_id = os.environ.get(slot_key_id_var, "").strip()
            if not key_id:
                missing_key_ids.append((slot_env_var, slot_key_id_var))
                continue
            if slot_adapter_var not in os.environ:
                _automation_fail(
                    f"Automation mode: missing required adapter binding variable {slot_adapter_var} for {slot_env_var}\n"
                    "  Set it to a comma-separated adapter list or leave it blank for compatibility/simple mode."
                )
            if not KEY_ID_RE.fullmatch(key_id):
                _automation_fail(
                    f"Automation mode: invalid key_id {key_id!r} from {slot_key_id_var}\n"
                    f"  Must match ^[a-z0-9][a-z0-9_-]{{2,63}}$"
                )
            if key_id in api_keys:
                existing_provider = api_keys[key_id][0]
                _automation_fail(
                    "Automation mode: duplicate key_id requested\n"
                    f"  key_id      : {key_id}\n"
                    f"  provider    : {provider}\n"
                    f"  collides with provider {existing_provider}\n"
                    f"  env var     : {slot_key_id_var}"
                )
            selected_adapter_ids = _parse_key_adapter_ids(
                os.environ.get(slot_adapter_var, ""),
                source=f"Automation mode {slot_adapter_var}",
                declared_adapter_ids=declared_adapter_ids,
            )
            _resolve_policy_for_direct_secret(
                key_id,
                provider,
                policy_index,
                selected_adapter_ids or ["subumbra-proxy"],
            )
            api_keys[key_id] = (
                provider,
                provider_entry["target_host"],
                provider_entry["auth_header"],
                provider_entry["auth_prefix"],
                val,
            )
            _bind_key_to_adapters(
                key_id,
                selected_adapter_ids,
                key_adapters_by_key_id=key_adapters_by_key_id,
                allowed_keys_by_adapter=allowed_keys_by_adapter,
            )

    if missing_key_ids:
        preview = ", ".join(
            f"{slot_env_var} -> {slot_key_id_var}"
            for slot_env_var, slot_key_id_var in missing_key_ids[:3]
        )
        if len(missing_key_ids) > 3:
            preview += f", +{len(missing_key_ids) - 3} more"
        warn(
            "Automation mode: detected key values without companion key IDs: "
            + preview
        )

    for import_path, app_label in _collect_automation_imports():
        declared_adapter_ids.add(app_label)
        allowed_keys_by_adapter.setdefault(app_label, [])
        detected = _parse_env_file(import_path)
        if not detected:
            _automation_fail(
                f"Automation mode: no recognised provider keys found in import path {import_path}"
            )
        for env_var, provider_id, raw_value in detected:
            target_host = _resolve_target_host(provider_id, prompt_if_missing=False)
            provider_entry = BUILTIN_PROVIDER_BY_ID[provider_id]
            auth_header = provider_entry["auth_header"]
            auth_prefix = provider_entry["auth_prefix"]
            key_id = _next_generated_key_id(provider_id, app_label, api_keys, existing_keys)
            _require_import_policy(key_id, policy_index, import_path)
            api_keys[key_id] = (provider_id, target_host, auth_header, auth_prefix, raw_value)
            _bind_key_to_adapters(
                key_id,
                [app_label],
                key_adapters_by_key_id=key_adapters_by_key_id,
                allowed_keys_by_adapter=allowed_keys_by_adapter,
            )

    if not api_keys:
        missing.append(f"at least one of: {', '.join(ev for _, ev in KNOWN_PROVIDERS)}")

    if missing:
        _automation_fail(
            "Automation mode: missing required environment variables:\n"
            + "\n".join(f"    {v}" for v in missing)
            + "\n\n  Populate .env.bootstrap with all credentials, or run interactively:\n"
            + "    docker compose --profile bootstrap run --rm -it bootstrap"
        )

    if _parse_allowed_keys_csv(os.environ.get("PROXY_ALLOWED_KEYS", "")):
        _automation_fail(
            "Automation mode: PROXY_ALLOWED_KEYS is no longer an input contract.\n"
            "  Use per-key *_ADAPTERS bindings instead."
        )
    allowed_keys_by_adapter["subumbra-ui"] = _parse_allowed_keys_csv(os.environ.get("UI_ALLOWED_KEYS", ""))
    probe_allowed_keys = _parse_allowed_keys_csv(os.environ.get("PROBE_ALLOWED_KEYS", ""))
    if probe_allowed_keys:
        allowed_keys_by_adapter["subumbra-probe"] = probe_allowed_keys
    if allowed_keys_by_adapter["subumbra-ui"]:
        die("UI_ALLOWED_KEYS must remain empty")

    token_ttl_days = _parse_token_ttl_days(os.environ.get("TOKEN_TTL_DAYS", "90"))

    return api_keys, cf_creds, allowed_keys_by_adapter, key_adapters_by_key_id, token_ttl_days


# ─────────────────────────────────────────────────────────────────────────────
# Interactive wizard
# ─────────────────────────────────────────────────────────────────────────────

def run_interactive_wizard(
    existing_keys: dict,
) -> tuple[
    dict[str, tuple[str, str, str, str, str]],
    dict[str, str],
    dict[str, list[str]],
    dict[str, list[str]],
    int,
    list[str],
]:
    """
    Interactive terminal wizard. Requires a real TTY (run with -it).
    Returns (api_keys, cf_creds, allowed_keys_by_adapter, key_adapters_by_key_id, token_ttl_days, shred_paths):
      api_keys: {key_id: (provider, target_host, auth_header, auth_prefix, raw_secret)}
      cf_creds: {"CF_API_TOKEN": ..., "CF_ACCOUNT_ID": ..., "CF_WORKER_NAME": ...}
    """
    policy_index = _load_policy_index()

    # ── Screen 1: Cloudflare Credentials ─────────────────────────────────────
    print("\n" + "═" * 70)
    print("  Subumbra Bootstrap — Step 1 of 4: Cloudflare Credentials")
    print("  These values exist in RAM only for the duration of this session.")
    print("═" * 70 + "\n")
    print("  Minimum required CF API Token scopes:")
    print("    • Account > Workers Scripts > Edit")
    print("    • Account > Workers KV Storage > Edit\n")

    while True:
        cf_token = getpass.getpass("  Cloudflare API Token (hidden): ").strip()
        if cf_token:
            break
        print("  ✗  API Token cannot be empty. Please try again.\n")

    while True:
        cf_account_id = getpass.getpass("  Cloudflare Account ID (hidden): ").strip()
        if cf_account_id:
            break
        print("  ✗  Account ID cannot be empty. Please try again.\n")

    _worker_default = os.environ.get("CF_WORKER_NAME", "subumbra-proxy")
    cf_worker_name = input(
        f"  CF Worker name (press Enter for '{_worker_default}'): "
    ).strip() or _worker_default

    cf_creds: dict[str, str] = {
        "CF_API_TOKEN":   cf_token,
        "CF_ACCOUNT_ID":  cf_account_id,
        "CF_WORKER_NAME": cf_worker_name,
    }

    declared_adapter_ids = _prompt_declared_adapter_ids()
    allowed_keys_by_adapter: dict[str, list[str]] = {
        "subumbra-proxy": [],
        "subumbra-ui": [],
    }
    key_adapters_by_key_id: dict[str, list[str]] = {}
    for adapter_id in declared_adapter_ids:
        allowed_keys_by_adapter.setdefault(adapter_id, [])

    # ── Screen 2: Provider API Keys ───────────────────────────────────────────
    api_keys: dict[str, tuple[str, str, str, str, str]] = {}
    shred_paths: list[str] = []
    n_known = len(KNOWN_PROVIDERS)
    prompted_policy_key_ids: set[str] = set()

    for key_id, policy in sorted(policy_index.items()):
        if policy.get("source") != "env":
            continue
        target_host = policy.get("target", {}).get("host", "")
        provider = next(
            (provider_id for provider_id, host in PROVIDER_HOSTS.items() if host == target_host),
            None,
        )
        if provider is None:
            die(
                f"Policy-backed interactive env prompt requires a built-in target.host; "
                f"no built-in provider matches {target_host!r} for key_id {key_id}"
            )
        policy_adapters = _policy_adapter_ids(policy)
        for adapter_id in policy_adapters:
            if adapter_id not in BUILTIN_ADAPTER_IDS:
                if adapter_id not in declared_adapter_ids:
                    declared_adapter_ids.append(adapter_id)
                    info(f"Registered adapter {adapter_id} from SUBUMBRA_POLICY_PATH for {key_id}")
                allowed_keys_by_adapter.setdefault(adapter_id, [])
        provider_entry = BUILTIN_PROVIDER_BY_ID[provider]
        print("\n" + "─" * 70)
        print(f"  Policy-backed secret required for {key_id}")
        print("  This key is declared in SUBUMBRA_POLICY_PATH with source=env.")
        print("─" * 70)
        while True:
            api_key_1 = getpass.getpass(f"  API Key for {key_id} (hidden, Enter to skip for now): ").strip()
            if not api_key_1:
                break
            api_key_2 = getpass.getpass("  Confirm API Key (hidden): ").strip()
            if api_key_1 != api_key_2:
                print("  ✗  Keys do not match. Please try again.\n")
                continue
            api_keys[key_id] = (
                provider,
                provider_entry["target_host"],
                provider_entry["auth_header"],
                provider_entry["auth_prefix"],
                api_key_1,
            )
            _bind_key_to_adapters(
                key_id,
                [] if policy_adapters == ["subumbra-proxy"] else policy_adapters,
                key_adapters_by_key_id=key_adapters_by_key_id,
                allowed_keys_by_adapter=allowed_keys_by_adapter,
            )
            prompted_policy_key_ids.add(key_id)
            ok(f"{provider:12s}  →  {key_id}  →  {_binding_label(key_adapters_by_key_id[key_id])}  (key hidden)")
            break

    while True:
        print("\n" + "═" * 70)
        print("  Subumbra Bootstrap — Step 3 of 4: Provider API Keys")
        print("  Add provider API keys. Multiple keys per provider are supported.\n")
        print("  Press Enter with no selection to finish adding keys.\n")
        print("  Known providers:")
        for i, (provider, _) in enumerate(KNOWN_PROVIDERS, 1):
            print(f"    {i}. {provider}")
        print(f"    {n_known + 1}. Custom provider\n")

        if api_keys:
            print("  Added so far:")
            for kid, (prov, _, _, _, _) in api_keys.items():
                tail = kid.rsplit("_", 1)
                if len(tail) == 2 and tail[1].isdigit() and kid.startswith(prov + "_"):
                    app = kid[len(prov) + 1:].rsplit("_", 1)[0]
                    print(f"    {kid}  ({prov} -> {app})")
                else:
                    print(f"    {kid}  ({prov})")
            print()

        choice = input(f"  Select provider (1-{n_known + 1}, or Enter to finish): ").strip()

        if not choice:
            if not api_keys:
                print("\n  ✗  At least one API key is required.\n")
                continue
            break

        # Parse numeric choice
        try:
            choice_num = int(choice)
            if not (1 <= choice_num <= n_known + 1):
                raise ValueError()
        except ValueError:
            print(f"\n  ✗  Enter a number between 1 and {n_known + 1}.\n")
            continue

        if choice_num <= n_known:
            provider = KNOWN_PROVIDERS[choice_num - 1][0]
        else:
            # Custom provider
            while True:
                provider = input("  Custom provider name (lowercase letters/numbers): ").strip().lower()
                if provider and re.match(r'^[a-z][a-z0-9_-]*$', provider):
                    break
                print("  ✗  Provider name must start with a letter and contain only lowercase alphanumeric, _ or -.\n")

        target_host = _resolve_target_host(provider, prompt_if_missing=(choice_num > n_known))
        if choice_num <= n_known:
            provider_entry = BUILTIN_PROVIDER_BY_ID[provider]
            auth_header = provider_entry["auth_header"]
            auth_prefix = provider_entry["auth_prefix"]
        else:
            while True:
                auth_header = input("  Auth header name (e.g. authorization, x-api-key): ").strip()
                if auth_header:
                    break
                print("  ✗  Auth header cannot be empty.\n")
            auth_prefix = input("  Auth prefix (e.g. Bearer , leave blank for none): ")
            _upsert_custom_provider_registry_entry(provider, target_host, auth_header, auth_prefix)

        app_label = None
        key_id = ""
        if choice_num <= n_known:
            app_label = _prompt_app_label("  Key label for key_id generation (e.g. litellm, shared): ")

        # Prompt for API key value (twice to confirm)
        key_prompt_label = provider if choice_num <= n_known else key_id
        while True:
            api_key_1 = getpass.getpass(f"  API Key for {key_prompt_label} (hidden): ").strip()
            if not api_key_1:
                print("  ✗  API Key cannot be empty.\n")
                continue
            api_key_2 = getpass.getpass(f"  Confirm API Key (hidden): ").strip()
            if api_key_1 != api_key_2:
                print("  ✗  Keys do not match. Please try again.\n")
                continue
            break

        if choice_num <= n_known:
            duplicate_key_id = _find_duplicate_secret_key_id(api_keys, provider, api_key_1)
            if duplicate_key_id is not None:
                create_new = _prompt_duplicate_secret_action(provider, duplicate_key_id)
                if not create_new:
                    ok(f"{provider:12s}  →  reusing {duplicate_key_id}  (key hidden)")
                    continue
            key_id = _next_generated_key_id(provider, app_label, api_keys, existing_keys)
            info(f"Generated key_id: {key_id}")
        else:
            default_key_id = _default_key_id(provider)
            while True:
                key_id_input = input(f"  Key ID [{default_key_id}]: ").strip()
                key_id = key_id_input or default_key_id

                if not KEY_ID_RE.match(key_id):
                    print(f"  ✗  Invalid key_id. Must match ^[a-z0-9][a-z0-9_-]{{2,63}}$\n")
                    continue
                if key_id in api_keys:
                    print(f"  ✗  key_id '{key_id}' already added in this session. Choose a different name.\n")
                    continue
                if key_id in existing_keys:
                    ex_provider = existing_keys[key_id].get("provider", "unknown")
                    if ex_provider != provider:
                        print(f"\n  ⚠  WARNING: key_id '{key_id}' already exists in keys.json")
                        print(f"     under provider '{ex_provider}', not '{provider}'.")
                        confirm = input("     Overwrite? [y/N]: ").strip().lower()
                        if confirm != "y":
                            print("  Cancelled. Choose a different key_id.\n")
                            continue
                break
            explicit_policy = policy_index.get(key_id)
            if explicit_policy is not None and explicit_policy.get("source") != "env":
                die(f"Policy for key_id {key_id} must use source='env' for direct secret entry")
            if explicit_policy is None:
                die(f"Refusing policy-less custom secret for key_id {key_id}")

        selected_adapter_ids = _prompt_key_adapter_ids(key_id, declared_adapter_ids)
        effective_adapters = selected_adapter_ids or ["subumbra-proxy"]
        if choice_num <= n_known:
            _resolve_policy_for_direct_secret(key_id, provider, policy_index, effective_adapters)
        api_keys[key_id] = (provider, target_host, auth_header, auth_prefix, api_key_1)
        _bind_key_to_adapters(
            key_id,
            selected_adapter_ids,
            key_adapters_by_key_id=key_adapters_by_key_id,
            allowed_keys_by_adapter=allowed_keys_by_adapter,
        )
        ok(f"{provider:12s}  →  {key_id}  →  {_binding_label(key_adapters_by_key_id[key_id])}  (key hidden)")

    missing_policy_key_ids = sorted(
        key_id
        for key_id, policy in policy_index.items()
        if policy.get("source") == "env" and key_id not in api_keys and key_id not in prompted_policy_key_ids
    )
    if missing_policy_key_ids:
        die(
            "Missing required policy-backed env secret(s): " + ", ".join(missing_policy_key_ids)
        )

    print("\n" + "═" * 70)
    print("  Subumbra Bootstrap — Step 4 of 4: Probe And Token Settings")
    print("═" * 70)
    enable_probe = input(
        "  Enable subumbra-probe optional diagnostic provisioning? [y/N]: "
    ).strip().lower()
    if enable_probe == "y":
        print("\n  subumbra-probe is an optional diagnostic container for verifying your")
        print("  deployment. Its scope is usually the same as or narrower than subumbra-proxy.")
        proxy_scope = allowed_keys_by_adapter["subumbra-proxy"]
        mirror = input(
            f"  Mirror subumbra-proxy scope ({len(proxy_scope)} key(s))? [Y/n]: "
        ).strip().lower()
        if mirror != "n":
            allowed_keys_by_adapter["subumbra-probe"] = list(proxy_scope)
            ok("subumbra-probe scope mirrors subumbra-proxy.")
        else:
            available_key_ids = sorted(api_keys.keys())
            allowed_keys_by_adapter["subumbra-probe"] = _prompt_allowed_keys(
                "subumbra-probe", available_key_ids
            )
    else:
        info("Probe provisioning skipped — optional diagnostic container not provisioned.")

    while True:
        raw_ttl = input("\n  Token TTL in days [90]: ").strip()
        try:
            token_ttl_days = _parse_token_ttl_days(raw_ttl or "90")
        except SystemExit:
            print("  ✗  Token TTL must be a positive integer.\n")
            continue
        break

    return api_keys, cf_creds, allowed_keys_by_adapter, key_adapters_by_key_id, token_ttl_days, shred_paths


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


def call_internal_rotate(worker_url: str, setup_token: str, rotate_payload: dict[str, Any]) -> dict[str, Any]:
    last_http_error: urllib.error.HTTPError | None = None
    body = json.dumps(rotate_payload, separators=(",", ":")).encode("utf-8")
    _MAX_ROTATE_ATTEMPTS = 24
    for attempt in range(1, _MAX_ROTATE_ATTEMPTS + 1):
        req = urllib.request.Request(
            f"{worker_url.rstrip('/')}/internal/rotate",
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
            if exc.code in (401, 403) and attempt < _MAX_ROTATE_ATTEMPTS:
                info(
                    "Cloudflare rotate token not visible yet; "
                    f"retrying /internal/rotate ({attempt}/{_MAX_ROTATE_ATTEMPTS})"
                )
                time.sleep(5)
                continue
            body_text = exc.read().decode("utf-8", errors="replace")
            die(
                f"Cloudflare internal rotate failed: HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            )
        except Exception as exc:
            die(f"Cloudflare internal rotate failed: {exc}")
    else:
        if last_http_error is not None:
            body_text = last_http_error.read().decode("utf-8", errors="replace")
            die(
                f"Cloudflare internal rotate failed after retry window: HTTP {last_http_error.code}\n"
                f"--- response body ---\n{body_text}"
            )
        die("Cloudflare internal rotate failed after retry window")

    ciphertext = payload.get("ciphertext")
    enc_version = payload.get("enc_version")
    if not isinstance(ciphertext, str) or not ciphertext:
        die("Cloudflare internal rotate returned invalid ciphertext")
    if enc_version != 3:
        die("Cloudflare internal rotate returned invalid enc_version")
    return payload


def _publish_structured_kv(
    cf_creds: dict[str, str],
    keys_payload: dict[str, dict[str, Any]],
    policy_by_key_id: dict[str, dict[str, Any]],
) -> None:
    namespace_id = _create_or_reuse_kv_namespace(cf_creds)
    env = _wrangler_env(cf_creds)
    entries = _build_structured_kv_entries(keys_payload, policy_by_key_id)
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

        for sample in (sample_key_entry, sample_policy_entry):
            _run(
                [
                    "wrangler", "kv", "key", "get",
                    sample["key"],
                    "--namespace-id", namespace_id,
                    "--remote",
                ],
                cwd=work_dir,
                env=env,
            )

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
      8. wrangler secret put SUBUMBRA_SETUP_TOKEN
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

    policy_index = _load_policy_index()
    persisted_adapters = _load_persisted_key_adapter_bindings()
    policy_by_key_id: dict[str, dict[str, Any]] = {}
    for key_id, record in keys_payload.items():
        provider = record.get("provider", "unknown")
        target_host = record.get("target_host")
        if not isinstance(target_host, str) or not target_host:
            die(f"keys.json record {key_id!r} missing target_host")
        if key_id in policy_index:
            allowed_adapters = _policy_adapter_ids(policy_index[key_id])
        else:
            allowed_adapters = persisted_adapters.get(key_id)
            if not allowed_adapters:
                die(
                    f"No persisted adapter binding found for key_id {key_id!r}.\n"
                    "  Re-run full bootstrap from this repo checkout or add an explicit SUBUMBRA_POLICY_PATH entry."
                )
        policy_by_key_id[key_id] = _resolve_policy_for_key(
            key_id,
            provider,
            target_host,
            policy_index,
            allowed_adapters,
        )

    step("Publishing structured KV entries to Cloudflare KV")
    try:
        _publish_structured_kv(cf_creds, keys_payload, policy_by_key_id)
    except SystemExit:
        die("structured publish aborted before registry_version update")
    ok("Structured KV publication complete")


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
        new_key = getpass.getpass(f"\n  New API key for {key_id} (hidden): ").strip()
        if not new_key:
            print("  ✗  API key cannot be empty.")
            continue
        confirm_key = getpass.getpass(f"  Confirm new API key (hidden): ").strip()
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

    record = {
        "key_id":      key_id,
        "enc_version": 3,
        "pub_key_fp":  fp,
        "wrapped_dek": wrapped,
        "ciphertext":  ciphertext,
        "provider":    provider,
        "target_host": target_host,
        "policy_id":   existing_policy_id,
        "policy_hash": existing_policy_hash,
        "vault_instance": vault_instance,
        "created_at":  now_iso,
        "label":       existing_record.get("label", key_id),
    }
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


def run_rotate_policy() -> None:
    print(BANNER, flush=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not KEYS_FILE.exists():
        die("keys.json not found — run a full bootstrap first.")

    try:
        existing_keys = json.loads(KEYS_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        die(f"Cannot read keys.json: {exc}")
    if not existing_keys:
        die("keys.json is empty — nothing to rotate.")

    policy_index = _load_policy_index()
    persisted_adapters = _load_persisted_key_adapter_bindings()

    records_to_rotate: list[tuple[str, dict[str, Any], dict[str, Any], str]] = []
    for key_id, record in existing_keys.items():
        provider = record.get("provider", "unknown")
        target_host = record.get("target_host")
        if not isinstance(target_host, str) or not target_host:
            die(f"keys.json record {key_id!r} missing target_host")
        if key_id in policy_index:
            allowed_adapters = _policy_adapter_ids(policy_index[key_id])
        else:
            allowed_adapters = persisted_adapters.get(key_id)
            if not allowed_adapters:
                die(
                    f"No persisted adapter binding found for key_id {key_id!r}.\n"
                    "  Re-run full bootstrap from this repo checkout or add an explicit SUBUMBRA_POLICY_PATH entry."
                )
        policy = _resolve_policy_for_key(key_id, provider, target_host, policy_index, allowed_adapters)
        desired_policy_hash = compute_policy_hash(policy)
        if (
            record.get("enc_version") != 3
            or record.get("policy_hash") != desired_policy_hash
            or record.get("policy_id") != policy["policy_id"]
        ):
            records_to_rotate.append((key_id, record, policy, desired_policy_hash))

    if not records_to_rotate:
        ok("No policy-bound rotation required — all records already match current policy_hash")
        return

    v2_candidates = [
        key_id
        for key_id, record, _policy, _desired_policy_hash in records_to_rotate
        if record.get("enc_version") != 3
    ]
    if v2_candidates:
        die(
            "--rotate-policy only supports existing V3 records. "
            f"V2 migration requires full bootstrap: {', '.join(sorted(v2_candidates))}"
        )

    cf_creds = _get_push_registry_cf_creds()
    worker_url = os.environ.get("CF_WORKER_URL", "").strip() or _build_worker_url(cf_creds["CF_WORKER_NAME"])

    step("Pushing transient SUBUMBRA_SETUP_TOKEN to CF Secrets for policy rotation")
    rotate_setup_token = secrets.token_urlsafe(48)
    _put_worker_secret(cf_creds, "SUBUMBRA_SETUP_TOKEN", rotate_setup_token)

    try:
        for key_id, record, policy, desired_policy_hash in records_to_rotate:
            rotate_payload = {
                "key_id": key_id,
                "enc_version": record.get("enc_version", 1),
                "ciphertext": record["ciphertext"],
                "wrapped_dek": record["wrapped_dek"],
                "pub_key_fp": record["pub_key_fp"],
                "vault_instance": record.get("vault_instance", "vault"),
                "new_policy_hash": desired_policy_hash,
            }
            if record.get("enc_version") == 3 and isinstance(record.get("policy_hash"), str):
                rotate_payload["policy_hash"] = record["policy_hash"]
            rotated = call_internal_rotate(worker_url, rotate_setup_token, rotate_payload)
            record["ciphertext"] = rotated["ciphertext"]
            record["enc_version"] = 3
            record["policy_id"] = policy["policy_id"]
            record["policy_hash"] = desired_policy_hash
            ok(f"rotate-policy complete for {key_id}")
    finally:
        step("Deleting transient SUBUMBRA_SETUP_TOKEN from CF Secrets")
        _delete_worker_secret(cf_creds, "SUBUMBRA_SETUP_TOKEN")

    tmp_keys = KEYS_FILE.with_suffix(".json.tmp")
    try:
        fd = os.open(str(tmp_keys), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w") as fh:
            json.dump(existing_keys, fh, indent=2)
            fh.write("\n")
        os.replace(str(tmp_keys), str(KEYS_FILE))
    except OSError as exc:
        die(f"Failed to write keys.json: {exc}")
    ok("Updated keys.json with rotated V3 ciphertext records")

    policy_by_key_id = {
        key_id: _resolve_policy_for_key(
            key_id,
            record.get("provider", "unknown"),
            record["target_host"],
            policy_index,
            _policy_adapter_ids(policy_index[key_id]) if key_id in policy_index else persisted_adapters[key_id],
        )
        for key_id, record in existing_keys.items()
    }
    step("Publishing structured KV entries after policy rotation")
    try:
        _publish_structured_kv(cf_creds, existing_keys, policy_by_key_id)
    except SystemExit:
        die("structured publish aborted before registry_version update")
    ok("Structured KV publication complete")


def run_provision_key(target_key_id: str) -> None:
    print(BANNER, flush=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not CHECKPOINT_FILE.exists():
        die("bootstrap-checkpoint.json not found — nothing to provision")

    try:
        existing_keys = json.loads(KEYS_FILE.read_text()) if KEYS_FILE.exists() else {}
    except (json.JSONDecodeError, OSError) as exc:
        die(f"Cannot read keys.json: {exc}")
    if target_key_id in existing_keys:
        die(f"{target_key_id!r} already exists in keys.json — no targeted repair needed")

    if _choose_bootstrap_mode():
        die("--provision requires automation/bootstrap env inputs; interactive repair is not supported")

    try:
        api_keys, cf_creds, _allowed_keys_by_adapter, key_adapters_by_key_id, _token_ttl_days = _load_env_fallback(existing_keys)
    except AutomationInputError as exc:
        die(str(exc))

    if target_key_id not in api_keys:
        die(f"{target_key_id!r} is not present in current bootstrap inputs")

    policy_index = _load_policy_index()
    checkpoint = _load_bootstrap_checkpoint()
    worker_url = str(checkpoint.get("worker_url", "")).strip()
    setup_token = str(checkpoint.get("setup_token", "")).strip()
    if not worker_url or not setup_token:
        die("bootstrap checkpoint is missing worker_url or setup_token")
    checkpoint_host_env_updates = checkpoint.get("host_env_updates", {})
    if checkpoint_host_env_updates and not isinstance(checkpoint_host_env_updates, dict):
        die("bootstrap checkpoint host_env_updates section is malformed")

    unique_key_flags = _load_unique_key_flags(list(api_keys.keys()))
    provider, target_host, _auth_header, _auth_prefix, raw = api_keys[target_key_id]
    vault_instance = _vault_instance_for_key(target_key_id, unique_key_flags)
    checkpoint_entry = checkpoint.get("keys", {}).get(target_key_id)
    if not isinstance(checkpoint_entry, dict) or checkpoint_entry.get("vault_instance") != vault_instance:
        checkpoint_entry = _checkpoint_entry_by_vault_instance(checkpoint, vault_instance)

    if checkpoint_entry is None:
        step(f"Provisioning missing vault for {target_key_id}")
        try:
            public_key_pem, pub_key_fp, _created_at = call_setup_keygen(worker_url, setup_token, vault_instance)
        except BootstrapFlowError as exc:
            die(str(exc))
        _store_checkpoint_entry(
            checkpoint,
            target_key_id,
            vault_instance=vault_instance,
            public_key_pem=public_key_pem,
            pub_key_fp=pub_key_fp,
        )
        checkpoint_entry = checkpoint["keys"][target_key_id]
    else:
        public_key_pem = checkpoint_entry["public_key_pem"]
        pub_key_fp = checkpoint_entry["pub_key_fp"]
        _store_checkpoint_entry(
            checkpoint,
            target_key_id,
            vault_instance=vault_instance,
            public_key_pem=public_key_pem,
            pub_key_fp=pub_key_fp,
        )

    public_key_file = _public_key_file_for_key(target_key_id, vault_instance)
    _write_public_key_file(public_key_file, public_key_pem)
    pub_key = _load_public_key_from_pem(public_key_pem)
    computed_fp = public_key_fingerprint(pub_key)
    if computed_fp != pub_key_fp:
        die(
            "Bootstrap checkpoint public key fingerprint mismatch\n"
            f"  stored:   {pub_key_fp}\n"
            f"  computed: {computed_fp}"
        )

    policy = _resolve_policy_for_key(
        target_key_id,
        provider,
        target_host,
        policy_index,
        key_adapters_by_key_id[target_key_id],
    )
    policy_hash = compute_policy_hash(policy)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dek = os.urandom(32)
    ciphertext = encrypt_api_key_v3(dek, raw, target_key_id, policy_hash)
    wrapped_dek = wrap_dek(pub_key, dek)
    del dek

    existing_keys[target_key_id] = {
        "key_id": target_key_id,
        "enc_version": 3,
        "pub_key_fp": pub_key_fp,
        "wrapped_dek": wrapped_dek,
        "ciphertext": ciphertext,
        "provider": provider,
        "target_host": target_host,
        "policy_id": policy["policy_id"],
        "policy_hash": policy_hash,
        "vault_instance": vault_instance,
        "created_at": now_iso,
        "label": target_key_id,
    }

    step(f"Updating {target_key_id} in keys.json")
    _write_keys_payload(existing_keys)
    ok(f"Added repaired record for {target_key_id}")

    policy_by_key_id = {}
    for key_id, record in existing_keys.items():
        policy_by_key_id[key_id] = _resolve_policy_for_key(
            key_id,
            record.get("provider", "unknown"),
            record["target_host"],
            policy_index,
            key_adapters_by_key_id[key_id],
        )

    step("Publishing structured KV entries after targeted repair")
    try:
        _publish_structured_kv(cf_creds, existing_keys, policy_by_key_id)
    except SystemExit:
        die("structured publish aborted before registry_version update")
    ok("Structured KV publication complete")

    if checkpoint_host_env_updates:
        checkpoint_host_env_updates["SUBUMBRA_SETUP_TOKEN"] = setup_token
        checkpoint["host_env_updates"] = checkpoint_host_env_updates
        _write_bootstrap_checkpoint(checkpoint)
        _sync_host_env_file(checkpoint_host_env_updates)

    if len(existing_keys) >= len(api_keys):
        step("Deleting transient SUBUMBRA_SETUP_TOKEN from CF Secrets")
        _delete_worker_secret(cf_creds, "SUBUMBRA_SETUP_TOKEN")
        if checkpoint_host_env_updates:
            checkpoint_host_env_updates["SUBUMBRA_SETUP_TOKEN"] = ""
            checkpoint["host_env_updates"] = checkpoint_host_env_updates
            _sync_host_env_file(checkpoint_host_env_updates)
        _delete_bootstrap_checkpoint()
        ok("All requested keys are present — checkpoint cleared")
    else:
        warn("Other missing keys remain — bootstrap checkpoint preserved")


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
        try:
            api_keys, cf_creds, allowed_keys_by_adapter, key_adapters_by_key_id, token_ttl_days, shred_paths = run_interactive_wizard(existing_keys)
        except KeyboardInterrupt:
            print("\n\nAborted. No changes written.", file=sys.stderr)
            sys.exit(0)
    if not use_wizard:
        shred_paths = []

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
        for kid, (provider, _target_host, _auth_header, _auth_prefix, _raw) in api_keys.items():
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

    policy_index = _load_policy_index()
    policy_by_key_id: dict[str, dict[str, Any]] = {}
    for key_id, (provider, target_host, _auth_header, _auth_prefix, _raw) in api_keys.items():
        policy_by_key_id[key_id] = _resolve_policy_for_key(
            key_id,
            provider,
            target_host,
            policy_index,
            key_adapters_by_key_id[key_id],
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
    setup_token = secrets.token_urlsafe(48)
    ok("SUBUMBRA_SETUP_TOKEN generated")
    adapter_registry = _build_adapter_registry(
        adapter_tokens,
        allowed_keys_by_adapter,
        token_ttl_days=token_ttl_days,
    )
    unique_key_flags = _load_unique_key_flags(list(api_keys.keys()))

    # ── Step 4: Phase 1 — deploy worker + push secrets ───────────────────
    # CRITICAL ORDER: remote secrets are pushed BEFORE keys.json is written.
    # If the deploy fails here, keys.json still holds the old blobs that match
    # the old key pair — the system remains consistent.
    bootstrapped_providers = {v[0] for v in api_keys.values()}
    worker_url = deploy_worker(
        cf_creds,
        adapter_tokens, subumbra_hmac_key,
        setup_token,
        provider_id_filter=bootstrapped_providers,
    )
    ok(f"Worker URL: {worker_url}")
    checkpoint = _load_bootstrap_checkpoint()
    checkpoint["worker_url"] = worker_url
    checkpoint["setup_token"] = setup_token
    host_env_updates = _build_host_env_updates(
        adapter_registry=adapter_registry,
        allowed_keys_by_adapter=allowed_keys_by_adapter,
        adapter_tokens=adapter_tokens,
        subumbra_hmac_key=subumbra_hmac_key,
        worker_url=worker_url,
        setup_token=setup_token,
    )
    checkpoint["host_env_updates"] = dict(host_env_updates)
    _write_bootstrap_checkpoint(checkpoint)
    _sync_host_env_file(host_env_updates)

    # ── Step 5: Phase 2 — provision per-key vault public keys ────────────
    step("Provisioning per-key vault public keys")
    phase2_material: dict[str, dict[str, str]] = {}
    phase2_failures: list[tuple[str, str]] = []
    public_keys_by_vault_instance: dict[str, Any] = {}
    forced_failure_key = os.environ.get("SUBUMBRA_FORCE_PROVISION_FAILURE_KEY", "").strip()

    for key_id, (provider, _target_host, _auth_header, _auth_prefix, _raw) in api_keys.items():
        vault_instance = _vault_instance_for_key(key_id, unique_key_flags)
        checkpoint_entry = checkpoint.get("keys", {}).get(key_id)
        if not isinstance(checkpoint_entry, dict) or checkpoint_entry.get("vault_instance") != vault_instance:
            checkpoint_entry = _checkpoint_entry_by_vault_instance(checkpoint, vault_instance)

        if checkpoint_entry is None:
            try:
                public_key_pem, pub_key_fp, _created_at = call_setup_keygen(worker_url, setup_token, vault_instance)
            except BootstrapFlowError as exc:
                phase2_failures.append((key_id, str(exc)))
                warn(f"{key_id}: vault provisioning failed")
                continue
            _store_checkpoint_entry(
                checkpoint,
                key_id,
                vault_instance=vault_instance,
                public_key_pem=public_key_pem,
                pub_key_fp=pub_key_fp,
            )
        else:
            public_key_pem = checkpoint_entry["public_key_pem"]
            pub_key_fp = checkpoint_entry["pub_key_fp"]
            _store_checkpoint_entry(
                checkpoint,
                key_id,
                vault_instance=vault_instance,
                public_key_pem=public_key_pem,
                pub_key_fp=pub_key_fp,
            )

        public_key_file = _public_key_file_for_key(key_id, vault_instance)
        _write_public_key_file(public_key_file, public_key_pem)
        pub_key = _load_public_key_from_pem(public_key_pem)
        computed_fp = public_key_fingerprint(pub_key)
        if computed_fp != pub_key_fp:
            die(
                "Cloudflare setup keygen returned inconsistent fingerprint\n"
                f"  returned: {pub_key_fp}\n"
                f"  computed: {computed_fp}"
            )
        info(f"{key_id}: vault_instance={vault_instance} fingerprint={computed_fp}")
        public_keys_by_vault_instance[vault_instance] = pub_key
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

    for key_id, (provider, target_host, _auth_header, _auth_prefix, raw) in api_keys.items():
        if key_id not in phase2_material:
            continue
        phase2_entry = phase2_material[key_id]
        vault_instance = phase2_entry["vault_instance"]
        pub_key = public_keys_by_vault_instance[vault_instance]
        pub_key_fp = phase2_entry["pub_key_fp"]
        policy = policy_by_key_id[key_id]
        policy_hash = compute_policy_hash(policy)
        dek = os.urandom(32)
        ciphertext = encrypt_api_key_v3(dek, raw, key_id, policy_hash)
        wrapped_dek = wrap_dek(pub_key, dek)
        keys_payload[key_id] = {
            "key_id": key_id,
            "enc_version": 3,
            "pub_key_fp": pub_key_fp,
            "wrapped_dek": wrapped_dek,
            "ciphertext": ciphertext,
            "provider": provider,
            "target_host": target_host,
            "policy_id": policy["policy_id"],
            "policy_hash": policy_hash,
            "vault_instance": vault_instance,
            "created_at": now_iso,
            "label": key_id,
        }
        del dek
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
        _publish_structured_kv(
            cf_creds,
            keys_payload,
            {key_id: policy_by_key_id[key_id] for key_id in keys_payload},
        )
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
        worker_url=worker_url,
        primary_pub_key_fp=primary_pub_key_fp,
    )
    _write_runtime_env_file(runtime_env_lines)
    _sync_host_env_file(host_env_updates)

    if not phase2_failures:
        step("Deleting transient SUBUMBRA_SETUP_TOKEN from CF Secrets")
        _delete_worker_secret(cf_creds, "SUBUMBRA_SETUP_TOKEN")
        _delete_bootstrap_checkpoint()
        ok("Bootstrap checkpoint cleared")

    # ── Step 10: zero sensitive memory ───────────────────────────────────
    step("Clearing sensitive values from memory")
    for adapter_id in list(adapter_tokens):
        adapter_tokens[adapter_id] = "\x00" * len(adapter_tokens[adapter_id])
    del adapter_tokens
    setup_token = "\x00" * len(setup_token)
    del setup_token
    # Zero raw API key values (tuples are immutable but we can overwrite the dict)
    for k in list(api_keys):
        provider, target_host, auth_header, auth_prefix, raw = api_keys[k]
        api_keys[k] = (provider, target_host, auth_header, auth_prefix, "\x00" * len(raw))
    del api_keys
    del allowed_keys_by_adapter
    del cf_creds
    gc.collect()
    ok("Sensitive memory cleared (best-effort)")

    if phase2_failures:
        print("\n" + "─" * 70)
        print("  Bootstrap completed with partial success")
        print("  Successful records are live; failed keys were skipped:")
        for key_id, message in phase2_failures:
            print(f"    • {key_id}: {message.splitlines()[0]}")
        print("\n  Retry each failed key with:")
        for key_id, _message in phase2_failures:
            print(f"    ./bootstrap.sh --provision {key_id}")
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
    Per-key rotate: existing V3 records only via docker compose --profile bootstrap run --rm -it bootstrap --rotate
    Policy rotate: V3 policy refresh only via docker compose --profile bootstrap run --rm -it bootstrap --rotate-policy
    Targeted repair: ./bootstrap.sh --provision <key_id>
    V2 migration:  full bootstrap required
"""))


if __name__ == "__main__":
    selected_modes = sum(flag in sys.argv for flag in ("--push-registry", "--rotate", "--rotate-policy", "--provision"))
    if selected_modes > 1:
        die("--push-registry, --rotate, --rotate-policy, and --provision are mutually exclusive")
    if "--push-registry" in sys.argv:
        run_push_registry()
    elif "--provision" in sys.argv:
        try:
            target_key_id = sys.argv[sys.argv.index("--provision") + 1]
        except IndexError:
            die("--provision requires <key_id>")
        run_provision_key(target_key_id)
    elif "--rotate-policy" in sys.argv:
        run_rotate_policy()
    elif "--rotate" in sys.argv:
        run_rotate_wizard()
    else:
        main()
