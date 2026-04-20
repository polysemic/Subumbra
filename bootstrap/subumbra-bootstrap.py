#!/usr/bin/env python3
"""
subumbra-bootstrap — V2 Asymmetric Envelope Encryption & Deployment.

Usage (interactive — primary):
    docker compose --profile bootstrap run --rm -it bootstrap

Usage (automation / CI — requires .env.bootstrap with all credentials):
    docker compose --profile bootstrap run --rm bootstrap

Single-key rotation (no Cloudflare interaction):
    docker compose --profile bootstrap run --rm -it bootstrap --rotate

What it does (full bootstrap, in order):
  1. Detects mode: interactive wizard (no TTY env vars) or env-var fallback (CI)
  2. Collects CF credentials + provider API keys (RAM only — never written to disk)
  3. Warns if keys.json already exists (rotation mode) and identifies any
     keys that will be removed because they are absent from this session
  4. Confirms with the operator before proceeding (interactive mode only)
  5. Generates an RSA-4096 key pair (RAM only)
  6. Writes public key to /app/data/public_key.pem (not sensitive)
  7. Generates NEW runtime auth tokens (per-adapter Subumbra tokens, SUBUMBRA_HMAC_KEY)
  8. Encrypts each API key: per-key DEK -> AES-256-GCM (AAD bound), DEK -> RSA-OAEP wrap
  9. Copies worker source to a temp dir and deploys via wrangler
 10. Pushes WORKER_PRIVATE_KEY + WORKER_KEY_FINGERPRINT + auth tokens to CF Secrets
 11. Deletes stale MASTER_DECRYPTION_KEY CF Secret (V1 cleanup)
 12. ONLY after remote deploy succeeds: atomically writes keys.json
 13. Writes runtime tokens to /app/data/runtime.env (mode 0600)
 14. Zeroes sensitive memory and exits

ROTATION NOTE (full bootstrap):
  Every run generates a new RSA key pair and new runtime tokens.  ALL keys
  that should remain accessible must be re-entered — any key omitted from
  the wizard (or from .env.bootstrap in CI mode) will be removed from
  keys.json and become permanently inaccessible under the new key pair.

  After bootstrap completes:
    ./post-bootstrap.sh
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
import urllib.error
import urllib.request
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

from cryptography.hazmat.primitives.asymmetric import rsa, padding
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
WORKER_SRC      = Path("/app/worker")
CUSTOM_PROVIDER_REGISTRY_FILE = DATA_DIR / "custom-providers.json"
KV_CONFIG_FILE = DATA_DIR / "kv-config.json"
PROVIDER_REGISTRY_KV_KEY = "subumbra_registry_v1"

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


def _load_provider_registry() -> list[dict]:
    """Load and validate the shared built-in provider registry."""
    reg_path = WORKER_SRC / "src" / "providers.json"
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

        for env_var, provider_id, raw_value in detected:
            target_host = _resolve_target_host(provider_id, prompt_if_missing=False)
            provider_entry = BUILTIN_PROVIDER_BY_ID[provider_id]
            auth_header = provider_entry["auth_header"]
            auth_prefix = provider_entry["auth_prefix"]

            default_key_id = _default_key_id(provider_id)
            while True:
                key_id_input = input(
                    f"\n  Key ID for {env_var} (provider={provider_id}) [{default_key_id}]: "
                ).strip()
                key_id = key_id_input or default_key_id

                if not KEY_ID_RE.match(key_id):
                    print(f"  ✗  Invalid key_id. Must match ^[a-z0-9][a-z0-9_-]{{2,63}}$")
                    continue
                if key_id in api_keys:
                    print(f"  ✗  key_id '{key_id}' already added. Choose a different name.")
                    continue
                if key_id in existing_keys:
                    ex_provider = existing_keys[key_id].get("provider", "unknown")
                    if ex_provider != provider_id:
                        print(f"\n  ⚠  WARNING: key_id '{key_id}' already exists under provider '{ex_provider}'.")
                        overwrite = input("     Overwrite? [y/N]: ").strip().lower()
                        if overwrite != "y":
                            print("  Cancelled. Choose a different key_id.")
                            continue
                break

            api_keys[key_id] = (provider_id, target_host, auth_header, auth_prefix, raw_value)
            ok(f"{provider_id:12s}  →  {key_id}  (from {env_var}, key hidden)")

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
        "subumbra-probe": {
            "token": adapter_tokens["subumbra-probe"],
            "allowed_keys": allowed_keys_by_adapter["subumbra-probe"],
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
    available_set = set(available_key_ids)
    while True:
        print(f"\n  {adapter_label} allowed key_ids (comma-separated, Enter for none)")
        print("    Available: " + ", ".join(available_key_ids))
        raw = input("    Allowed key_ids: ").strip()
        result = _parse_allowed_keys_csv(raw)
        invalid = [k for k in result if k not in available_set]
        if invalid:
            print(f"  ✗  Unknown key_id(s): {', '.join(sorted(invalid))}")
            print(f"     Valid options: {', '.join(available_key_ids)}\n")
            continue
        return result


# ─────────────────────────────────────────────────────────────────────────────
# V2 Crypto — RSA-4096-OAEP + AES-256-GCM with AAD
# ─────────────────────────────────────────────────────────────────────────────

def generate_keypair():
    """Generate RSA-4096 key pair. Returns (private_key, public_key)."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
    )
    return private_key, private_key.public_key()


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


def _load_env_fallback() -> tuple[dict[str, tuple[str, str, str, str, str]], dict[str, str], dict[str, list[str]], int]:
    """
    Load credentials from environment variables.
    Returns (api_keys, cf_creds, allowed_keys_by_adapter, token_ttl_days) in the same shape as
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

    api_keys: dict[str, tuple[str, str, str, str, str]] = {}
    for provider, env_var in KNOWN_PROVIDERS:
        val = os.environ.get(env_var, "").strip()
        if val:
            key_id, key_id_var = _resolve_env_key_id(provider, env_var)
            if key_id in api_keys:
                existing_provider = api_keys[key_id][0]
                die(
                    "Automation mode: duplicate key_id requested\n"
                    f"  key_id      : {key_id}\n"
                    f"  provider    : {provider}\n"
                    f"  collides with provider {existing_provider}\n"
                    f"  env var     : {key_id_var}"
                )
            provider_entry = BUILTIN_PROVIDER_BY_ID[provider]
            target_host = provider_entry["target_host"]
            api_keys[key_id] = (
                provider,
                target_host,
                provider_entry["auth_header"],
                provider_entry["auth_prefix"],
                val,
            )

    if not api_keys:
        missing.append(f"at least one of: {', '.join(ev for _, ev in KNOWN_PROVIDERS)}")

    if missing:
        die(
            "Automation mode: missing required environment variables:\n"
            + "\n".join(f"    {v}" for v in missing)
            + "\n\n  Populate .env.bootstrap with all credentials, or run interactively:\n"
            + "    docker compose --profile bootstrap run --rm -it bootstrap"
        )

    custom_adapter_ids = _parse_adapter_ids(os.environ.get("ADAPTER_IDS", ""))
    custom_scope_vars = _build_custom_adapter_scope_vars(custom_adapter_ids)
    allowed_keys_by_adapter = {
        adapter_id: _parse_allowed_keys_csv(os.environ.get(scope_var, ""))
        for adapter_id, scope_var in ADAPTER_SCOPE_VARS.items()
    }
    for adapter_id, scope_var in custom_scope_vars.items():
        allowed_keys_by_adapter[adapter_id] = _parse_allowed_keys_csv(os.environ.get(scope_var, ""))
    if allowed_keys_by_adapter["subumbra-ui"]:
        die("UI_ALLOWED_KEYS must remain empty")

    token_ttl_days = _parse_token_ttl_days(os.environ.get("TOKEN_TTL_DAYS", "90"))

    return api_keys, cf_creds, allowed_keys_by_adapter, token_ttl_days


# ─────────────────────────────────────────────────────────────────────────────
# Interactive wizard
# ─────────────────────────────────────────────────────────────────────────────

def run_interactive_wizard(
    existing_keys: dict,
) -> tuple[dict[str, tuple[str, str, str, str, str]], dict[str, str], dict[str, list[str]], int, list[str]]:
    """
    Interactive terminal wizard. Requires a real TTY (run with -it).
    Returns (api_keys, cf_creds, allowed_keys_by_adapter, token_ttl_days, shred_paths):
      api_keys: {key_id: (provider, target_host, auth_header, auth_prefix, raw_secret)}
      cf_creds: {"CF_API_TOKEN": ..., "CF_ACCOUNT_ID": ..., "CF_WORKER_NAME": ...}
    """

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
        cf_account_id = input("  Cloudflare Account ID: ").strip()
        if cf_account_id:
            break
        print("  ✗  Account ID cannot be empty. Please try again.\n")

    cf_worker_name = input("  CF Worker name [subumbra-proxy]: ").strip() or "subumbra-proxy"

    cf_creds: dict[str, str] = {
        "CF_API_TOKEN":   cf_token,
        "CF_ACCOUNT_ID":  cf_account_id,
        "CF_WORKER_NAME": cf_worker_name,
    }

    # ── Screen 2: Provider API Keys ───────────────────────────────────────────
    api_keys: dict[str, tuple[str, str, str, str, str]] = {}
    shred_paths: list[str] = []
    n_known = len(KNOWN_PROVIDERS)

    print("\n" + "═" * 70)
    print("  Subumbra Bootstrap — Step 2 of 4: Provider API Keys")
    print("  Add one key per provider. Press Enter when finished.\n")
    print("  Option: import provider keys from an existing .env file.")
    print("  Run bootstrap with: -v /opt/litellm:/host_litellm:ro")
    print("  then enter the in-container path (e.g. /host_litellm/.env)\n")
    do_import = input("  Import from .env file(s)? [y/N]: ").strip().lower()
    if do_import == "y":
        api_keys, shred_paths = _run_import_screen(api_keys, existing_keys)

    while True:
        print("\n" + "═" * 70)
        print("  Subumbra Bootstrap — Step 2 of 4: Provider API Keys")
        print("  Add one key per provider. Press Enter when finished.\n")
        print("  Known providers:")
        for i, (provider, _) in enumerate(KNOWN_PROVIDERS, 1):
            print(f"    {i}. {provider}")
        print(f"    {n_known + 1}. Custom provider\n")

        if api_keys:
            print("  Added so far: " + ", ".join(
                f"{kid} ({prov})" for kid, (prov, _, _, _, _) in api_keys.items()
            ) + "\n")

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

        # Prompt for key_id
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
            # Cross-provider collision check against existing keys.json
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

        # Prompt for API key value (twice to confirm)
        while True:
            api_key_1 = getpass.getpass(f"  API Key for {key_id} (hidden): ").strip()
            if not api_key_1:
                print("  ✗  API Key cannot be empty.\n")
                continue
            api_key_2 = getpass.getpass(f"  Confirm API Key (hidden): ").strip()
            if api_key_1 != api_key_2:
                print("  ✗  Keys do not match. Please try again.\n")
                continue
            break

        api_keys[key_id] = (provider, target_host, auth_header, auth_prefix, api_key_1)
        ok(f"{provider:12s}  →  {key_id}  (key hidden)")

    print("\n" + "═" * 70)
    print("  Subumbra Bootstrap — Step 3 of 4: Adapter Key Scopes")
    print("═" * 70)
    print("  Choose which key_ids each built-in adapter may fetch from subumbra-keys.")
    print("  1. subumbra-proxy: all key_ids that LiteLLM and other apps access via")
    print("     the transparent sidecar (api_base: http://subumbra-proxy:8090/t).")
    print("     For most deployments, enter all provider key_ids here.")
    print("  2. subumbra-probe: keys available to the verification/proof container")
    print("  subumbra-ui is metadata-only and never receives ciphertext fetch scope.")
    print("═" * 70 + "\n")

    available_key_ids = sorted(api_keys.keys())
    allowed_keys_by_adapter = {
        "subumbra-proxy": _prompt_allowed_keys("subumbra-proxy", available_key_ids),
        "subumbra-probe": _prompt_allowed_keys("subumbra-probe", available_key_ids),
        "subumbra-ui": [],
    }

    while True:
        raw_ttl = input("\n  Token TTL in days [90]: ").strip()
        try:
            token_ttl_days = _parse_token_ttl_days(raw_ttl or "90")
        except SystemExit:
            print("  ✗  Token TTL must be a positive integer.\n")
            continue
        break

    return api_keys, cf_creds, allowed_keys_by_adapter, token_ttl_days, shred_paths


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


def _create_or_reuse_kv_namespace(cf_creds: dict[str, str]) -> str:
    if KV_CONFIG_FILE.exists():
        return _load_kv_namespace_id()

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
    list_req = urllib.request.Request(base_url, headers=auth_headers)
    try:
        with urllib.request.urlopen(list_req) as resp:
            list_result = json.loads(resp.read())
    except Exception as exc:
        die(f"Failed to list KV namespaces: {exc}")

    existing = list_result.get("result") or []
    if len(existing) >= 100:
        warn(
            "KV namespace list returned 100 results; the account may have more "
            "namespaces than the page limit. If a matching namespace exists on "
            "a later page it will not be found and a new one will be created."
        )
    for entry in existing:
        if entry.get("title") == title:
            namespace_id = entry["id"]
            info(f"Reusing existing KV namespace: {title}")
            with KV_CONFIG_FILE.open("w") as fh:
                json.dump({"namespace_id": namespace_id, "title": title}, fh, indent=2)
                fh.write("\n")
            return namespace_id

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
    except Exception as exc:
        die(f"Failed to create provider-registry KV namespace: {exc}")

    if not result.get("success") or "result" not in result or "id" not in result["result"]:
        die("Failed to create provider-registry KV namespace")

    namespace_id = result["result"]["id"]
    with KV_CONFIG_FILE.open("w") as fh:
        json.dump({"namespace_id": namespace_id, "title": title}, fh, indent=2)
        fh.write("\n")
    return namespace_id


def _append_provider_registry_kv_binding(wrangler_toml: Path, namespace_id: str) -> None:
    with wrangler_toml.open("a") as fh:
        fh.write(
            "\n[[kv_namespaces]]\n"
            'binding = "PROVIDER_REGISTRY_KV"\n'
            f'id = "{namespace_id}"\n'
        )


def deploy_worker(
    cf_creds: dict[str, str],
    private_key_b64: str,
    pub_key_fp: str,
    adapter_tokens: dict[str, str],
    subumbra_hmac_key: str,
    provider_id_filter: "set[str] | None" = None,
) -> str:
    """
    Deploy the CF Worker and push V2 secrets.  Returns the worker URL.

    Steps:
      1. Copy worker source to a temp dir (source mount is :ro)
      2. wrangler deploy --name <name>
      3. wrangler secret put WORKER_PRIVATE_KEY
      4. wrangler secret put WORKER_KEY_FINGERPRINT
      5. wrangler secret delete MASTER_DECRYPTION_KEY (V1 cleanup, best-effort)
      6. wrangler secret put SUBUMBRA_ADAPTER_TOKENS
      7. wrangler secret put SUBUMBRA_HMAC_KEY
    """
    if not WORKER_SRC.exists():
        die(
            f"Worker source not found at {WORKER_SRC}.\n"
            "  Ensure ./worker is mounted into the bootstrap container\n"
            "  (check volumes in docker-compose.yml)."
        )

    worker_name = cf_creds["CF_WORKER_NAME"]

    # Wrangler reads CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID from env
    env = {
        **os.environ,
        "CLOUDFLARE_API_TOKEN":   cf_creds["CF_API_TOKEN"],
        "CLOUDFLARE_ACCOUNT_ID":  cf_creds["CF_ACCOUNT_ID"],
        # Tell wrangler not to prompt for login
        "CI": "true",
    }

    with tempfile.TemporaryDirectory(prefix="subumbra-worker-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copytree(WORKER_SRC, tmp_dir / "worker")
        work_dir = tmp_dir / "worker"
        namespace_id = _create_or_reuse_kv_namespace(cf_creds)
        _append_provider_registry_kv_binding(work_dir / "wrangler.toml", namespace_id)

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

        step("Pushing provider registry to Cloudflare KV")
        registry_json = _build_live_provider_registry_json(provider_id_filter=provider_id_filter)
        _run(
            [
                "wrangler", "kv", "key", "put",
                PROVIDER_REGISTRY_KV_KEY,
                registry_json,
                "--namespace-id", namespace_id,
                "--remote",
            ],
            cwd=work_dir,
            env=env,
        )
        ok("Provider registry pushed")

        # ── push WORKER_PRIVATE_KEY (RSA-4096 PKCS#8 DER, base64) ────────────
        step("Pushing WORKER_PRIVATE_KEY to CF Secrets")
        _run(
            ["wrangler", "secret", "put", "WORKER_PRIVATE_KEY",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=private_key_b64 + "\n",
        )
        ok("WORKER_PRIVATE_KEY pushed (never written to disk)")

        # ── push WORKER_KEY_FINGERPRINT ───────────────────────────────────────
        step("Pushing WORKER_KEY_FINGERPRINT to CF Secrets")
        _run(
            ["wrangler", "secret", "put", "WORKER_KEY_FINGERPRINT",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=pub_key_fp + "\n",
        )
        ok(f"WORKER_KEY_FINGERPRINT pushed ({pub_key_fp[:24]}...)")

        # ── delete stale V1 secret (best-effort) ─────────────────────────────
        step("Cleaning up stale MASTER_DECRYPTION_KEY (V1)")
        del_result = subprocess.run(
            ["wrangler", "secret", "delete", "MASTER_DECRYPTION_KEY",
             "--name", worker_name, "--force"],
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if del_result.returncode == 0:
            ok("Deleted stale MASTER_DECRYPTION_KEY secret")
        else:
            info("MASTER_DECRYPTION_KEY not present — already clean")

        # ── push SUBUMBRA_ADAPTER_TOKENS ─────────────────────────────────────
        step("Pushing SUBUMBRA_ADAPTER_TOKENS to CF Secrets")
        adapter_tokens_json = json.dumps(list(adapter_tokens.values()), separators=(",", ":"))
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


    # Derive URL — wrangler prints it, but it's also deterministic
    worker_url = f"https://{worker_name}.workers.dev"
    for line in deploy_out.splitlines():
        for token in line.split():
            if token.startswith("https://") and "workers.dev" in token:
                worker_url = token.rstrip(".,")
                break

    return worker_url


def run_push_registry() -> None:
    cf_creds = _get_push_registry_cf_creds()
    namespace_id = _load_kv_namespace_id()
    registry_json = _build_live_provider_registry_json()

    env = {
        **os.environ,
        "CLOUDFLARE_API_TOKEN": cf_creds["CF_API_TOKEN"],
        "CLOUDFLARE_ACCOUNT_ID": cf_creds["CF_ACCOUNT_ID"],
        "CI": "true",
    }

    _run(
        [
            "wrangler", "kv", "key", "put",
            PROVIDER_REGISTRY_KV_KEY,
            registry_json,
            "--namespace-id", namespace_id,
            "--remote",
        ],
        cwd=DATA_DIR,
        env=env,
    )
    ok("Provider registry pushed to Cloudflare KV")


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

    # ── 1. Load and validate public key ──────────────────────────────────
    if not PUBLIC_KEY_FILE.exists():
        die(
            f"public_key.pem not found at {PUBLIC_KEY_FILE}\n"
            "  Run a full bootstrap first to generate the RSA key pair."
        )

    try:
        pub_key = serialization.load_pem_public_key(PUBLIC_KEY_FILE.read_bytes())
    except Exception as exc:
        die(f"Failed to load public_key.pem: {exc}\n  File may be corrupted — run a full bootstrap.")

    fp = public_key_fingerprint(pub_key)

    # ── 2. Display info ──────────────────────────────────────────────────
    print("\n" + "═" * 70)
    print("  Subumbra — Per-Key Rotation")
    print("  Uses existing RSA public key — no Cloudflare interaction needed")
    print("═" * 70)
    print(f"\n  Public key fingerprint: {fp}")

    # ── 3. Load existing keys ────────────────────────────────────────────
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

    # ── 4. Select key to rotate ──────────────────────────────────────────
    provider = None
    target_host = None
    print()
    while True:
        choice = input("  Select key to rotate (number, key_id, or new key_id): ").strip()
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
                if not target_host:
                    target_host = _resolve_target_host(provider, prompt_if_missing=True)
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
            if not target_host:
                target_host = _resolve_target_host(provider, prompt_if_missing=True)
            break

        # Try as new key_id
        if KEY_ID_RE.match(choice):
            key_id = choice
            print(f"\n  New key: {key_id} (not in current keys.json)")
            while True:
                prov_input = input("  Provider name: ").strip().lower()
                if prov_input and re.match(r'^[a-z][a-z0-9_-]*$', prov_input):
                    provider = prov_input
                    target_host = _resolve_target_host(provider, prompt_if_missing=True)
                    break
                print("  ✗  Provider must be lowercase alphanumeric.\n")
            break

        print(f"  ✗  '{choice}' is not a valid selection or key_id format.\n")

    print(f"\n  Rotating: {key_id} ({provider})")

    # ── 5. Get new API key ───────────────────────────────────────────────
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

    # ── 6. Encrypt with V2 envelope ──────────────────────────────────────
    step(f"Encrypting new key for {key_id}")
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dek = os.urandom(32)
    ciphertext = encrypt_api_key_v2(dek, new_key, key_id)
    wrapped = wrap_dek(pub_key, dek)

    record = {
        "key_id":      key_id,
        "enc_version": 2,
        "pub_key_fp":  fp,
        "wrapped_dek": wrapped,
        "ciphertext":  ciphertext,
        "provider":    provider,
        "target_host": target_host,
        "created_at":  now_iso,
        "label":       key_id,
    }
    ok(f"Encrypted {provider:12s} → {key_id}")

    # ── 7. Zero sensitive values ─────────────────────────────────────────
    del dek
    new_key = "\x00" * len(new_key)
    del new_key
    del confirm_key
    gc.collect()

    # ── 8. Atomically update keys.json ───────────────────────────────────
    step(f"Updating {key_id} in keys.json")
    existing_keys[key_id] = record
    tmp_keys = KEYS_FILE.with_suffix(".json.tmp")
    try:
        fd = os.open(str(tmp_keys), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w") as fh:
            json.dump(existing_keys, fh, indent=2)
            fh.write("\n")
        os.replace(str(tmp_keys), str(KEYS_FILE))
    except OSError as exc:
        die(f"Failed to write keys.json: {exc}")

    ok(f"Updated {key_id} — only this record changed")
    info("All other records are untouched")
    info("No Cloudflare interaction, no runtime token changes")
    info("subumbra-keys will serve the new record on next request")
    print()


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
    use_wizard = not _has_env_credentials()

    if not use_wizard:
        step("Automation mode — loading credentials from environment")
        api_keys, cf_creds, allowed_keys_by_adapter, token_ttl_days = _load_env_fallback()
        ok(f"Found {len(api_keys)} API key(s): {', '.join(api_keys.keys())}")
        ok("Cloudflare credentials present")
    else:
        step("Interactive wizard — no credentials found in environment")
        try:
            api_keys, cf_creds, allowed_keys_by_adapter, token_ttl_days, shred_paths = run_interactive_wizard(existing_keys)
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
            print(f"    {kid:30s} → {provider}")

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

    # ── Step 3: generate RSA-4096 key pair (RAM only) ────────────────────
    step("Generating RSA-4096 key pair")
    private_key, pub_key = generate_keypair()
    ok("RSA-4096 key pair generated (private key stays in RAM)")

    # ── Step 4: serialize and store public key ───────────────────────────
    step(f"Writing public key → {PUBLIC_KEY_FILE}")
    pem_public = pub_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    try:
        fd = os.open(str(PUBLIC_KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem_public)
    except OSError as exc:
        die(f"Failed to write public_key.pem: {exc}")
    ok("Public key written (mode 0644 — safe to store)")

    # Compute fingerprint
    pub_key_fp = public_key_fingerprint(pub_key)
    info(f"Fingerprint: {pub_key_fp}")

    # Serialize private key for CF Secrets push
    private_key_b64 = b64encode(
        private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")

    # ── Step 5: generate runtime auth tokens ─────────────────────────────
    # SECURITY: These are privileged bearer/HMAC secrets. Anyone who obtains
    # an adapter token can drive the Worker as a scoped decryption oracle.
    # Treat them with the same care as the API keys they protect.
    step("Generating runtime auth tokens")
    adapter_tokens = {
        "subumbra-proxy": secrets.token_hex(32),
        "subumbra-ui": secrets.token_hex(32),
        "subumbra-probe": secrets.token_hex(32),
    }
    for adapter_id in allowed_keys_by_adapter:
        if adapter_id not in adapter_tokens:
            adapter_tokens[adapter_id] = secrets.token_hex(32)
    subumbra_hmac_key = secrets.token_hex(32)   # 64-char hex
    ok("SUBUMBRA_TOKEN_PROXY generated")
    ok("SUBUMBRA_TOKEN_UI generated")
    ok("SUBUMBRA_TOKEN_PROBE generated")
    for adapter_id in allowed_keys_by_adapter:
        if adapter_id not in BUILTIN_ADAPTER_IDS:
            ok(f"SUBUMBRA_TOKEN_{_normalize_adapter_id(adapter_id)} generated")
    ok("SUBUMBRA_HMAC_KEY generated")
    adapter_registry = _build_adapter_registry(
        adapter_tokens,
        allowed_keys_by_adapter,
        token_ttl_days=token_ttl_days,
    )

    # ── Step 6: encrypt API keys (V2 envelope) ───────────────────────────
    step("Encrypting API keys — V2 envelope (RSA-4096-OAEP + AES-256-GCM)")
    keys_payload: dict[str, dict] = {}

    for key_id, (provider, target_host, _auth_header, _auth_prefix, raw) in api_keys.items():
        dek = os.urandom(32)
        ciphertext = encrypt_api_key_v2(dek, raw, key_id)
        wrapped_dek = wrap_dek(pub_key, dek)
        keys_payload[key_id] = {
            "key_id":      key_id,
            "enc_version": 2,
            "pub_key_fp":  pub_key_fp,
            "wrapped_dek": wrapped_dek,
            "ciphertext":  ciphertext,
            "provider":    provider,
            "target_host": target_host,
            "created_at":  now_iso,
            "label":       key_id,
        }
        del dek
        ok(f"Encrypted {provider:12s} → {key_id}")

    # ── Step 7+8: deploy worker + push secrets ───────────────────────────
    # CRITICAL ORDER: remote secrets are pushed BEFORE keys.json is written.
    # If the deploy fails here, keys.json still holds the old blobs that match
    # the old key pair — the system remains consistent.
    bootstrapped_providers = {v[0] for v in api_keys.values()}
    worker_url = deploy_worker(
        cf_creds, private_key_b64, pub_key_fp,
        adapter_tokens, subumbra_hmac_key,
        provider_id_filter=bootstrapped_providers,
    )
    ok(f"Worker URL: {worker_url}")

    # ── Step 9: atomically write encrypted blobs ─────────────────────────
    # Write to a temp file in the same directory, then os.replace() for atomic
    # promotion.  subumbra-keys will never read a partially written file.
    # Mode 0o644: keys.json holds ciphertext only — safe to be world-readable.
    # The subumbra-keys container runs as a non-root 'subumbra' user and must be able
    # to read this file.  runtime.env (auth tokens) stays at 0o600.
    step(f"Atomically writing encrypted blobs → {KEYS_FILE}")
    tmp_keys = KEYS_FILE.with_suffix(".json.tmp")
    try:
        fd = os.open(str(tmp_keys), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w") as fh:
            json.dump(keys_payload, fh, indent=2)
            fh.write("\n")
        os.replace(str(tmp_keys), str(KEYS_FILE))
    except OSError as exc:
        die(f"Failed to write keys.json: {exc}")
    ok(f"Wrote {len(keys_payload)} key blob(s) — atomic rename complete")
    info("Blobs are useless without the CF private key — safe to store")

    # ── Step 10: write runtime env with restricted permissions ───────────
    # SECURITY: These tokens are privileged secrets.  Write with mode 0600
    # and do NOT print values to stdout (which may be captured in CI/CD logs).
    step(f"Writing runtime env → {RUNTIME_ENV_OUT}")
    runtime_env_lines = [
        f"# Generated by subumbra-bootstrap on {now_iso}",
        "# PRIVILEGED — treat like an API key; restrict access to this file",
        f"SUBUMBRA_ADAPTER_REGISTRY={json.dumps(adapter_registry, separators=(',', ':'))}",
        f"PROXY_ALLOWED_KEYS={','.join(allowed_keys_by_adapter['subumbra-proxy'])}",
        f"PROBE_ALLOWED_KEYS={','.join(allowed_keys_by_adapter['subumbra-probe'])}",
        f"UI_ALLOWED_KEYS={','.join(allowed_keys_by_adapter['subumbra-ui'])}",
        f"SUBUMBRA_TOKEN_PROXY={adapter_tokens['subumbra-proxy']}",
        f"SUBUMBRA_TOKEN_UI={adapter_tokens['subumbra-ui']}",
        f"SUBUMBRA_TOKEN_PROBE={adapter_tokens['subumbra-probe']}",
    ]
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
            f"WORKER_KEY_FINGERPRINT={pub_key_fp}",
        ]
    )
    runtime_env_content = "\n".join(runtime_env_lines) + "\n"
    try:
        fd = os.open(str(RUNTIME_ENV_OUT), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(runtime_env_content)
    except OSError as exc:
        die(f"Failed to write runtime.env: {exc}")
    ok("Runtime env written with mode 0600")

    # ── Step 11: zero sensitive memory ───────────────────────────────────
    step("Clearing sensitive values from memory")
    del private_key
    private_key_b64 = "0" * len(private_key_b64)  # noqa: F841
    del private_key_b64
    for adapter_id in list(adapter_tokens):
        adapter_tokens[adapter_id] = "\x00" * len(adapter_tokens[adapter_id])
    del adapter_tokens
    # Zero raw API key values (tuples are immutable but we can overwrite the dict)
    for k in list(api_keys):
        provider, target_host, auth_header, auth_prefix, raw = api_keys[k]
        api_keys[k] = (provider, target_host, auth_header, auth_prefix, "\x00" * len(raw))
    del api_keys
    del allowed_keys_by_adapter
    del cf_creds
    gc.collect()
    ok("Sensitive memory cleared (best-effort)")

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

    # ── Step 12: print summary (NO token values) ─────────────────────────
    rule = "═" * 68
    print(f"\n{rule}")
    print("  Bootstrap complete!")
    print(rule)
    print(textwrap.dedent(f"""
  New runtime tokens have been written to:
    {RUNTIME_ENV_OUT}

  Token values are NOT printed here (to avoid CI/CD log capture).
  Run post-bootstrap.sh to copy them into .env:

    ./post-bootstrap.sh

  Next steps:
    0. ⚠  If you used .env.bootstrap (automation path), back it up NOW if you
       want to keep a copy — post-bootstrap.sh will shred it.
       You do NOT need it after this point; your real keys are in CF Secrets.
    1. ./post-bootstrap.sh
       (copies SUBUMBRA_ADAPTER_REGISTRY, per-adapter Subumbra tokens, SUBUMBRA_HMAC_KEY, CF_WORKER_URL into .env)
    2. Start/restart ALL services (new tokens generated):
       docker compose up -d --force-recreate
    3. Check all containers running:  docker compose ps
    4. Check worker health:           curl {worker_url}/health
    5. For standalone LiteLLM or another app-owned integration, use:
         api_base: http://subumbra-proxy:8090/t
         api_key:  <key_id>   (plain, no subumbra: prefix)
       See docs/standalone-litellm.md for the canonical example.

  V2 envelope encryption active:
    Public key:    {PUBLIC_KEY_FILE}
    Fingerprint:   {pub_key_fp}
    Per-key rotate: docker compose --profile bootstrap run --rm -it bootstrap --rotate
"""))


if __name__ == "__main__":
    if "--push-registry" in sys.argv and "--rotate" in sys.argv:
        die("--push-registry and --rotate are mutually exclusive")
    if "--push-registry" in sys.argv:
        run_push_registry()
    elif "--rotate" in sys.argv:
        run_rotate_wizard()
    else:
        main()
