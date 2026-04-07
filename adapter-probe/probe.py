import hashlib
import hmac
import json
import os
import sys
import time

import httpx


FORGE_ACCESS_TOKEN = os.environ.get("FORGE_ACCESS_TOKEN", "")
FORGE_HMAC_KEY = os.environ.get("FORGE_HMAC_KEY", "")
FORGE_URL = os.environ.get("FORGE_URL", "")
CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "").rstrip("/")

REQUIRED_ENV = {
    "FORGE_ACCESS_TOKEN": FORGE_ACCESS_TOKEN,
    "FORGE_HMAC_KEY": FORGE_HMAC_KEY,
    "FORGE_URL": FORGE_URL,
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
    "openai_prod": {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Say test"}],
        "max_tokens": 10,
    },
    "groq_prod": {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": "Say test"}],
        "max_tokens": 10,
    },
    "deepseek_prod": {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "Say test"}],
        "max_tokens": 10,
    },
    "anthropic_prod": {
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


def forge_headers(key_id):
    timestamp = str(int(time.time()))
    signature = hmac.new(
        FORGE_HMAC_KEY.encode(),
        f"{key_id}:{timestamp}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Forge-Token": FORGE_ACCESS_TOKEN,
        "X-Forge-Timestamp": timestamp,
        "X-Forge-Signature": signature,
    }


def fetch_record(client, key_id):
    response = client.get(f"{FORGE_URL}/keys/{key_id}", headers=forge_headers(key_id))
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
        fail(f"{key_id}: forge record missing fields: {', '.join(missing)}")
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
        "body": PROVIDER_PAYLOADS[key_id],
        "wrapped_dek": record["wrapped_dek"],
        "pub_key_fp": record["pub_key_fp"],
        "key_id": record["key_id"],
        "enc_version": record["enc_version"],
    }


def worker_headers():
    return {
        "Content-Type": "application/json",
        "X-Forge-Token": FORGE_ACCESS_TOKEN,
    }


def run_provider_success(client, key_id):
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
            "X-Forge-Token": FORGE_ACCESS_TOKEN,
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
    keys = ["openai_prod", "groq_prod", "anthropic_prod", "deepseek_prod"]
    with httpx.Client() as client:
        records = {}
        for key_id in keys:
            records[key_id] = run_provider_success(client, key_id)
        run_provider_mismatch(client, records["openai_prod"], "openai_prod")
        run_ssrf_check(client, records["openai_prod"], "openai_prod")
        run_worker_non_json(client)
    print("PASS adapter-probe complete")


if __name__ == "__main__":
    main()
