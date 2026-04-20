# Round 42.2 Review — Runtime Auth Reconciliation

Date: 2026-04-19
Author: Codex
Round: round-42-2-runtime-auth-reconciliation

## Findings Table

| Severity | Finding | Evidence |
|---|---|---|
| High | Any plan that migrates LiteLLM onto `subumbra-proxy` must also update bootstrap scope guidance and LiteLLM alignment hints, or fresh installs can still produce `403 key_scope_denied` by following the current prompts literally. | [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1045-L1055), [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L648-L664), [README.md](/home/eric/git/Subumbra/README.md#L221-L226), [subumbra-keys/app.py](/home/eric/git/Subumbra/subumbra-keys/app.py#L546-L561), [claude-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-proposal-2.md#L61-L80) |
| Medium | Gemini’s provider-prefixed `api_base` shape is mechanically wrong for the current transparent sidecar route and would corrupt upstream URLs. | [gemini-proposal.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-proposal.md#L49-L52), [gemini-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-proposal-2.md#L31-L34), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L182-L190), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L266-L315), [claude-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-proposal-2.md#L82-L106) |
| Medium | The round-local prompt and several proposals point at a non-existent `docs/standalone-litellm.md`, while the real callback-era operator guidance still lives in `README.md` and `docs/subumbra-install.md`. If the approved plan updates the wrong doc target, stale `subumbra:`/callback instructions will remain in place. | [proposal-prompt.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/proposal-prompt.md#L10-L18), [README.md](/home/eric/git/Subumbra/README.md#L395-L426), [docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L152-L170), [codex-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/codex-proposal-2.md#L102-L109), [claude-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-proposal-2.md#L108-L118) |

## Detailed Analysis

### 1. Decoupling direction is right, but bootstrap/operator flow must move with it

Claude and Gemini are right on the main architectural point: the recurring break comes from LiteLLM carrying Subumbra auth state via callback-era env vars and module-level imports. The current callback path still depends on `SUBUMBRA_ACCESS_TOKEN`, `SUBUMBRA_HMAC_KEY`, `SUBUMBRA_KEYS_URL`, and `CF_WORKER_URL` inside LiteLLM itself. [litellm/custom_callbacks.py](/home/eric/git/Subumbra/litellm/custom_callbacks.py#L71-L76), [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L98-L109)

The transparent sidecar already owns the equivalent fetch/sign/proxy logic on its own boundary. It extracts bare `key_id`, fetches the record from `subumbra-keys`, and calls the Worker using proxy-owned auth state. [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L18-L25), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L135-L179), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L266-L315)

The part I think still needs to be treated as blocking is fresh-install/operator truth. Today the bootstrap wizard and README still teach that LiteLLM keys belong under the LiteLLM scope and proxy keys belong to “direct non-LiteLLM API calls.” [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1045-L1055), [README.md](/home/eric/git/Subumbra/README.md#L221-L226) Bootstrap also still prints callback-era alignment hints of the form `api_key: "subumbra:<key_id>"` for LiteLLM-scoped keys. [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L648-L664)

That matters because `subumbra-keys` still enforces per-adapter allowed-key lists. If `subumbra-proxy` is the new authority path but its `allowed_keys` is not expanded, the migrated LiteLLM flow fails with `403`. [subumbra-keys/app.py](/home/eric/git/Subumbra/subumbra-keys/app.py#L546-L561) Claude’s proposal-2 correctly notices the key-scope prerequisite, but leaving it purely as an operational prerequisite is not enough for the product-facing install path. [claude-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-proposal-2.md#L128-L147)

My position: the round should either:

- update bootstrap prompts/summary and real install docs so the new supported path is truthful for fresh installs, or
- explicitly limit itself to a migration of an already-running manually-aligned deployment and say that bootstrap/install truth is deferred

I do not think “decouple LiteLLM” and “leave bootstrap/operator truth callback-shaped” is a safe merged plan.

### 2. Gemini’s provider-prefix `api_base` variant should be rejected

Gemini’s initial and proposal-2 variants both push an `api_base` shape with embedded provider/path prefixes such as `http://subumbra-proxy:8090/t/<provider_prefix>/` or `.../t/v1/`. [gemini-proposal.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-proposal.md#L49-L52), [gemini-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/gemini-proposal-2.md#L31-L34)

That does not match the current sidecar mechanics. `subumbra-proxy` captures everything after `/t/` as `{path:path}` and then appends that captured path directly to `record["target_host"]`. [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L182-L190), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L266-L315) So a prefixed `api_base` would duplicate path segments into the upstream URL.

Claude’s corrected `/t`-only form is the mechanically sound one. [claude-proposal-2.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/claude-proposal-2.md#L82-L106)

### 3. There is real doc/prompt drift around the missing LiteLLM standalone doc

The round-local proposal prompt still instructs proposal writers to read `docs/standalone-litellm.md`. [proposal-prompt.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/proposal-prompt.md#L10-L18) That file is not present in the current checkout.

Command:
```bash
ls docs
```

Output:
```text
adapter-contract.md
council-memory.md
n8n-workflows
operator-guide.md
project-memory.md
provider-catalog.md
subumbra-developer.md
subumbra-install.md
subumbra-testing.md
testbed-install.md
vps-deployment.md
```

The more important consequence is not just the missing file itself. The real operator guidance that users will actually follow still lives in `README.md` and `docs/subumbra-install.md`, and both are still callback-era:

- `README.md` still says model additions always use `subumbra:` keys and still documents `SUBUMBRA_PROVIDER_PREFIXES`. [README.md](/home/eric/git/Subumbra/README.md#L395-L426)
- `docs/subumbra-install.md` still tells operators to edit `subumbra:<key_id>` values in `litellm/config.yaml`. [docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L152-L170)

So the important review outcome here is: the approved plan must update the real docs and prompts that exist now, not just a doc path that used to exist or was assumed to exist.

## Commands Run

### Command
```bash
nl -ba subumbra-proxy/app.py | sed -n '166,180p;193,247p;266,315p'
```

### Important output
```text
170 def strip_transparent_headers(headers: dict[str, str]) -> dict[str, str]:
174     if lower in TRANSPARENT_STRIP_HEADERS:
182 def build_transparent_target_url(target_host: str, path: str, query: str) -> str:
185         target_url = f"https://{target_host}/{clean_path}"
193 async def proxy_via_worker(
266 @app.api_route("/t/{path:path}", methods=TRANSPARENT_METHODS)
307     target_url = build_transparent_target_url(record["target_host"], path, request.url.query)
```

### Command
```bash
nl -ba bootstrap/subumbra-bootstrap.py | sed -n '1042,1058p'
```

### Important output
```text
1045 print("  Choose which key_ids each built-in adapter may fetch from subumbra-keys.")
1046 print("  1. LiteLLM: keys referenced by subumbra:key_id values in litellm/config.yaml")
1047 print("  2. subumbra-proxy: keys available through the explicit/transparent sidecar")
1053 allowed_keys_by_adapter = {
1054     "litellm": _prompt_allowed_keys("LiteLLM", available_key_ids),
1055     "subumbra-proxy": _prompt_allowed_keys("subumbra-proxy", available_key_ids),
```

### Command
```bash
ls docs
```

### Important output
```text
adapter-contract.md
council-memory.md
n8n-workflows
operator-guide.md
project-memory.md
provider-catalog.md
subumbra-developer.md
subumbra-install.md
subumbra-testing.md
testbed-install.md
vps-deployment.md
```

## Recommendations

1. Use Claude’s decoupling direction as the base, but do not approve a plan that leaves bootstrap scope prompts, bootstrap LiteLLM alignment hints, and real install docs in callback-era form.
2. Reject Gemini’s provider-prefixed `api_base` variant outright; the correct proxy base is `/t`, not `/t/<provider_prefix>/`.
3. Update the approved plan to target the docs and prompts that actually exist now: `README.md`, `docs/subumbra-install.md`, and the round-local proposal/prompt inputs if they are still part of active round guidance.
4. Minimal logging/error-handling addition only: if LiteLLM is migrated onto `subumbra-proxy`, it would be worthwhile for `subumbra-proxy` to distinguish record-fetch `403` from generic record-fetch failure in its operator-visible log path, because missing `PROXY_ALLOWED_KEYS` becomes a round-specific failure mode. [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L98-L101), [subumbra-proxy/app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L298-L305) No secret-bearing logging should be added.
