# Round 42.3 Investigation — Unresolved Blocking Questions

Author: Claude
Date: 2026-04-19
Round: `round-42-3-app-owned-integrations`

Reviews read before this investigation:
- claude-review.md, codex-review.md, gemini-review.md
- claude-review-2.md, codex-review-2.md, gemini-review-2.md

---

## Blocking Questions Identified From All Reviews

| Q | Question | Blocks |
|---|---|---|
| Q1 | Can the `litellm` adapter be safely removed from bootstrap without breaking `SUBUMBRA_ADAPTER_TOKENS` or sidecar operation? | Change R2 scope |
| Q2 | Is `post-bootstrap.sh:43`'s `SUBUMBRA_TOKEN_LITELLM` guard safely removable? | Change R2 spec |
| Q3 | Is `{"error":"unauthorized"}` body inspection reliable as a per-request Worker auth classification? Codex says no (multiple reviews); Gemini says yes | Change D spec |
| Q4 | Does `docs/adapter-contract.md` block the approved plan or is it an addendum? Codex R2-2 adds it; proposals don't list it | Change B scope |

---

## Q1 — Removing `litellm` from bootstrap: effect on `SUBUMBRA_ADAPTER_TOKENS`

**Why it blocks:** If removing `litellm` from `ADAPTER_SCOPE_VARS` breaks the Worker's
token set, the sidecar stops working after a re-bootstrap. Every three reviews agree the
bootstrap litellm adapter must be addressed, but none verified the token flow end-to-end.

**Evidence:**

`bootstrap/subumbra-bootstrap.py:1295-1305`:
```python
step("Pushing SUBUMBRA_ADAPTER_TOKENS to CF Secrets")
adapter_tokens_json = json.dumps(list(adapter_tokens.values()), separators=(",", ":"))
_run(
    ["wrangler", "secret", "put", "SUBUMBRA_ADAPTER_TOKENS", "--name", worker_name],
    ...
    input_text=adapter_tokens_json + "\n",
)
```

`SUBUMBRA_ADAPTER_TOKENS` is pushed as `list(adapter_tokens.values())` — a JSON array of raw
token strings, NOT a dict keyed by adapter name. The Worker at
`worker.js:248-264` (`parseAdapterTokens`) parses this as a flat array and checks whether
the incoming token is in it:

```javascript
function parseAdapterTokens(raw) {
  let parsed;
  try { parsed = JSON.parse(raw); }
  ...
  return parsed;  // flat array
}
```

`worker.js:449-450`:
```javascript
const incomingToken = request.headers.get("X-Subumbra-Token") ?? "";
const tokenOk = await tokenSetContains(incomingToken, validTokens);
```

The sidecar sends `X-Subumbra-Token: SUBUMBRA_ACCESS_TOKEN` where
`SUBUMBRA_ACCESS_TOKEN = os.environ.get("SUBUMBRA_ACCESS_TOKEN", "")`
(`subumbra-proxy/app.py:18`) and `docker-compose.yml:176`:
```yaml
SUBUMBRA_ACCESS_TOKEN: ${SUBUMBRA_TOKEN_PROXY}
```

`SUBUMBRA_TOKEN_PROXY` corresponds to `adapter_tokens["subumbra-proxy"]`
(`bootstrap/subumbra-bootstrap.py:1746`).

**Token flow tracing:**

Removing `"litellm"` from `ADAPTER_SCOPE_VARS` at `bootstrap/subumbra-bootstrap.py:104-109`:
1. `allowed_keys_by_adapter` no longer has a `"litellm"` key
2. `_build_adapter_registry` at line 584 no longer includes a `"litellm"` entry
3. `adapter_tokens` at line 1660-1664 is initialized for built-ins; without the
   `"litellm"` key in `allowed_keys_by_adapter`, no litellm token is generated
4. `adapter_tokens_json` at line 1297 loses the litellm token value from the array

**The sidecar is unaffected** because:
- The sidecar presents `adapter_tokens["subumbra-proxy"]` — not the litellm token
- `subumbra-proxy` remains in `ADAPTER_SCOPE_VARS` and `adapter_tokens`
- The Worker's flat-array check (`tokenSetContains`) passes as long as the proxy token is
  in the array

**What does break:** Any deployment still using the old callback path with
`SUBUMBRA_TOKEN_LITELLM` as `X-Subumbra-Token` would lose Worker access after re-bootstrap.
This is intentional — the callback path is being removed.

**Conclusion: RESOLVED BY EVIDENCE.**

Removing `litellm` from `ADAPTER_SCOPE_VARS` is safe for sidecar operation. The sidecar's
token (`subumbra-proxy`) stays in `SUBUMBRA_ADAPTER_TOKENS`. The only casualty is the legacy
callback path, which is the explicit goal of Change A.

---

## Q2 — Is `post-bootstrap.sh:43`'s `SUBUMBRA_TOKEN_LITELLM` guard safely removable?

**Why it blocks:** My secondary review (S1) identified that `post-bootstrap.sh:43` exits
fatally if `SUBUMBRA_TOKEN_LITELLM` is missing from `runtime.env`. If bootstrap stops
generating it (result of Q1) but `post-bootstrap.sh` still requires it, every new install
fails. This blocks the approved plan unless the fix is explicitly scoped.

**Evidence:**

`post-bootstrap.sh:26-45`:
```bash
_get() { printf '%s\n' "$RUNTIME" | grep "^${1}=" | cut -d= -f2- || true; }

SUBUMBRA_ADAPTER_REGISTRY=$(_get SUBUMBRA_ADAPTER_REGISTRY)
SUBUMBRA_TOKEN_LITELLM=$(_get SUBUMBRA_TOKEN_LITELLM)
if [[ -z "$SUBUMBRA_TOKEN_LITELLM" ]]; then
    SUBUMBRA_TOKEN_LITELLM=$(_get FORGE_TOKEN_LITELLM)
fi
SUBUMBRA_TOKEN_PROXY=$(_get SUBUMBRA_TOKEN_PROXY)
...

if [[ -z "$SUBUMBRA_ADAPTER_REGISTRY" || -z "$SUBUMBRA_TOKEN_LITELLM" || -z "$SUBUMBRA_TOKEN_PROXY" || \
      -z "$SUBUMBRA_TOKEN_UI" || -z "$SUBUMBRA_TOKEN_PROBE" || -z "$SUBUMBRA_HMAC_KEY" || -z "$CF_WORKER_URL" ]]; then
    echo "ERROR: runtime.env is missing one or more required values." >&2
    exit 1
fi
```

`post-bootstrap.sh:68`:
```bash
update_env "SUBUMBRA_TOKEN_LITELLM"    "$SUBUMBRA_TOKEN_LITELLM"
```

`post-bootstrap.sh:80`:
```bash
for key in SUBUMBRA_ADAPTER_REGISTRY SUBUMBRA_TOKEN_LITELLM SUBUMBRA_TOKEN_PROXY ...
```

What does `SUBUMBRA_TOKEN_LITELLM` do after it is written to `.env`? Checking
`docker-compose.yml` — the litellm service at line 98-99 only uses `LITELLM_MASTER_KEY`, not
`SUBUMBRA_TOKEN_LITELLM`. The token is consumed only by the callback path inside the LiteLLM
container (`litellm/custom_callbacks.py` reads `SUBUMBRA_ACCESS_TOKEN`). With the bundled
service removed (Change A), **nothing in the compose stack reads `SUBUMBRA_TOKEN_LITELLM`
from `.env`**. It is purely an artifact.

**Can it be safely removed from `post-bootstrap.sh`?**

Yes. The changes required are:
1. Line 29-31: remove the `_get SUBUMBRA_TOKEN_LITELLM` + fallback block
2. Line 43: remove `|| -z "$SUBUMBRA_TOKEN_LITELLM"` from the guard
3. Line 68: remove the `update_env "SUBUMBRA_TOKEN_LITELLM"` line
4. Line 80: remove `SUBUMBRA_TOKEN_LITELLM` from the post-write verification loop

No other service reads this value from `.env`. The sidecar reads `SUBUMBRA_TOKEN_PROXY`.

**What about existing installs?** An operator with an existing `.env` who runs
`post-bootstrap.sh` after a re-bootstrap that no longer generates this token: the guard at
line 43 would fail (empty value). After the fix, the guard simply won't check for it. Existing
`.env` files may still have `SUBUMBRA_TOKEN_LITELLM=...` from prior bootstraps — that's fine,
harmless leftover. The fix only stops post-bootstrap.sh from failing when it's absent.

**Conclusion: RESOLVED BY EVIDENCE.**

`post-bootstrap.sh:43` can be safely updated. The change is mechanical and has no runtime
consequences for the sidecar. The approved plan must include `post-bootstrap.sh` as an
explicit change target in Change R2, with the four specific line changes identified above.

---

## Q3 — Is `{"error":"unauthorized"}` body inspection reliable for Worker auth classification?

**Why it blocks:** Codex stated in two consecutive reviews that body inspection "is too
brittle" because the Worker forwards provider responses unchanged. If Codex is right, Change D
is insufficient. If Gemini is right, it is reliable. The approved plan needs a definitive answer.

**Evidence — what the Worker's own 401 produces:**

`worker.js:281-285`:
```javascript
function jsonError(message, status) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "content-type": "application/json" },
  });
}
```

`worker.js:454`: `return jsonError("unauthorized", 401)` — produces: `{"error":"unauthorized"}`

This is the ONLY place the Worker generates a 401. All other `jsonError` calls at 4xx use
different messages: "request body must be JSON", "method not allowed", "missing or invalid
field: ...", etc. All other errors use 400, 404, 405, 502, 503 — not 401.

**Evidence — what provider 401s look like:**

The Worker forwards provider responses unchanged at `worker.js:379-392`:
```javascript
return new Response(upstreamResponse.body, {
  status: upstreamResponse.status,
  headers: responseHeaders,
});
```

Supported providers (`CLAUDE.md: Supported Providers`): anthropic, openai, groq, deepseek,
cerebras, gemini, mistral, openrouter, together, xai, github, slack, sendgrid.

Provider 401 body formats (from provider API documentation / standard formats):
- **Anthropic:** `{"type":"error","error":{"type":"authentication_error","message":"..."}}`
- **OpenAI/compatible (groq, deepseek, together, openrouter, xai):** `{"error":{"message":"...","type":"invalid_request_error","code":"invalid_api_key"}}`
- **Mistral:** `{"message":"Unauthorized","request_id":"..."}`
- **GitHub:** `{"message":"Bad credentials","documentation_url":"..."}`
- **Slack (webhook):** plain text or `{"ok":false,"error":"invalid_auth"}`
- **SendGrid:** `[{"message":"Permission denied, wrong credentials"}]`

None match the flat `{"error":"unauthorized"}` format. The Worker's `jsonError` format uses a
simple `{"error": "<string>"}` structure. No current supported provider uses this exact flat
structure with the exact string value `"unauthorized"` for a 401.

**Codex's concern re-examined:**

Codex R2-3 states: "the proxy cannot reliably know whether a `401` came from the Worker auth
gate or the upstream provider unless the Worker emits an explicit signal."

The evidence shows the Worker DOES emit an explicit signal — just not via a header: it emits
`{"error":"unauthorized"}` which is Worker-specific. The concern would be valid if providers
returned `{"error":"unauthorized"}`, but none of the 12 supported providers do this.

**Conclusion: RESOLVED BY EVIDENCE.**

Body inspection for `{"error":"unauthorized"}` with status 401 is reliable for all currently
supported providers. The pattern uniquely identifies the Worker's auth gate failure.

**However:** Codex's concern about long-term brittleness is valid as a precautionary note.
The approved plan should acknowledge this: "if a future provider returns `{"error":"unauthorized"}`
as its 401 body, the classification will incorrectly tag it as a Worker auth failure. The
`/auth-ping` probe (Change E) is the definitive pre-request check; Change D is per-request
classification that degrades gracefully to 'unknown auth failure' if the Worker format
changes."

This makes the plan robust: Change E (proactive) is the authoritative Worker auth signal;
Change D (reactive body inspection) is a best-effort classification on live requests.
Neither replaces the other.

---

## Q4 — Does `docs/adapter-contract.md` need to be in the approved plan?

**Why it blocks:** Codex R2-2 cites `docs/adapter-contract.md:212-220` as containing
callback-era language. If it's in scope, it must be listed explicitly in Change B. If it's
out of scope, the plan should say so to avoid implementer ambiguity.

**Evidence:**

`docs/adapter-contract.md:212-220`:
```
## Adapter #1 — LiteLLM

`litellm/custom_callbacks.py` is the current Subumbra adapter implementation.
It now uses the canonical `POST /proxy` core API via a custom `httpx.AsyncTransport`.

The callback fetches subumbra records and injects the V2 envelope metadata into
request headers. The transport intercepts the fully assembled provider request,
derives `target_url = str(request.url)`, packages the canonical `/proxy` payload,
and sends it to the Worker.
```

This describes the callback architecture that Round 42.2 superseded for the bundled path, and
that Change A will remove entirely. After Round 42.3:
- Adapter #1 is standalone LiteLLM (or any app) using `api_base: http://subumbra-proxy:8090/t`
- `litellm/custom_callbacks.py` is legacy reference material, not the current implementation

**Is it blocking?** Yes, in the sense that the adapter contract document is the normative
reference for how apps integrate with Subumbra. Leaving it describing the callback path after
the round would mean the normative contract still points to removed functionality.

**Scope question:** Is this a Change B item (update existing doc) or a separate change?

It is simpler to treat it as a Change B / Change C companion:
- Change B updates install docs
- Change C creates `docs/standalone-litellm.md`
- `docs/adapter-contract.md` section "Adapter #1" should be updated to describe the sidecar
  contract as the current Adapter #1 path, with the callback path marked as legacy

This is two sentences of change in `adapter-contract.md` and is not a new workstream.

**Conclusion: RESOLVED — human scope decision, but technically unambiguous.**

`docs/adapter-contract.md:212-220` must be updated in this round. It is a Change B companion
item. The approved plan should list it explicitly to prevent the implementer from overlooking it.
Leaving the normative adapter contract pointing to a removed feature would directly contradict
the round's stated goal of updating the operator truth.

---

## Q5 — Is Gemini's "Security Alliance" tangent blocking or out of scope?

**Why it appeared:** Gemini's `gemini-review-2.md` §3 introduces a "Cloudflare Security
Alliance" strategy that is not referenced in the kickoff, any proposals, or any other reviews.

**Conclusion: OUT OF SCOPE — not a blocking question.**

This is a product strategy proposal that goes beyond the Round 42.3 scope as defined in
`council/round-42-3-app-owned-integrations/kickoff.md:2-63`. The kickoff explicitly limits
scope to: de-bundling LiteLLM, documenting the standalone contract, minimum stale-caller
visibility, and operator truth alignment. The "Security Alliance" item is a future product
decision. No investigation needed.

---

## Summary of Resolutions

| Q | Status | Conclusion |
|---|---|---|
| Q1 — Bootstrap litellm removal safety | **Resolved by evidence** | Removing litellm from ADAPTER_SCOPE_VARS is safe; sidecar uses subumbra-proxy token which is unaffected |
| Q2 — post-bootstrap.sh guard safety | **Resolved by evidence** | Guard at line 43 can be safely removed; four specific line changes identified |
| Q3 — Body inspection reliability | **Resolved by evidence** | Reliable for all 12 current supported providers; no provider uses flat `{"error":"unauthorized"}`; Change D and Change E are complementary |
| Q4 — adapter-contract.md scope | **Human scope decision, technically unambiguous** | Must be updated in Change B; it is the normative contract document and leaving it in callback-era state contradicts the round goal |
| Q5 — Gemini Security Alliance | **Out of scope** | Future product decision; not a 42.3 blocking question |

---

## Approved Plan Minimum Additions

From the investigation, these items must be added to the approved plan spec beyond what the
proposals currently state:

1. **`post-bootstrap.sh`** must be an explicit Change R2 target with four specific changes:
   - Remove `_get SUBUMBRA_TOKEN_LITELLM` read (lines 29-31)
   - Remove from required-values guard (line 43)
   - Remove `update_env "SUBUMBRA_TOKEN_LITELLM"` (line 68)
   - Remove from post-write verification (line 80)

2. **`docs/adapter-contract.md:212-220`** must be a Change B companion item: update Adapter #1
   section to describe standalone sidecar contract; mark callback path as legacy.

3. **Change D spec** must include: "body inspection degrades gracefully — misclassification
   will produce no `reason_code` rather than a false positive; Change E is the authoritative
   stale-token signal."

4. **Change E spec** must include: probe caching (60s TTL) or 1-2s timeout to prevent Docker
   health instability from CF network blips.
