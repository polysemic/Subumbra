# Round 42.2 Investigation — Runtime Auth Reconciliation

Date: 2026-04-19
Author: Codex
Round: round-42-2-runtime-auth-reconciliation

## Blocking Question 1

### 1. Exact question to investigate

Does LiteLLM’s Anthropic provider honor `litellm_params.api_base`, so that
Anthropic models can be migrated to `api_base: http://subumbra-proxy:8090/t`
without needing the current callback path or an `openai/...` model rewrite?

### 2. Why it still blocks consensus

This question is the technical core of the remaining scope disagreement:

- Claude/Gemini say Anthropic can stay in the migration scope.
- My earlier scope caution was that Round 42 had only approved the sidecar as
  primary for OpenAI-compatible / low-mutation flows, and I did not want the
  round to overclaim Anthropic compatibility without proof.

If Anthropic ignores `api_base`, a “migrate all working providers” plan would be
technically wrong.

### 3. Relevant source files

- [litellm/config.yaml](/home/eric/git/Subumbra/litellm/config.yaml#L17-L31)
- [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L135-L179)
- [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L182-L190)
- [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L266-L315)
- [worker/src/providers.json](/home/eric/git/Subumbra/worker/src/providers.json#L1-L16)
- LiteLLM package in image:
  - `/app/litellm/llms/anthropic/common_utils.py:546-555`
  - `/app/litellm/llms/anthropic/chat/handler.py:193-230`
  - `/app/litellm/llms/anthropic/experimental_pass_through/messages/transformation.py:129-134`

### 4. Commands/tests run

#### Command
```bash
docker run --rm --entrypoint sh ghcr.io/berriai/litellm:main-latest -lc "nl -ba /app/litellm/llms/anthropic/common_utils.py | sed -n '546,555p'"
```

#### Important output
```text
546	    @staticmethod
547	    def get_api_base(api_base: Optional[str] = None) -> Optional[str]:
550	        return (
551	            api_base
552	            or get_secret_str("ANTHROPIC_API_BASE")
553	            or get_secret_str("ANTHROPIC_BASE_URL")
554	            or "https://api.anthropic.com"
555	        )
```

#### Command
```bash
docker run --rm --entrypoint sh ghcr.io/berriai/litellm:main-latest -lc "nl -ba /app/litellm/llms/anthropic/chat/handler.py | sed -n '193,230p;360,404p;466,470p'"
```

#### Important output
```text
193	    async def acompletion_stream_function(
197	        api_base: str,
219	        completion_stream, headers = await make_call(
221	            api_base=api_base,
369	        logging_obj.pre_call(
375	                "api_base": api_base,
386	                return self.acompletion_stream_function(
390	                    api_base=api_base,
466	                try:
467	                    response = client.post(
468	                        api_base,
469	                        headers=headers,
470	                        data=json.dumps(data),
```

#### Command
```bash
docker run --rm --entrypoint sh ghcr.io/berriai/litellm:main-latest -lc "nl -ba /app/litellm/llms/anthropic/experimental_pass_through/messages/transformation.py | sed -n '129,134p'"
```

#### Important output
```text
129	        api_base = (
130	            AnthropicModelInfo.get_api_base(api_base) or "https://api.anthropic.com"
131	        )
132	        if not api_base.endswith("/v1/messages"):
133	            api_base = f"{api_base}/v1/messages"
134	        return api_base
```

### 5. Conclusion

Anthropic compatibility is **resolved by evidence**.

The LiteLLM package does honor an explicit `api_base` for Anthropic:

- `AnthropicModelInfo.get_api_base()` prefers the passed `api_base` over env
  defaults or the Anthropic default host.
- the Anthropic chat handler carries `api_base` all the way into the actual
  request call.
- the Anthropic message path appends `/v1/messages` to that base.

On the Subumbra side, the transparent route captures everything after `/t/` and
rebuilds the upstream URL from `record["target_host"]` plus the captured path.
[subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L182-L190),
[subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L266-L315)

So `api_base: http://subumbra-proxy:8090/t` for `anthropic/...` models gives the
sidecar `/v1/messages`, and the sidecar then rebuilds
`https://api.anthropic.com/v1/messages`. The Worker-side provider metadata also
expects Anthropic auth to use `x-api-key`, which matches this route.
[worker/src/providers.json](/home/eric/git/Subumbra/worker/src/providers.json#L1-L8)

### 6. Resolution status

**Resolved by evidence**

---

## Blocking Question 2

### 1. Exact question to investigate

Does `DEEPSEEK_API_BASE` in `docker-compose.yml` override a per-model
`litellm_params.api_base`, and therefore need to be removed for the sidecar
migration to be technically correct?

### 2. Why it still blocks consensus

All three reviewers converged on “remove it,” but the reason matters:

- if it overrides model config, removal is mandatory for correctness
- if it does not override model config, removal is still probably the right
  cleanup, but that becomes a smaller and less controversial claim

### 3. Relevant source files

- [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L98-L110)
- LiteLLM package in image:
  - `/app/litellm/llms/deepseek/chat/transformation.py:97-105`
  - `/app/litellm/llms/deepseek/chat/transformation.py:109-125`

### 4. Commands/tests run

#### Command
```bash
docker run --rm --entrypoint sh ghcr.io/berriai/litellm:main-latest -lc "nl -ba /app/litellm/llms/deepseek/chat/transformation.py | sed -n '97,125p'"
```

#### Important output
```text
97:        self, api_base: Optional[str], api_key: Optional[str]
99:        api_base = (
100:            api_base
101:            or get_secret_str("DEEPSEEK_API_BASE")
105:        return api_base, dynamic_api_key
109:        api_base: Optional[str],
119:        if not api_base:
120:            api_base = "https://api.deepseek.com/beta"
122:        if not api_base.endswith("/chat/completions"):
123:            api_base = f"{api_base}/chat/completions"
125:        return api_base
```

### 5. Conclusion

This question is **resolved by evidence**.

LiteLLM’s DeepSeek code prefers a passed `api_base` first, and only falls back
to `DEEPSEEK_API_BASE` if the model-level `api_base` is absent. So the env var
does **not** override a per-model sidecar base. The technical precedence is:

1. `litellm_params.api_base`
2. `DEEPSEEK_API_BASE`
3. built-in DeepSeek default

That means removal of `DEEPSEEK_API_BASE` is not mandatory because of override
precedence, but it is still the cleaner round outcome because leaving it in
place preserves a callback-era/raw-provider default that is no longer wanted
once the sidecar becomes the authority.

### 6. Resolution status

**Resolved by evidence**

---

## Blocking Question 3

### 1. Exact question to investigate

If LiteLLM is migrated behind `subumbra-proxy`, are bootstrap prompt/alignment
changes and install-doc updates technically required for correctness, or are
they only optional operator polish?

### 2. Why it still blocks consensus

This is the main remaining disagreement from the three-amigo docs:

- Claude treats bootstrap/operator truth updates as required in-scope changes.
- Gemini treats proxy key-scope expansion mainly as an operational prerequisite.
- I flagged that leaving callback-era prompts and docs in place can produce
  fresh-install `403 key_scope_denied`.

If the product can still generate a broken registry by following the official
wizard/README literally after 42.2, that is not just polish.

### 3. Relevant source files

- [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L574-L625)
- [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L648-L664)
- [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1045-L1056)
- [README.md](/home/eric/git/Subumbra/README.md#L221-L226)
- [README.md](/home/eric/git/Subumbra/README.md#L395-L426)
- [docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L152-L170)
- [subumbra-keys/app.py](/home/eric/git/Subumbra/subumbra-keys/app.py#L546-L561)

### 4. Commands/tests run

#### Command
```bash
nl -ba bootstrap/subumbra-bootstrap.py | sed -n '574,625p;648,664p;1045,1056p'
```

#### Important output
```text
574	def _build_adapter_registry(
585	        "litellm": {
587	            "allowed_keys": allowed_keys_by_adapter["litellm"],
593	        "subumbra-proxy": {
595	            "allowed_keys": allowed_keys_by_adapter["subumbra-proxy"],
648	def _build_litellm_alignment_lines(
653	    "    Update litellm/config.yaml so each model uses the exact Subumbra key_id entered during bootstrap.",
663	        lines.append(f'      {provider:12s} {key_id:20s} api_key: "subumbra:{key_id}"')
1045	    print("  Choose which key_ids each built-in adapter may fetch from subumbra-keys.")
1046	    print("  1. LiteLLM: keys referenced by subumbra:key_id values in litellm/config.yaml")
1047	    print("  2. subumbra-proxy: keys available through the explicit/transparent sidecar")
```

#### Command
```bash
nl -ba README.md | sed -n '221,226p;395,426p'
```

#### Important output
```text
221	- `LiteLLM` scope:
222	  Use this for key IDs referenced by `subumbra:<key_id>` in
223	  `litellm/config.yaml`.
224	- `subumbra-proxy` scope:
225	  Use this for sidecar-driven keys such as GitHub, Slack, SendGrid, or any
226	  direct non-LiteLLM API calls routed through `subumbra-proxy`.
395	Edit [litellm/config.yaml](litellm/config.yaml) to add models. The only required change is the `model:` line — `api_key` always uses the `subumbra:` prefix pointing to the correct key ID:
409	### Custom Provider Path Prefixes
411	The callback dynamically resolves each provider's API path prefix using LiteLLM's
412	internal registry.
```

#### Command
```bash
nl -ba docs/subumbra-install.md | sed -n '152,170p'
```

#### Important output
```text
152	## 7. Update `litellm/config.yaml`
154	The committed config uses the bootstrap default `key_id` suggestions
156	bootstrap, update the `subumbra:<key_id>` values to match before starting the stack.
163	api_key: "subumbra:anthropic_prod"
169	api_key: "subumbra:anthropic_test"
```

### 5. Conclusion

This question is **resolved by evidence**: bootstrap/install truth changes are
technically required for correctness if 42.2 migrates LiteLLM behind
`subumbra-proxy`.

Why:

- the adapter registry keeps separate `allowed_keys` lists for `litellm` and
  `subumbra-proxy`
- `subumbra-keys` enforces those lists strictly
- the wizard and docs still teach operators to think of LiteLLM scope and proxy
  scope as separate and to configure LiteLLM with `subumbra:<key_id>` values

That combination can still produce a fresh-install or re-bootstrap path where:

1. operators put model keys under LiteLLM scope
2. operators leave proxy scope narrower
3. LiteLLM is migrated to call the proxy
4. the proxy then gets `403 key_scope_denied`

So this is not just “nicer docs.” At minimum, the approved plan needs to update:

- bootstrap Step 3 wording
- `_build_litellm_alignment_lines()`
- the README adapter-scope section
- the README model-adding section
- `docs/subumbra-install.md` Section 7

Whether bootstrap should also auto-copy LiteLLM scope into proxy scope is **not**
technically required. The current code already supports correct behavior if the
operator enters the right scope values. So “auto-merge scope in bootstrap code”
is a product/UX decision, but “update prompts/docs to stop teaching the wrong
scope model” is a correctness requirement.

### 6. Resolution status

**Resolved by evidence**

---

## Blocking Question 4

### 1. Exact question to investigate

Does 42.2 require a bootstrap logic change that automatically expands
`PROXY_ALLOWED_KEYS`, or is an operational prerequisite plus prompt/doc updates
enough?

### 2. Why it still blocks consensus

Gemini originally proposed bootstrap code changes; Claude rejected that as too
broad; I was concerned mainly with correctness on fresh installs.

### 3. Relevant source files

- [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L560-L625)
- [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L632-L644)

### 4. Commands/tests run

#### Command
```bash
nl -ba bootstrap/subumbra-bootstrap.py | sed -n '560,625p;632,644p'
```

#### Important output
```text
560	def _validate_allowed_keys(
564	    valid_key_ids = set(api_keys.keys())
565	    for adapter_id, allowed_keys in allowed_keys_by_adapter.items():
574	def _build_adapter_registry(
585	        "litellm": {
593	        "subumbra-proxy": {
632	def _prompt_allowed_keys(adapter_label: str, available_key_ids: list[str]) -> list[str]:
635	        print(f"\n  {adapter_label} allowed key_ids (comma-separated, Enter for none)")
638	        result = _parse_allowed_keys_csv(raw)
```

### 5. Conclusion

This is **not** a remaining technical blocker. It is a product/UX choice.

Technically:

- the existing bootstrap code can already generate a correct registry for 42.2
- no structural code change is required
- the operator can simply scope the same key IDs to `subumbra-proxy`

So the investigation result is:

- bootstrap code automation is **not technically required**
- prompt/doc truth updates **are** technically required

### 6. Resolution status

**A human policy/product decision rather than a technical one**

---

## Summary

| Question | Conclusion | Status |
|---|---|---|
| Anthropic `api_base` honored? | Yes. Anthropic can remain in migration scope. | Resolved by evidence |
| Does `DEEPSEEK_API_BASE` override model `api_base`? | No. Model `api_base` wins, but remove the env var anyway for clarity. | Resolved by evidence |
| Are bootstrap/prompt/doc updates required? | Yes. They are needed for correctness, not just polish. | Resolved by evidence |
| Must bootstrap auto-expand proxy scope in code? | No. Existing logic is sufficient; automation is optional UX. | Human policy/product decision |

## Narrow Spec Impact

This supports a clean, narrow approved plan:

1. Migrate LiteLLM models onto `api_base: http://subumbra-proxy:8090/t` with
   plain `api_key: <key_id>`
2. Remove callback-era LiteLLM env ownership
3. Remove `DEEPSEEK_API_BASE`
4. Update bootstrap/operator truth so fresh installs and re-bootstrap runs do
   not generate a broken proxy scope configuration
5. Keep proxy-scope auto-merge out of scope unless the council chooses it as a
   UX improvement
