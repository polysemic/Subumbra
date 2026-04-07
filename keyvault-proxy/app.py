import hashlib
import hmac
import logging
import os
import sys
import time
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.background import BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


FORGE_ACCESS_TOKEN = os.environ.get("FORGE_ACCESS_TOKEN", "")
FORGE_HMAC_KEY = os.environ.get("FORGE_HMAC_KEY", "")
FORGE_URL = os.environ.get("FORGE_URL", "").rstrip("/")
CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "").rstrip("/")
CF_ACCESS_CLIENT_ID = os.environ.get("CF_ACCESS_CLIENT_ID", "")
CF_ACCESS_CLIENT_SECRET = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")

REQUIRED = ("FORGE_ACCESS_TOKEN", "FORGE_HMAC_KEY", "FORGE_URL", "CF_WORKER_URL")
MISSING = [name for name in REQUIRED if not os.environ.get(name)]
if MISSING:
    print(f"ERROR: missing env vars: {MISSING}", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("keyvault-proxy")

STRIP_HEADERS = {
    # Standard hop-by-hop (Round 25)
    "connection",
    "keep-alive",
    "transfer-encoding",
    "content-length",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
    # Security: upstream-domain cookie (Round 25)
    "set-cookie",
    # CF infrastructure metadata (Round 26 — explicit operator-usability policy)
    "cf-cache-status",
    "cf-ray",
    "nel",
    "report-to",
    "alt-svc",
    "server",
}

EXPECTED_RECORD_FIELDS = {
    "ciphertext",
    "provider",
    "target_host",
    "wrapped_dek",
    "pub_key_fp",
    "enc_version",
    "key_id",
}

app = FastAPI()
CLIENT = httpx.AsyncClient()


class ProxyRequest(BaseModel):
    key_id: str
    target_url: str
    method: str
    headers: dict
    body: Optional[Any] = None


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


async def fetch_record(client, key_id):
    response = await client.get(f"{FORGE_URL}/keys/{key_id}", headers=forge_headers(key_id))
    if response.status_code != 200:
        raise RuntimeError(f"status {response.status_code}")
    record = response.json()
    missing = sorted(field for field in EXPECTED_RECORD_FIELDS if field not in record)
    if missing:
        raise RuntimeError(f"missing fields: {', '.join(missing)}")
    return record


def proxy_payload(record, key_id, *, target_url, method, headers, body):
    return {
        "ciphertext": record["ciphertext"],
        "provider": record["provider"],
        "target_url": target_url,
        "method": method,
        "headers": headers,
        "body": body,
        "wrapped_dek": record["wrapped_dek"],
        "pub_key_fp": record["pub_key_fp"],
        "key_id": record["key_id"],
        "enc_version": record["enc_version"],
    }


def worker_headers():
    headers = {
        "Content-Type": "application/json",
        "X-Forge-Token": FORGE_ACCESS_TOKEN,
    }
    if CF_ACCESS_CLIENT_ID:
        headers["CF-Access-Client-Id"] = CF_ACCESS_CLIENT_ID
        headers["CF-Access-Client-Secret"] = CF_ACCESS_CLIENT_SECRET
    return headers


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/request")
async def handle_request(req: ProxyRequest):
    LOG.info(
        "request key_id=%s method=%s target_url=%s",
        req.key_id,
        req.method,
        req.target_url,
    )

    try:
        record = await fetch_record(CLIENT, req.key_id)
    except httpx.ConnectError:
        LOG.error("forge failure key_id=%s error=forge-keys unreachable", req.key_id)
        raise HTTPException(502, detail="forge-keys unreachable")
    except Exception as exc:
        LOG.error("forge failure key_id=%s error=%s", req.key_id, exc)
        raise HTTPException(502, detail=f"forge record fetch failed: {exc}")

    payload = proxy_payload(
        record,
        req.key_id,
        target_url=req.target_url,
        method=req.method,
        headers=req.headers,
        body=req.body,
    )

    worker_req = CLIENT.build_request(
        "POST",
        f"{CF_WORKER_URL}/proxy",
        headers=worker_headers(),
        json=payload,
        timeout=120.0,
    )
    worker_resp = await CLIENT.send(worker_req, stream=True)

    if worker_resp.status_code >= 400:
        LOG.warning("worker failure key_id=%s status=%s", req.key_id, worker_resp.status_code)

    response_headers = {
        key: value
        for key, value in worker_resp.headers.items()
        if key.lower() not in STRIP_HEADERS
    }

    tasks = BackgroundTasks()
    tasks.add_task(worker_resp.aclose)

    LOG.info("complete key_id=%s status=%s", req.key_id, worker_resp.status_code)
    return StreamingResponse(
        worker_resp.aiter_raw(),
        status_code=worker_resp.status_code,
        headers=response_headers,
        background=tasks,
    )
