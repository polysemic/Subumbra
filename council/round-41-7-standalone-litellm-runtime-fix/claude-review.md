# Claude Review — Round 41.7 Standalone LiteLLM Runtime Fix

## Findings Table

| ID | Claim | Verdict | Evidence |
|----|-------|---------|----------|
| R1 | Primary 401 cause is missing callback/config wiring, not a transport provider gap | ACCEPT | `litellm/custom_callbacks.py:364-381,435-445`, `docs/testbed-install.md:160-177`, `litellm/config.yaml:115-118` |
| R2 | Primary `subumbra-keys unreachable` cause is missing network path from standalone LiteLLM to `internal` network | ACCEPT | `docker-compose.yml:7-10,39-47,83-85` |
| R3 | Gemini's fix of adding `subumbra-net` to `subumbra-keys` is a security regression | REJECT Gemini | `docker-compose.yml:7-10`, `README.md:388-389`, `docs/subumbra-install.md:191-206` |
| R4 | Gemini's `_wire_transport_once()` expansion to openai/together_ai/cerebras is not supported by the code | REJECT Gemini | `litellm/custom_callbacks.py:253-256,258-275` |
| R5 | Claude's stable `name: subumbra_internal` approach is the correct network topology fix | ACCEPT | `docker-compose.yml:7-10,83-85`, `council/round-41-7-standalone-litellm-runtime-fix/claude-proposal.md:116-149` |
| R6 | Token identity is critical: standalone LiteLLM must use `SUBUMBRA_TOKEN_LITELLM`, not `SUBUMBRA_TOKEN_PROXY` | ACCEPT | `subumbra-keys/app.py:323-340,546`, `docker-compose.yml:101` |
| R7 | Gemini's `post-bootstrap.sh` modification is scope creep | REJECT Gemini | `post-bootstrap.sh:66-77` |
| R8 | LiteLLM image pinning is warranted as compatibility-risk reduction | ACCEPT (with caveat) | `litellm/custom_callbacks.py:246-249`, `docker-compose.yml:78` |

---

## Detailed Analysis

### 1. The `401 subumbra:...` failure — callback wiring is the correct root cause

The `docs/testbed-install.md` standalone template mounts only `./config.yaml:/app/config.yaml:ro` and
does not mount `custom_callbacks.py`. Without that mount, the Python path has no `custom_callbacks`
module, the `litellm_settings.callbacks: custom_callbacks.proxy_handler_instance` stanza in
`litellm/config.yaml:115-118` silently fails to load, and `async_pre_call_deployment_hook` never
runs. The api_key rewrite at `litellm/custom_callbacks.py:435` — which replaces `subumbra:<key_id>`
with `SUBUMBRA_ACCESS_TOKEN` — never fires. The raw `subumbra:openai_prod` string reaches the
upstream provider, which rejects it with `401 Incorrect API key provided`.

This is the complete causal chain supported by the repo files. There is no gap in the argument.
The `_wire_transport_once()` path (`litellm/custom_callbacks.py:233-275`) is irrelevant to this
failure mode because the callback never runs at all.

Gemini attributes this 401 in part to `_wire_transport_once()` not covering openai/together_ai/
cerebras (`gemini-proposal.md:5-8`). That claim does not hold against the code. Line 255 patches
`litellm.module_level_aclient.client` directly, and lines 258-275 loop over handler instances for
the `AsyncHTTPHandler`-backed providers (anthropic, groq, deepseek, openrouter, mistral, xai).
OpenAI SDK-based providers (openai, together, cerebras) use the module-level async client, which IS
patched. There is no evidence in this round that a fully-loaded callback on a properly configured
standalone deployment leaks `subumbra:` credentials for those providers.

If future runtime evidence shows otherwise after the mount/config/network fixes land, that is a
valid Round 42 topic. It should not enter this round.

### 2. The `subumbra-keys unreachable` failure — network topology

`subumbra-keys` is declared with `networks: internal` only at `docker-compose.yml:39-47`, and the
`internal` network carries `internal: true` at `docker-compose.yml:7-10` (Docker enforces this via
iptables: no default route out). The bundled LiteLLM works because it joins both `internal` and
`external` (`docker-compose.yml:83-85`) and uses `SUBUMBRA_KEYS_URL=http://subumbra-keys:9090` at
`docker-compose.yml:101-103`.

A standalone LiteLLM deployment that sits only on `subumbra-net` has no path to a container that is
only on `internal`. The callback's `_fetch_subumbra_record()` call fails at the network level and
raises the `subumbra-keys service is unreachable` error logged at
`litellm/custom_callbacks.py:410-415`.

**Why Claude's fix is correct:** Adding `name: subumbra_internal` to the `internal` network block
gives that network a stable, addressable name in Docker's namespace. A standalone LiteLLM project
can then join it as an external network (`external: true`) in its own compose file. The container
still reaches `subumbra-keys` via Docker DNS, and `internal: true` on the network continues to
block outbound internet from `subumbra-keys` itself — because that property is a property of the
network, not of the containers attached to it. The security invariant is preserved.

**Why Gemini's fix is a security regression:** Adding `subumbra-net` to `subumbra-keys`
(`gemini-proposal.md:18-20`) gives `subumbra-keys` a second network attachment to a non-`internal`
bridge network. Docker's `internal: true` isolation is per-network, not per-container. Once
`subumbra-keys` is also attached to `subumbra-net`, it inherits that network's default route and can
reach the internet through `subumbra-net`'s bridge gateway. The product documentation explicitly
states this service must have no internet access (`CLAUDE.md:Key Design Decisions`, `README.md`).
Gemini's own open-question acknowledgment in `gemini-proposal.md:44` ("Is adding subumbra-keys to
a public-facing bridge a security regression?") and self-answer ("Assessment: `internal: true` on
the `internal` network is the primary air-gap") is incorrect: `internal: true` applies only to the
`internal` network, not to additional networks the container joins.

### 3. Token scope — `SUBUMBRA_TOKEN_LITELLM` not `SUBUMBRA_TOKEN_PROXY`

`subumbra-keys/app.py:323-340` (`_resolve_adapter()`) validates `X-Subumbra-Token` against the
adapter registry and returns `adapter_unknown` if no match is found. The callback sends the value of
`SUBUMBRA_ACCESS_TOKEN` as that header. In the bundled compose, that env var is set to
`${SUBUMBRA_TOKEN_LITELLM}` (`docker-compose.yml:101`). A standalone deployment that copies only
the sidecar token (`SUBUMBRA_TOKEN_PROXY`) into `/opt/litellm/.env` will hit `adapter_unknown` →
401 from subumbra-keys, even after the network path is fixed.

This is not exotic — it is easy to miscopy from `docker-compose.yml` during a manual standalone
setup. Claude's proposal explicitly calls out this token distinction at `claude-proposal.md:186-195`.
The operator documentation must be equally explicit.

Additionally, `subumbra-keys/app.py:546` checks `key_id not in adapter["allowed_keys"]` and returns
403 `key_scope_denied` if the token is valid but not authorized for the requested key. The
standalone operator must confirm the `subumbra_litellm` adapter record's `allowed_keys` covers every
`subumbra:key_id` referenced in the config.

### 4. LiteLLM image version

`litellm/custom_callbacks.py:246-249` hard-fails with `RuntimeError` if
`litellm.module_level_aclient.client` is absent. That attribute's existence depends on LiteLLM's
internal structure. The bundled service is pinned to a known-good digest at
`docker-compose.yml:78`. A standalone deployment using `main-latest` without the same pin may land
on a LiteLLM build that has moved or removed that attribute, producing a hard import failure before
any request is made.

Pinning the standalone image to the same digest as the bundled service is the correct risk-reduction
move. It should be documented as compatibility-risk reduction, not as a proven root cause of the
current 401/500 failures.

### 5. Gemini's `post-bootstrap.sh` modification is out of scope

`post-bootstrap.sh` currently writes only to `/opt/subumbra/.env` (`post-bootstrap.sh:66-77`). It
has no knowledge of, and no business knowing about, operator-specific standalone deployment paths
like `/opt/litellm/.env`. Making `post-bootstrap.sh` check for or modify that path:

- couples the bootstrap script to a deployment convention it doesn't own
- creates a footgun if an operator's standalone path differs
- is better addressed in Round 42 operator hardening (preflight script or standalone setup guide)

The correct 41.7 fix is to document and prove the manual token-copy step, not to automate it in
bootstrap.

### 6. Minimal logging is sufficient for this round

The callback already distinguishes the key failure modes:

- `subumbra-keys returned <status>` (`litellm/custom_callbacks.py:392-399`)
- `subumbra-keys service is unreachable` (`litellm/custom_callbacks.py:410-415`)

A single import-time warning when `SUBUMBRA_ACCESS_TOKEN` or `SUBUMBRA_HMAC_KEY` are missing is
already emitted at `litellm/custom_callbacks.py:95-100`. No broad new observability is needed.

A callback-side warning distinguishing "callback module not loaded" from "callback loaded but
env vars absent" would be useful for operators debugging standalone deployments, but it should be
terse and must not log token values.

### 7. What should not enter 41.7

- `_wire_transport_once()` provider expansion (no runtime evidence required)
- `subumbra-keys → subumbra-net` network attachment (security regression)
- `post-bootstrap.sh` standalone-path sync logic (scope creep)
- OpenWebUI / N8N / operator template overhaul (deferred to Round 42)
- Broad observability or config preflight automation

---

## Recommendations

1. **Use Claude's proposal as the base.** It correctly identifies both root causes, proposes the
   narrower and safer network fix, and explicitly excludes the Round 42 operator hardening work.

2. **Explicitly reject Gemini's `subumbra-keys → subumbra-net` change.** The argument that
   `internal: true` protects the service after it joins a second bridge is incorrect. The security
   model depends on `subumbra-keys` having no route to the internet, which requires keeping it
   off non-`internal` networks.

3. **Explicitly defer Gemini's `_wire_transport_once()` expansion.** The claim that
   openai/together/cerebras bypass the transport is not supported by `custom_callbacks.py:253-256`.
   This claim should be re-evaluated with runtime evidence after the mount/config/network fixes land,
   and if proven, addressed in Round 42.

4. **Keep `post-bootstrap.sh` unchanged.** Token propagation to `/opt/litellm/.env` is an operator
   step documented in the standalone setup, not a bootstrap responsibility.

5. **Require the token-scope clarification in the operator instructions.** The distinction between
   `SUBUMBRA_TOKEN_LITELLM` and `SUBUMBRA_TOKEN_PROXY` must be explicit; copying the wrong token is
   a realistic operator error.

6. **Maintain minimal logging scope.** The existing callback error paths are sufficient for
   distinguishing the three failure modes (callback not loaded, token/auth failure, network
   unreachable). No secret-bearing values should enter any log path.
