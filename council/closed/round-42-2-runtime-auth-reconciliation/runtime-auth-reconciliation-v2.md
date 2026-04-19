# Round 42.2 Approved Plan v2 — Runtime Auth Reconciliation

Date: 2026-04-19
Status: Approved — ready for implementation
Supersedes: `council/approved/runtime-auth-reconciliation.md` (v1)

Dispute resolution applied before this version:
- D1 (V3 command used `base64.b64decode` — wrong): fixed. Bootstrap writes plain
  JSON. `bootstrap/subumbra-bootstrap.py:1720` confirms: `json.dumps(...)` only,
  no encoding.
- D2 (Gemini `api_base` exception not in all three syntheses): Gemini model
  excluded from this round. Google's API path requires `/v1beta/openai/` prefix;
  the universal `/t` contract produces a `404`. Deferred to follow-on round.

Evidence trail:
- Three-synthesis consensus: `claude-synthesis.md`, `codex-synthesis.md`,
  `gemini-synthesis.md` — all APPROVED
- Dispute resolution: `codex-disputes.md`, `claude-dispute-review.md`
- Investigations: `claude-investigation.md`, `codex-investigation.md`,
  `gemini-investigation.md`

---

## Scope

Eliminate `custom_callbacks.py` as the required LiteLLM integration path.
Replace the callback + Subumbra env vars pattern with transparent sidecar
routing: `api_base: http://subumbra-proxy:8090/t` + plain `api_key: <key_id>`.
Real API keys continue to never appear in any file; LiteLLM's environment
loses all Subumbra auth material.

**This round does NOT:**
- Change `subumbra-proxy/app.py` — no sidecar code changes
- Change subumbra-keys, the CF Worker, or the bootstrap crypto
- Remove `custom_callbacks.py` from the repo — legacy-labeled only
- Migrate Gemini (`gemini-2.0-flash`) — deferred, see below
- Implement credential file import / "swap & shred" — future round
- Add per-app adapter tokens for n8n, open-webui, etc.
- Move `post-bootstrap.sh` into a container — future round
- Modify bootstrap auto-merge of LiteLLM scope into proxy scope — deferred

---

## Required Invariants

1. Real API keys must never appear in plaintext in any file.
2. The split-trust boundary (subumbra-keys + CF Worker) must not be altered.
3. No token values, wrapped DEKs, fingerprints, or decrypted material may be
   added to any log.
4. The `--rotate` per-key rotation path must not be modified.
5. `LITELLM_MASTER_KEY` is a LiteLLM-internal auth key and must not be removed.

---

## Prerequisite: Verify `PROXY_ALLOWED_KEYS` Scope

Run this **before** live testing (V3). The transparent sidecar fails with
`403 key_scope_denied` at `subumbra-keys/app.py:546` for any key_id not in
`subumbra-proxy`'s `allowed_keys` list.

`SUBUMBRA_ADAPTER_REGISTRY` is stored as plain JSON in `.env`
(`bootstrap/subumbra-bootstrap.py:1720`). Check the scope with:

```bash
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

Or if the env var is already exported in the shell:
```bash
python3 -c "import os,json; d=json.loads(os.environ['SUBUMBRA_ADAPTER_REGISTRY']); print(d.get('subumbra-proxy',{}).get('allowed_keys',[]))"
```

If the output does not include all key_ids referenced in `litellm/config.yaml`,
re-run bootstrap (interactive wizard, Step 3) and expand `subumbra-proxy` scope.
A re-run requires `./post-bootstrap.sh` and
`docker compose up -d --force-recreate` afterwards.

---

## Exact Code Changes

### 1. `litellm/config.yaml` — Full replacement

Replace the entire file. Changes: all models get `api_base:
http://subumbra-proxy:8090/t` and plain `api_key: <key_id>` (no `subumbra:`
prefix); `callbacks:` stanza removed; header updated. Gemini entry is commented
out — it requires a non-standard URL path not approved in this round.

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
  # The universal api_base: http://subumbra-proxy:8090/t routes to the wrong
  # path (404). A per-model path-prefix exception is required but was not
  # approved by all three syntheses. Deferred to a follow-on round.
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

**2a. Environment block (lines 98-110)**

Current:
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

`LITELLM_MASTER_KEY` is a LiteLLM-internal auth key, not a Subumbra key — keep
it. Everything else is removed: `DEEPSEEK_API_BASE` (cargo-cult; model-level
`api_base` wins per codex-investigation evidence), the six Subumbra auth vars,
and the `SUBUMBRA_PROVIDER_PREFIXES` dead comment.

**2b. `depends_on` block (lines 113-115)**

Current:
```yaml
    depends_on:
      subumbra-keys:
        condition: service_healthy
```

Replace with:
```yaml
    depends_on:
      subumbra-keys:
        condition: service_healthy
      subumbra-proxy:
        condition: service_healthy
```

LiteLLM now depends on `subumbra-proxy` being healthy. The proxy's healthcheck
(`docker-compose.yml:194-199`) supports `service_healthy`. LiteLLM remains on
the `internal` network, which also carries `subumbra-proxy`, so Docker DNS
resolution of `subumbra-proxy:8090` works without network changes.

**2c. Optional cleanup (not required for correctness)**

Remove the `./litellm/custom_callbacks.py:/app/custom_callbacks.py:ro`
bind-mount from the `volumes:` list. The callbacks stanza is gone so the file
will not be loaded; the mount is inert if left in place.

---

### 3. `litellm/custom_callbacks.py` — Add legacy header

Replace the opening docstring at lines 1-5. Current first line:
```python
"""
Subumbra Adapter #1 — LiteLLM
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
Subumbra Adapter #1 — LiteLLM
```

No logic changes anywhere in the file.

---

### 4. `post-bootstrap.sh` — Remove litellm from drift check loop

The drift check loop at lines 90-107 checks running containers for stale
`SUBUMBRA_ACCESS_TOKEN`. After this round, litellm no longer carries that env
var, so the check is a no-op for litellm and should be removed.

Current block:
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
            subumbra-ui)    token_val="$SUBUMBRA_TOKEN_UI" ;;
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

Two changes only: `litellm` removed from the `for svc in` list; `litellm)`
case branch removed. All other lines are character-for-character identical.

---

### 5. `bootstrap/subumbra-bootstrap.py` — Update wizard Step 3 text

**5a. Lines 1046-1047** — wizard Step 3 description

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

**5b. Lines 647-664** — `_build_litellm_alignment_lines` function

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

The function signature is unchanged. The function body is a full replacement.

---

### 6. `README.md` — Update scope descriptions and model-adding instructions

**6a. Lines 221-226** — Adapter key scopes section

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

**6b. Lines 393-427** — "Adding / Changing Models" section

Replace the entire section (from `## Adding / Changing Models` through the
end of the `SUBUMBRA_PROVIDER_PREFIXES` block) with:

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

**None required.** No new log lines are added by this round.

Existing operator-visible signals cover all new failure modes:
- `subumbra-keys/app.py:546` already emits `reason_code=key_scope_denied` in
  the `403` response body — operator-visible in litellm logs and subumbra-keys
  access log. This is the primary new failure mode.
- `subumbra-proxy/app.py:200, 229-237` already logs record-fetch errors and
  upstream failures.
- LiteLLM's `unhealthy_endpoints` list surfaces models failing first
  completion.

No secret-bearing log lines may be introduced. No API keys, tokens, wrapped
DEKs, fingerprints, or `X-Subumbra-*` header values may appear in any log.

---

## Failure Modes

| Failure | Operator signal | Notes |
|---|---|---|
| `subumbra-proxy` not running when LiteLLM starts | LiteLLM unhealthy model list | Mitigated by `depends_on: subumbra-proxy: service_healthy` |
| `PROXY_ALLOWED_KEYS` missing a key_id | `403 key_scope_denied` in litellm logs | Run V3 prerequisite check before V2 live test |
| `api_base` misconfigured | Connection refused or wrong-path error | Verify with V1 static check first |
| Gemini model | n/a — entry removed | See Deferred |

---

## Verification Steps

### V1 — Static checks (no running stack required)

```bash
# 1. No "subumbra:" prefix remaining in api_key values
grep -n 'api_key.*subumbra:' litellm/config.yaml \
  && echo "FAIL: subumbra: prefix still present" || echo "PASS"

# 2. callbacks: stanza is gone
grep -n '^  callbacks:' litellm/config.yaml \
  && echo "FAIL: callbacks stanza present" || echo "PASS"

# 3. Subumbra auth vars gone from litellm environment block
python3 -c "
import re, sys
text = open('docker-compose.yml').read()
litellm_block = re.search(r'(  litellm:.*?)(?=\n  \w)', text, re.DOTALL)
if litellm_block:
    block = litellm_block.group(1)
    bad = [v for v in ['SUBUMBRA_ACCESS_TOKEN','SUBUMBRA_HMAC_KEY',
                        'SUBUMBRA_KEYS_URL','CF_WORKER_URL','DEEPSEEK_API_BASE']
           if v in block]
    print('FAIL:', bad) if bad else print('PASS')
"

# 4. api_base set for each active (non-commented) model
grep -c 'api_base:' litellm/config.yaml

# 5. Legacy header present in custom_callbacks.py
grep -n 'LEGACY' litellm/custom_callbacks.py | head -1
```

### V3 — PROXY_ALLOWED_KEYS scope check (prerequisite — run before V2)

Run the scope check from the Prerequisite section above. Confirm the output
includes all key_ids referenced in `litellm/config.yaml`'s active (uncommented)
model entries.

### V2 — Live end-to-end per provider

After V3 passes:

```bash
./scripts/council/clean-run.sh --round round-42-2-runtime-auth-reconciliation --agent <name>
```

Minimum passing set: Anthropic, OpenAI, Groq. Aspirational: DeepSeek, Mistral.
Gemini excluded from this round.

Expected: `200 OK` at `http://localhost:4000/v1/chat/completions`, streamed
response through LiteLLM → sidecar → CF Worker → provider → back.

---

## Known Limitations

- `LITELLM_ALLOWED_KEYS` and `SUBUMBRA_TOKEN_LITELLM` remain in `.env` and
  `SUBUMBRA_ADAPTER_REGISTRY` after this round. The `litellm` adapter registry
  entry is now a no-op but harmless. Cleanup is a future round.
- The `custom_callbacks.py` bind-mount in `docker-compose.yml` may remain if
  the optional cleanup is not applied. The file will not be loaded; mount is
  inert.
- Gemini (`gemini-2.0-flash`) is excluded. Entry is commented out in
  `config.yaml`. See Deferred.

---

## Deferred (Future Rounds)

- **Gemini migration** — Google's API path is `/v1beta/openai/` not `/v1/`.
  Universal `/t` api_base produces a `404`. Requires a per-model prefix
  exception (`api_base: http://subumbra-proxy:8090/t/v1beta/openai`) approved
  by all three reviewers before re-adding the entry.
- **Credential file import** ("swap & shred") — `bootstrap --import /path/.env`;
  enabled once this round is proven stable.
- **Post-bootstrap containerization** — host-side `.env` write must be resolved
  first.
- **Legacy `litellm` adapter registry cleanup** — remove `LITELLM_ALLOWED_KEYS`
  dead-write from `post-bootstrap.sh`, remove `litellm` entry from
  `SUBUMBRA_ADAPTER_REGISTRY`, remove `custom_callbacks.py` from repo.
- **Network cleanup** — remove `internal` network from `litellm` service (no
  longer contacts subumbra-keys directly; low priority).
