#!/usr/bin/env python3
"""Adapter display and mutation commands for Subumbra bootstrap."""

from __future__ import annotations

from subumbra_core import *
from subumbra_core import (
    _ADAPTER_CATALOG_CACHE,
    _CATALOG_CACHE,
    _WIZARD_SECRETS,
    _load_keys_payload_or_die,
    _manifest_die,
    _policy_adapter_ids,
    _prompt_hidden_line,
    _read_env_file_value,
    _write_keys_payload,
)
from subumbra_keys import (
    _get_push_registry_cf_creds,
    _load_management_manifest_authority,
    _publish_after_local_record_update,
    _require_existing_active_record,
    _require_existing_active_ssh_record,
    _rewrite_v3_record_from_plaintext,
    _update_record_policy_without_reencrypt,
)

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
    existing_candidate = keys_payload.get(target_key_id)
    if isinstance(existing_candidate, dict) and existing_candidate.get("type") == "ssh_key":
        existing_record = _require_existing_active_ssh_record(keys_payload, target_key_id)
    else:
        existing_record = _require_existing_active_record(keys_payload, target_key_id)
    authority = _load_management_manifest_authority(target_key_id, str(existing_record.get("provider", "")))
    new_policy = authority["policy"]
    new_adapters = authority["adapters"]
    new_policy_hash = compute_policy_hash(new_policy)
    old_policy_hash = str(existing_record.get("policy_hash", "")).strip()

    if existing_record.get("type") == "ssh_key":
        step(f"Publishing SSH policy update for key_id {target_key_id}")
        updated = dict(existing_record)
        updated["policy_id"] = new_policy["policy_id"]
        updated["policy_hash"] = new_policy_hash
        updated["policy"] = new_policy
        updated["adapters"] = list(new_adapters)
        updated["revoked"] = False
        keys_payload[target_key_id] = updated
        _write_keys_payload(keys_payload)
        ok(f"Updated SSH policy for {target_key_id} in keys.json")
        _publish_after_local_record_update(cf_creds, keys_payload)
        return

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

def print_help() -> None:
    print("""
Subumbra Bootstrap Utility

Usage: ./bootstrap.sh [OPTIONS]

Options:
  --help, -h                  Show this help message and exit
  --list-key-ids              List all key IDs defined in the manifest (subumbra.yaml)
  --list-adapters             List supported integrations with token status and authorized keys
  --show <adapter_id>         Print paste-ready setup config for a specific integration
  --status                    Compare manifest authority to deployed record state
  --upgrade                   Rebuild images and recreate containers
  --nuke                      Destructive run: destroys existing Cloudflare Vault keypairs
                              and regenerates everything from scratch
  --rotate                    Rotate upstream keys for existing records
  --add-ssh-key <key_id>      Generate and publish a new SSH key for day-2 use
    --adapters <csv>            Required adapter IDs allowed to sign with the key
    --allow-hosts <csv>         Optional hostnames/IPs to resolve into allowed SSH host keys
  --rotate-ssh-key <key_id>   Rotate an existing generated SSH key in place
    --allow-hosts <csv>         Optional hostnames/IPs to replace the current allowed host set
  --revoke-ssh-key <key_id>   Revoke an existing SSH key and delete its live KV entries
  --push-registry             Push keys.json state directly to Cloudflare KV
  --deploy-worker             Redeploy the Cloudflare Worker code (+ KV binding)
                              without rotating secrets. Run this after a round
                              that changes worker/src/worker.js; --upgrade only
                              rebuilds local Docker images.
  --nuke-cloudflare           Delete Cloudflare-managed Tunnel / DNS / Access resources
  --provision <key_id>        Targeted provisioning/repair for a single key
  --revoke-key <key_id>       Revoke a key (deletes from KV; --offline updates local keys.json only)
  --add-adapter <key_id>      Add an adapter binding to an existing key
  --revoke-adapter <key_id>   Revoke an adapter binding from an existing key
  --publish-policy <key_id>   Republish a key's policy/adapters to KV
  --session start             Open a session (enables key-fetch for the duration)
    --ttl <duration>            Required. How long the session stays open.
                                Format: <number><unit>  s=seconds  m=minutes  h=hours  d=days
                                Examples: 30m  2h  8h  1d
    --adapters <csv|all>        Adapters to open. Omit or pass 'all' for all.
    --keys <csv|all>            Keys to allow. Omit or pass 'all' for all.
    --name <label>              Optional human-readable label for this session.
    --max-queries <n>           Optional query cap before the session auto-closes.
    --max-sign-ops <n>          Optional SSH sign cap before the session auto-closes.
    (If --ttl or --adapters are omitted on a TTY, an interactive wizard starts.)
  --session end [session_id]  Close one active session immediately.
    --all                       Close every active session.
    (With multiple sessions on a TTY, omitting session_id shows a picker.)
  --session status            Show lockdown state and all active session details.
  --session list              Show the 20 most recent sessions (any status).

For a full initial bootstrap, run without arguments.
""")


def print_key_ids() -> None:
    try:
        key_ids = _load_manifest_key_ids_only()
        for kid in sorted(key_ids):
            print(kid)
    except SystemExit:
        sys.exit(1)


_ADAPTER_CATEGORY_LABELS: dict[str, str] = {
    "llm_frontend": "LLM Frontends",
    "llm_gateway":  "LLM Gateways",
    "automation":   "Automation",
    "cms":          "CMS & Ecommerce",
    "ecommerce":    "Ecommerce",
    "internal":     "Internal",
}
_ADAPTER_CATEGORY_ORDER = ["llm_frontend", "llm_gateway", "automation", "cms", "ecommerce", "internal"]


def _adapter_authorized_key_ids(allow_adapters: list[str], records: list[dict]) -> list[str]:
    """Return key_ids from the manifest whose effective_adapters overlap with allow_adapters."""
    allow_set = set(allow_adapters)
    return [r["key_id"] for r in records if set(r.get("effective_adapters", [])) & allow_set]


def print_adapters() -> None:
    try:
        catalog = _load_adapter_catalog()
        records = _load_manifest_records()
    except SystemExit:
        sys.exit(1)

    # Fallback: if no catalog (e.g. user-templates only), print bare manifest adapters
    if not catalog:
        all_adapters: set[str] = set()
        for r in records:
            all_adapters.update(r.get("effective_adapters", []))
        for a in sorted(all_adapters):
            print(a)
        return

    # Group catalog entries by category
    by_category: dict[str, list[dict]] = {}
    for entry in catalog.values():
        cat = entry.get("category", "internal")
        by_category.setdefault(cat, []).append(entry)

    configured = sum(
        1 for e in catalog.values()
        if _read_env_file_value(HOST_ENV_FILE, e.get("default_token_env_var", "")).strip()
    )
    print(f"\nSubumbra integrations — {configured}/{len(catalog)} configured\n")

    for cat in _ADAPTER_CATEGORY_ORDER:
        entries = by_category.get(cat)
        if not entries:
            continue
        print(f"  {_ADAPTER_CATEGORY_LABELS.get(cat, cat)}")
        for entry in sorted(entries, key=lambda e: e.get("display_name", e["adapter_id"])):
            aid        = entry["adapter_id"]
            dname      = entry.get("display_name", aid)
            token_var  = entry.get("default_token_env_var", "")
            token_val  = _read_env_file_value(HOST_ENV_FILE, token_var).strip() if token_var else ""
            allow_adps = entry.get("allow_adapters", [])
            key_ids    = _adapter_authorized_key_ids(allow_adps, records)

            if token_val:
                token_str = f"{token_val[:8]}..."
                key_str   = ", ".join(key_ids) if key_ids else "(no keys authorized)"
                status    = "✓"
            else:
                token_str = "(not configured)"
                key_str   = ""
                status    = "-"

            if key_str:
                print(f"    {status} {aid:<16} {dname:<20} {token_str:<16} keys: {key_str}")
            else:
                print(f"    {status} {aid:<16} {dname:<20} {token_str}")
        print()

    print("  Run: ./bootstrap.sh --show <adapter_id>  for paste-ready setup")
    print()


def print_show_adapter(adapter_id: str) -> None:
    try:
        catalog = _load_adapter_catalog()
        records = _load_manifest_records()
    except SystemExit:
        sys.exit(1)

    if adapter_id not in catalog:
        known = ", ".join(sorted(catalog)) if catalog else "(none)"
        die(f"Adapter {adapter_id!r} not found in catalog. Known: {known}")

    entry      = catalog[adapter_id]
    dname      = entry.get("display_name", adapter_id)
    cat        = entry.get("category", "")
    cat_label  = _ADAPTER_CATEGORY_LABELS.get(cat, cat)
    docs_path  = entry.get("docs_path", "")
    token_var  = entry.get("default_token_env_var", "")
    token_val  = _read_env_file_value(HOST_ENV_FILE, token_var).strip() if token_var else ""
    allow_adps = entry.get("allow_adapters", [])
    key_ids    = _adapter_authorized_key_ids(allow_adps, records)
    fmt        = entry.get("config_format", {})
    fmt_type   = fmt.get("type", "env_file")
    fmt_target = fmt.get("target", "")
    fields     = fmt.get("fields", [])
    notes      = entry.get("config_notes", "").strip()

    # Substitute placeholders
    primary_key = key_ids[0] if key_ids else "{key_id}"

    def sub(val: str) -> str:
        return val.replace("{adapter_token}", token_val or "{adapter_token}") \
                  .replace("{key_id}", primary_key)

    print(f"\n=== {dname} ===")
    print(f"Category : {cat_label}")
    if docs_path:
        print(f"Guide    : {docs_path}")
    if not token_val:
        print(f"Status   : not configured — run bootstrap to generate {token_var or 'adapter token'}")
    else:
        print(f"Token    : {token_val[:8]}...  (from {token_var})")
    if key_ids:
        print(f"Keys     : {', '.join(key_ids)}")
    else:
        print("Keys     : (no keys authorized for this adapter in subumbra.yaml)")

    if not fields:
        print("\n  No config_format fields defined — see the guide above.")
        if notes:
            print(f"\n  Note: {notes}")
        return

    target_hint = f" into {fmt_target}" if fmt_target else ""
    print()
    if fmt_type == "env_file":
        print(f"  Paste{target_hint}:\n")
        for f in fields:
            val  = f["value"] if f.get("static") else sub(f["value"])
            note = f"  # {f['note']}" if f.get("note") else ""
            print(f"  {f['name']}={val}{note}")
    elif fmt_type == "yaml_file":
        print(f"  Paste{target_hint}:\n")
        if key_ids and len(key_ids) > 1:
            # Expand one block per key_id
            for kid in key_ids:
                def sub_kid(val: str) -> str:
                    return val.replace("{adapter_token}", token_val or "{adapter_token}") \
                              .replace("{key_id}", kid)
                print(f"  # --- {kid} ---")
                for f in fields:
                    val  = f["value"] if f.get("static") else sub_kid(f["value"])
                    note = f"  # {f['note']}" if f.get("note") else ""
                    print(f"  {f['name']}: {val}{note}")
                print()
        else:
            for f in fields:
                val  = f["value"] if f.get("static") else sub(f["value"])
                note = f"  # {f['note']}" if f.get("note") else ""
                print(f"  {f['name']}: {val}{note}")
    else:  # ui
        print(f"  Enter{target_hint}:\n")
        for f in fields:
            val  = f["value"] if f.get("static") else sub(f["value"])
            note = f"  ({f['note']})" if f.get("note") else ""
            print(f"  {f['name']:<28} {val}  {note}")

    if len(key_ids) > 1:
        print(f"\n  Multiple keys available: {', '.join(key_ids)}")
        print("  Change the key_id in the path to switch providers.")
    if notes:
        print(f"\n  Note: {notes}")
    print()
