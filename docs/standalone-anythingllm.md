# Standalone AnythingLLM Guide

*Canonical clean-install AnythingLLM app-owned Subumbra integration.*

AnythingLLM is not part of the core `/opt/subumbra` compose stack. The supported
model for this round is:

- Subumbra core runs in `/opt/subumbra`
- AnythingLLM runs in its own install, for example `/opt/anythingllm`
- AnythingLLM talks to `subumbra-proxy` over the OpenAI-compatible transparent path

## Scope

**Scope: Clean Install Path**

This round documents and verifies AnythingLLM configured for Subumbra from first
boot.

Existing-instance migration / takeover is deferred to
`round-43-1-anythingllm`.

## What This Round Proves

This guide covers the supported AnythingLLM path that has live proof for:

- chat through Subumbra
- embeddings through Subumbra
- zero-restart per-key rotation
- fail-closed behavior for invalid or unscoped keys

## Prerequisites

Before pointing AnythingLLM at Subumbra, confirm:

1. the Subumbra core stack is already running in `/opt/subumbra`
2. `subumbra-proxy` reports healthy Worker auth
3. the required `key_id` is already present in `PROXY_ALLOWED_KEYS`
4. AnythingLLM is attached to `subumbra-net`
5. you have an AnythingLLM management/API key available for verification

```bash
cd /opt/subumbra
docker compose ps
curl -sS http://127.0.0.1:8090/health
grep '^PROXY_ALLOWED_KEYS=' .env
```

Healthy proxy output should include:

```json
{"status":"ok","worker_auth":"ok"}
```

## Supported Compose / Env Configuration

The supported AnythingLLM Subumbra base is:

```text
http://subumbra-proxy:8090/t/v1
```

AnythingLLM appends its OpenAI-compatible endpoints after that base. Use this
env block in `/opt/anythingllm/docker-compose.yml`:

```yaml
environment:
  - LLM_PROVIDER=generic-openai
  - GENERIC_OPEN_AI_BASE_PATH=http://subumbra-proxy:8090/t/v1
  - GENERIC_OPEN_AI_API_KEY=openai_prod
  - GENERIC_OPEN_AI_EMBEDDING_API_KEY=openai_prod
  - GENERIC_OPEN_AI_MODEL_PREF=gpt-4o-mini
  - EMBEDDING_ENGINE=generic-openai
  - EMBEDDING_BASE_PATH=http://subumbra-proxy:8090/t/v1
  - EMBEDDING_MODEL_PREF=text-embedding-3-small
  - EMBEDDING_MODEL_MAX_CHUNK_LENGTH=8192
  - VECTOR_DB=lancedb
```

Two important rules:

1. `GENERIC_OPEN_AI_API_KEY` is the plain `key_id`
2. `GENERIC_OPEN_AI_EMBEDDING_API_KEY` is also required

Without `GENERIC_OPEN_AI_EMBEDDING_API_KEY`, chat can still work while the
embedding path falls back to a null key and fails through `subumbra-proxy`.

After editing the compose file, recreate the container so the new env actually
loads:

```bash
cd /opt/anythingllm
docker compose up -d --force-recreate anythingllm
```

Then confirm the container env:

```bash
docker exec anythingllm env | sort | grep -E 'GENERIC_OPEN_AI(_EMBEDDING)?_API_KEY|GENERIC_OPEN_AI_BASE_PATH|EMBEDDING_'
```

Expected output:

```text
EMBEDDING_BASE_PATH=http://subumbra-proxy:8090/t/v1
EMBEDDING_ENGINE=generic-openai
EMBEDDING_MODEL_MAX_CHUNK_LENGTH=8192
EMBEDDING_MODEL_PREF=text-embedding-3-small
GENERIC_OPEN_AI_API_KEY=openai_prod
GENERIC_OPEN_AI_BASE_PATH=http://subumbra-proxy:8090/t/v1
GENERIC_OPEN_AI_EMBEDDING_API_KEY=openai_prod
```

## RAM Note

The supported path uses:

```text
EMBEDDING_ENGINE=generic-openai
```

This avoids the local native embedder path and keeps embeddings inside the
Subumbra proof surface. In practice this is a lower-friction app path than
OpenWebUI because there is no persistence flag or DB scrub requirement for the
supported clean-install route.

Do not describe this as “just works on first boot.” The env still has to be set
correctly for both chat and embeddings.

## Embedding Timing Note

Embeddings happen at fresh ingestion / vectorization time, not during ordinary
chat requests.

That means embedding proof must be captured during a brand new ingest:

- create or choose a workspace
- upload a fresh unique document
- add/update embeddings for that document in the workspace
- capture the corresponding proxy logs

If you only send chat traffic, you may see the embedder initialize without ever
producing a live `/v1/embeddings` request.

## Operator Notes

- no custom headers are needed
- use plain `key_id` values such as `openai_prod`
- do **not** use raw provider keys in the supported Subumbra path
- AnythingLLM currently reports native tool calling disabled for
  `generic-openai`; this is an app limitation, not a Subumbra failure

## Verification API Key

The verification harness uses an AnythingLLM management/API key for the app’s
own HTTP API.

Generate one from the AnythingLLM admin UI if needed, then export it before
running the council verifier:

```bash
export ALLM_API_KEY='<anythingllm-api-key>'
```

The harness does **not** write to `anythingllm.db` to create or inject this key.

## Fresh Ingestion Proof Shape

The preferred proof path for embeddings is API-driven:

1. create or reuse a known test workspace
2. upload a fresh unique raw-text document through `/api/v1/document/raw-text`
3. add/update embeddings for that document in the workspace
4. capture proxy logs proving the embeddings request

If the live install proves version-fragile for the API path, a clearly labeled
manual fresh document upload is acceptable as a fallback for proof capture.

## Rotation Proof

The supported rotation proof is zero-restart:

```bash
cd /opt/subumbra
docker compose --profile bootstrap run --rm -T bootstrap --rotate openai_prod
```

Then send a fresh AnythingLLM request and confirm in `subumbra-proxy` logs that:

- `key_id=openai_prod`
- the request still succeeds
- no containers were restarted

Do **not** add `--force-recreate` to the rotation proof. Per-key rotation does
not require an AnythingLLM restart.

## Functional Checks

### Proxy health

```bash
curl -sS http://127.0.0.1:8090/health
```

Expected:

```json
{"status":"ok","worker_auth":"ok"}
```

### Chat proof

A successful AnythingLLM chat request should produce proxy logs like:

```text
request key_id=openai_prod method=POST target_url=https://api.openai.com/v1/chat/completions
complete key_id=openai_prod status=200
```

### Embedding proof

A fresh document ingest should produce proxy logs like:

```text
request key_id=openai_prod method=POST target_url=https://api.openai.com/v1/embeddings
complete key_id=openai_prod status=200
```

This is the round-blocking proof for AnythingLLM.

### Fail-closed check

An invalid or unscoped key ID must fail closed:

```bash
curl -sS -i \
  -H 'Authorization: Bearer definitely_not_allowed' \
  -H 'Content-Type: application/json' \
  -X POST \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"test"}]}' \
  http://127.0.0.1:8090/t/v1/chat/completions
```

Expected result: non-200 failure from the proxy path, typically `502`.

## Operator Checklist

1. Put the AnythingLLM key IDs you want to use into `PROXY_ALLOWED_KEYS` during bootstrap.
2. Confirm `subumbra-proxy` health is `worker_auth":"ok"`.
3. Set `GENERIC_OPEN_AI_BASE_PATH=http://subumbra-proxy:8090/t/v1`.
4. Set `GENERIC_OPEN_AI_API_KEY=<plain key_id>`.
5. Set `GENERIC_OPEN_AI_EMBEDDING_API_KEY=<plain key_id>`.
6. Set `EMBEDDING_ENGINE=generic-openai`.
7. Recreate AnythingLLM after env changes.
8. Confirm the live request paths in proxy logs.
