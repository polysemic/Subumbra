import hashlib
import hmac
import json
import os
import secrets
import sys
import time

import httpx


SUBUMBRA_ACCESS_TOKEN = os.environ.get("SUBUMBRA_ACCESS_TOKEN", "")
SUBUMBRA_HMAC_KEY = os.environ.get("SUBUMBRA_HMAC_KEY", "")
SUBUMBRA_KEYS_URL = os.environ.get("SUBUMBRA_KEYS_URL", "")
CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "").rstrip("/")
PROBE_ALLOWED_KEYS = os.environ.get("PROBE_ALLOWED_KEYS", "")
CF_ACCESS_CLIENT_ID = os.environ.get("CF_ACCESS_CLIENT_ID", "")
CF_ACCESS_CLIENT_SECRET = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")

REQUIRED_ENV = {
    "SUBUMBRA_ACCESS_TOKEN": SUBUMBRA_ACCESS_TOKEN,
    "SUBUMBRA_HMAC_KEY": SUBUMBRA_HMAC_KEY,
    "SUBUMBRA_KEYS_URL": SUBUMBRA_KEYS_URL,
    "CF_WORKER_URL": CF_WORKER_URL,
}

PROVIDER_PATHS = {
    "openai": "/v1/chat/completions",
    "groq": "/openai/v1/chat/completions",
    "deepseek": "/v1/chat/completions",
    "anthropic": "/v1/messages",
}

PROVIDER_HEADERS = {
    "openai": {"content-type": "application/json"},
    "groq": {"content-type": "application/json"},
    "deepseek": {"content-type": "application/json"},
    "anthropic": {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    },
}

PROVIDER_PAYLOADS = {
    "openai": {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Say test"}],
        "max_tokens": 10,
    },
    "groq": {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": "Say test"}],
        "max_tokens": 10,
    },
    "deepseek": {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "Say test"}],
        "max_tokens": 10,
    },
    "anthropic": {
        "model": "claude-haiku-4-5-20251001",
        "messages": [{"role": "user", "content": "Say test"}],
        "max_tokens": 10,
    },
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
    }
    missing = sorted(field for field in expected_fields if field not in record)
    if missing:
        fail(f"{key_id}: subumbra record missing fields: {', '.join(missing)}")
    return record


def target_url_for(record):
    provider = record["provider"]
    return f"https://{record['target_host']}{PROVIDER_PATHS[provider]}"


def proxy_payload(record, key_id, provider=None, target_url=None):
    provider_id = provider or record["provider"]
    return {
        "ciphertext": record["ciphertext"],
        "provider": provider_id,
        "target_url": target_url or target_url_for(record),
        "method": "POST",
        "headers": PROVIDER_HEADERS[record["provider"]],
        "body": PROVIDER_PAYLOADS[record["provider"]],
        "wrapped_dek": record["wrapped_dek"],
        "pub_key_fp": record["pub_key_fp"],
        "key_id": record["key_id"],
        "enc_version": record["enc_version"],
    }


def worker_headers():
    headers = {
        "Content-Type": "application/json",
        "X-Subumbra-Token": SUBUMBRA_ACCESS_TOKEN,
    }
    if CF_ACCESS_CLIENT_ID:
        headers["CF-Access-Client-Id"] = CF_ACCESS_CLIENT_ID
        headers["CF-Access-Client-Secret"] = CF_ACCESS_CLIENT_SECRET
    return headers


def run_provider_success(client, key_id, record=None):
    if record is None:
        record = fetch_record(client, key_id)
    payload = proxy_payload(record, key_id)
    response = client.post(
        f"{CF_WORKER_URL}/proxy",
        headers=worker_headers(),
        json=payload,
        timeout=120.0,
    )
    if response.status_code != 200:
        fail(
            f"{key_id}: expected 200, got {response.status_code}: {response.text[:300]}"
        )
    print(f"PASS provider {key_id}: HTTP 200")
    return record


def run_provider_mismatch(client, record, key_id):
    wrong_provider = "groq" if record["provider"] != "groq" else "openai"
    payload = proxy_payload(record, key_id, provider=wrong_provider)
    response = client.post(
        f"{CF_WORKER_URL}/proxy",
        headers=worker_headers(),
        json=payload,
    )
    if response.status_code != 400:
        fail(
            f"provider mismatch: expected 400, got {response.status_code}: {response.text[:300]}"
        )
    print("PASS provider mismatch: HTTP 400")


def run_ssrf_check(client, record, key_id):
    payload = proxy_payload(
        record,
        key_id,
        target_url="https://example.com/v1/chat/completions",
    )
    response = client.post(
        f"{CF_WORKER_URL}/proxy",
        headers=worker_headers(),
        json=payload,
    )
    if response.status_code != 403:
        fail(f"ssrf check: expected 403, got {response.status_code}: {response.text[:300]}")
    print("PASS ssrf rejection: HTTP 403")


def run_worker_non_json(client):
    response = client.post(
        f"{CF_WORKER_URL}/proxy",
        headers={
            "Content-Type": "text/plain",
            "X-Subumbra-Token": SUBUMBRA_ACCESS_TOKEN,
        },
        content=b"not json",
    )
    if response.status_code != 400:
        fail(
            f"worker non-json: expected 400, got {response.status_code}: {response.text[:300]}"
        )
    try:
        body = response.json()
    except Exception:
        fail(f"worker non-json: response was not json: {response.text[:300]}")
    if body.get("error") != "request body must be JSON":
        fail(f"worker non-json: unexpected body: {json.dumps(body)}")
    print("PASS worker non-json boundary: HTTP 400")


def main():
    require_env()
    with httpx.Client() as client:
        records = {}
        skipped = []
        for key_id in resolve_probe_key_ids():
            record = fetch_record(client, key_id)
            provider = record["provider"]
            if provider not in PROVIDER_PAYLOADS:
                skipped.append(f"{key_id} ({provider})")
                continue
            if provider in records:
                continue
            records[provider] = (key_id, run_provider_success(client, key_id, record=record))

        if not records:
            detail = ", ".join(skipped) if skipped else "none"
            fail(f"no compatible probe keys found; skipped unsupported providers: {detail}")

        baseline_provider = "openai" if "openai" in records else next(iter(records))
        baseline_key_id, baseline_record = records[baseline_provider]
        run_provider_mismatch(client, baseline_record, baseline_key_id)
        run_ssrf_check(client, baseline_record, baseline_key_id)
        run_worker_non_json(client)
    print("PASS subumbra-probe complete")


if __name__ == "__main__":
    main()
