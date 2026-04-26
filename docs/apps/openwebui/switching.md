# OpenWebUI — Provider Switching Guide

*Tested in Round 43-6 (2026-04-25/26). All configurations verified against proxy logs.*

---

## 1. Direct → Subumbra (OpenAI-compatible providers)

**Navigation:** Settings → Admin Panel → Connections → OpenAI API

- **API Base URL:** `http://subumbra-proxy:8090/t/v1`
- **API Key:** plain `key_id` (e.g., `openai_prod`, `groq_prod`)
- **Connection type:** OpenAI

Click the arrows icon to fetch the model list. Models are fetched at connection time from
the provider via the transparent sidecar.

**Providers confirmed working with this pattern:**
openai, groq, deepseek, cerebras, mistral, openrouter, together, xai

For Groq specifically: use `/t/openai/v1` as the base URL (Groq's API uses an `/openai/v1` prefix).

---

## 2. Direct → Subumbra (Anthropic)

Anthropic requires a different connection approach in OpenWebUI — the OpenAI-compatible
connection type sends the wrong body format and is missing `anthropic-version`.

**Navigation:** Settings → Admin Panel → Connections → Add connection

- **Connection type:** Local (not OpenAI)
- **Base URL:** `http://subumbra-proxy:8090/t/v1`
- **API Key:** `anthropic_prod`
- **Custom headers (JSON):**
  ```json
  {
    "anthropic-version": "2023-06-01"
  }
  ```

PASS: model list pulls, chat succeeds, image upload (multimodal) succeeds,
Cloudflare DO visible in dashboard.

**Why this works:** OpenWebUI's native/Local connector sends Anthropic-format request
bodies unchanged through the transparent sidecar. The `anthropic-version` header passes
through (sidecar strips only `authorization`, `x-api-key`, and `x-subumbra-*` headers).

---

## 3. Via LiteLLM → Subumbra

- **API Base URL:** `http://litellm:4000/v1`
- **API Key:** any non-empty value (LiteLLM handles auth internally)
- **Connection type:** OpenAI

Model list shows `litellm/config.yaml` `model_name` aliases (e.g., `gpt-4o`, `claude-opus-4`),
not Subumbra key_ids. All providers in the LiteLLM config are accessible, including Anthropic.

---

## 4. Via Bifrost → Subumbra

- **API Base URL:** `http://bifrost:8080/v1`
- **API Key:** Bifrost's own API key (not a Subumbra key_id)
- **Connection type:** OpenAI

Bifrost's Subumbra configuration is set in Bifrost's own config (unchanged from R43-3-1).
The frontend app's key_id is Bifrost's own auth, not a Subumbra key_id.

---

## 5. Tested combinations

See [provider-matrix.md](../../provider-matrix.md) OpenWebUI column.

All 9 providers (openai, anthropic, groq, deepseek, cerebras, mistral, openrouter, together, xai)
confirmed working in R43-6. Gemini N/A (path mismatch; see matrix notes).

---

## App-Specific Notes

- `ENABLE_PERSISTENT_CONFIG=False` must be set in OpenWebUI's `.env` for env-defined config
  to be authoritative. Without it, UI-set config takes precedence over env vars and can
  override configured connections after restart.
- `/t/v1` is required for OpenAI-compatible connections — do not use bare `/t`.
- Anthropic native connector (Local type) works with `/t/v1`; OpenAI-compatible type
  fails for Anthropic (wrong body format, missing `anthropic-version` header).
- DeepSeek model responses may self-identify as another model (e.g., Claude) — this is a
  model quirk from DeepSeek's training data, not a routing issue. Proxy logs confirm
  correct routing to `api.deepseek.com`.
