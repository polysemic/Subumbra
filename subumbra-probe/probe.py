import hashlib
import hmac
import os
import secrets
import sys
import time

import httpx


SUBUMBRA_ACCESS_TOKEN = os.environ.get("SUBUMBRA_ACCESS_TOKEN", "")
SUBUMBRA_HMAC_KEY = os.environ.get("SUBUMBRA_HMAC_KEY", "")
SUBUMBRA_KEYS_URL = os.environ.get("SUBUMBRA_KEYS_URL", "")
PROBE_ALLOWED_KEYS = os.environ.get("PROBE_ALLOWED_KEYS", "")

REQUIRED_ENV = {
    "SUBUMBRA_ACCESS_TOKEN": SUBUMBRA_ACCESS_TOKEN,
    "SUBUMBRA_HMAC_KEY": SUBUMBRA_HMAC_KEY,
    "SUBUMBRA_KEYS_URL": SUBUMBRA_KEYS_URL,
}


def fail(message):
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def require_env():
    missing = [name for name, value in REQUIRED_ENV.items() if not value]
    if missing:
        fail(f"missing required env vars: {', '.join(missing)}")


def resolve_probe_key_ids():
    raw = PROBE_ALLOWED_KEYS.strip() or os.environ.get("PROBE_KEY_IDS", "").strip()
    key_ids = [item.strip() for item in raw.split(",") if item.strip()]
    if not key_ids:
        fail("no probe key_ids configured; set PROBE_ALLOWED_KEYS or PROBE_KEY_IDS")
    return key_ids


def subumbra_headers(key_id):
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = hmac.new(
        SUBUMBRA_HMAC_KEY.encode(),
        f"{key_id}:{timestamp}:{nonce}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Subumbra-Token": SUBUMBRA_ACCESS_TOKEN,
        "X-Subumbra-Timestamp": timestamp,
        "X-Subumbra-Nonce": nonce,
        "X-Subumbra-Signature": signature,
    }


def fetch_record(client, key_id):
    response = client.get(f"{SUBUMBRA_KEYS_URL}/keys/{key_id}", headers=subumbra_headers(key_id))
    response.raise_for_status()
    record = response.json()
    expected_fields = {
        "ciphertext",
        "provider",
        "target_host",
        "wrapped_dek",
        "pub_key_fp",
        "enc_version",
        "key_id",
        "policy_id",
        "policy_hash",
        "vault_instance",
    }
    missing = sorted(field for field in expected_fields if field not in record)
    if missing:
        fail(f"{key_id}: subumbra record missing fields: {', '.join(missing)}")
    return record


def main():
    require_env()
    with httpx.Client() as client:
        for key_id in resolve_probe_key_ids():
            record = fetch_record(client, key_id)
            print(
                "PASS key_id=%s provider=%s vault_instance=%s enc_version=%s"
                % (
                    key_id,
                    record["provider"],
                    record["vault_instance"],
                    record["enc_version"],
                )
            )
    print("PASS subumbra-probe complete")


if __name__ == "__main__":
    main()
