# AnythingLLM — Provider Switching Guide

*Tested in Round 43-6 (2026-04-25/26).*

---

## 1. Direct → Subumbra (OpenAI-compatible, single provider)

**Navigation:** Workspace settings → LLM Preference → Generic OpenAI

- **Base Path:** `http://subumbra-proxy:8090/t/v1`
- **API Key:** plain `key_id` (e.g., `openai_prod`)
- **Model:** the exact model name (e.g., `gpt-4o-mini`)
- **Max Tokens:** as desired

PASS: routes correctly through Subumbra. Proxy log confirms `complete key_id=openai_prod status=200`.

**Limitation:** Generic OpenAI is a single-model input — no model chooser UI; `GENERIC_OPEN_AI_MODEL_PREF`
is a single value. AnythingLLM does not fetch `/models` dynamically for this provider type.

---

## 2. Named Providers — Blocked by App Design

AnythingLLM's named provider integrations (Anthropic, OpenAI, Groq, and others) hardcode
their official API endpoints. There is no base URL override for any named provider.

PR #5295 to add `ANTHROPIC_BASE_URL` was explicitly rejected by the maintainer:
> "The Anthropic provider is only for Anthropic intentionally."

This is not a Subumbra limitation. The Generic OpenAI path is the only direct path that
allows base URL override.

---

## 3. Via LiteLLM → Subumbra (recommended for multi-provider)

**Navigation:** Settings → LLM Preference → LiteLLM

- **Base Path:** `http://litellm:4000`
- or set `LITE_LLM_BASE_PATH=http://litellm:4000` in `.env`

PASS: full model list from LiteLLM, all providers including Anthropic, proper model chooser.
This is the recommended path for AnythingLLM users who need multi-provider access.

---

## 4. Via Bifrost → Subumbra

Not tested in R43-6. Via LiteLLM is the recommended aggregator path.

---

## 5. Tested combinations

See [provider-matrix.md](../../provider-matrix.md) AnythingLLM column.

Direct path: Generic OpenAI with `openai_prod` confirmed working (R43-1).
All named providers: blocked by app design (not Subumbra).
Via LiteLLM: confirmed working with full provider list (R43-6).

---

## App-Specific Notes

- AnythingLLM stores provider config in a SQLite workspace database. After changing the
  base URL or model, restart the workspace or recreate the container to clear cached state.
- The base URL applies to both chat and embedding endpoints for the Generic OpenAI provider.
- For multi-provider access, LiteLLM is the supported path — not multiple Generic OpenAI
  workspace instances (this adds config overhead without solving the model-chooser limitation).
