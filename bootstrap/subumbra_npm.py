#!/usr/bin/env python3
"""npm token helpers for Subumbra bootstrap day-2 flows."""

from __future__ import annotations

from subumbra_core import (
    _build_fat_record,
    _load_keys_payload_or_die,
    _prompt_hidden_line,
    _require_fat_record_fields,
    _verify_embedded_policy_hash,
    _write_keys_payload,
    compute_policy_hash,
    die,
    encrypt_api_key_v3,
    info,
    ok,
    step,
    timezone,
    datetime,
    gc,
    os,
    wrap_dek,
)


def build_npm_token_record(
    *,
    key_id: str,
    provider: str,
    target_host: str,
    raw_secret: str,
    policy: dict[str, object],
    adapters: list[str],
    vault_instance: str,
    pub_key,
    pub_key_fp: str,
    label: str,
    created_at: str,
) -> dict[str, object]:
    policy_hash = compute_policy_hash(policy)
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
        created_at=created_at,
        label=label,
        record_type="npm_token",
    )


def rewrite_npm_token_record_from_plaintext(
    *,
    key_id: str,
    existing_record: dict[str, object],
    raw_secret: str,
    policy: dict[str, object],
    adapters: list[str],
) -> dict[str, object]:
    from subumbra_keys import _load_existing_public_key_for_record

    provider = str(existing_record.get("provider", "")).strip()
    if not provider:
        die(f"keys.json record {key_id!r} missing provider")
    target_host = str(policy.get("target", {}).get("host", "")).strip()
    if not target_host:
        die(f"policy for {key_id!r} is missing target.host")
    vault_instance, pub_key, pub_key_fp = _load_existing_public_key_for_record(key_id, existing_record)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return build_npm_token_record(
        key_id=key_id,
        provider=provider,
        target_host=target_host,
        raw_secret=raw_secret,
        policy=policy,
        adapters=adapters,
        vault_instance=vault_instance,
        pub_key=pub_key,
        pub_key_fp=pub_key_fp,
        label=str(existing_record.get("label", key_id)),
        created_at=now_iso,
    )


def run_rotate_npm_token(target_key_id: str) -> None:
    from subumbra_keys import _require_existing_active_record

    print("Subumbra npm token rotation\n", flush=True)
    existing_keys = _load_keys_payload_or_die()
    existing_record = _require_existing_active_record(existing_keys, target_key_id)
    if existing_record.get("type") != "npm_token":
        die(f"--rotate-npm-token requires a type 'npm_token' record. key_id {target_key_id!r} is {existing_record.get('type', 'api_key')!r}.")
    _require_fat_record_fields(existing_record, target_key_id)
    _verify_embedded_policy_hash(existing_record, target_key_id)

    vault_instance = existing_record.get("vault_instance", "vault")
    if not isinstance(vault_instance, str) or not vault_instance:
        die(f"--rotate-npm-token requires vault_instance on the existing V3 record for key_id {target_key_id!r}.")
    policy, adapters = _require_fat_record_fields(existing_record, target_key_id)

    print(f"  Rotating npm token: {target_key_id}")

    while True:
        new_token = _prompt_hidden_line(f"new npm token for {target_key_id!r}")
        if not new_token:
            print("  ✗  npm token cannot be empty.")
            continue
        confirm_token = _prompt_hidden_line(f"same new npm token again to confirm for {target_key_id!r}")
        if new_token != confirm_token:
            print("  ✗  Tokens do not match. Try again.")
            continue
        break

    step(f"Encrypting new npm token for {target_key_id}")
    record = rewrite_npm_token_record_from_plaintext(
        key_id=target_key_id,
        existing_record=existing_record,
        raw_secret=new_token,
        policy=policy,
        adapters=adapters,
    )
    ok(f"Encrypted npm token  → {target_key_id}")

    new_token = "\x00" * len(new_token)
    del new_token
    del confirm_token
    gc.collect()

    step(f"Updating {target_key_id} in keys.json")
    existing_keys[target_key_id] = record
    _write_keys_payload(existing_keys)
    ok(f"Updated {target_key_id} — only this record changed")
    info("All other records are untouched")
    info("No Cloudflare interaction, no runtime token changes")
    info("subumbra-keys will serve the new record on next request")
