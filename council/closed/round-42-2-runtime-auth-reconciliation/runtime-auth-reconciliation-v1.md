# Round 42.2 Approved Plan — Runtime Auth Reconciliation

Date: 2026-04-19
Status: Approved
Based on: three-synthesis consensus
  - `claude-synthesis.md` — APPROVED
  - `codex-synthesis.md`  — APPROVED
  - `gemini-synthesis.md` — APPROVED
Evidence trail: claude-review.md, codex-review.md, gemini-review.md,
  claude-review-2.md, codex-review-2.md, gemini-review-2.md,
  claude-investigation.md, codex-investigation.md, gemini-investigation.md

Codex-investigation provides direct package evidence (from running LiteLLM
container) for two previously-disputed claims:
- Anthropic `api_base`: `AnthropicModelInfo.get_api_base()` at
  `/app/litellm/llms/anthropic/common_utils.py:550-554` prefers passed
  `api_base` over env/defaults; handler carries it to actual request.
- DEEPSEEK_API_BASE precedence: `/app/litellm/llms/deepseek/chat/
  transformation.py:97-105` — model `api_base` wins; env var is fallback only.

---

## Scope

Eliminate `custom_callbacks.py` as the required LiteLLM integration path.
Replace the callback + Subumbra env vars pattern with transparent sidecar
routing: `api_base: http://subumbra-proxy:8090/t` + plain `api_key: <key_id>`.
Real API keys continue to never appear in any file; LiteLLM's environment
loses all Subumbra auth material.

This round does NOT:
- Change the sidecar itself (`subumbra-proxy/app.py`) — no code changes there
- Change subumbra-keys, the CF Worker, or the bootstrap crypto
- Remove `custom_callbacks.py` from the repo — it becomes legacy-labeled only
- Move `post-bootstrap.sh` into a container (future round)
- Implement credential file import / "swap & shred" (future round, enabled by
  this round proving the pattern)
- Add per-app adapter tokens for n8n, open-webui, etc. (not needed; they
  route through the existing proxy token)

---

## Required Invariants

1. Real API keys must never appear in plaintext in any file.
2. The split-trust boundary (subumbra-keys + CF Worker) must not be altered.
3. No token values, wrapped DEKs, fingerprints, or decrypted material may be
   added to any log.
4. The `--rotate` per-key rotation path must not be modified.
5. `LITELLM_MASTER_KEY` is a LiteLLM-internal auth key and must not be removed.

---

## Prerequisite: Verify `PROXY_ALLOWED_KEYS` Scope (V3 — run before live test)

This is the gating check. The transparent sidecar fails with `403 key_scope_denied`
at `subumbra-keys/app.py:546` for any key_id not in `subumbra-proxy`'s
`allowed_keys` list.

Before running the live end-to-end tests, verify that `subumbra-proxy` scope
covers all key_ids that LiteLLM models will use:

```bash
# Read the registry and extract subumbra-proxy allowed_keys
# (SUBUMBRA_ADAPTER_REGISTRY is plain JSON, not base64 — bootstrap/subumbra-bootstrap.py:1720)
python3 -c "
import os, json
env_file = '.env'
reg = ''
for line in open(env_file):
    if line.startswith('SUBUMBRA_ADAPTER_REGISTRY='):
        reg = line.split('=', 1)[1].strip()
        break
if not reg:
    print('ERROR: SUBUMBRA_ADAPTER_REGISTRY not found in .env')
    exit(1)
data = json.loads(reg)
proxy_keys = data.get('subumbra-proxy', {}).get('allowed_keys', [])
print('subumbra-proxy allowed_keys:', proxy_keys)
"
```

Or, if the env var is already exported in the shell:
```bash
python3 -c "import os,json; d=json.loads(os.environ['SUBUMBRA_ADAPTER_REGISTRY']); print('subumbra-proxy allowed_keys:', d.get('subumbra-proxy',{}).get('allowed_keys',[]))"
```

If the output does not include all key_ids referenced in `litellm/config.yaml`,
re-run bootstrap (interactive wizard, Step 3) and expand the `subumbra-proxy`
scope before proceeding with live tests. A bootstrap re-run requires
`./post-bootstrap.sh` and `docker compose up -d --force-recreate` afterwards.

---

## Exact Code Changes

### 1. `litellm/config.yaml` — Replace callback pattern with sidecar routing

Replace the entire file content. The changes are:
- New header comment explaining the proxy routing pattern
- Each model entry: add `api_base: http://subumbra-proxy:8090/t`, change
  `api_key: "subumbra:<key_id>"` → `api_key: "<key_id>"` (plain)
- Remove `callbacks: custom_callbacks.proxy_handler_instance` from
  `litellm_settings:` section
- Remove the comment block about callback intercept behavior
- **Gemini (`gemini-2.0-flash`) is excluded from this round** (see Deferred
  section). Its non-standard Google API URL path (`/v1beta/openai/`) requires a
  per-model `api_base` prefix that was not approved by all three syntheses.
  Remove the Gemini entry from config.yaml entirely until a follow-on round
  explicitly approves the exception.

**New file content:**

```yaml
# ─────────────────────────────────────────────────────────────────────────────
# LiteLLM Proxy Config — Subumbra (Round 42.2+)
#
# All models route through the transparent sidecar:
#   api_base: http://subumbra-proxy:8090/t
#   api_key:  <key_id>   (plain, no "subumbra:" prefix)
#
# The sidecar extracts the key_id from the Authorization header, fetches the
# encrypted record from subumbra-keys, and routes to the CF Worker. Real API
# keys never appear in this file.
#
# Key_id values must match what you entered during bootstrap. If you used
# custom labels, update the api_key values below to match exactly.
# Mismatch symptom: subumbra-keys returns HTTP 403 with reason_code=key_scope_denied.
# Ensure subumbra-proxy scope (PROXY_ALLOWED_KEYS) includes all key_ids used here.
# ─────────────────────────────────────────────────────────────────────────────

model_list:

  # ── Anthropic ───────────────────────────────────────────────────────────────
  - model_name: claude-opus-4
    litellm_params:
      model: anthropic/claude-opus-4-5
      api_base: http://subumbra-proxy:8090/t
      api_key: anthropic_prod

  - model_name: claude-sonnet-4
    litellm_params:
      model: anthropic/claude-sonnet-4-5
      api_base: http://subumbra-proxy:8090/t
      api_key: anthropic_prod

  - model_name: claude-haiku-4
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001
      api_base: http://subumbra-proxy:8090/t
      api_key: anthropic_prod

  # ── OpenAI ──────────────────────────────────────────────────────────────────
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_base: http://subumbra-proxy:8090/t
      api_key: openai_prod

  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_base: http://subumbra-proxy:8090/t
      api_key: openai_prod

  - model_name: o3
    litellm_params:
      model: openai/o3
      api_base: http://subumbra-proxy:8090/t
      api_key: openai_prod

  # ── Groq ────────────────────────────────────────────────────────────────────
  - model_name: llama-3.3-70b
    litellm_params:
      model: groq/llama-3.3-70b-versatile
      api_base: http://subumbra-proxy:8090/t
      api_key: groq_prod

  - model_name: llama-3.1-8b
    litellm_params:
      model: groq/llama-3.1-8b-instant
      api_base: http://subumbra-proxy:8090/t
      api_key: groq_prod

  # ── DeepSeek ────────────────────────────────────────────────────────────────
  - model_name: deepseek-chat
    litellm_params:
      model: deepseek/deepseek-chat
      api_base: http://subumbra-proxy:8090/t
      api_key: deepseek_prod

  - model_name: deepseek-reasoner
    litellm_params:
      model: deepseek/deepseek-reasoner
      api_base: http://subumbra-proxy:8090/t
      api_key: deepseek_prod

  # ── Cerebras ───────────────────────────────────────────────────────────────
  - model_name: cerebras-llama-3.3-70b
    litellm_params:
      model: cerebras/llama3.1-8b
      api_base: http://subumbra-proxy:8090/t
      api_key: cerebras_prod

  # ── Gemini — EXCLUDED FROM ROUND 42.2 ─────────────────────────────────────
  # Google's OpenAI-compatible endpoint is at /v1beta/openai/ not /v1/.
  # Using the universal api_base: http://subumbra-proxy:8090/t routes to
  # generativelanguage.googleapis.com/v1/chat/completions — a 404.
  # A per-model path prefix exception is required but was not approved by
  # all three syntheses. Remove this entry; add back in a follow-on round.
  # - model_name: gemini-2.0-flash
  #   litellm_params:
  #     model: openai/gemini-2.0-flash-001
  #     api_base: http://subumbra-proxy:8090/t/v1beta/openai
  #     api_key: gemini_prod

  # ── Mistral ────────────────────────────────────────────────────────────────
  - model_name: mistral-large
    litellm_params:
      model: mistral/mistral-large-latest
      api_base: http://subumbra-proxy:8090/t
      api_key: mistral_prod

  # ── OpenRouter ─────────────────────────────────────────────────────────────
  - model_name: openrouter-claude-sonnet-4
    litellm_params:
      model: openrouter/anthropic/claude-sonnet-4
      api_base: http://subumbra-proxy:8090/t
      api_key: openrouter_prod

  # ── Together ───────────────────────────────────────────────────────────────
  - model_name: together-llama-3.3-70b
    litellm_params:
      model: together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo
      api_base: http://subumbra-proxy:8090/t
      api_key: together_prod

  # ── xAI ────────────────────────────────────────────────────────────────────
  - model_name: grok-2
    litellm_params:
      model: xai/grok-3
      api_base: http://subumbra-proxy:8090/t
      api_key: xai_prod

# ─────────────────────────────────────────────────────────────────────────────
# LiteLLM settings
# ─────────────────────────────────────────────────────────────────────────────
litellm_settings:
  # Drop keys from logs — belt-and-suspenders
  redact_user_api_key_info: true

# ─────────────────────────────────────────────────────────────────────────────
# General / proxy settings
# ─────────────────────────────────────────────────────────────────────────────
general_settings:
  # Disable the built-in UI (we have our own)
  disable_spend_logs: true
  no_database: true
```

---

### 2. `docker-compose.yml` — Remove Subumbra auth material from litellm service

The `litellm:` service `environment:` block currently reads (lines 98-110):

```yaml
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      DEEPSEEK_API_BASE: https://api.deepseek.com/v1
      # Subumbra auth — non-sensitive tokens generated by bootstrap
      SUBUMBRA_ACCESS_TOKEN: ${SUBUMBRA_TOKEN_LITELLM}
      SUBUMBRA_HMAC_KEY: ${SUBUMBRA_HMAC_KEY}
      SUBUMBRA_KEYS_URL: http://subumbra-keys:9090
      # Cloudflare Worker endpoint
      CF_WORKER_URL: ${CF_WORKER_URL}
      # Optional: CF Access service token if Worker is behind CF Access
      CF_ACCESS_CLIENT_ID: ${CF_ACCESS_CLIENT_ID:-}
      CF_ACCESS_CLIENT_SECRET: ${CF_ACCESS_CLIENT_SECRET:-}
      # SUBUMBRA_PROVIDER_PREFIXES='{"my_provider":"/api/v2"}'
```

Replace with:
```yaml
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
```

Everything except `LITELLM_MASTER_KEY` is removed. This includes the
`DEEPSEEK_API_BASE` line (codex-investigation confirmed model `api_base` wins
over this env var, but it is cargo-cult config now that the sidecar is the
authority), all six Subumbra auth vars, and the `SUBUMBRA_PROVIDER_PREFIXES`
comment block (callback-era, now dead).

Also update the `depends_on:` block for the `litellm:` service (lines 113-115):

```yaml
# Current:
depends_on:
  subumbra-keys:
    condition: service_healthy

# Replace with:
depends_on:
  subumbra-keys:
    condition: service_healthy
  subumbra-proxy:
    condition: service_healthy
```

The `subumbra-proxy` healthcheck at lines 194-199 supports `service_healthy`.
Litellm remains on the `internal` network, which also carries `subumbra-proxy`,
so Docker DNS resolution of `subumbra-proxy:8090` works without network changes.

**Optional cleanup (not required for correctness):** Remove the
`./litellm/custom_callbacks.py:/app/custom_callbacks.py:ro` bind-mount from
the `volumes:` list. The file is no longer loaded; the mount is inert but
harmless if left in place.

---

### 3. `litellm/custom_callbacks.py` — Add legacy header

Replace the opening docstring (lines 1-5) with a legacy-labeled header.
Current:
```python
"""
Subumbra Adapter #1 — LiteLLM
─────────────────────────────────────────────────
This file implements the LiteLLM adapter for Subumbra. LiteLLM now reaches the
Subumbra core through the canonical POST /proxy API using a custom transport.
```

Replace with:
```python
"""
Subumbra Adapter #1 — LiteLLM (LEGACY — callback path, superseded by Round 42.2)
─────────────────────────────────────────────────────────────────────────────────
As of Round 42.2, LiteLLM routes through subumbra-proxy transparent sidecar:
  api_base: http://subumbra-proxy:8090/t
  api_key:  <key_id>  (plain, no "subumbra:" prefix)

This callback is no longer loaded by litellm/config.yaml (callbacks: stanza
removed). It is retained for reference and for deployments that have not yet
migrated to the sidecar routing pattern.

The original implementation follows below, unchanged.
─────────────────────────────────────────────────────────────────────────────────
This file implements the LiteLLM adapter for Subumbra. LiteLLM now reaches the
Subumbra core through the canonical POST /proxy API using a custom transport.
```

---

### 4. `post-bootstrap.sh` — Remove litellm from drift check loop

The drift check loop at lines 90-107 inspects running containers for stale
`SUBUMBRA_ACCESS_TOKEN`. After this round, the litellm container no longer
carries that env var, so the check becomes a no-op for litellm and should be
removed.

**Full before/after for the affected block (lines 90-107):**

Current:
```bash
echo ""
echo "Checking for token drift in running containers..."
DRIFT=false
for svc in litellm subumbra-ui subumbra-proxy subumbra-probe; do
    if docker compose ps --status running "$svc" 2>/dev/null | grep -q "$svc"; then
        case "$svc" in
            litellm)      token_val="$SUBUMBRA_TOKEN_LITELLM" ;;
            subumbra-ui)  token_val="$SUBUMBRA_TOKEN_UI" ;;
            subumbra-proxy) token_val="$SUBUMBRA_TOKEN_PROXY" ;;
            subumbra-probe) token_val="$SUBUMBRA_TOKEN_PROBE" ;;
        esac
        running_val="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$svc" 2>/dev/null | grep "^SUBUMBRA_ACCESS_TOKEN=" | cut -d= -f2- || true)"
        if [[ -n "$running_val" && "$running_val" != "$token_val" ]]; then
            echo "  WARNING: $svc has stale token. Run: docker compose up -d --force-recreate" >&2
            DRIFT=true
        fi
    fi
done
[[ "$DRIFT" == "false" ]] && echo "  No drift detected."
```

Replace with:
```bash
echo ""
echo "Checking for token drift in running containers..."
DRIFT=false
for svc in subumbra-ui subumbra-proxy subumbra-probe; do
    if docker compose ps --status running "$svc" 2>/dev/null | grep -q "$svc"; then
        case "$svc" in
            subumbra-ui)  token_val="$SUBUMBRA_TOKEN_UI" ;;
            subumbra-proxy) token_val="$SUBUMBRA_TOKEN_PROXY" ;;
            subumbra-probe) token_val="$SUBUMBRA_TOKEN_PROBE" ;;
        esac
        running_val="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$svc" 2>/dev/null | grep "^SUBUMBRA_ACCESS_TOKEN=" | cut -d= -f2- || true)"
        if [[ -n "$running_val" && "$running_val" != "$token_val" ]]; then
            echo "  WARNING: $svc has stale token. Run: docker compose up -d --force-recreate" >&2
            DRIFT=true
        fi
    fi
done
[[ "$DRIFT" == "false" ]] && echo "  No drift detected."
```

Two changes: `litellm` removed from `for svc in ...` list; `litellm)` case
branch removed from the `case` block. All other lines are unchanged.

---

### 5. `bootstrap/subumbra-bootstrap.py` — Update wizard Step 3 text

**5a. Wizard Step 3 description (lines 1046-1047)**

Current:
```python
print("  1. LiteLLM: keys referenced by subumbra:key_id values in litellm/config.yaml")
print("  2. subumbra-proxy: keys available through the explicit/transparent sidecar")
```

Replace with:
```python
print("  1. LiteLLM: legacy callback path only. Leave empty if LiteLLM routes")
print("     through subumbra-proxy (the default since Round 42.2).")
print("  2. subumbra-proxy: all key_ids that LiteLLM and other apps access via")
print("     the transparent sidecar (api_base: http://subumbra-proxy:8090/t).")
print("     For most deployments, enter all provider key_ids here.")
```

**5b. `_build_litellm_alignment_lines` function (lines 647-664)**

Replace the entire function body:

Current:
```python
def _build_litellm_alignment_lines(
    api_keys: dict[str, tuple[str, str, str, str, str]],
    allowed_keys_by_adapter: dict[str, list[str]],
) -> list[str]:
    lines = [
        "  LiteLLM key_id alignment:",
        "    Update litellm/config.yaml so each model uses the exact Subumbra key_id entered during bootstrap.",
        "    Copy/paste hints for LiteLLM-scoped keys:",
    ]
    litellm_key_ids = allowed_keys_by_adapter.get("litellm", [])
    if not litellm_key_ids:
        lines.append("      (no key_ids scoped to LiteLLM in this bootstrap run)")
        return lines

    for key_id in litellm_key_ids:
        provider = api_keys[key_id][0]
        lines.append(f'      {provider:12s} {key_id:20s} api_key: "subumbra:{key_id}"')
    return lines
```

Replace with:
```python
def _build_litellm_alignment_lines(
    api_keys: dict[str, tuple[str, str, str, str, str]],
    allowed_keys_by_adapter: dict[str, list[str]],
) -> list[str]:
    litellm_key_ids = allowed_keys_by_adapter.get("litellm", [])
    proxy_key_ids = allowed_keys_by_adapter.get("subumbra-proxy", [])

    if litellm_key_ids:
        # Legacy callback path still in use for this deployment
        lines = [
            "  LiteLLM key_id alignment (legacy callback path):",
            "    Update litellm/config.yaml so each model uses the exact key_id entered during bootstrap.",
            "    Copy/paste hints for LiteLLM-scoped keys:",
        ]
        for key_id in litellm_key_ids:
            provider = api_keys[key_id][0]
            lines.append(f'      {provider:12s} {key_id:20s} api_key: "subumbra:{key_id}"')
        return lines

    # Standard proxy-routing path (Round 42.2+)
    lines = [
        "  LiteLLM proxy-routing alignment:",
        "    LiteLLM is configured for subumbra-proxy transparent routing.",
        "    In litellm/config.yaml, set for each model:",
        "      api_base: http://subumbra-proxy:8090/t",
        "      api_key: <key_id>   (plain, no subumbra: prefix)",
        "    subumbra-proxy scope covers these key_ids:",
    ]
    if proxy_key_ids:
        for key_id in proxy_key_ids:
            provider = api_keys[key_id][0]
            lines.append(f'      {provider:12s} {key_id:20s} api_key: {key_id}')
    else:
        lines.append("      (no key_ids scoped to subumbra-proxy — re-run bootstrap Step 3)")
    return lines
```

---

### 6. `README.md` — Update scope descriptions and model-adding instructions

**6a. Adapter key scopes section (lines 221-226)**

Current:
```markdown
- `LiteLLM` scope:
  Use this for key IDs referenced by `subumbra:<key_id>` in
  `litellm/config.yaml`.
- `subumbra-proxy` scope:
  Use this for sidecar-driven keys such as GitHub, Slack, SendGrid, or any
  direct non-LiteLLM API calls routed through `subumbra-proxy`.
```

Replace with:
```markdown
- `LiteLLM` scope:
  Legacy callback path only. Leave empty if LiteLLM routes through
  `subumbra-proxy` (the default since Round 42.2).
- `subumbra-proxy` scope:
  All key_ids accessible via the transparent sidecar
  (`api_base: http://subumbra-proxy:8090/t`). Include all key_ids used by
  LiteLLM and any other app that routes through the sidecar.
```

**6b. "Adding / Changing Models" section (lines 393-427)**

Replace this section entirely:

Current:
```markdown
## Adding / Changing Models

Edit [litellm/config.yaml](litellm/config.yaml) to add models. The only required change is the `model:` line — `api_key` always uses the `subumbra:` prefix pointing to the correct key ID:

```yaml
- model_name: my-new-model
  litellm_params:
    model: anthropic/claude-3-5-haiku-20241022
    api_key: "subumbra:anthropic_prod"
```

Restart LiteLLM to pick up the change:
```bash
docker compose restart litellm
```

### Custom Provider Path Prefixes

The callback dynamically resolves each provider's API path prefix using LiteLLM's
internal registry. If a provider isn't auto-detected (or you need to override the
default), set `SUBUMBRA_PROVIDER_PREFIXES` in your `.env`:

```bash
SUBUMBRA_PROVIDER_PREFIXES={"my_provider":"/api/v2"}
```

This is a JSON map of provider name to path prefix. The prefix is appended to the
CF Worker adapter base URL before the SDK adds its own endpoint path.

> **Important:** Setting a path prefix alone does NOT enable a new provider.
> You must also add the provider to `worker/src/providers.json` (the bootstrap
> seed/template), republish the live registry via re-bootstrap or `--push-registry`,
> and add the relevant LiteLLM model configuration. See
> [docs/operator-guide.md](docs/operator-guide.md) for the live registry workflow.
```

Replace with:
```markdown
## Adding / Changing Models

Edit [litellm/config.yaml](litellm/config.yaml) to add models. Set `api_base`
to the transparent sidecar and `api_key` to the plain key_id (no `subumbra:`
prefix):

```yaml
- model_name: my-new-model
  litellm_params:
    model: anthropic/claude-3-5-haiku-20241022
    api_base: http://subumbra-proxy:8090/t
    api_key: anthropic_prod
```

Any app that supports a custom API base URL and accepts a plain string as an
API key can use the same pattern — no custom code or Subumbra-specific SDK
required. The sidecar resolves the `api_key` value as a key_id, fetches the
encrypted record, and routes to the correct provider.

Ensure the key_id you reference is in `subumbra-proxy`'s allowed scope
(`PROXY_ALLOWED_KEYS`). If not, re-run the bootstrap wizard (Step 3) and add it.

Recreate LiteLLM to pick up config changes:
```bash
docker compose up -d --force-recreate litellm
```
```

---

### 7. `docs/subumbra-install.md` — Update Section 7 (lines 152-170)

Current:
```markdown
## 7. Update `litellm/config.yaml`

The committed config uses the bootstrap default `key_id` suggestions
(`anthropic_prod`, `openai_prod`, etc.). If you entered custom labels during
bootstrap, update the `subumbra:<key_id>` values to match before starting the stack.

Use the copy/paste hints bootstrap printed at the end of step 5.

Example: if you named your Anthropic key `anthropic_test`, change:

```yaml
api_key: "subumbra:anthropic_prod"
```

to:

```yaml
api_key: "subumbra:anthropic_test"
```
```

Replace with:
```markdown
## 7. Verify `litellm/config.yaml` key_ids

The committed config uses `api_base: http://subumbra-proxy:8090/t` for each
model and sets `api_key` to the plain key_id (no `subumbra:` prefix).

If you entered custom key_id labels during bootstrap, update the `api_key`
values to match your chosen labels exactly. Use the copy/paste hints bootstrap
printed at the end of step 5.

Example: if you named your Anthropic key `anthropic_test`, change:

```yaml
api_key: anthropic_prod
```

to:

```yaml
api_key: anthropic_test
```

Also ensure the key_ids you use are in the `subumbra-proxy` allowed scope.
Bootstrap step 3 prompts for this; the summary output lists the scoped key_ids.
```

---

## Logging and Error Handling

**No new log lines are required by this round.**

Existing operator-visible signals are sufficient:
- `subumbra-keys` access log already emits `reason_code=key_scope_denied` on
  `403` responses — this is the primary new failure mode for misconfigured scope.
- `subumbra-proxy` already logs record-fetch errors and upstream failures
  (`app.py:200, 229-237`).
- LiteLLM's existing `unhealthy_endpoints` list surfaces models that fail their
  first completion attempt.

The only new diagnostic the codex-synthesis specifically called for — clearer
operator distinction between proxy-side `403 key_scope_denied` and a generic
record-fetch failure — is already present in the existing `reason_code` field
returned by `subumbra-keys/app.py:546`. No new logging code is needed.

No secret-bearing log lines may be introduced. No API keys, tokens, DEKs,
fingerprints, or `X-Subumbra-*` header values may appear in any log output.

---

## Failure Modes Introduced by This Round

| Failure | Signal | Notes |
|---|---|---|
| `subumbra-proxy` not running when LiteLLM starts | LiteLLM health shows unhealthy models immediately | Mitigated by `depends_on: subumbra-proxy: service_healthy` added above |
| `PROXY_ALLOWED_KEYS` missing a key_id | `403 key_scope_denied` from subumbra-keys on first request for that model | Operator-visible in litellm logs + subumbra-keys access log |
| `api_base` misconfigured (wrong path, extra prefix) | LiteLLM sends to wrong URL; connection refused or wrong upstream | Verify with V2 steps below |
| Gemini — not in this round | n/a | Gemini entry removed from config.yaml; see Deferred section |

No new secret-bearing log lines are introduced. Existing sidecar logging
(`subumbra-proxy/app.py:200, 229-237`) covers the new routing path.

---

## Verification Steps

### V1 — Static checks (no running stack required)

```bash
# 1. Confirm no "subumbra:" prefix in config.yaml api_key values
grep -n 'subumbra:' litellm/config.yaml && echo "FAIL: subumbra: prefix still present" || echo "PASS"

# 2. Confirm callbacks: stanza is removed
grep -n 'callbacks:' litellm/config.yaml && echo "FAIL: callbacks stanza present" || echo "PASS"

# 3. Confirm removed env vars are gone from litellm block
grep -n 'SUBUMBRA_ACCESS_TOKEN\|SUBUMBRA_HMAC_KEY\|SUBUMBRA_KEYS_URL\|CF_WORKER_URL\|DEEPSEEK_API_BASE' docker-compose.yml | grep -A5 -B5 'litellm:' || echo "PASS: no Subumbra auth vars in litellm block"

# 4. Confirm api_base is set for each model
grep -c 'api_base:' litellm/config.yaml

# 5. Confirm legacy header on custom_callbacks.py
grep -n 'LEGACY' litellm/custom_callbacks.py | head -3
```

### V2 — Live end-to-end per provider

Run via the council verification harness after re-bootstrap or after confirming
`PROXY_ALLOWED_KEYS` scope (see V3 prerequisite above):

```bash
./scripts/council/clean-run.sh --round round-42-2-runtime-auth-reconciliation --agent <name>
```

Per-provider checks: confirm at least one completion returns successfully for
Anthropic, OpenAI, and Groq (minimum set). DeepSeek, Mistral per operator key
availability. Gemini excluded from this round.

Expected: `200 OK` at LiteLLM `:4000/v1/chat/completions`, streamed response
passes through sidecar to CF Worker to provider and back.

### V3 — PROXY_ALLOWED_KEYS scope check (prerequisite for V2)

Run the registry introspection command from the "Prerequisite" section above.
Confirm the output includes all key_ids referenced in `litellm/config.yaml`.

---

## Known Limitations

- `LITELLM_ALLOWED_KEYS` and `SUBUMBRA_TOKEN_LITELLM` remain in `.env` and
  `SUBUMBRA_ADAPTER_REGISTRY` after this round (the `litellm` adapter registry
  entry is still present but becomes a no-op). This is harmless. Cleanup of
  the legacy `litellm` adapter entry is deferred to a future round after the
  proxy-routing pattern is proven stable.
- The `custom_callbacks.py` bind mount in `docker-compose.yml` remains (unless
  operator removes it). The file is no longer loaded; mount is inert.
- Gemini (`gemini-2.0-flash`) is excluded from this round. Entry is commented
  out in the approved config.yaml. See Deferred section.

---

## Deferred (Future Rounds)

- Credential file import / "swap & shred" (bootstrap `--import /path/to/.env`)
  — blocked by this round; enabled once this pattern is proven
- Post-bootstrap containerization
- Removal of legacy `litellm` adapter registry entry
- Removal of `LITELLM_ALLOWED_KEYS` dead-write from post-bootstrap.sh
- Removing `custom_callbacks.py` from the repo entirely
- Removing the `internal` network from the `litellm` service (no longer contacts
  subumbra-keys directly, but network change is low-priority)
- Gemini (`gemini-2.0-flash`) model migration — Google's OpenAI-compatible
  endpoint is at `/v1beta/openai/` not `/v1/`, requiring
  `api_base: http://subumbra-proxy:8090/t/v1beta/openai` as a per-model prefix
  exception. This was not approved by all three syntheses; requires a dedicated
  review in a follow-on round before re-adding to config.yaml.
