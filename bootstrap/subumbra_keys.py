#!/usr/bin/env python3
"""Bootstrap, provision, rotate, revoke, and status pipeline for Subumbra."""

from __future__ import annotations

from subumbra_core import *
from subumbra_cf import *
from subumbra_core import (
    _WIZARD_SECRETS,
    _automation_fail,
    _bind_key_to_adapters,
    _binding_label,
    _build_adapter_registry,
    _build_fat_record,
    _build_host_env_updates,
    _build_runtime_env_lines,
    _choose_bootstrap_mode,
    _delete_file_if_present,
    _has_env_credentials,
    _is_revoked_record,
    _load_keys_payload_if_present,
    _load_manifest_key_ids_only,
    _load_manifest_records,
    _load_manifest_repair_authority,
    _load_policy_index,
    _load_public_key_from_pem,
    _load_unique_key_flags,
    _normalize_adapter_id,
    _parse_token_ttl_days,
    _prompt_after_automation_error,
    _prompt_hidden_line,
    _public_key_file_for_key,
    _read_env_file_value,
    _read_runtime_credential_value,
    _representative_key_id_for_vault_instance,
    _require_fat_record_fields,
    _resolve_manifest_secret,
    _resolve_policy_for_key,
    _sync_host_env_file,
    _validate_allowed_keys,
    _vault_instance_for_key,
    _verify_embedded_policy_hash,
    _write_keys_payload,
    _write_public_key_file,
    _write_runtime_env_file,
)
from subumbra_cf import (
    _call_internal_vault_reset,
    _create_or_reuse_kv_namespace,
    _default_cf_access_app_name,
    _default_cf_service_token_name,
    _default_cf_tunnel_name,
    _delete_kv_namespace_if_present,
    _delete_worker_secret,
    _get_push_registry_cf_creds,
    _kv_delete_key,
    _load_cf_autoprovision_from_sources,
    _provision_cloudflare_resources,
    _publish_structured_kv,
    _put_worker_secret,
    _resolved_cf_worker_name_from_operator_context,
    _wizard_collect_cf_access,
    _worker_control_headers,
)

def _load_manifest_bootstrap() -> tuple[
    dict[str, tuple[str, str, str, str, str]],
    dict[str, str],
    dict[str, list[str]],
    dict[str, list[str]],
    int,
    dict[str, str],
    dict[str, str],
    dict[str, bool],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
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
    cf_runtime_creds: dict[str, str] = {}
    for var in ("TUNNEL_TOKEN", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"):
        val = _read_runtime_credential_value(var)
        if val:
            cf_runtime_creds[var] = val
    cf_autoprovision = _load_cf_autoprovision_from_sources(
        runtime_creds=cf_runtime_creds,
        cf_worker_name=cf_creds["CF_WORKER_NAME"],
    )

    declared_adapter_ids: list[str] = []
    seen_declared: set[str] = set()
    api_keys: dict[str, tuple[str, str, str, str, str]] = {}
    key_adapters_by_key_id: dict[str, list[str]] = {}
    policy_by_key_id: dict[str, dict[str, Any]] = {}
    unique_key_flags: dict[str, bool] = {}
    ssh_records: list[dict[str, Any]] = []
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
        policy_by_key_id[key_id] = record["policy"]
        unique_key_flags[key_id] = record["unique_vault"]
        _bind_key_to_adapters(
            key_id,
            record["adapters"],
            key_adapters_by_key_id=key_adapters_by_key_id,
            allowed_keys_by_adapter=allowed_keys_by_adapter,
        )
        if record.get("type") == "ssh_key":
            ssh_records.append(record)
            continue
        api_keys[key_id] = (
            record["provider"],
            record["target_host"],
            record["auth_header"],
            record["auth_prefix"],
            record["secret_ref"],
        )

    token_ttl_days = _parse_token_ttl_days(os.environ.get("TOKEN_TTL_DAYS", ""))
    return (
        api_keys,
        cf_creds,
        allowed_keys_by_adapter,
        key_adapters_by_key_id,
        token_ttl_days,
        cf_runtime_creds,
        cf_autoprovision,
        unique_key_flags,
        policy_by_key_id,
        ssh_records,
    )


# ── TOMBSTONED (R58): legacy env-only bootstrap ─────────────────────────────
# `_load_env_fallback` only calls `_automation_fail(...)`. `main()` still dispatches here when
# the mounted manifest is missing in automation mode so operators get a structured error (manifest-only flow).
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
    dict[str, str],
    dict[str, str],
    dict[str, bool],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    list[str],
]:
    """
    Interactive manifest bootstrap: collect Cloudflare credentials and per-key
    secrets in RAM, bind adapters, and return the same logical credential bundle
    as _load_manifest_bootstrap plus an empty shred_paths list.
    """
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
    cf_runtime_creds: dict[str, str] = {}
    cf_autoprovision: dict[str, str] = {}

    step("Cloudflare Tunnel runtime token (optional)")
    existing_tunnel = _read_env_file_value(HOST_ENV_FILE, "TUNNEL_TOKEN").strip()
    if existing_tunnel:
        use_existing = input("  Found existing TUNNEL_TOKEN. Reuse? [Y/n]: ").strip().lower()
        if use_existing not in ("n", "no"):
            cf_runtime_creds["TUNNEL_TOKEN"] = existing_tunnel
            ok("Reusing existing TUNNEL_TOKEN (value not printed)")
        else:
            tunnel_tok = _prompt_hidden_line("Cloudflare Tunnel token (leave blank to skip)")
            if tunnel_tok:
                cf_runtime_creds["TUNNEL_TOKEN"] = tunnel_tok
                ok("Tunnel token captured (value not printed)")
            else:
                info("No Tunnel token provided — skipping Tunnel runtime credential")
    else:
        tunnel_tok = _prompt_hidden_line("Cloudflare Tunnel token (leave blank to skip)")
        if tunnel_tok:
            cf_runtime_creds["TUNNEL_TOKEN"] = tunnel_tok
            ok("Tunnel token captured (value not printed)")
        else:
            info("No Tunnel token provided — skipping Tunnel runtime credential")

    step("Cloudflare Access service token (optional)")
    existing_id = _read_env_file_value(HOST_ENV_FILE, "CF_ACCESS_CLIENT_ID").strip()
    existing_secret = _read_env_file_value(HOST_ENV_FILE, "CF_ACCESS_CLIENT_SECRET").strip()
    if existing_id and existing_secret:
        use_existing = input("  Found existing CF Access credentials. Reuse? [Y/n]: ").strip().lower()
        if use_existing not in ("n", "no"):
            cf_runtime_creds["CF_ACCESS_CLIENT_ID"] = existing_id
            cf_runtime_creds["CF_ACCESS_CLIENT_SECRET"] = existing_secret
            ok("Reusing existing CF Access credentials (values not printed)")
        else:
            _wizard_collect_cf_access(cf_runtime_creds)
    else:
        _wizard_collect_cf_access(cf_runtime_creds)

    step("Cloudflare auto-provisioning (optional)")
    auto_tunnel_choice = "n"
    auto_access_choice = "n"
    if not cf_runtime_creds.get("TUNNEL_TOKEN"):
        auto_tunnel_choice = input(
            "  Auto-provision Cloudflare Tunnel / DNS? [y/N]: "
        ).strip().lower()
    if not (
        cf_runtime_creds.get("CF_ACCESS_CLIENT_ID")
        and cf_runtime_creds.get("CF_ACCESS_CLIENT_SECRET")
    ):
        auto_access_choice = input(
            "  Auto-provision CF Access app / policy / service token? [y/N]: "
        ).strip().lower()
    if auto_tunnel_choice in ("y", "yes") or auto_access_choice in ("y", "yes"):
        while True:
            zone_id = _prompt_hidden_line("Cloudflare zone ID")
            if zone_id:
                break
            print("  ✗  Zone ID cannot be empty.\n")
        tunnel_hostname = ""
        if auto_tunnel_choice in ("y", "yes"):
            while True:
                tunnel_hostname = input("  Cloudflare Tunnel hostname (e.g. subumbra.example.com): ").strip()
                if tunnel_hostname:
                    break
                print("  ✗  Tunnel hostname cannot be empty when Tunnel auto-provisioning is requested.\n")
        cf_autoprovision = {
            "CF_ZONE_ID": zone_id,
            "CF_TUNNEL_HOSTNAME": tunnel_hostname,
            "CF_TUNNEL_NAME": input(
                f"  Tunnel name [default: {_default_cf_tunnel_name(cf_worker_name)}]: "
            ).strip() or _default_cf_tunnel_name(cf_worker_name),
            "CF_ACCESS_APP_NAME": input(
                f"  Access app name [default: {_default_cf_access_app_name(cf_worker_name)}]: "
            ).strip() or _default_cf_access_app_name(cf_worker_name),
            "CF_SERVICE_TOKEN_NAME": input(
                f"  Access service token name [default: {_default_cf_service_token_name(cf_worker_name)}]: "
            ).strip() or _default_cf_service_token_name(cf_worker_name),
            "AUTO_PROVISION_TUNNEL": "1" if auto_tunnel_choice in ("y", "yes") else "0",
            "AUTO_PROVISION_ACCESS": "1" if auto_access_choice in ("y", "yes") else "0",
        }
        ok("Cloudflare auto-provisioning inputs captured (values not printed)")
    else:
        info("Cloudflare auto-provisioning skipped — BYOC runtime secrets only")

    step("Loading manifest records")
    records = _load_manifest_records()
    ok(f"Found {len(records)} manifest key record(s)")

    step("Per-key provider secrets (RAM only; not echoed)")
    accepted: list[dict[str, Any]] = []
    for record in records:
        key_id = record["key_id"]
        provider = record["provider"]
        secret_ref = record.get("secret_ref")
        if record.get("type") == "ssh_key" and record.get("key_source") == "generated":
            ok(f"{key_id}: SSH key will be generated in the vault")
            accepted.append(record)
            continue
        if not isinstance(secret_ref, str) or not secret_ref:
            die(f"Manifest record {key_id!r} is missing secret_ref for interactive secret collection.")
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
    ssh_records: list[dict[str, Any]] = []
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
        policy_by_key_id[kid] = rec["policy"]
        unique_key_flags[kid] = rec["unique_vault"]
        _bind_key_to_adapters(
            kid,
            rec["adapters"],
            key_adapters_by_key_id=key_adapters_by_key_id,
            allowed_keys_by_adapter=allowed_keys_by_adapter,
        )
        if rec.get("type") == "ssh_key":
            ssh_records.append(rec)
            continue
        api_keys[kid] = (
            rec["provider"],
            rec["target_host"],
            rec["auth_header"],
            rec["auth_prefix"],
            rec["secret_ref"],
        )

    token_ttl_days = _parse_token_ttl_days(os.environ.get("TOKEN_TTL_DAYS", ""))
    shred_paths: list[str] = []
    return (
        api_keys,
        cf_creds,
        allowed_keys_by_adapter,
        key_adapters_by_key_id,
        token_ttl_days,
        cf_runtime_creds,
        cf_autoprovision,
        unique_key_flags,
        policy_by_key_id,
        ssh_records,
        shred_paths,
    )



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


def _parse_ssh_adapters_csv(raw: str) -> list[str]:
    value = raw.strip()
    if not value:
        die("--adapters requires a comma-separated list of adapter IDs")
    parsed: list[str] = []
    seen: set[str] = set()
    for adapter_id in [part.strip() for part in value.split(",") if part.strip()]:
        if not ADAPTER_ID_RE.fullmatch(adapter_id):
            die(f"Invalid adapter_id {adapter_id!r} for --adapters")
        if adapter_id in BUILTIN_ADAPTER_IDS:
            die(f"adapter_id {adapter_id!r} is reserved and cannot be used for SSH daily-use commands")
        if adapter_id in seen:
            continue
        seen.add(adapter_id)
        parsed.append(adapter_id)
    if not parsed:
        die("--adapters requires at least one adapter ID")
    return parsed


def _parse_ssh_allow_hosts_csv(raw: str) -> list[str]:
    value = raw.strip()
    if not value:
        die("--allow-hosts requires a comma-separated list of hostnames")
    parsed: list[str] = []
    seen: set[str] = set()
    for host in [part.strip() for part in value.split(",") if part.strip()]:
        if host in seen:
            continue
        seen.add(host)
        parsed.append(host)
    if not parsed:
        die("--allow-hosts requires at least one hostname")
    return parsed


def _ssh_record_allowed_host_fingerprints(record: dict[str, Any]) -> list[str]:
    policy = record.get("policy")
    if not isinstance(policy, dict):
        die(f"SSH record {record.get('key_id', '<unknown>')!r} is missing policy")
    allow = policy.get("allow")
    if not isinstance(allow, dict):
        return []
    hosts = allow.get("hosts")
    if hosts is None:
        return []
    if not isinstance(hosts, list) or not all(isinstance(host, str) and host for host in hosts):
        die(f"SSH record {record.get('key_id', '<unknown>')!r} has invalid policy.allow.hosts")
    return list(hosts)


def _run_with_temporary_setup_token(
    cf_creds: dict[str, str],
    callback,
) -> Any:
    worker_url = _read_runtime_credential_value("CF_WORKER_URL")
    if not worker_url:
        die(
            "SSH day-2 commands require CF_WORKER_URL in the runtime environment.\n"
            f"  Add CF_WORKER_URL to {HOST_ENV_FILE} and retry."
        )
    setup_token = secrets.token_urlsafe(48)
    step("Pushing transient SUBUMBRA_SETUP_TOKEN for SSH day-2 operation")
    _put_worker_secret(cf_creds, "SUBUMBRA_SETUP_TOKEN", setup_token)
    try:
        return callback(worker_url, setup_token)
    finally:
        step("Deleting transient SUBUMBRA_SETUP_TOKEN after SSH day-2 operation")
        _delete_worker_secret(cf_creds, "SUBUMBRA_SETUP_TOKEN", quiet_missing=True)


def _require_existing_active_ssh_record(
    keys_payload: dict[str, dict[str, Any]],
    key_id: str,
) -> dict[str, Any]:
    if key_id not in keys_payload:
        die(f"key_id {key_id!r} not found in keys.json")
    record = keys_payload[key_id]
    if not isinstance(record, dict):
        die(f"keys.json record {key_id!r} is malformed")
    if _is_revoked_record(record):
        die(f"key_id {key_id!r} is already revoked")
    if record.get("type") != "ssh_key":
        die(f"key_id {key_id!r} is not an SSH key")
    return record


def run_add_ssh_key(target_key_id: str, adapters_csv: str, allow_hosts_csv: str | None = None) -> None:
    if not KEY_ID_RE.fullmatch(target_key_id):
        die(f"Invalid SSH key_id {target_key_id!r}")
    adapters = _parse_ssh_adapters_csv(adapters_csv)
    requested_hosts = _parse_ssh_allow_hosts_csv(allow_hosts_csv) if isinstance(allow_hosts_csv, str) else []
    try:
        allowed_host_fingerprints = resolve_allowed_host_fingerprints(requested_hosts)
    except SshBootstrapError as exc:
        die(str(exc))
    keys_payload = _load_keys_payload_or_die()
    existing = keys_payload.get(target_key_id)
    if isinstance(existing, dict) and not _is_revoked_record(existing):
        die(f"key_id {target_key_id!r} already exists in keys.json")

    cf_creds = _get_push_registry_cf_creds()

    def _provision(worker_url: str, setup_token: str) -> dict[str, Any]:
        step(f"Generating SSH key {target_key_id} in the vault")
        try:
            return provision_generated_ssh_key(
                worker_url=worker_url,
                headers=_worker_control_headers(setup_token),
                key_id=target_key_id,
                adapters=adapters,
                allowed_host_fingerprints=allowed_host_fingerprints,
                vault_instance="vault",
            )
        except SshBootstrapError as exc:
            die(str(exc))

    keys_payload[target_key_id] = _run_with_temporary_setup_token(cf_creds, _provision)

    _write_keys_payload(keys_payload)
    ok(f"Added SSH key {target_key_id} to keys.json")
    _publish_after_local_record_update(cf_creds, keys_payload)


def run_rotate_ssh_key(target_key_id: str, allow_hosts_csv: str | None = None) -> None:
    keys_payload = _load_keys_payload_or_die()
    existing_record = _require_existing_active_ssh_record(keys_payload, target_key_id)
    if existing_record.get("key_source") != "generated":
        die(
            f"--rotate-ssh-key currently supports generated SSH keys only. key_id {target_key_id!r} "
            "was provisioned from a provided private key."
        )

    cf_creds = _get_push_registry_cf_creds()
    adapters = existing_record.get("adapters", [])
    if not isinstance(adapters, list) or not adapters:
        die(f"SSH record {target_key_id!r} is missing adapters")
    vault_instance = str(existing_record.get("vault_instance", "")).strip() or "vault"
    if isinstance(allow_hosts_csv, str):
        requested_hosts = _parse_ssh_allow_hosts_csv(allow_hosts_csv)
        try:
            allowed_host_fingerprints = resolve_allowed_host_fingerprints(requested_hosts)
        except SshBootstrapError as exc:
            die(str(exc))
    else:
        allowed_host_fingerprints = _ssh_record_allowed_host_fingerprints(existing_record)

    def _rotate(worker_url: str, setup_token: str) -> dict[str, Any]:
        step(f"Rotating SSH key {target_key_id} in the vault")
        try:
            return provision_generated_ssh_key(
                worker_url=worker_url,
                headers=_worker_control_headers(setup_token),
                key_id=target_key_id,
                adapters=[str(adapter_id) for adapter_id in adapters],
                allowed_host_fingerprints=allowed_host_fingerprints,
                vault_instance=vault_instance,
            )
        except SshBootstrapError as exc:
            die(str(exc))

    keys_payload[target_key_id] = _run_with_temporary_setup_token(cf_creds, _rotate)

    _write_keys_payload(keys_payload)
    ok(f"Rotated SSH key {target_key_id} in keys.json")
    _publish_after_local_record_update(cf_creds, keys_payload)


def run_revoke_ssh_key(target_key_id: str) -> None:
    keys_payload = _load_keys_payload_or_die()
    record = _require_existing_active_ssh_record(keys_payload, target_key_id)
    revoked_record = dict(record)
    revoked_record["revoked"] = True
    revoked_record["status"] = "revoked"
    keys_payload[target_key_id] = revoked_record

    step(f"Marking SSH key {target_key_id} as revoked in keys.json")
    _write_keys_payload(keys_payload)
    ok(f"Revocation marker persisted for SSH key {target_key_id}")

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

def run_bootstrap() -> None:
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
                cf_runtime_creds,
                cf_autoprovision,
                unique_key_flags,
                policy_by_key_id,
                ssh_records,
            ) = _load_manifest_bootstrap()
            ok(
                f"Loaded {len(key_adapters_by_key_id)} manifest key(s): "
                f"{', '.join(sorted(key_adapters_by_key_id))}"
            )
            ok("Cloudflare credentials present")
        else:
            # Automation without a manifest: `_load_env_fallback` is tombstoned (immediate `_automation_fail`).
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
                cf_runtime_creds,
                cf_autoprovision,
                unique_key_flags,
                policy_by_key_id,
                ssh_records,
                shred_paths,
            ) = run_interactive_wizard(existing_keys)
        except KeyboardInterrupt:
            print("\n\nAborted. No changes written.", file=sys.stderr)
            sys.exit(0)
    if not use_wizard:
        shred_paths = []
        if not MANIFEST_FILE.exists():
            cf_runtime_creds = {}
            cf_autoprovision = {}
            policy_index = _load_policy_index()
            policy_by_key_id = {}
            ssh_records = []
            for key_id, (provider, target_host, _auth_header, _auth_prefix, _secret_ref) in api_keys.items():
                policy_by_key_id[key_id] = _resolve_policy_for_key(
                    key_id,
                    provider,
                    target_host,
                    policy_index,
                    key_adapters_by_key_id[key_id],
                )
            unique_key_flags = _load_unique_key_flags(list(api_keys.keys()))

    all_manifest_keys: dict[str, Any] = {key_id: True for key_id in key_adapters_by_key_id}
    _validate_allowed_keys(all_manifest_keys, allowed_keys_by_adapter)

    # ── Step 2: rotation safety check ────────────────────────────────────
    # Every bootstrap run generates a NEW RSA key pair.  Any key omitted from
    # this session will be unreachable after this run.
    incoming_key_ids = set(key_adapters_by_key_id.keys())
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

        print("  Keys to provision:")
        for kid, (provider, _target_host, _auth_header, _auth_prefix, _secret_ref) in api_keys.items():
            print(f"    {kid:30s} → {provider:12s} → {_binding_label(key_adapters_by_key_id[kid])}")
        for rec in ssh_records:
            print(
                f"    {rec['key_id']:30s} → {'ssh':12s} → "
                f"{_binding_label(key_adapters_by_key_id[rec['key_id']])}"
            )

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
            for key_id in key_adapters_by_key_id.keys()
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
        worker_name=cf_creds["CF_WORKER_NAME"],
        setup_token=setup_token,
        cf_runtime_creds=cf_runtime_creds,
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

    if cf_autoprovision:
        step("Auto-provisioning Cloudflare Tunnel / DNS / Access resources")
        try:
            cf_runtime_creds, _manifest_payload = _provision_cloudflare_resources(
                cf_creds,
                cf_autoprovision,
                cf_runtime_creds,
            )
        except BootstrapFlowError as exc:
            die(str(exc))
        host_env_updates = _build_host_env_updates(
            adapter_registry=adapter_registry,
            allowed_keys_by_adapter=allowed_keys_by_adapter,
            adapter_tokens=adapter_tokens,
            subumbra_hmac_key=subumbra_hmac_key,
            management_token=management_token,
            worker_url=worker_url,
            worker_name=cf_creds["CF_WORKER_NAME"],
            setup_token=setup_token,
            cf_runtime_creds=cf_runtime_creds,
        )
        _sync_host_env_file(host_env_updates)

    # ── Step 5a: Phase 1 — /setup/keygen per vault instance (before secrets) ─
    step("Phase 1 — vault /setup/keygen (per vault instance)")
    phase1_failures: list[tuple[str, str]] = []
    public_keys_by_vault_instance: dict[str, Any] = {}

    for vault_instance in candidate_vault_instances:
        rep_key = _representative_key_id_for_vault_instance(
            key_adapters_by_key_id.keys(),
            unique_key_flags,
            vault_instance,
        )
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

    for key_id in key_adapters_by_key_id.keys():
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
        record_type = "ssh" if any(rec["key_id"] == key_id for rec in ssh_records) else api_keys[key_id][0]
        ok(f"Provisioned {record_type:12s} → {key_id}  →  {vault_instance}")

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

    if ssh_records:
        step("Provisioning SSH keys")
    for record in ssh_records:
        key_id = record["key_id"]
        if key_id not in phase2_material:
            continue
        phase2_entry = phase2_material[key_id]
        vault_instance = phase2_entry["vault_instance"]
        try:
            if record["key_source"] == "generated":
                keys_payload[key_id] = provision_generated_ssh_key(
                    worker_url=worker_url,
                    headers=_worker_control_headers(setup_token),
                    key_id=key_id,
                    adapters=key_adapters_by_key_id[key_id],
                    allowed_host_fingerprints=record["policy"]["allow"].get("hosts"),
                    vault_instance=vault_instance,
                )
            else:
                keys_payload[key_id] = provision_imported_ssh_key(
                    worker_url=worker_url,
                    headers=_worker_control_headers(setup_token),
                    key_id=key_id,
                    adapters=key_adapters_by_key_id[key_id],
                    allowed_host_fingerprints=record["policy"]["allow"].get("hosts"),
                    vault_instance=vault_instance,
                    public_key_pem=phase2_entry["public_key_pem"],
                    raw_secret=_resolve_manifest_secret(record["secret_ref"]),
                )
        except SshBootstrapError as exc:
            warn(f"{key_id}: SSH provisioning failed")
            die(str(exc))
        ok(
            f"Provisioned {'ssh':12s} → {key_id}  →  "
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
    SSH agent socket (host): {operator_ssh_auth_sock()}
    Pause/unpause: Worker management API via SUBUMBRA_MANAGEMENT_TOKEN
    Revoke key:    ./bootstrap.sh --revoke-key <key_id> [--offline]
                   (--offline: keys.json only; then re-run without --offline for KV delete)
    SSH day-2:     ./bootstrap.sh --add-ssh-key <key_id> --adapters <csv> [--allow-hosts <csv>]
                   ./bootstrap.sh --rotate-ssh-key <key_id> [--allow-hosts <csv>]
                   ./bootstrap.sh --revoke-ssh-key <key_id>
    Adapter edit:  ./bootstrap.sh --add-adapter <key_id> <adapter_id>
                   ./bootstrap.sh --revoke-adapter <key_id> <adapter_id>
    Policy publish: ./bootstrap.sh --publish-policy <key_id>
    Targeted repair: ./bootstrap.sh --provision <key_id>
"""))

