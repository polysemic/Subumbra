# AnythingLLM Takeover

*Canonical takeover path for moving an existing direct-provider AnythingLLM install
onto Subumbra.*

AnythingLLM is not part of the core `/opt/subumbra` compose stack. This guide
covers the **existing-instance takeover** path. For the clean-install path, see
[install.md](./install.md).

## Scope

This guide covers takeover of an existing direct-provider AnythingLLM install.

This round proves the OpenAI-compatible Generic OpenAI path only:

- chat routed through `subumbra-proxy`
- existing workspace continuity after LLM takeover
- fresh mediated embeddings after switching the embedder

Broader provider and workspace expansion is deferred to Round 44.

## Before-State Assumptions

Before takeover:

1. AnythingLLM is already running with direct OpenAI credentials
2. app data persists under `/opt/anythingllm/storage`
3. the app is not already using Subumbra for the proof path
4. `subumbra-proxy` is healthy
5. the AnythingLLM consumer token is available to the container

Useful checks:

```bash
cd /opt/subumbra
docker compose ps
curl -sS http://127.0.0.1:10199/health
```

Expected proxy health:

```json
{"status":"ok","worker_auth":"ok"}
```

## Target Env Shape After LLM Takeover

The supported takeover LLM path is:

```text
http://subumbra-proxy:8090/t/openai_prod/v1
```

Use the tracked LLM takeover template:

- [`templates/env-takeover-llm.env`](./templates/env-takeover-llm.env)

## Fresh Mediated Embedding Proof Shape

For fresh embeddings through Subumbra, use the tracked embedding takeover
template:

- [`templates/env-takeover-embed.env`](./templates/env-takeover-embed.env)

Changing the embedder or embedding model causes AnythingLLM to reset LanceDB
namespaces as part of the embedding transition. That reset is expected transition
behavior for this proof.

## Exact Cut-Over Steps

### Phase A — LLM takeover

1. Update `/opt/anythingllm/.env` to the Generic OpenAI LLM takeover shape.
2. Recreate the container:

```bash
cd /opt/anythingllm
docker compose up -d --force-recreate anythingllm
```

3. Confirm the live env inside the container:

```bash
docker exec anythingllm env | sort | grep -E 'LLM_PROVIDER|GENERIC_OPEN_AI_BASE_PATH|GENERIC_OPEN_AI_API_KEY'
```

Expected output includes:

```text
GENERIC_OPEN_AI_API_KEY=${SUBUMBRA_TOKEN_ANYTHINGLLM}
GENERIC_OPEN_AI_BASE_PATH=http://subumbra-proxy:8090/t/openai_prod/v1
LLM_PROVIDER=generic-openai
```

4. Verify an existing workspace still chats successfully and returns RAG context.

### Phase B — fresh mediated embedding proof

1. Update the embedding env vars to the Generic OpenAI embedding shape.
2. Recreate AnythingLLM if needed for your verification flow:

```bash
cd /opt/anythingllm
docker compose up -d --force-recreate anythingllm
```

3. Ingest one fresh document.
4. Confirm the proxy shows `/v1/embeddings`.
5. Confirm post-ingest chat/RAG still succeeds.

## What Persists vs What Changes

Persists:

- app data
- workspaces
- documents
- chat history

Changes:

- LLM routing moves to Subumbra
- after the embedding transition, future fresh ingestion uses Subumbra-mediated
  embeddings

## Workspace Provider Note

If a workspace was explicitly pinned to an older direct-provider mode, it may fail
after takeover until reset to “Default Provider” in the AnythingLLM UI.

This is an operator action, not a Subumbra core defect.

## Existing-Workspace Verification

After LLM takeover:

1. open an existing workspace
2. send a chat that should use existing document context
3. confirm the response succeeds
4. confirm source context or citations still appear
5. confirm `subumbra-proxy` logs show:

```text
target_url=https://api.openai.com/v1/chat/completions
```

## Fresh Mediated Embedding Verification

After switching the embedder:

1. upload one fresh unique document
2. confirm AnythingLLM logs include `GenericOpenAiEmbedder`
3. confirm `subumbra-proxy` logs show:

```text
target_url=https://api.openai.com/v1/embeddings
```

4. send a fresh chat or retrieval request
5. confirm chat/RAG still succeeds

## Fail-Closed Verification

An invalid consumer token must fail closed:

```bash
curl -sS -i \
  -H 'Authorization: Bearer definitely_not_allowed' \
  -H 'Content-Type: application/json' \
  -X POST \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"test"}]}' \
  http://127.0.0.1:10199/t/openai_prod/v1/chat/completions
```

Expected result: non-200 failure from the proxy path.

## Governance Note

AnythingLLM admin routes can change provider settings at runtime. Restarting or
force-recreating the container restores the Docker-provided env authority.

Treat the env file as the authoritative operator surface for this round.
