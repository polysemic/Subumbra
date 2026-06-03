# Subumbra Provider × App Matrix — historical (Round 43-6)

*Historical regression matrix (2026-04-25 through 2026-04-26). All direct-path cells used the transparent sidecar at that time.*
*Proxy log pass pattern referenced: `complete key_id=<provider>_prod status=200`*

For current **example `curl`** paths and REST notes, see [integration-recipes.md](integration-recipes.md). This file is retained for **traceability**; cell results are not re-verified each release.

---

## §3A — Provider × App (direct Subumbra path)

| Provider | OpenWebUI | AnythingLLM | LibreChat | Bifrost | N8N |
|----------|-----------|-------------|-----------|---------|-----|
| openai | ✓ R43 | ✓ R43-1 † | ✓ R43-5 | ✓ R43-3 | ✓ R43-6 |
| anthropic | ✓ R43-6 | BLOCKED ‡ | ✓ R43-6 | ✓ R43-6 | ✓ R43-6 |
| groq | ✓ R43-6 | N/A ‡ | ✓ R43-6 | ✓ R43-6 | — |
| deepseek | ✓ R43-6 | N/A ‡ | ✓ R43-6 | ✓ R43-6 | — |
| mistral | ✓ R43-6 | N/A ‡ | ✓ R43-6 ★ | ✓ R43-6 | — |
| openrouter | ✓ R43-6 | N/A ‡ | ✓ R43-6 | ✓ R43-6 | — |
| together | ✓ R43-6 | N/A ‡ | ✓ R43-6 ✦ | FAIL ◆ | — |
| xai | ✓ R43-6 | N/A ‡ | ✓ R43-6 | ✓ R43-6 | — |
| cerebras | ✓ R43-6 | N/A ‡ | ✓ R43-6 | ✓ R43-6 | — |
| gemini | N/A ◇ | N/A ◇ | N/A ◇ | N/A ◇ | N/A ◇ |

**† AnythingLLM openai:** Generic OpenAI path only; no model chooser; single model at a time.
Use the app consumer token as the API key and carry `openai_prod` in the base path.
`GENERIC_OPEN_AI_BASE_PATH=http://subumbra-proxy:8090/t/openai_prod/v1`

**‡ AnythingLLM named providers:** All named providers (Anthropic, Groq, etc.) hardcode their
official endpoints. PR #5295 to add `ANTHROPIC_BASE_URL` was explicitly rejected by the
maintainer. No base URL override exists for any named provider. Not a Subumbra limitation.
Full multi-provider access requires the LiteLLM aggregator path (see §3B).

**★ LibreChat Mistral:** requires `dropParams: [user, frequency_penalty, presence_penalty,
parallel_tool_calls, stop]` — Mistral rejects these OpenAI-specific fields with 422.

**✦ LibreChat Together:** requires `fetch: false` — Together's `/models` endpoint returns a
raw array, not OpenAI `{data:[]}` format; LibreChat's model-list parser fails with
`Cannot read properties of undefined (reading 'map')`.

**◆ Bifrost Together:** FAIL — Together is not in Bifrost's built-in provider list. Custom
provider configuration gives `failed to unmarshal response from provider API`. Model list
unavailable; no test cells completable. Bifrost limitation, not Subumbra.

**◇ Gemini N/A:** Google's OpenAI-compatible endpoint is at `/v1beta/openai/`, not `/v1/`.
The historical transparent sidecar path routed to the wrong path (404). The
current LiteLLM example keeps the Gemini entry commented with the corrected
path shape in [`docs/apps/litellm/templates/config.yaml`](apps/litellm/templates/config.yaml).

**— N8N not tested:** N8N AI-node integration was validated for Anthropic and OpenAI only.
Other providers are expected to work via the same credential base URL override pattern but
were not tested in this round.

---

## §3B — Routing Path × Frontend App (aggregator paths)

Model list: at least one entry appears in the app's model selector.
Chat: proxy log shows `complete key_id=openai_prod status=200` via the aggregator path.

| Frontend App | LiteLLM: model list | LiteLLM: chat | Bifrost: model list | Bifrost: chat |
|---|---|---|---|---|
| OpenWebUI | full list, all providers ✓ | ✓ R43-6 | ✓ R43-6 | ✓ R43-6 |
| AnythingLLM | full list, all providers incl. Anthropic ✓ | ✓ R43-6 | — | — |
| LibreChat | not tested R43-6 | not tested R43-6 | not tested R43-6 | not tested R43-6 |
| N8N | N/A (node-type specific) | ✓ R43-6 (OpenAI node → litellm:4000) | N/A | — |

**Via LiteLLM:** model names shown in app are `config.yaml` `model_name` aliases
(e.g., `gpt-4o`, `claude-opus-4`), not Subumbra key_ids. Expected behavior.

**Via Bifrost — AnythingLLM/LibreChat:** not tested in R43-6. Via LiteLLM is the
recommended aggregator path for these apps.

---

## Base URL Reference

### Direct → Subumbra

| App / node | Base URL | Notes |
|---|---|---|
| OpenWebUI | `http://subumbra-proxy:8090/t/openai_prod/v1` | OpenAI-compatible; use Local conn type for Anthropic |
| AnythingLLM | `http://subumbra-proxy:8090/t/openai_prod/v1` | Generic OpenAI path only |
| LibreChat (most) | `http://subumbra-proxy:8090/t/openai_prod/v1` | OpenAI-compatible endpoints |
| LibreChat Groq | `http://subumbra-proxy:8090/t/groq_prod/openai/v1` | Groq uses `/openai/v1` prefix |
| LibreChat OpenRouter | `http://subumbra-proxy:8090/t/openrouter_prod/api/v1` | OpenRouter uses `/api/v1` prefix |
| LibreChat Together | `http://subumbra-proxy:8090/t/together_prod` | bare; Together /models is non-standard |
| LibreChat Anthropic | `ANTHROPIC_REVERSE_PROXY=http://subumbra-proxy:8090/t/anthropic_prod` | native endpoint type in yaml |
| Bifrost | `http://subumbra-proxy:8090/t/openai_prod` | bare; Bifrost appends path itself |
| N8N Anthropic AI-node | `http://subumbra-proxy:8090/t/anthropic_prod` | node appends `/v1/messages` |
| N8N OpenAI AI-node | `http://subumbra-proxy:8090/t/openai_prod/v1` | node appends `/responses` |
| N8N Workflow-node | `https://<worker-url>/proxy` | calls CF Worker directly, not subumbra-proxy |

### Via LiteLLM aggregator

| App | Base URL |
|---|---|
| OpenWebUI | `http://litellm:4000/v1` |
| AnythingLLM | `http://litellm:4000/v1` |
| LibreChat | `http://litellm:4000/v1` |
| N8N (AI-node) | `http://litellm:4000/v1` |

### Via Bifrost aggregator

| App | Base URL |
|---|---|
| OpenWebUI | `http://bifrost:8080/v1` |
| AnythingLLM | `http://bifrost:8080/v1` |
| LibreChat | `http://bifrost:8080/v1` |

---

## Known Limitations

| ID | Description |
|----|-------------|
| GEMINI-PATH | Gemini requires `/v1beta/openai/` path — transparent sidecar routes to wrong path. Deferred. |
| BIFROST-TOGETHER | Together not in Bifrost's built-in provider list; custom provider unmarshal error. Bifrost limitation. |
| ANYTHINGLLM-PROVIDERS | Named providers hardcoded. Only Generic OpenAI path allows base URL override. Use LiteLLM aggregator for multi-provider. |
| DOUBLE-FETCH | `subumbra-proxy` makes two `GET /keys/<key_id>` calls per request. High priority; dedicated round required. See `PROJECT_STATUS.md`. |
| LIBRECHAT-MISTRAL-PARAMS | Mistral requires `dropParams` to strip incompatible OpenAI fields. Documented in `librechat.yaml` template. |
| LIBRECHAT-TOGETHER-FETCH | Together `/models` returns raw array; `fetch: false` required in LibreChat config. |
