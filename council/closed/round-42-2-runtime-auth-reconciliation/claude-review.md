# Round 42.2 — Independent Review: Runtime Auth Reconciliation

Date: 2026-04-19
Reviewer: Claude
Files reviewed: `litellm/custom_callbacks.py`, `litellm/config.yaml`,
`subumbra-proxy/app.py`, `docker-compose.yml`, `post-bootstrap.sh`,
`subumbra-keys/app.py`, `bootstrap/subumbra-bootstrap.py`,
`worker/src/providers.json`, all proposals in `council/round-42-2-runtime-auth-reconciliation/`

---

## Findings Table

| # | Finding | Severity | File:Line |
|---|---|---|---|
| F1 | Key-scope is enforced per-adapter; `PROXY_ALLOWED_KEYS` set independently at bootstrap — likely does NOT include LiteLLM key_ids | **BLOCKER** | `subumbra-keys/app.py:546`, `bootstrap/subumbra-bootstrap.py:1053-1057` |
| F2 | LiteLLM Anthropic provider may not honor `api_base` in `litellm_params` | **HIGH RISK** | `litellm/config.yaml:18-29`, `custom_callbacks.py:259` |
| F3 | `DEEPSEEK_API_BASE` env var in docker-compose may override per-model `api_base` | **MEDIUM RISK** | `docker-compose.yml:100` |
| F4 | Transparent sidecar mechanism is technically sound for the proposed change | PASS | `subumbra-proxy/app.py:266-315` |
| F5 | Both LiteLLM auth header shapes extracted correctly | PASS | `subumbra-proxy/app.py:135-163` |
| F6 | `api_base` format `/t` (no provider prefix) is correct | PASS | `subumbra-proxy/app.py:182-190`, `app.py:307` |
| F7 | Callback deactivation mechanism is correct | PASS | `custom_callbacks.py:144-147`, `365-366` |
| F8 | `depends_on` for `litellm` service not updated to require `subumbra-proxy` | MINOR GAP | `docker-compose.yml:113-115` |
| F9 | `LITELLM_ALLOWED_KEYS` still written to `.env` after this round — dead write | MINOR | `post-bootstrap.sh:74` |
| F10 | `custom_callbacks.py` still bind-mounted after `callbacks:` stanza removed | MINOR | `docker-compose.yml:91` |
| F11 | No new secret-bearing log lines introduced by the proposed changes | PASS (logging) | — |
| F12 | Existing sidecar logging is sufficient for new failure modes | PASS (logging) | `subumbra-proxy/app.py:200, 229-237` |

---

## Detailed Analysis

### F1 — Key-scope blocker (BLOCKER)

`subumbra-keys/app.py:546`:
```python
if key_id not in adapter["allowed_keys"]:
    ...
    reason_code="key_scope_denied"
    return _err("forbidden", 403)
```

This check runs on every `/keys/{key_id}` request. Each adapter has its own
`allowed_keys` list built at bootstrap time.

`bootstrap/subumbra-bootstrap.py:1053-1057` (interactive wizard path):
```python
allowed_keys_by_adapter = {
    "litellm": _prompt_allowed_keys("LiteLLM", available_key_ids),
    "subumbra-proxy": _prompt_allowed_keys("subumbra-proxy", available_key_ids),
    "subumbra-probe": _prompt_allowed_keys("subumbra-probe", available_key_ids),
    "subumbra-ui": [],
}
```

The wizard prompts for LiteLLM and subumbra-proxy scope **separately**. In a
typical deployment, operators likely gave LiteLLM access to all provider keys
and gave subumbra-proxy a different (possibly smaller or different) set.

`bootstrap/subumbra-bootstrap.py:876-881` (CI/headless path):
```python
allowed_keys_by_adapter = {
    adapter_id: _parse_allowed_keys_csv(os.environ.get(scope_var, ""))
    ...
}
```

An empty `PROXY_ALLOWED_KEYS` env var (common in a headless deployment that
only specified LiteLLM scope) means `subumbra-proxy` gets an empty `allowed_keys`
list → every record fetch via the transparent sidecar returns `403`.

**Impact:** In any existing deployment where `PROXY_ALLOWED_KEYS` does not
include all LiteLLM key_ids, every completion request through the new path will
fail immediately at the subumbra-keys fetch step, before the CF Worker is
ever contacted.

**Required action:** The approved plan must include a prerequisite step:

```bash
# Inspect current scope before live test
grep SUBUMBRA_ADAPTER_REGISTRY .env | python3 -c \
  "import sys, json; r = json.loads(sys.stdin.read().split('=',1)[1]); \
   [print(k, '->', r[k]['allowed_keys']) for k in r]"
```

If `subumbra-proxy` is missing key_ids that appear in `litellm`'s `allowed_keys`,
the operator must re-bootstrap with `PROXY_ALLOWED_KEYS` expanded to match. This
is an input change at bootstrap time, not a code change. The approved plan should
explicitly gate V2 (live test) on this V3 check.

---

### F2 — Anthropic `api_base` honor (HIGH RISK)

`litellm/config.yaml:18-21`:
```yaml
- model_name: claude-opus-4
  litellm_params:
    model: anthropic/claude-opus-4-5
    api_key: "subumbra:anthropic_prod"
```

When `model` uses the `anthropic/` prefix, LiteLLM routes through its native
Anthropic SDK path. The Anthropic SDK constructs its own base URL. Whether it
honors `api_base` from `litellm_params` when using the `anthropic/` prefix is
not confirmed by code review alone.

`custom_callbacks.py:259`:
```python
("anthropic",  _litellm.LlmProviders.ANTHROPIC,   None),
```

The current callback path explicitly wires `SubumbraTransport` into the Anthropic
provider's HTTP client. This workaround exists precisely because the native
Anthropic SDK path has non-standard behavior. That workaround is removed by
this round's change.

**If LiteLLM's `anthropic/` provider does not honor `api_base`:**
- The Anthropic-prefixed models will bypass the sidecar entirely and attempt to
  reach `api.anthropic.com` directly
- The call will fail because the plain key_id (`anthropic_prod`) is not a real
  Anthropic API key

**Mitigation path:** Declare Anthropic models as `openai/claude-*` with `api_base`
pointing to the sidecar. The sidecar derives the real upstream from
`record["target_host"]` (= `api.anthropic.com`) regardless of the LiteLLM
provider prefix. This requires `api.anthropic.com` to accept OpenAI-shaped
requests, which it does not — the request body shape must match the Anthropic
API.

**Correct mitigation path:** The sidecar is transparent to body content. LiteLLM
with `model: anthropic/claude-opus-4-5` builds an Anthropic-shaped request body.
If `api_base` is honored, the request arrives at the sidecar with an
Anthropic-shaped body, which the sidecar passes through to `api.anthropic.com`
unchanged. The sidecar never touches the body. So if `api_base` is honored, the
flow is correct. **The only question is whether `api_base` is honored for the
`anthropic/` prefix.** This must be verified empirically before the round closes.

---

### F3 — `DEEPSEEK_API_BASE` env var conflict (MEDIUM RISK)

`docker-compose.yml:100`:
```yaml
DEEPSEEK_API_BASE: https://api.deepseek.com/v1
```

This env var is passed to the LiteLLM container unconditionally. If LiteLLM
resolves `DEEPSEEK_API_BASE` before the per-model `api_base` in `litellm_params`,
DeepSeek models will bypass the sidecar and attempt to call
`api.deepseek.com/v1/chat/completions` directly with the plain key_id (not a
real API key).

**Required action:** Remove `DEEPSEEK_API_BASE` from the LiteLLM environment
block in `docker-compose.yml` as part of this round. The per-model `api_base`
in `litellm/config.yaml` is the correct override, and the env var is redundant
at minimum, harmful at worst.

---

### F4 — Sidecar mechanism is sound (PASS)

`subumbra-proxy/app.py:266-315` correctly:
1. Extracts key_id from auth header — `app.py:135-163`
2. Validates format — `app.py:166-167`
3. Fetches V2 record via HMAC-signed request to subumbra-keys — `app.py:298-305`
4. Derives target URL from `record["target_host"]` + inbound path — `app.py:307`
5. Strips inbound auth headers — `app.py:56, 308`
6. Forwards to CF Worker — `app.py:309` via `proxy_via_worker()`
7. Streams response

The sidecar does not inspect or modify the request body. LiteLLM's
provider-shaped body (Anthropic or OpenAI format) passes through unchanged.

---

### F5 — Both auth header shapes work (PASS)

`subumbra-proxy/app.py:135-163`:
- `Authorization: Bearer <key_id>` — Bearer stripped, `key_id` extracted
- `x-api-key: <key_id>` — extracted directly
- When both present: `Authorization` takes precedence, warning logged at `app.py:279`

LiteLLM sends `x-api-key` for Anthropic provider calls and `Authorization:
Bearer` for OpenAI-compat calls. Both paths are handled. The plain key_id
(`anthropic_prod`) satisfies `KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")` at
`app.py:55`.

---

### F6 — `api_base` format `/t` is correct (PASS)

The transparent route is `@app.api_route("/t/{path:path}")` at `app.py:266`.
`{path:path}` captures everything after `/t/` with no length or content
restriction.

LiteLLM sending to `http://subumbra-proxy:8090/t` will append its provider path:
- OpenAI: `http://subumbra-proxy:8090/t/v1/chat/completions` → path = `v1/chat/completions`
- Anthropic: `http://subumbra-proxy:8090/t/v1/messages` → path = `v1/messages`

`build_transparent_target_url()` at `app.py:182-190`:
```python
def build_transparent_target_url(target_host: str, path: str, query: str) -> str:
    clean_path = path.lstrip("/")
    if clean_path:
        target_url = f"https://{target_host}/{clean_path}"
    ...
```

Leading slashes are stripped — double-slash from LiteLLM's URL construction
`/t//v1/...` is handled correctly.

The proposals that add provider prefixes to `api_base` (e.g. `/t/openai/`) are
incorrect — they would produce `api.openai.com/openai/v1/chat/completions`,
which is a broken URL. Correct format is `http://subumbra-proxy:8090/t` only.

---

### F7 — Callback deactivation is correct (PASS)

`custom_callbacks.py:144-147` (transport gate):
```python
ciphertext = request.headers.get("X-Subumbra-Ciphertext")
if not ciphertext:
    return await self._passthrough.handle_async_request(request)
```

`custom_callbacks.py:365-366` (hook gate):
```python
api_key = kwargs.get("api_key", "")
if not isinstance(api_key, str) or not api_key.startswith("subumbra:"):
    return None
```

When `api_key` is a plain string (no `subumbra:` prefix), the hook returns
immediately and `_wire_transport_once()` is never called (`custom_callbacks.py:381`).
No `X-Subumbra-Ciphertext` header is injected. The transport passthrough fires.

Even if the `callbacks:` stanza is left in `litellm_settings`, the callback is
inert for all non-prefixed keys. Removing the stanza makes this explicit and
prevents the module from being imported at all.

---

### F8 — `depends_on` not updated (MINOR GAP)

`docker-compose.yml:113-115`:
```yaml
depends_on:
  subumbra-keys:
    condition: service_healthy
```

After this round LiteLLM's critical dependency shifts from `subumbra-keys`
(direct) to `subumbra-proxy` (which itself depends on `subumbra-keys`). The
`depends_on` should be updated to require `subumbra-proxy: condition: service_healthy`
instead of (or in addition to) `subumbra-keys`. Without this, LiteLLM can start
before `subumbra-proxy` is healthy and fail on the first request.

`subumbra-proxy` already has a healthcheck defined at `docker-compose.yml:194-198`.

This is a minor gap, not a blocker, but should be included in the approved plan.

---

### F9, F10 — Dead writes and bind-mount (MINOR)

`post-bootstrap.sh:74`: `update_env "LITELLM_ALLOWED_KEYS" "$LITELLM_ALLOWED_KEYS"` —
still written to `.env` after this round. The value is no longer passed to the
LiteLLM container, so it's a dead write. Not harmful, but cleanup item.

`docker-compose.yml:91`: `./litellm/custom_callbacks.py:/app/custom_callbacks.py:ro`
— still bind-mounted even after the `callbacks:` stanza is removed. With no
stanza, LiteLLM never imports or uses the file. Not harmful. Cleanup item.

---

### F11, F12 — Logging (PASS)

The proposed changes introduce two new failure modes with operator-visible signals:

| Failure mode | Existing log signal | Location |
|---|---|---|
| `key_scope_denied` (proxy token lacks key_id) | `get_key: forbidden adapter=subumbra-proxy key_id=<key_id>` at WARNING | `subumbra-keys/app.py:547-549` |
| `subumbra-proxy unreachable` from LiteLLM | LiteLLM will log an httpx connection error | LiteLLM internal |
| `api_base` not honored | LiteLLM attempts provider URL directly, fails with auth error | LiteLLM internal |

The existing sidecar logging at `app.py:200` (`request key_id=... method=... target_url=...`)
and `app.py:241` (`complete key_id=... status=...`) is sufficient to trace requests
through the new path without additional instrumentation.

No new log lines are needed in `subumbra-proxy/app.py` for this round.
No secret values appear in any existing or proposed log lines for this path.

---

## Recommendations

### Required before approval

1. **The approved plan must include F1 as a mandatory prerequisite step**: verify
   `PROXY_ALLOWED_KEYS` in the running `SUBUMBRA_ADAPTER_REGISTRY` includes all
   LiteLLM key_ids before any live test. Provide the diagnostic command.

2. **The approved plan must address F2 explicitly**: state whether Anthropic
   models are tested with `model: anthropic/<model>` as-is (and report whether
   `api_base` is honored), or fall back to `model: openai/<model>` if needed.
   Either path is acceptable if documented and tested.

3. **The approved plan must remove `DEEPSEEK_API_BASE` from docker-compose.yml**
   (F3): this is a clean correctness fix, not extra scope.

### Recommended additions to approved plan

4. **Update `depends_on` for the `litellm` service** (F8) to include
   `subumbra-proxy: condition: service_healthy`. This is a small correct change
   that prevents startup races.

5. **Add a V3 verification step** that runs the `SUBUMBRA_ADAPTER_REGISTRY`
   scope diagnostic and confirms `subumbra-proxy` has the required key_ids before
   V2 live test. The round should not be marked PASS until this step passes.

### Carry-forward cleanup (not this round)

- Remove `LITELLM_ALLOWED_KEYS` write from `post-bootstrap.sh` (F9) — separate
  bootstrap cleanup round
- Remove `custom_callbacks.py` bind-mount from docker-compose (F10) — after
  full legacy removal
- Remove `litellm` entry from `SUBUMBRA_ADAPTER_REGISTRY` — after legacy removal
