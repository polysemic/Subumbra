# Subumbra Adapter Contract

*Canonical reference for the Subumbra core API.*
*Source: `worker/src/worker.js` — see implementation for current behavior.*

---

## Overview

Subumbra is a zero-trust key-broker core. The decrypt/proxy contract is:

- **Adapters** request narrow capability by supplying a V3 envelope, a target
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

## Prerequisites — Obtaining a V3 Envelope

Before an adapter can call `POST /proxy`, it must first obtain a V3 envelope
record from `subumbra-keys`.

Request:

```text
GET /keys/<key_id>
```

Required headers:

```text
X-Subumbra-Token: <app adapter token such as SUBUMBRA_TOKEN_LITELLM or SUBUMBRA_TOKEN_OPENWEBUI>
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
| `enc_version` | number | Must be `3` |
| `vault_instance` | string | Vault routing identity: shared keys use `vault`; unique keys use `vault-<key_id>` |

Adapters are expected to derive the canonical `/proxy` payload fields from this
subumbra-keys response before making the Worker call.

---

## `POST /proxy` — Adapter Responsibilities

Authentication header (required on every request):

```
X-Subumbra-Token: <adapter token — SUBUMBRA_TOKEN_<APP> from .env>
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
| `intent` | object, optional | Optional request-side attestation metadata. Transparent-path callers may supply this via the `X-Subumbra-Intent-*` headers documented below; direct callers may send the canonical top-level object themselves. |
| `wrapped_dek` | string | RSA-OAEP-wrapped per-record DEK, base64 |
| `pub_key_fp` | string | SHA-256 fingerprint of the RSA key pair used for wrapping (`sha256:<hex>`) |
| `policy_hash` | string | V3 policy-binding hash from the live record |
| `key_id` | string | Record identity; used as AAD: `subumbra:v3:<key_id>:<policy_hash>` |
| `enc_version` | number | Must be `3`; V2 records are hard-rejected |
| `vault_instance` | string | Target vault instance for decrypt/rotate routing |

---

### Optional `intent` Field

When present, `intent` is a top-level field in the canonical Worker `/proxy`
payload:

```json
"intent": {
  "source": "<string or null>",
  "trust": {
    "allowed_initiators": ["..."],
    "allowed_content_sources": ["..."]
  }
}
```

R48 activates optional request-side guardrails only:

- `intent.trust.allowed_initiators`
- `intent.trust.allowed_content_sources`

Missing `intent` remains accepted by default in R48. A later round may add
stricter opt-in blocking semantics, but that is not part of the current
runtime contract.

### Transparent-Path `intent` Carrier

Transparent `/t/<key_id>/...` callers can supply the same metadata with these
Subumbra-specific headers:

- `X-Subumbra-Intent-Source`
- `X-Subumbra-Intent-Initiators`
- `X-Subumbra-Intent-Content-Sources`

Header value rules:

- `X-Subumbra-Intent-Source`: single string
- `X-Subumbra-Intent-Initiators`: comma-separated list
- `X-Subumbra-Intent-Content-Sources`: comma-separated list

`subumbra-proxy` converts those headers into the canonical top-level `intent`
object sent to the Worker and strips the raw `X-Subumbra-*` headers before any
upstream provider fetch.

---

## Worker-Core Guarantees

The Worker enforces these security invariants before any upstream request is made:

1. **Token authentication** — `X-Subumbra-Token` is resolved against the live
   adapter-token registry via timing-safe comparison; any mismatch is rejected
   before parsing the body.

2. **SSRF prevention** — `target_url` hostname must appear in the live provider
   registry stored in Cloudflare KV; unlisted hostnames are rejected with 403.
   Rejected with a warning log, no details surfaced to the caller.

3. **Provider/host consistency** — declared `provider` must match the live
   registry entry for the `target_url` hostname; mismatch rejected with 400.
   Prevents decrypting one provider's key and sending it to a different
   provider's endpoint.

4. **Encryption** — only V3 (`enc_version: 3`) records are accepted. V2
   records are rejected with `410 Gone` and body
   `{"error":"enc_version 2 not supported — run --rotate-policy to upgrade"}`.

5. **Decryption** — the `SubumbraVault` Durable Object loads the custody row it
   generated during `/setup/keygen`, RSA-OAEP unwraps the per-record DEK, and
   AES-256-GCM decrypts the ciphertext with V3 policy-binding AAD
   `subumbra:v3:<key_id>:<policy_hash>`. The request `pub_key_fp` must match
   the vault-stored public-key fingerprint.

6. **Auth injection** — the Durable Object reads `auth.scheme` from the live
   policy entry and dispatches upstream auth as follows:
   `bearer` injects `Authorization: Bearer <key>`;
   `basic` injects `Authorization: Basic <btoa(key + ':')>`;
   `header` injects the custom header named by `auth.header_name`;
   `query` injects the query parameter named by `auth.query_param`.
   The adapter never receives or sees the decrypted API key.

7. **Header stripping** — the Worker strips all hop-by-hop headers and all
   `X-Subumbra-*` / `CF-Access-*` headers before the upstream fetch.

8. **Streaming** — `POST /proxy` streams the Durable Object response body back
   to the caller unchanged. Callers do not need to buffer the full upstream
   response.

9. **Body-size enforcement** — `allow.max_body_bytes` is enforced against the
   UTF-8 byte length of the JSON-serialized outbound body.

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
| V2 `enc_version` (deprecated) | 410 |
| Non-V3 `enc_version` or missing `wrapped_dek` | 400 |
| Invalid JSON body | 400 |
| Decryption failure (generic) | 500 |
| RSA fingerprint mismatch | 500 |
| CF Secrets not configured | 503 |
| Provider registry binding missing / key missing / invalid | 503 |

---

## Health Surface

Both the Worker and `subumbra-proxy` expose an unauthenticated minimal health
payload:

```json
{"status":"ok"}
```

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
- Send `enc_version` other than `3` — rejected with 400/410.
- Expect the decrypted API key in any response field — the core never returns it.

---

## App-Owned Integrations

The current supported app-owned contract is the transparent sidecar path:

- app presents an adapter token in `Authorization` or `X-API-Key`
- app points to `api_base: http://subumbra-proxy:8090/t/<key_id>/...`
- `subumbra-proxy` uses the first path segment after `/t/` as the requested
  `key_id`
- `subumbra-keys` enforces the adapter's `allowed_keys`
- the proxy forwards the caller token to the Worker so `allow.adapters`
  enforcement stays app-specific on request-time proxying

This is the current primary adapter path for standalone LiteLLM and similar
external apps.

`litellm/custom_callbacks.py` remains in the repo as a legacy callback-era
implementation reference, but it is no longer the current primary integration
contract.

---

## R45 Policy Schema

Starting in Round 45, each secret record is backed by a declarative policy
document. The policy schema governs what the holder of a token is permitted to
do with the record it unlocks. Bootstrap ingestion (R45-2) validates incoming
policy documents; the Worker (R45-4 onward) enforces the allow block.

### Protocols

Two protocol values are supported:

- `openai_compatible` — upstream implements the OpenAI chat/completions API shape
- `http_rest` — upstream is a generic REST endpoint (not OpenAI-compatible)

### Required Fields

```json
{
  "key_id": "<string> — matches the subumbra-keys record key_id",
  "policy_id": "<string> — stable identifier for this policy document",
  "protocol": "openai_compatible | http_rest",
  "capability_class": "<enum> — see Capability Class below",
  "source": "env | import_path",
  "target": {
    "host": "<string> — exact FQDN, no wildcard, no scheme, no path",
    "base_path": "<string, optional> — common prefix for all allowed paths"
  },
  "auth": {
    "scheme": "bearer | basic | header | query"
  },
  "allow": {
    "adapters": ["<adapter_id>", ...],
    "methods": ["GET" | "POST" | "PUT" | "PATCH" | "DELETE", ...],
    "path_prefixes": ["/<prefix>", ...],
    "content_types": ["application/json", ...],
    "max_body_bytes": <integer>
  }
}
```

Optional fields:

```json
{
  "target": {
    "base_path": "/v1"
  },
  "deny": {
    "path_prefixes": ["/<prefix>", ...]
  },
  "intent": {},
  "response": {},
  "velocity": {}
}
```

### Capability Class

Required enum. Every policy must declare exactly one capability class.

| Value | Intended use |
|-------|-------------|
| `llm` | Large language model inference |
| `payments_read` | Payment platform read-only calls |
| `payments_write` | Payment platform write calls (charges, refunds) |
| `source_control_read` | Source control read (repos, issues, PRs) |
| `source_control_write` | Source control write (commits, PRs, webhooks) |
| `email_send` | Outbound email delivery |
| `webhook_verify` | Inbound webhook signature verification |
| `custom_rest` | Generic REST endpoint not covered by the above |

`capability_class` is used as a vocabulary for audit output and future
`intent.policy_match` enforcement. It must appear in the policy document even
if not yet enforced at runtime.

### Auth Schemes

| Scheme | Behavior |
|--------|----------|
| `bearer` | `Authorization: Bearer <key>` |
| `basic` | `Authorization: Basic base64(<key>:)` |
| `header` | Custom header; header name specified as `auth.header_name` in the policy |
| `query` | Query parameter; requires `auth.query_param` and `auth.allow_query: true` in the policy; see Query Auth Acknowledgement below |

### Query Auth Acknowledgement

`query` auth passes the decrypted key as a URL query parameter. This has
elevated exposure risk (logs, referrer headers, browser history). A policy that
uses `auth.scheme: "query"` must also include:

```json
"auth": {
  "scheme": "query",
  "query_param": "<param_name>",
  "allow_query": true
}
```

A policy with `auth.scheme: "query"` that omits `allow_query: true` is rejected
at bootstrap ingestion.

### Rejection Rules

Bootstrap ingestion must reject any policy document that fails these rules.
None of these can be overridden by any per-record configuration.

| Rule | Reason |
|------|--------|
| `target.host` is `"*"` or contains `*` | Wildcard host defeats SSRF protection |
| `allow.adapters` is empty or absent | No adapter can use the record |
| `allow.methods` is empty or absent | No call can succeed |
| `allow.path_prefixes` is empty or absent | No path can be called |
| `allow.path_prefixes` contains `"/"` alone | Equivalent to allow all — rejected |
| `allow.path_prefixes` contains `"*"` or `""` | Wildcard or empty prefix — rejected |
| `auth.scheme: "query"` without `allow_query: true` | Query auth requires explicit opt-in |

### Reserved Blocks

The `intent`, `response`, and `velocity` blocks are reserved in the schema. They
must parse without error if present.

- R48 activates request-side `intent.trust.*` guardrails.
- Response-side `response.deny_patterns` enforcement is still deferred to R48-5.
- `velocity` remains reserved and not enforced at runtime.

```json
"intent": {
  "policy_match": "<pattern or null>",
  "trust": {
    "allowed_initiators": ["user", "agent", "schedule"],
    "allowed_content_sources": ["direct", "retrieved", "injected"]
  }
},
"response": {
  "deny_patterns": ["<pattern>", ...]
},
"velocity": {}
```

#### Safe Pattern Vocabulary

`intent.policy_match` and `response.deny_patterns` accept string patterns.
These patterns are validated at bootstrap ingestion. Patterns outside the safe
vocabulary are rejected before the record is written.

**Allowed:**
- Anchored literal equality: `^exact-string$`
- Anchored bounded alternation: `^(value1|value2|value3)$` — the alternation
  must be the entire pattern (anchored both ends); each alternative must be a
  literal string with no metacharacters

**Forbidden (rejected at ingestion):**
- Unbounded quantifiers: `.*`, `.+`, `\w+`, `\d+`, `[^...]+` — ReDoS risk
- Unanchored patterns (no leading `^` or trailing `$`)
- Lookaheads or lookbehinds
- Backreferences
- Nested groups
- Any character class with unbounded repetition

### V3 Record and AAD Construction

Starting in R45-3, new records use V3 format. The V3 AAD binds the ciphertext
to both the record identity and the policy in effect at encryption time:

```
AAD = "subumbra:v3:<key_id>:<policy_hash>"
```

Where:
- `key_id` is the record's `key_id` string
- `policy_hash` is the lowercase hex SHA-256 digest of the canonical JSON
  **baseline binding object** below: UTF-8 encoded, object keys sorted
  lexicographically, no trailing newline

Baseline binding object:

```json
{
  "key_id": "<key_id>",
  "target": {
    "host": "<target.host>"
  },
  "auth": {
    "scheme": "<auth.scheme>",
    "header_name": "<auth.header_name when present>",
    "query_param": "<auth.query_param when present>",
    "allow_query": true
  },
  "allow": {
    "adapters": ["<sorted adapter ids>"],
    "methods": ["<sorted methods>"],
    "path_prefixes": ["<sorted prefixes>"],
    "content_types": ["<sorted content types>"],
    "max_body_bytes": 1048576
  }
}
```

Only the fields shown above enter the R45-3 `policy_hash`. Future
operator-selectable extra binding controls remain deferred beyond this round.

V3 record fields (additions to V2):

| Field | Type | Description |
|-------|------|-------------|
| `enc_version` | number | `3` for V3 records |
| `policy_id` | string | The `policy_id` from the backing policy document |
| `policy_hash` | string | `<hex>` — SHA-256 of the canonical baseline binding object above |

V2 records are rejected at `handleProxy` entry with HTTP 410 and body
`{"error":"enc_version 2 not supported — run --rotate-policy to upgrade"}`.
Use full bootstrap for actual V2 migration.

### Policy Starter Templates

#### Minimal `openai_compatible` template

```json
{
  "key_id": "REPLACE_WITH_KEY_ID",
  "policy_id": "REPLACE_WITH_POLICY_ID",
  "protocol": "openai_compatible",
  "capability_class": "llm",
  "source": "env",
  "target": {
    "host": "REPLACE_WITH_PROVIDER_HOST"
  },
  "auth": {
    "scheme": "bearer"
  },
  "allow": {
    "adapters": ["subumbra-proxy"],
    "methods": ["POST"],
    "path_prefixes": ["/v1/chat/completions"],
    "content_types": ["application/json"],
    "max_body_bytes": 1048576
  }
}
```

#### Minimal `http_rest` template

```json
{
  "key_id": "REPLACE_WITH_KEY_ID",
  "policy_id": "REPLACE_WITH_POLICY_ID",
  "protocol": "http_rest",
  "capability_class": "custom_rest",
  "source": "env",
  "target": {
    "host": "REPLACE_WITH_HOST"
  },
  "auth": {
    "scheme": "bearer"
  },
  "allow": {
    "adapters": ["subumbra-proxy"],
    "methods": ["GET", "POST"],
    "path_prefixes": ["/REPLACE_WITH_BASE_PATH"],
    "content_types": ["application/json"],
    "max_body_bytes": 524288
  }
}
```
