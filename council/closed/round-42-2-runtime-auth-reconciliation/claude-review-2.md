# Round 42.2 — Evidence-Based Review 2: Eric's Questions

Date: 2026-04-19
Reviewer: Claude
Input: `council/round-42-2-runtime-auth-reconciliation/eric-questions.md`,
`council/eric-questions.md`, all proposals and review-1 for this round

---

## Summary

Eric's questions point in exactly the direction this round is moving — away from
LiteLLM-specific plumbing and toward `subumbra-proxy` as the universal app
integration layer. This review maps each question to current code evidence,
identifies what Round 42.2 resolves, and separates what is deferred.

---

## Findings Table

| # | Question | Answer | In Scope 42.2? | Evidence |
|---|---|---|---|---|
| EQ1 | Same API key used across multiple apps (LiteLLM + n8n + Open WebUI) — how is this handled? | Single encrypted record per key_id; multiple adapters access it via their `allowed_keys` scope | Partially — proxy-as-gateway pattern proven | `subumbra-keys/app.py:546`, `bootstrap/subumbra-bootstrap.py:584-600` |
| EQ2 | Can post-bootstrap move into a docker container? | Technically possible; current design requires host-side `.env` writes. Deferred | No — future round | `post-bootstrap.sh:19-24, 56-73` |
| EQ3 | Read existing env/config files, encrypt the keys, shred the original? | Not yet implemented; becomes much simpler after 42.2 proves the sidecar pattern | No — future round | `bootstrap/subumbra-bootstrap.py` (new mode needed) |
| EQ4 | Does the same API key get encrypted multiple times (once per app)? | No. One encrypted record per key_id, shared by all adapters that have scope | PASS (current design is already correct) | `subumbra-keys/app.py:563-595`, `bootstrap/subumbra-bootstrap.py:574-600` |
| EQ5 | Future apps (AnythingLLM, LibreChat, Dify, etc.) — what does integration look like? | All of these support custom API base URL — same pattern proven in this round applies directly | Round 42.2 proves the pattern | `subumbra-proxy/app.py:58, 266-315` |
| EQ6 | Consolidating scope: should subumbra-proxy be the single gateway for all apps? | Yes — this is the correct long-term shape; Round 42.2 moves the first adapter (LiteLLM) to this model | Partially in scope | `bootstrap/subumbra-bootstrap.py:1053-1057`, `docker-compose.yml:178-193` |

---

## Detailed Analysis

### EQ4 — Single encrypted key, many apps (PASS — already correct)

**This is the most important clarification for the current architecture.**

`subumbra-keys/app.py:563-595`: each key_id maps to one record with one
ciphertext, one wrapped DEK, one pub_key_fp. There is no per-app copy.

`bootstrap/subumbra-bootstrap.py:584-600`:
```python
registry = {
    "litellm": {
        "token": adapter_tokens["litellm"],
        "allowed_keys": allowed_keys_by_adapter["litellm"],
        ...
    },
    "subumbra-proxy": {
        "token": adapter_tokens["subumbra-proxy"],
        "allowed_keys": allowed_keys_by_adapter["subumbra-proxy"],
        ...
    },
    ...
}
```

Multiple adapters can include the same key_id in their `allowed_keys` list.
The encrypted record is fetched once per request; the CF Worker decrypts it
per-call in its Durable Object. No re-encryption per app. No key duplication.

**After Round 42.2:** LiteLLM, n8n, and Open WebUI can all use the same
`anthropic_prod` key_id. They each route requests through `subumbra-proxy /t/`
using that key_id as their API key value. The proxy fetches the single record
from subumbra-keys (using `SUBUMBRA_TOKEN_PROXY`) and the CF Worker handles the
rest. No per-app token management beyond granting scope in the registry.

---

### EQ1 — Multi-app with same key — the proxy-as-gateway model

**Round 42.2 is the enabler for multi-app support.**

`subumbra-proxy/app.py:266`:
```python
@app.api_route("/t/{path:path}", methods=TRANSPARENT_METHODS)
```

`TRANSPARENT_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]`
at `app.py:58` — every HTTP method is supported.

The sidecar is already generic. It does not contain LiteLLM-specific logic.
Any app that can:
1. Set a custom API base URL to `http://subumbra-proxy:8090/t`
2. Use a plain string as an API key (which becomes the key_id)

…is supported today with no code changes. Every app in Eric's planned list
(AnythingLLM, LibreChat, Dify, Chatwoot, Langfuse, etc.) supports custom API
base URL and custom API key — this is standard configuration in every
self-hosted AI/LLM stack.

**The network path for external Docker apps:**

`docker-compose.yml:17-19`:
```yaml
subumbra-net:
  external: true
  name: subumbra-net
```

`docker-compose.yml:178-183`:
```yaml
subumbra-proxy:
  networks:
    - internal
    - external
    - subumbra-net
  ports:
    - "127.0.0.1:8090:8090"
```

External Docker apps (n8n, open-webui, AnythingLLM, etc.) join `subumbra-net`
and reach the proxy via Docker DNS: `http://subumbra-proxy:8090/t`. The host
port binding (`127.0.0.1:8090`) also allows native processes on the host to
reach the proxy. This network surface already exists — no changes needed.

**Per-app token decision after 42.2:**

After this round there are two valid models for adding a new app:

| Model | How it works | When to use |
|---|---|---|
| App routes through proxy | App uses `subumbra-proxy /t/` as api_base; proxy's token covers the key_id | Default — use for any standard app |
| App has its own adapter token | App gets its own entry in `SUBUMBRA_ADAPTER_REGISTRY` with scoped `allowed_keys`; accesses subumbra-keys directly | Only if the app needs a different scope or direct subumbra-keys access |

For the multi-app scenario Eric describes (LiteLLM + n8n + open-webui using
the same API keys), Model 1 (all route through proxy) is simpler:
- One adapter token in the registry (`subumbra-proxy`)
- One `PROXY_ALLOWED_KEYS` list covering all keys
- No per-app token management

This also minimizes entries in `SUBUMBRA_ADAPTER_REGISTRY`, which reduces the
blast radius of a compromised adapter token.

---

### EQ5, EQ6 — The broader integration pattern (this round proves it)

The full list of planned apps in `council/eric-questions.md:99-114` share a
common trait: all are `.env`-driven self-hosted applications with API keys as
their primary secret surface. After Round 42.2:

1. Bootstrap stores the real API key in subumbra-keys (already works today)
2. The app's API base URL is set to `http://subumbra-proxy:8090/t`
3. The app's API key is set to the plain key_id (e.g. `anthropic_prod`)
4. The app joins `subumbra-net`
5. The app's own `.env` is updated to remove the real key

Steps 2-5 are the pattern Round 42.2 proves with LiteLLM. No custom callbacks,
no Subumbra-specific code in the app, no HMAC keys in the app's environment.

The app's `.env` file would then contain only the key_id reference and the
`subumbra-proxy` base URL — both non-sensitive. This directly addresses Q3
(replacing real keys in existing env files).

---

### EQ3 — Credential file import (future round, but 42.2 is the prerequisite)

Eric's Q3 asks whether bootstrap could read an existing `.env`, encrypt the
keys, and replace them with key_id references.

This requires a new bootstrap mode. The flow would be:
1. `bootstrap --import /path/to/.env` reads raw keys from the file
2. Encrypts each key into a subumbra-keys record
3. Writes the updated `.env` with key_ids replacing real values
4. Adds `SUBUMBRA_API_BASE=http://subumbra-proxy:8090/t` to the `.env`
5. Shreds the intermediate copy

**This is blocked by Round 42.2 being incomplete.** Until LiteLLM is decoupled
from custom callbacks, the app-level integration pattern (`api_base` +
plain key_id) is not fully proven and documented. Round 42.2 proves it.
The credential import feature is the natural next round after 42.2 closes.

The shred requirement is already handled for `.env.bootstrap` at
`post-bootstrap.sh:112-128`. The same pattern can be applied to any imported
file.

---

### EQ2 — Post-bootstrap in docker (future round)

`post-bootstrap.sh:19-24` runs `docker compose run --rm -u 0 -T subumbra-keys`
to read `runtime.env`. It then writes to the host `.env` file at lines 56-78.

The host-side `.env` write is the reason this cannot trivially move into a
container: the `.env` is bind-mounted by other services and must exist on the
host for `docker compose up` to read it.

After Round 42.2, if all apps route through the proxy and the proxy has
comprehensive key scope, the post-bootstrap drift check for LiteLLM (which
this round removes) no longer applies. The remaining bootstrap-side work is
simpler and the post-bootstrap surface shrinks.

**Moving post-bootstrap into a container remains a future round** once the host
`.env` bind-mount pattern is replaced by a runtime secret distribution model.

---

## Implications for Round 42.2 Scope

Eric's questions confirm the round is on the right path. The specific items to
address in the approved plan based on this review:

### Must be in this round (from EQ1/EQ6)

**PROXY_ALLOWED_KEYS must include all key_ids that LiteLLM currently accesses.**

This is the gating prerequisite. For the proxy-as-gateway model to work for
LiteLLM now (and for all future apps), the proxy's scope must be comprehensive.
The approved plan must make this explicit.

When operators re-bootstrap after this round using the wizard, they should be
guided to give `subumbra-proxy` access to ALL their key_ids — not a subset.
The wizard prompt at `bootstrap/subumbra-bootstrap.py:1055` is:
```python
"subumbra-proxy": _prompt_allowed_keys("subumbra-proxy", available_key_ids),
```

A note in the approved plan (or in the config.yaml comment) should say:
"For the transparent sidecar pattern, grant subumbra-proxy access to all
key_ids it will need to serve." This is an operator guidance change, not a
code change.

### Confirmed out of scope for this round

- Credential file import (Q3) — future round, enabled by 42.2
- Post-bootstrap containerization (Q2) — future round
- Automatic credential file watching (far future) — noted, not designed yet
- Per-app adapter token provisioning for n8n, open-webui, etc. — not needed
  if they route through the existing proxy

### A note on the LITELLM_ALLOWED_KEYS dead-write

After this round, `LITELLM_ALLOWED_KEYS` is still written to `.env` by
`post-bootstrap.sh:74`, and `litellm` still has a registry entry with its own
token. This is harmless for now but signals that the `litellm` adapter is a
legacy concept once LiteLLM routes through the proxy. Cleaning up that registry
entry is a future round after the pattern is proven and stable.

---

## Recommendations

1. **Add operator guidance in the approved plan**: explicitly state that
   `PROXY_ALLOWED_KEYS` at bootstrap should include all key_ids that any
   app will need to access via the proxy. This is the practical prerequisite
   for the multi-app model Eric describes.

2. **Add to approved plan documentation**: a one-paragraph note in the
   config.yaml header explaining the new integration pattern — "set `api_base`
   to `http://subumbra-proxy:8090/t` and `api_key` to your key_id. Any app
   that supports custom API base URL is supported without custom code." This
   directly enables future app onboarding.

3. **Log a future round**: "credential file import" (`bootstrap --import
   /path/to/.env`) as the natural follow-on to 42.2. Add to `council/cleanup.md`
   or project roadmap.

4. **Note for the approved plan**: the `depends_on` for the `litellm` service
   should be updated to require `subumbra-proxy: service_healthy` (from
   claude-review.md F8), which is now the correct dependency chain for the
   multi-app model.
