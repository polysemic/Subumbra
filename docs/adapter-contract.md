# Subumbra Adapter Contract

*Canonical reference for the Subumbra core API.*
*Source: `worker/src/worker.js` — see implementation for current behavior.*

---

## Overview

Subumbra is a zero-trust key-broker core. The decrypt/proxy contract is:

- **Adapters** request narrow capability by supplying a V2 envelope, a target
  destination, and transport payload.
- **The Worker/core** authenticates, validates, decrypts, injects auth, and
  proxies — the adapter never sees the decrypted API key.

---

## Routes

### `POST /proxy` — Canonical Core API

The canonical Subumbra core API. The adapter owns the complete request and
provides a fully-qualified `target_url`. This is the interface future adapters
should target.

---

## Prerequisites — Obtaining a V2 Envelope

Before an adapter can call `POST /proxy`, it must first obtain a V2 envelope
record from `subumbra-keys`.

Request:

```text
GET /keys/<key_id>
```

Required headers:

```text
X-Subumbra-Token: <SUBUMBRA_ACCESS_TOKEN>
X-Subumbra-Timestamp: <unix epoch seconds>
X-Subumbra-Nonce: <single-use hex nonce>
X-Subumbra-Signature: <hex hmac>
```

Signature algorithm:

```text
HMAC-SHA256(f"{key_id}:{timestamp}:{nonce}", SUBUMBRA_HMAC_KEY)
```

Replay protection:

- single-use nonce per fetch
- timestamp window remains approximately `±30s`

Expected subumbra-keys response fields:

| Field | Type | Description |
|---|---|---|
| `key_id` | string | Envelope record identity |
| `ciphertext` | string | AES-256-GCM encrypted API key, base64 |
| `provider` | string | Provider identity for mismatch protection |
| `target_host` | string | Provider hostname used to derive `target_url` |
| `wrapped_dek` | string | RSA-OAEP-wrapped per-record DEK, base64 |
| `pub_key_fp` | string | RSA key-pair fingerprint (`sha256:<hex>`) |
| `enc_version` | number | Must be `2` |

Adapters are expected to derive the canonical `/proxy` payload fields from this
subumbra-keys response before making the Worker call.

---

## `POST /proxy` — Adapter Responsibilities

Authentication header (required on every request):

```
X-Subumbra-Token: <SUBUMBRA_ACCESS_TOKEN>
```

Request body (JSON, all fields required unless noted):

| Field | Type | Description |
|---|---|---|
| `ciphertext` | string | AES-256-GCM encrypted API key, base64; from subumbra-keys record |
| `provider` | string | Provider identity; must match the live provider-registry entry for the `target_url` hostname |
| `target_url` | string | Full `https://` URL including path and query — adapter-owned |
| `method` | string | HTTP method for the upstream call (e.g. `"POST"`) |
| `headers` | object | Headers to forward to the upstream; adapter must include all provider-required headers (e.g. `content-type`, `anthropic-version`); the Worker strips hop-by-hop headers before forwarding |
| `body` | JSON-serializable or null | Request body; must be null for GET/HEAD; see payload limitation below |
| `wrapped_dek` | string | RSA-OAEP-wrapped per-record DEK, base64 |
| `pub_key_fp` | string | SHA-256 fingerprint of the RSA key pair used for wrapping (`sha256:<hex>`) |
| `key_id` | string | Record identity; used as AAD: `subumbra:v2:<key_id>` |
| `enc_version` | number | Must be `2`; non-V2 records are hard-rejected |

---

## Worker-Core Guarantees

The Worker enforces these security invariants before any upstream request is made:

1. **Token authentication** — `X-Subumbra-Token` validated against `SUBUMBRA_ACCESS_TOKEN`
   via timing-safe comparison; any mismatch is rejected before parsing the body.

2. **SSRF prevention** — `target_url` hostname must appear in the live provider
   registry stored in Cloudflare KV; unlisted hostnames are rejected with 403.
   Rejected with a warning log, no details surfaced to the caller.

3. **Provider/host consistency** — declared `provider` must match the live
   registry entry for the `target_url` hostname; mismatch rejected with 400.
   Prevents decrypting one provider's key and sending it to a different
   provider's endpoint.

4. **V2 enforcement** — `enc_version !== 2` or missing `wrapped_dek` is
   hard-rejected with 400.

5. **Decryption** — RSA-OAEP unwraps the per-record DEK using `WORKER_PRIVATE_KEY`
   (from CF Secrets); AES-256-GCM decrypts the ciphertext with
   AAD `subumbra:v2:<key_id>`; pub_key_fp fingerprint must match
   `WORKER_KEY_FINGERPRINT` (from CF Secrets). Fingerprint mismatch surfaced
   in the error message to help operators diagnose key rotation issues.

6. **Auth injection** — the Durable Object injects provider-specific auth
   headers using auth policy from the live provider registry (`auth_header`,
   `auth_prefix`). The adapter never receives or sees the decrypted API key.

7. **Header stripping** — the Worker strips all hop-by-hop headers and all
   `X-Subumbra-*` / `CF-Access-*` headers before the upstream fetch.

8. **Streaming** — `POST /proxy` streams the Durable Object response body back
   to the caller unchanged. Callers do not need to buffer the full upstream
   response.

---

## Provider Registry Freshness

The live provider registry is read from Cloudflare KV with a bounded cache TTL.

- Newly published provider entries should become visible without Worker
  redeployment.
- Expect about 90 seconds worst-case before every Worker isolate sees a new
  entry.
- Adapter-facing request shape does not change.

---

## Error Responses

All error responses use `Content-Type: application/json` with body
`{"error": "<message>"}`.

| Condition | HTTP Status |
|---|---|
| Token missing or invalid | 401 |
| `target_url` not in the live provider registry | 403 |
| Provider/host mismatch | 400 |
| Missing or malformed required field | 400 |
| Non-V2 `enc_version` or missing `wrapped_dek` | 400 |
| Invalid JSON body | 400 |
| Decryption failure (generic) | 500 |
| RSA fingerprint mismatch | 500 (message includes detail) |
| CF Secrets not configured | 503 |
| Provider registry binding missing / key missing / invalid | 503 |

---

## Current Payload Limitation

The current core supports **JSON-style upstream request bodies only**.

- The `POST /proxy` request body is a JSON envelope; `body` is a
  JSON-serializable value (or null).
- The Durable Object serializes `body` with `JSON.stringify(body)` before the
  upstream fetch — binary values cannot be embedded.
- LiteLLM frontend may reject malformed external requests before the transport
  runs.
- The current app-owned transparent path rejects outbound non-JSON provider
  request content-types with HTTP 400 before `/proxy` packaging.
- Worker `/proxy` rejects malformed outer request bodies with
  `400 request body must be JSON`.

**Out of scope for the current core:** multipart form data, binary file uploads,
audio transcription, image uploads. These require future work on the DO upstream
fetch path.

---

## Headers Field Note

In `POST /proxy`, the `headers` object is **adapter-owned**. The adapter must
explicitly include all headers the upstream provider requires (e.g.
`content-type: application/json`, provider-specific version headers).

---

## What Adapters Must NOT Do

- Attempt to pass `target_url` hostnames not in the live provider registry — rejected
  with 403.
- Declare a `provider` that does not match the hostname's registry entry —
  rejected with 400.
- Send `enc_version` other than `2` — rejected with 400.
- Expect the decrypted API key in any response field — the core never returns it.

---

## App-Owned Integrations

The current supported app-owned contract is the transparent sidecar path:

- app presents an adapter token in `Authorization` or `X-API-Key`
- app points to `api_base: http://subumbra-proxy:8090/t/<key_id>/...`
- `subumbra-proxy` uses the first path segment after `/t/` as the requested
  `key_id`
- `subumbra-keys` enforces the adapter's `allowed_keys`

This is the current primary adapter path for standalone LiteLLM and similar
external apps.

`litellm/custom_callbacks.py` remains in the repo as a legacy callback-era
implementation reference, but it is no longer the current primary integration
contract.
