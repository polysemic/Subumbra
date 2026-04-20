# Round 42.2 Proposal — Runtime Auth Reconciliation

Date: 2026-04-19
Author: Claude
Round: round-42-2-runtime-auth-reconciliation

---

## 1. Evidence

### 1A. Module-level env freeze in `custom_callbacks.py`

`litellm/custom_callbacks.py:71-76` reads all Subumbra auth material as
module-level constants at process startup:

```python
SUBUMBRA_KEYS_URL       = os.environ.get("SUBUMBRA_KEYS_URL",       "http://subumbra-keys:9090")
SUBUMBRA_ACCESS_TOKEN   = os.environ.get("SUBUMBRA_ACCESS_TOKEN",   "")
SUBUMBRA_HMAC_KEY       = os.environ.get("SUBUMBRA_HMAC_KEY",       "").encode()
CF_WORKER_URL           = os.environ.get("CF_WORKER_URL",           "").rstrip("/")
CF_ACCESS_CLIENT_ID     = os.environ.get("CF_ACCESS_CLIENT_ID",     "")
CF_ACCESS_CLIENT_SECRET = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
```

These values are frozen at container startup. Any bootstrap rerun that
regenerates `SUBUMBRA_TOKEN_LITELLM` or `SUBUMBRA_HMAC_KEY` leaves the running
LiteLLM process with stale credentials until it is recreated.
`custom_callbacks.py:94-100` warns at import time if these are absent — but
there is no mechanism to refresh them at runtime.

### 1B. LiteLLM environment block in `docker-compose.yml`

`docker-compose.yml:98-109`: the bundled LiteLLM service receives six
Subumbra-specific environment variables:

```
SUBUMBRA_ACCESS_TOKEN: ${SUBUMBRA_TOKEN_LITELLM}
SUBUMBRA_HMAC_KEY: ${SUBUMBRA_HMAC_KEY}
SUBUMBRA_KEYS_URL: http://subumbra-keys:9090
CF_WORKER_URL: ${CF_WORKER_URL}
CF_ACCESS_CLIENT_ID: ${CF_ACCESS_CLIENT_ID:-}
CF_ACCESS_CLIENT_SECRET: ${CF_ACCESS_CLIENT_SECRET:-}
```

Every bootstrap rerun regenerates at least `SUBUMBRA_TOKEN_LITELLM` and
`SUBUMBRA_HMAC_KEY`. Both must be resynced into the running container or calls
fail. This is the root cause of recurring reconciliation breaks.

### 1C. The callback is gated by the `subumbra:` api_key prefix

`litellm/config.yaml:21` and all subsequent working model entries use
`api_key: "subumbra:<key_id>"`. `litellm/config.yaml:118` wires the global
callback: `callbacks: custom_callbacks.proxy_handler_instance`. The callback
intercept is guarded in the transport at `custom_callbacks.py:144-147`:

```python
ciphertext = request.headers.get("X-Subumbra-Ciphertext")
if not ciphertext:
    return await self._passthrough.handle_async_request(request)
```

Remove the `subumbra:` prefix → no `X-Subumbra-Ciphertext` header is injected
→ transport passthrough fires → callback is bypassed entirely. The activation
mechanism is the api_key prefix, not the callback registration itself.

### 1D. The transparent sidecar already does what the callback does

`subumbra-proxy/app.py:266-315` (`handle_transparent_request`):

1. Extracts `key_id` from `Authorization: Bearer <key_id>` or `x-api-key:
   <key_id>` — `app.py:135-163`
2. Fetches the full V2 record from subumbra-keys with HMAC signing —
   `app.py:298-305` via `fetch_record()` at `app.py:98-106`
3. Builds the target URL from `record["target_host"]` and the inbound path —
   `app.py:307` via `build_transparent_target_url()` at `app.py:182-190`
4. Strips inbound auth headers before calling the Worker — `app.py:56`, `app.py:308`
5. Calls CF Worker via canonical `POST /proxy` — `proxy_via_worker()` at `app.py:193-247`
6. Streams the response back

The sidecar owns all Subumbra auth material. `app.py:18-23` shows it holds
`SUBUMBRA_ACCESS_TOKEN`, `SUBUMBRA_HMAC_KEY`, `CF_WORKER_URL`, and CF Access
credentials. None of these need to exist in LiteLLM's environment.

### 1E. The CF Worker handles per-provider auth injection

`worker/src/providers.json:4-8` (Anthropic):
```json
"auth_header": "x-api-key",
"auth_prefix": ""
```
`worker/src/providers.json:12-15` (OpenAI, Groq, DeepSeek, Mistral, xAI):
```json
"auth_header": "authorization",
"auth_prefix": "Bearer "
```

The Worker strips all inbound auth headers and injects the correct credential
for the target provider. LiteLLM's auth header in the transparent path carries
the plain key_id — the sidecar strips it before the Worker sees it
(`app.py:56`, `TRANSPARENT_STRIP_HEADERS`). The Worker never sees LiteLLM's
auth header value.

### 1F. The sidecar accepts both LiteLLM auth header shapes

LiteLLM sends `x-api-key: <value>` for Anthropic provider calls and
`Authorization: Bearer <value>` for OpenAI-compatible calls.
`app.py:135-163` handles both patterns without any LiteLLM-side configuration
beyond `api_base` and a plain `api_key` string.

When both headers are present, `Authorization` takes precedence (`app.py:159`)
with a warning logged (`app.py:279-281`).

### 1G. Target URL is derived from the key record, not the inbound URL

`app.py:307`:
```python
target_url = build_transparent_target_url(record["target_host"], path, request.url.query)
```

The upstream host comes from the subumbra-keys record. The inbound path suffix
(e.g. `/v1/messages`, `/v1/chat/completions`) is preserved and appended.
LiteLLM sending to `http://subumbra-proxy:8090/t/v1/messages` produces path
`/v1/messages` which the sidecar appends to `api.anthropic.com`. The provider
URL is correct regardless of what LiteLLM passes as `api_base`.

### 1H. Gemini already uses per-model `api_base`

`litellm/config.yaml:81`:
```yaml
api_base: https://generativelanguage.googleapis.com/v1beta/openai/
```

This confirms LiteLLM honors per-model `api_base` overrides in `litellm_params`.
The same mechanism is the change vector for this round.

### 1I. Drift check in `post-bootstrap.sh` covers LiteLLM's token

`post-bootstrap.sh:92-106` checks `SUBUMBRA_ACCESS_TOKEN` for `litellm`,
`subumbra-ui`, `subumbra-proxy`, `subumbra-probe`. After this round LiteLLM
will not have `SUBUMBRA_ACCESS_TOKEN` set — the `litellm` case will always find
an empty `running_val` and silently pass. This is a dead check and should be
removed.

### 1J. `docs/standalone-litellm.md` does not exist

A standalone LiteLLM doc is referenced in prior council documents but the file
does not exist in the repository. No changes to this file are required.

---

## 2. Current vs Desired

### Current (callback path)

```
App → LiteLLM → [SubumbraTransport in custom_callbacks.py]
                  → subumbra-keys (HMAC-signed fetch — internal network, LiteLLM signs directly)
                  → CF Worker /proxy (internet)
                  → Provider
```

LiteLLM environment: `SUBUMBRA_ACCESS_TOKEN`, `SUBUMBRA_HMAC_KEY`,
`SUBUMBRA_KEYS_URL`, `CF_WORKER_URL`, optional CF Access values.
All must be resynced after every bootstrap rerun.

### Desired (transparent sidecar path)

```
App → LiteLLM (shape translation only, no Subumbra auth material)
    → subumbra-proxy /t/{path} (api_key = key_id in auth header)
    → [sidecar: HMAC-signed fetch, Worker call, streaming]
    → CF Worker /proxy
    → Provider
```

LiteLLM environment: `LITELLM_MASTER_KEY` only. Zero Subumbra auth material.
The sidecar already holds and maintains all auth material. Reconciliation
becomes unnecessary.

---

## 3. Proposal

### 3A. Update `litellm/config.yaml`

For each working model entry, replace:
```yaml
api_key: "subumbra:anthropic_prod"
```
With:
```yaml
api_base: http://subumbra-proxy:8090/t
api_key: anthropic_prod
```

The `api_base` routes LiteLLM's outbound request to the transparent sidecar.
The plain `api_key` value becomes the key_id that the sidecar extracts from
whichever auth header LiteLLM sends.

Remove the `callbacks:` stanza from `litellm_settings`. The callback is only
triggered when `api_key` starts with `"subumbra:"`. Without the prefix it is
inert, but removing the stanza makes the config unambiguous and eliminates
the callback import entirely.

**Scope:** Apply to all confirmed working providers: Anthropic, OpenAI, Groq,
DeepSeek, Mistral. The Gemini entry (`litellm/config.yaml:77-82`) already has
a custom `api_base` for a different reason and is explicitly out of scope.

### 3B. Update `docker-compose.yml` — bundled LiteLLM environment

Remove from the `litellm` service `environment:` block:
- `SUBUMBRA_ACCESS_TOKEN`
- `SUBUMBRA_HMAC_KEY`
- `SUBUMBRA_KEYS_URL`
- `CF_WORKER_URL`
- `CF_ACCESS_CLIENT_ID`
- `CF_ACCESS_CLIENT_SECRET`

Keep: `LITELLM_MASTER_KEY`, `DEEPSEEK_API_BASE` (verify against Q2 before
removing). LiteLLM reaches `subumbra-proxy:8090` on the existing `internal`
network — no network membership change needed.

### 3C. Update `post-bootstrap.sh` — remove dead drift check case

`post-bootstrap.sh:92-106`: Remove the `litellm` entry from the drift check
`for` loop. After this round `SUBUMBRA_ACCESS_TOKEN` will not be set in the
LiteLLM container; the check will always silently pass, which is misleading.

The `post-bootstrap.sh:80` check that verifies `SUBUMBRA_TOKEN_LITELLM` is
written to `.env` should remain unchanged — the token is still generated by
bootstrap and is a valid service token for subumbra-keys.

### 3D. Mark `custom_callbacks.py` as legacy

Add a header comment to `litellm/custom_callbacks.py` marking it as the legacy
integration path, superseded by the transparent sidecar in Round 42.2. Do not
delete the file in this round.

### 3E. Verification

**V1 — Static config check:**
- `litellm/config.yaml` contains no `callbacks:` stanza and no `subumbra:`
  prefix in any `api_key` value.
- `docker-compose.yml` `litellm` environment block contains no
  `SUBUMBRA_ACCESS_TOKEN`, `SUBUMBRA_HMAC_KEY`, or `CF_WORKER_URL`.

**V2 — Live end-to-end:**
After `docker compose up -d --force-recreate litellm`, make one successful
completion request through LiteLLM for each confirmed working provider
(Anthropic, OpenAI, Groq, DeepSeek, Mistral). Success condition: HTTP 200
from LiteLLM, no `subumbra-keys` auth errors in `subumbra-proxy` logs.

---

## 4. Failure Modes

| Failure | Cause | Handling |
|---|---|---|
| LiteLLM Anthropic provider ignores `api_base` in `litellm_params` | Native Anthropic SDK may bypass `api_base` | Declare model as `openai/<model>` with `api_base` if needed; sidecar derives upstream from key record regardless |
| LiteLLM does not append `/v1` path when `api_base` ends with `/t` | URL construction varies | Test both `/t` and `/t/`; sidecar captures full `{path:path}` suffix |
| `subumbra-proxy` unreachable from bundled LiteLLM | Network misconfiguration | Both on `internal`; `http://subumbra-proxy:8090` resolves via Docker DNS |
| `DEEPSEEK_API_BASE` env var overrides per-model `api_base` | LiteLLM env var precedence | Verify DeepSeek; remove `DEEPSEEK_API_BASE` from compose if it conflicts |
| Stale `subumbra:key_id` values left in config | Incomplete edit | V1 static check catches all remaining `subumbra:` prefixes before any live test |
| Gemini entry inadvertently touched | Out-of-scope change | Leave `litellm/config.yaml:77-82` unchanged; Gemini is explicitly excluded |

---

## 5. Exclusions

- Deleting `custom_callbacks.py` — mark legacy only; remove in a later round
- Changing `subumbra-proxy/app.py` — it already works correctly
- Changing the CF Worker
- Handling Gemini or GitHub providers — not in the confirmed working set
- Creating `docs/standalone-litellm.md` — file does not exist; doc work is a
  separate round
- Removing `SUBUMBRA_TOKEN_LITELLM` from the `.env` write-check in
  `post-bootstrap.sh` — token is still generated and used by subumbra-keys
- Redesigning token or HMAC architecture
- Broad observability changes

---

## 6. Open Questions

**Q1: Does LiteLLM honor `api_base` from `litellm_params` for the Anthropic
provider specifically?**

LiteLLM's Anthropic provider may use the native SDK's base URL resolution
instead of honoring `api_base`. If so, the model entry may need to be declared
as `openai/<model-name>` to force the OpenAI-compat code path, which does
honor `api_base`. The sidecar derives the real upstream from
`record["target_host"]` regardless, so the provider routing is unaffected.
This must be confirmed before the round is closed.

**Q2: Does `DEEPSEEK_API_BASE` in `docker-compose.yml:100` take precedence
over per-model `api_base` in config.yaml?**

If LiteLLM resolves the env var before the config value, DeepSeek models will
bypass the sidecar. This must be verified; if conflicting, remove
`DEEPSEEK_API_BASE` from the compose environment block in this round.

**Q3: Should the `litellm` case in the `post-bootstrap.sh` drift check be
replaced or simply removed?**

After this round the check is a dead no-op. The clean fix is removal with no
replacement. A future round could add a sidecar-health check instead, but that
is out of scope here.
