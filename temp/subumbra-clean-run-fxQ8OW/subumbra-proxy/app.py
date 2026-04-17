import hashlib
import hmac
import logging
import os
import re
import secrets
import sys
import time
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.background import BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


SUBUMBRA_ACCESS_TOKEN = os.environ.get("SUBUMBRA_ACCESS_TOKEN", "")
SUBUMBRA_HMAC_KEY = os.environ.get("SUBUMBRA_HMAC_KEY", "")
SUBUMBRA_KEYS_URL = os.environ.get("SUBUMBRA_KEYS_URL", "").rstrip("/")
CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "").rstrip("/")
CF_ACCESS_CLIENT_ID = os.environ.get("CF_ACCESS_CLIENT_ID", "")
CF_ACCESS_CLIENT_SECRET = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")

REQUIRED = ("SUBUMBRA_ACCESS_TOKEN", "SUBUMBRA_HMAC_KEY", "SUBUMBRA_KEYS_URL", "CF_WORKER_URL")
MISSING = [name for name in REQUIRED if not os.environ.get(name)]
if MISSING:
    print(f"ERROR: missing env vars: {MISSING}", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("subumbra-proxy")

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
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
TRANSPARENT_STRIP_HEADERS = {"authorization", "x-api-key", "x-api-key-id"}
TRANSPARENT_STRIP_PREFIXES = ("x-subumbra-",)
TRANSPARENT_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

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


async def fetch_record(client, key_id):
    response = await client.get(f"{SUBUMBRA_KEYS_URL}/keys/{key_id}", headers=subumbra_headers(key_id))
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
        "X-Subumbra-Token": SUBUMBRA_ACCESS_TOKEN,
    }
    if CF_ACCESS_CLIENT_ID:
        headers["CF-Access-Client-Id"] = CF_ACCESS_CLIENT_ID
        headers["CF-Access-Client-Secret"] = CF_ACCESS_CLIENT_SECRET
    return headers


def extract_transparent_key_id(headers: dict[str, str]) -> tuple[Optional[str], bool]:
    auth_value = None
    x_api_key_value = None
    for key, value in headers.items():
        lower = key.lower()
        if lower == "authorization":
            auth_value = value
        elif lower == "x-api-key":
            x_api_key_value = value

    parsed_auth = None
    if auth_value is not None:
        candidate = auth_value.strip()
        if candidate.lower().startswith("bearer"):
            candidate = candidate[6:].strip()
        if candidate:
            parsed_auth = candidate

    parsed_x_api_key = None
    if x_api_key_value is not None:
        candidate = x_api_key_value.strip()
        if candidate:
            parsed_x_api_key = candidate

    if parsed_auth is not None:
        return parsed_auth, parsed_x_api_key is not None
    if parsed_x_api_key is not None:
        return parsed_x_api_key, False
    return None, False


def validate_transparent_key_id(key_id: str) -> bool:
    return bool(KEY_ID_RE.fullmatch(key_id))


def strip_transparent_headers(headers: dict[str, str]) -> dict[str, str]:
    stripped = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in TRANSPARENT_STRIP_HEADERS:
            continue
        if any(lower.startswith(prefix) for prefix in TRANSPARENT_STRIP_PREFIXES):
            continue
        stripped[key] = value
    return stripped


def build_transparent_target_url(target_host: str, path: str, query: str) -> str:
    clean_path = path.lstrip("/")
    if clean_path:
        target_url = f"https://{target_host}/{clean_path}"
    else:
        target_url = f"https://{target_host}/"
    if query:
        target_url += f"?{query}"
    return target_url


async def proxy_via_worker(
    key_id: str,
    target_url: str,
    method: str,
    headers: dict,
    body: Optional[Any],
) -> StreamingResponse:
    LOG.info("request key_id=%s method=%s target_url=%s", key_id, method, target_url)

    try:
        record = await fetch_record(CLIENT, key_id)
    except httpx.ConnectError:
        LOG.error("subumbra failure key_id=%s error=subumbra-keys unreachable", key_id)
        raise HTTPException(502, detail="subumbra-keys unreachable")
    except Exception as exc:
        LOG.error("subumbra failure key_id=%s error=%s", key_id, exc)
        raise HTTPException(502, detail=f"subumbra record fetch failed: {exc}")

    payload = proxy_payload(
        record,
        key_id,
        target_url=target_url,
        method=method,
        headers=headers,
        body=body,
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
        LOG.warning("worker failure key_id=%s status=%s", key_id, worker_resp.status_code)

    response_headers = {
        key: value
        for key, value in worker_resp.headers.items()
        if key.lower() not in STRIP_HEADERS
    }

    tasks = BackgroundTasks()
    tasks.add_task(worker_resp.aclose)

    LOG.info("complete key_id=%s status=%s", key_id, worker_resp.status_code)
    return StreamingResponse(
        worker_resp.aiter_raw(),
        status_code=worker_resp.status_code,
        headers=response_headers,
        background=tasks,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/request")
async def handle_request(req: ProxyRequest):
    return await proxy_via_worker(
        req.key_id,
        req.target_url,
        req.method,
        req.headers,
        req.body,
    )


@app.api_route("/t/{path:path}", methods=TRANSPARENT_METHODS)
async def handle_transparent_request(path: str, request: Request):
    inbound_headers = dict(request.headers)
    key_id, dual_header_present = extract_transparent_key_id(inbound_headers)

    if key_id is None:
        LOG.warning("transparent reject reason=missing_pseudo_key")
        raise HTTPException(401, detail="missing pseudo-key")

    if not validate_transparent_key_id(key_id):
        LOG.warning("transparent reject reason=invalid_key_id")
        raise HTTPException(400, detail="invalid key_id")

    if dual_header_present:
        LOG.warning("transparent warning reason=authorization_precedence key_id=%s", key_id)

    body = None
    raw_body = await request.body()
    if raw_body:
        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("application/json"):
            LOG.warning(
                "transparent reject reason=unsupported_content_type key_id=%s",
                key_id,
            )
            raise HTTPException(400, detail="unsupported content-type")
        try:
            body = await request.json()
        except Exception:
            LOG.warning("transparent reject reason=invalid_json_body key_id=%s", key_id)
            raise HTTPException(400, detail="invalid JSON body")

    try:
        record = await fetch_record(CLIENT, key_id)
    except httpx.ConnectError:
        LOG.error("subumbra failure key_id=%s error=subumbra-keys unreachable", key_id)
        raise HTTPException(502, detail="subumbra-keys unreachable")
    except Exception as exc:
        LOG.error("subumbra failure key_id=%s error=%s", key_id, exc)
        raise HTTPException(502, detail=f"subumbra record fetch failed: {exc}")

    target_url = build_transparent_target_url(record["target_host"], path, request.url.query)
    stripped_headers = strip_transparent_headers(inbound_headers)
    return await proxy_via_worker(
        key_id,
        target_url,
        request.method,
        stripped_headers,
        body,
    )
