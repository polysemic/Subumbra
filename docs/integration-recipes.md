# Integration recipes (HTTP REST and transparent `/t`)

*Cookbook for operator-declared `http_rest` policies and example `curl` calls through `subumbra-proxy`. For the canonical adapter API see [adapter-contract.md](adapter-contract.md). For manifest authoring see [operator-guide.md](operator-guide.md).*

**Status:** Several paths below are still **experimental** for the `0.0.1-alpha` operator path; validate against your own `subumbra.json` before production.

---

## 1. Generic REST (`protocol: "http_rest"`)

Any HTTPS JSON API can be brokered when you set:

- `policy.protocol`: `"http_rest"`
- `policy.target.host` / `policy.target.base_path`
- `policy.auth` (`bearer`, `basic`, `header`, or `query` per operator guide)

Declare labels, hosts, and auth in `subumbra.json`; use [subumbra.example.json](../subumbra.example.json) as the **gold** reference (every signed catalog template plus one full inline policy example) and [subumbra.minimal.json](../subumbra.minimal.json) for the **smallest** valid manifest (one OpenAI key via `template` only). For sample `curl` paths per provider, see the sections below.

---

## 2. GitHub REST (experimental)

**Target:** `api.github.com` | **Scheme:** `bearer` | **Capability:** e.g. `source_control_read`

Author policy in `subumbra.json` with the correct `target.host` and path allowlist. Internal proof templates lived under historical council rounds; treat those JSON blobs as **inspiration only**—your manifest is source of truth.

---

## 3. Stripe test mode (experimental)

**Target:** `api.stripe.com` | **Scheme:** `bearer` | **Capability:** e.g. `payments_read`

Use test keys only. Policy JSON used in past proof rounds may exist only on operator machines under `council/` archives—not shipped in this repo.

---

## 4. Custom header auth (`auth.scheme: "header"`)

Set `policy.auth.scheme` to `"header"` and `policy.auth.header_name` (for example `x-api-key` for Anthropic-style providers). Proof excerpts in older docs used `api.anthropic.com`; align `path_prefixes` and methods with your manifest.

---

## 5. Example `curl` — transparent `/t` route

Subumbra does **not** ship a hardcoded provider catalog. Replace `<your_*_key_id>` and `<your_adapter_token>` with values from your **local** `subumbra.json` (gitignored) and `.env` after bootstrap.

Host below uses `localhost:10199` (published proxy on the VPS). Apps on the Docker internal network use `http://subumbra-proxy:8090/t/<key_id>/...` instead.

### Anthropic

```bash
curl -s -X POST http://127.0.0.1:10199/t/<your_anthropic_key_id>/v1/messages \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-3-5-haiku-latest","max_tokens":16,"messages":[{"role":"user","content":"Say test"}]}'
```

### OpenAI

```bash
curl -s -X POST http://127.0.0.1:10199/t/<your_openai_key_id>/v1/chat/completions \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}'
```

### Groq

```bash
curl -s -X POST http://127.0.0.1:10199/t/<your_groq_key_id>/openai/v1/chat/completions \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama-3.1-8b-instant","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}'
```

### DeepSeek

```bash
curl -s -X POST http://127.0.0.1:10199/t/<your_deepseek_key_id>/v1/chat/completions \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Say test"}],"max_tokens":16}'
```

### GitHub

GitHub requires a `User-Agent` header (HTTP 403 without it).

```bash
curl -s http://127.0.0.1:10199/t/<your_github_key_id>/user \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "User-Agent: subumbra-proxy/1.0"
```

### Slack

```bash
curl -s -X POST http://127.0.0.1:10199/t/<your_slack_key_id>/api/auth.test \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### SendGrid

```bash
curl -s http://127.0.0.1:10199/t/<your_sendgrid_key_id>/v3/scopes \
  -H "Authorization: Bearer <your_adapter_token>" \
  -H "Content-Type: application/json"
```

---

## See also

- [docs/provider-matrix.md](provider-matrix.md) — historical app × provider matrix (regression notes)
- [docs/apps/litellm/install.md](apps/litellm/install.md) — standalone LiteLLM + Subumbra
