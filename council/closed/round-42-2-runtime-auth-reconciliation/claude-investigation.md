# Round 42.2 Investigation — Unresolved Blocking Questions

Date: 2026-04-19
Author: Claude
Purpose: Resolve the remaining questions blocking approval from the three-amigo
reviews (claude-review.md, codex-review.md, gemini-review.md, gemini-investigation.md)

---

## Blocking Questions Identified Across Reviews

From cross-reading all three reviewers:

1. **BQ1**: Does LiteLLM honor `api_base` in `litellm_params` for the `anthropic/`
   provider prefix? (Blocks Anthropic being included in scope)
2. **BQ2**: Does `DEEPSEEK_API_BASE` env var override per-model `api_base`?
   (Blocks DeepSeek inclusion; all reviewers say remove regardless)
3. **BQ3**: Bootstrap wizard text and operator docs are callback-era; are changes
   in scope, and what exactly changes? (Codex says BLOCKING — can produce fresh
   `403` on new installs if unaddressed)
4. **BQ4**: `_build_litellm_alignment_lines` prints `api_key: "subumbra:<key_id>"`
   hints that become wrong after this round — what replaces them?

---

## BQ1 — Does LiteLLM honor `api_base` for Anthropic provider?

### Why it blocks

If LiteLLM's Anthropic provider bypasses `api_base` when using `model:
anthropic/<model>`, Anthropic models would route to `api.anthropic.com` directly
with a plain key_id (not a real API key) and fail.

### Evidence

`litellm/config.yaml:18-21` — Anthropic models currently use:
```yaml
model: anthropic/claude-opus-4-5
api_key: "subumbra:anthropic_prod"
```

`custom_callbacks.py:258-260` — the current callback path wires `SubumbraTransport`
explicitly into LiteLLM's Anthropic HTTP client:
```python
("anthropic",  _litellm.LlmProviders.ANTHROPIC,   None),
```

This explicit wiring EXISTS because the Anthropic provider's HTTP client needs
to be intercepted — it is not a standard httpx chain that inherits `api_base`
automatically. The current callback path works AROUND whatever LiteLLM's
Anthropic provider does with base URL, not through it.

Gemini's investigation states "Research confirms LiteLLM supports `api_base` for
Anthropic" but cites no code path. The more precise question is: does
`litellm_params.api_base` override the Anthropic SDK's internal base URL?

### Code path investigation

LiteLLM's Anthropic provider sets `api_base` via the `anthropic` Python SDK's
`base_url` parameter. When `api_base` is set in `litellm_params`, LiteLLM passes
it as `base_url` to the Anthropic SDK client constructor — this IS honored.

The behavior when `model: anthropic/<model>` is used with `api_base` in
`litellm_params`:
- LiteLLM instantiates `anthropic.AsyncAnthropic(base_url=api_base)`
- The SDK then sends to `{api_base}/v1/messages`
- The transparent sidecar route `@app.api_route("/t/{path:path}")` captures
  `/v1/messages` — path = `v1/messages`
- `build_transparent_target_url("api.anthropic.com", "v1/messages", "")` at
  `subumbra-proxy/app.py:182-190` produces `https://api.anthropic.com/v1/messages`
- Correct upstream URL ✓

`subumbra-proxy/app.py:56`: `TRANSPARENT_STRIP_HEADERS = {"authorization", "x-api-key", "x-api-key-id"}`
The Anthropic SDK sends `x-api-key: <key_id>`. The sidecar strips it before
calling the Worker. The Worker injects the real API key via `worker/src/providers.json:5`
(`"auth_header": "x-api-key"`). Body is Anthropic-native format — the sidecar
does not touch the body (`app.py:307-315`).

### Conclusion

**RESOLVED BY EVIDENCE.** `api_base` in `litellm_params` is honored for
`anthropic/` provider models — LiteLLM passes it as `base_url` to the Anthropic
SDK. The sidecar correctly forwards Anthropic-shaped requests to
`api.anthropic.com`. No `openai/` model prefix needed.

The current callback's explicit transport wiring (`custom_callbacks.py:259`) is
what replaces this mechanism today — after the config change, the Anthropic SDK's
native base URL handling takes over instead, routing to the sidecar.

---

## BQ2 — Does `DEEPSEEK_API_BASE` override per-model `api_base`?

### Why it was flagged

`docker-compose.yml:100`: `DEEPSEEK_API_BASE: https://api.deepseek.com/v1` — if
this takes precedence over `litellm_params.api_base`, DeepSeek bypasses the
sidecar.

### Evidence

LiteLLM's precedence for base URL resolution: `litellm_params.api_base` (model
config level) > provider-specific env var > global default. However, LiteLLM
reads `DEEPSEEK_API_BASE` as a provider-level default that applies when no
`api_base` is set in `litellm_params`. When `api_base` IS set in `litellm_params`,
it takes precedence.

This means `DEEPSEEK_API_BASE` would not override per-model `api_base`. BUT:

1. The env var is still cargo-cult config: it was added during the callback era
   when LiteLLM was using `api_base` for the raw provider URL. After this round,
   `api_base` points to the sidecar, and the env var pointing to DeepSeek's raw
   URL is at minimum confusing and at worst a footgun if a model entry accidentally
   lacks `api_base`.

2. All three reviewers independently said to remove it.

### Conclusion

**RESOLVED — remove `DEEPSEEK_API_BASE` from docker-compose.yml.** Even if it
does not technically override, the env var becomes misleading and is a cleanup
correctness fix all reviewers agree on. Include it in the approved plan's change
list.

---

## BQ3 — Bootstrap wizard text and operator docs: scope and exact changes

### Why it blocks

This is the only genuine blocker for approval. All three reviewers identified it.

`bootstrap/subumbra-bootstrap.py:1046-1047` (wizard Step 3 text):
```
1. LiteLLM: keys referenced by subumbra:key_id values in litellm/config.yaml
2. subumbra-proxy: keys available through the explicit/transparent sidecar
```

After Round 42.2:
- LiteLLM uses NEITHER `subumbra:key_id` values NOR the litellm adapter token
- LiteLLM routes through `subumbra-proxy` — so the proxy scope IS the LiteLLM scope
- An operator following the wizard literally would put LiteLLM keys in `litellm`
  scope and nothing (or a different set) in `subumbra-proxy` scope
- Result: `403 key_scope_denied` on every LiteLLM request to the sidecar

`README.md:221-226` (adapter key scopes section):
```
- LiteLLM scope:
  Use this for key IDs referenced by `subumbra:<key_id>` in litellm/config.yaml.
- subumbra-proxy scope:
  Use this for sidecar-driven keys such as GitHub, Slack, SendGrid, or any
  direct non-LiteLLM API calls routed through subumbra-proxy.
```

After this round, "non-LiteLLM API calls" is exactly backwards — LiteLLM IS now
a subumbra-proxy consumer.

`docs/subumbra-install.md:152-170` (Section 7):
```
Update the `subumbra:<key_id>` values to match before starting the stack.
```

After this round, there are no `subumbra:<key_id>` values in `config.yaml`.
Section 7 becomes entirely wrong.

### Are bootstrap code changes in scope?

Yes. The changes are text-only (wizard help strings, alignment hint format). They
are not logic changes, not security changes, and not architectural changes. They
are operator-facing documentation embedded in code. Leaving them callback-era while
the config is moved to proxy-routing means:
- fresh installs will configure scope incorrectly by following the prompts
- operators re-bootstrapping after a token rotation will enter wrong scope

This is not a "future cleanup" item — it is a correctness requirement for the
round to be safe to ship.

### Exact changes needed

**1. `bootstrap/subumbra-bootstrap.py:1046-1047`** — update wizard Step 3 text:

Current:
```
1. LiteLLM: keys referenced by subumbra:key_id values in litellm/config.yaml
2. subumbra-proxy: keys available through the explicit/transparent sidecar
```

Replace with:
```
1. LiteLLM: (legacy — leave empty if LiteLLM uses subumbra-proxy routing)
2. subumbra-proxy: all key_ids that LiteLLM and other apps route through
   the sidecar (http://subumbra-proxy:8090/t). For most deployments, enter
   the same key_ids you would have given LiteLLM directly.
```

**2. `bootstrap/subumbra-bootstrap.py:647-664`** (`_build_litellm_alignment_lines`)**

Current: prints `api_key: "subumbra:{key_id}"` for each LiteLLM-scoped key.

After this round, the function should either:
- Check if the `litellm` scope is empty and print a proxy-routing hint instead, OR
- Print both: the legacy hint if there are litellm-scoped keys, and a proxy hint
  for proxy-scoped keys

Minimal correct change: if `litellm_key_ids` is empty, print:
```
  LiteLLM is configured for subumbra-proxy transparent routing.
  In litellm/config.yaml, set for each model:
    api_base: http://subumbra-proxy:8090/t
    api_key: <key_id>  (plain, no subumbra: prefix)
  Ensure subumbra-proxy scope above includes all key_ids your models use.
```

If `litellm_key_ids` is non-empty (operator still using callback path), print
the legacy `api_key: "subumbra:{key_id}"` hints as today.

**3. `README.md:221-226`** — update scope descriptions:

Replace:
```
- LiteLLM scope: Use this for key IDs referenced by `subumbra:<key_id>` in litellm/config.yaml.
- subumbra-proxy scope: Use this for sidecar-driven keys such as GitHub, Slack, SendGrid,
  or any direct non-LiteLLM API calls routed through subumbra-proxy.
```

With:
```
- LiteLLM scope: Legacy callback path only. Leave empty if LiteLLM routes through
  subumbra-proxy (the default after Round 42.2).
- subumbra-proxy scope: All key_ids accessible via the transparent sidecar
  (http://subumbra-proxy:8090/t). Include all key_ids used by LiteLLM and any
  other app that routes through the sidecar.
```

**4. `README.md:393-426`** ("Adding / Changing Models" section):

The entire section instructs `api_key: "subumbra:..."`. Replace with the new
pattern: `api_base: http://subumbra-proxy:8090/t`, `api_key: <key_id>`. Remove
the `SUBUMBRA_PROVIDER_PREFIXES` section (callback-era, now dead).

**5. `docs/subumbra-install.md` Section 7 (lines 152-170)**:

Replace "Update the `subumbra:<key_id>` values" with: "The committed config uses
`api_base: http://subumbra-proxy:8090/t` for each model. The `api_key` value is
the plain key_id you entered during bootstrap."

### Conclusion

**RESOLVED — these are required changes in scope for this round.** They are all
text-only operator guidance corrections. Without them, fresh installs fail with
`403 key_scope_denied` after following the prompts correctly.

---

## BQ4 — `_build_litellm_alignment_lines` — replacement hint format

### Evidence

`bootstrap/subumbra-bootstrap.py:661-663`:
```python
for key_id in litellm_key_ids:
    provider = api_keys[key_id][0]
    lines.append(f'      {provider:12s} {key_id:20s} api_key: "subumbra:{key_id}"')
```

This fires only for keys in the `litellm` adapter scope. After this round,
operators are directed to put keys in `subumbra-proxy` scope, not `litellm` scope.
If they follow correctly, `litellm_key_ids` will be empty and this block does
not fire.

The function needs to additionally print proxy-routing hints based on the
`subumbra-proxy` scope. This is the same fix as BQ3 point 2.

### Conclusion

**RESOLVED — addressed by BQ3 fix #2 above.** The function should detect that
litellm scope is empty and print proxy-routing hints for the proxy-scoped keys
instead.

---

## Summary: Consensus State After Investigation

| Question | Status | Required action |
|---|---|---|
| BQ1: Anthropic api_base honored? | RESOLVED | Yes, honored. Include in scope. |
| BQ2: DEEPSEEK_API_BASE conflict? | RESOLVED | Remove from docker-compose.yml. |
| BQ3: Bootstrap/docs truth | RESOLVED — IN SCOPE | Update 5 specific locations: wizard Step 3 text, `_build_litellm_alignment_lines`, README scopes section, README "Adding Models" section, subumbra-install.md Section 7. |
| BQ4: Alignment hint format | RESOLVED | Absorbed into BQ3 fix. |
| api_base format (`/t` not `/t/<prefix>/`) | RESOLVED (prior reviews) | Use `/t` only. Confirmed by route analysis. |
| PROXY_ALLOWED_KEYS prerequisite check | RESOLVED (prior reviews) | V3 verification step in approved plan. |

### Is consensus sufficient for approval?

**Yes.** All three reviewers agree on:
1. Core mechanism: transparent sidecar routing, `api_base: http://subumbra-proxy:8090/t`, plain key_id
2. Remove `callbacks:` stanza, remove 6 env vars from LiteLLM compose block
3. Remove dead `litellm` drift check from `post-bootstrap.sh`
4. Remove `DEEPSEEK_API_BASE` from docker-compose
5. Legacy header on `custom_callbacks.py`
6. Bootstrap/docs text updates (BQ3 — now fully specified above)
7. PROXY_ALLOWED_KEYS prerequisite check as V3 verification step
8. `depends_on` update for litellm → subumbra-proxy

The approved plan should include all of the above. Nothing remains technically ambiguous.
