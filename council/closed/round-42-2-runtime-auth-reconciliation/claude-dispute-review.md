# Round 42.2 — Claude Dispute Review

Date: 2026-04-19
Author: Claude
Input: `codex-disputes.md`, source code reads

Both disputes are resolvable by code read alone. No new investigation is needed.
Synthesis can be skipped. The approved plan should be updated directly (v2).

---

## Dispute 1 — V3 prerequisite command uses base64 decode incorrectly

### Codex's claim

The approved plan's V3 prerequisite command calls `base64.b64decode(reg)` before
parsing JSON, but bootstrap writes `SUBUMBRA_ADAPTER_REGISTRY` as plain JSON.

### Evidence — confirmed correct

`bootstrap/subumbra-bootstrap.py:1720`:
```python
f"SUBUMBRA_ADAPTER_REGISTRY={json.dumps(adapter_registry, separators=(',', ':'))}",
```

The registry is written as compact JSON with no encoding step.
`post-bootstrap.sh:67` copies this raw value into `.env` via `update_env`.

The approved plan's V3 command includes:
```python
reg = json.loads(base64.b64decode(reg))
```

This will raise `binascii.Error` on a plain JSON string. The command is broken
as written and must be corrected.

### Fix — code read only

Replace the V3 prerequisite command in the approved plan with:

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

If the operator runs `docker compose up -d` (env loaded from `.env` into the
shell), they can also run:

```bash
python3 -c "
import os, json
reg = os.environ['SUBUMBRA_ADAPTER_REGISTRY']
data = json.loads(reg)
print('subumbra-proxy allowed_keys:', data.get('subumbra-proxy', {}).get('allowed_keys', []))
"
```

**Resolution: Dispute 1 is valid. Approved plan must be corrected.**

---

## Dispute 2 — Gemini `api_base` exception not in Codex/Gemini syntheses

### Codex's claim

The approved plan includes `api_base: http://subumbra-proxy:8090/t/v1beta/openai`
for the Gemini model. Only the Claude synthesis approved this exception. Codex
and Gemini syntheses state the contract is `/t` with no provider prefix.

### Position and reasoning

**The exception is mechanically correct — but Gemini should be excluded from
this round regardless.**

**Why the exception is mechanically sound:**

`subumbra-proxy/app.py:266-307`:
```python
@app.api_route("/t/{path:path}", methods=TRANSPARENT_METHODS)
async def handle_transparent_request(path: str, request: Request):
    ...
    target_url = build_transparent_target_url(record["target_host"], path, request.url.query)
```

`build_transparent_target_url` at `app.py:182-190` builds:
`https://{target_host}/{captured_path}`

With `api_base: http://subumbra-proxy:8090/t/v1beta/openai` and LiteLLM sending
`/chat/completions`, the full URL is:
`http://subumbra-proxy:8090/t/v1beta/openai/chat/completions`

Captured path: `v1beta/openai/chat/completions`
Target URL: `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` ✓

Without the prefix (plain `/t`), the captured path is `v1/chat/completions` →
`https://generativelanguage.googleapis.com/v1/chat/completions` — a `404` because
Google's OpenAI-compatible endpoint lives at `/v1beta/openai/`, not `/v1/`.

So the Gemini model CANNOT use the universal `/t` base URL. It requires either
the path prefix in `api_base` or removal from config.

**Why the exception does not belong in this round:**

The two other syntheses did not include the Gemini path exception in their
consensus sections. Including it in the approved plan under "three-synthesis
consensus" attribution is incorrect. The clean resolution is to remove the
Gemini model entries from the approved plan entirely:

1. Neither Codex nor Gemini syntheses listed Gemini in their V2 verification
   provider sets (Anthropic, OpenAI, Groq, DeepSeek, Mistral only).
2. The current `gemini-2.0-flash` entry already carries a non-standard
   `api_base` directly to Google — it is already a special case.
3. Removing it from config.yaml for this round has no impact on the core
   mechanism being proven.

**Fix — remove Gemini from the approved plan:**

Remove the `gemini-2.0-flash` model entry from the approved plan's
`config.yaml` replacement entirely. Add it to the deferred list with a note:

> Gemini (`generativelanguage.googleapis.com`) requires `api_base` to include
> the `/v1beta/openai` path prefix because Google's OpenAI-compatible endpoint
> is not at `/v1/`. A future round should explicitly verify the
> `api_base: http://subumbra-proxy:8090/t/v1beta/openai` exception and add it
> back to config.yaml once all three syntheses have reviewed and approved the
> per-model prefix mechanism.

**Resolution: Dispute 2 is valid. Remove Gemini from approved plan scope.**

---

## Summary

| Dispute | Valid? | Resolution | Approved plan change |
|---|---|---|---|
| D1: V3 command uses base64 incorrectly | **YES** | Code read confirms plain JSON | Replace V3 command with plain JSON parser |
| D2: Gemini exception not in consensus | **YES** | Remove Gemini from scope | Drop `gemini-2.0-flash` entry; add to deferred |

Both fixes are unambiguous code reads. No new investigation required.
**Synthesis can be skipped. Proceed directly to approved plan v2.**
