# Claude Synthesis — Round 41.7 Standalone LiteLLM Runtime Fix

## Consensus Items

All three reviews agree on the following points without reservation:

**C1 — Primary 401 root cause is missing callback mount and config wiring.**
The `docs/testbed-install.md` standalone template omits the `custom_callbacks.py` volume mount.
Without it, `litellm_settings.callbacks: custom_callbacks.proxy_handler_instance` silently fails to
load, `async_pre_call_deployment_hook` never runs, and the raw `subumbra:<key_id>` string reaches
the upstream provider.
Evidence: `docs/testbed-install.md:168-171`, `litellm/custom_callbacks.py:364-381,435-445`,
`litellm/config.yaml:115-118`.

**C2 — Primary `subumbra-keys unreachable` root cause is a missing network path.**
`subumbra-keys` is on `internal` only (`docker-compose.yml:39-47`). A standalone LiteLLM that lives
only on `subumbra-net` has no route to it. The error at `litellm/custom_callbacks.py:410-415` is
the expected result.

**C3 — Claude's `name: subumbra_internal` fix is the correct network topology fix.**
All three reviews accept this approach and reject the alternative. Gemini's own review explicitly
acknowledges that their original proposal (adding `subumbra-net` to `subumbra-keys`) would grant the
keys service internet access by inheriting `subumbra-net`'s bridge gateway — a security regression
against the product's air-gap requirement.

**C4 — `_wire_transport_once()` expansion is not warranted in this round.**
Codex and the Claude review reject it outright; Gemini's review votes to defer. All three reach the
same practical conclusion: this change should not enter 41.7. The code at
`litellm/custom_callbacks.py:253-256` already patches `module_level_aclient.client`, which covers
the openai/together/cerebras paths.

**C5 — `post-bootstrap.sh` modification is out of scope.**
All three reviews reject Gemini's proposal to have `post-bootstrap.sh` sync tokens to
`/opt/litellm/.env`. Bootstrap should not own operator deployment paths.

**C6 — Claude's proposal is the correct base for the approved plan.**
All three reviews reach this conclusion independently.

---

## Disagreements

### D1 — Token scope: explicit finding vs. implicit assumption

**Codex** does not list token scope (`SUBUMBRA_TOKEN_LITELLM` vs. `SUBUMBRA_TOKEN_PROXY`) as a
discrete finding, though it is implied in the change-bucket list (`codex-review.md:135`).

**Claude review** treats this as a named finding (R6) with specific code evidence:
`subumbra-keys/app.py:323-340` returns `adapter_unknown` on a token mismatch, and
`subumbra-keys/app.py:546` would return `key_scope_denied` if the token is valid but not authorized
for the requested key_id.

**Gemini review** calls out the token precision requirement in recommendation R3 but does not
specifically distinguish the `allowed_keys` scope check.

**My position:** Claude's review is the most complete here. The `allowed_keys` check at
`subumbra-keys/app.py:546` is a second independent failure mode beyond the token identity check.
An operator with the right token but a misconfigured `allowed_keys` list gets 403 `key_scope_denied`
from `subumbra-keys` — not a network error, not a callback error. Operator instructions must confirm
both: correct token identity AND correct `allowed_keys` scope for the adapter. This should be
explicit in the approved plan.

### D2 — Image pinning: framing

**Codex** does not mention image pinning as a finding.

**Claude review** accepts it as risk reduction (R8) but explicitly frames it as "compatibility-risk
reduction, not a proven root cause."

**Gemini review** recommends it in R4 without that caveat, framing it as preventing transport wiring
failures from version skew.

**My position:** Claude's framing is correct. The hard-fail at `custom_callbacks.py:246-249`
establishes a real compatibility dependency on `module_level_aclient.client` existing. Pinning the
standalone image to the same digest as the bundled service (`docker-compose.yml:78`) is the right
call. But it should be presented as a risk-reduction measure that eliminates a failure mode, not as
the explanation for the observed 401/500 failures — because the observed failures are fully explained
by the mount/config/network gaps. This framing distinction matters for the implementation: the pin
should be done but should not be treated as the root-cause fix.

### D3 — Startup verification: `ModuleNotFoundError` logging

**Gemini review** specifically calls out that the verification plan should confirm LiteLLM startup
logs do NOT contain `ModuleNotFoundError: No module named 'custom_callbacks'` — treating the startup
log as a signal, not just the request outcome.

**Codex** and the **Claude review** focus verification on the end-to-end request result, not
explicitly on the startup log check.

**My position:** Gemini adds genuine value here. Verifying that the callback loads successfully at
startup is a faster signal than waiting for a full round-trip request to fail. The proof artifact
for this round should include a startup log excerpt showing the callback loaded (or at minimum no
`ModuleNotFoundError`) before the end-to-end test. This costs nothing and disambiguates between
"callback loaded, request still failed" and "callback never loaded."

---

## Things Missed or Underweighted

**CF Access optional env vars (`CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`):**
All three reviews discuss `SUBUMBRA_TOKEN_LITELLM`, `SUBUMBRA_HMAC_KEY`, and `CF_WORKER_URL` as the
required env vars to copy. None explicitly call out that CF Access client credentials are optional —
but that they must be present if the worker URL is protected by Cloudflare Access. Claude's proposal
(`claude-proposal.md:186-195`) lists them, but the reviews don't probe whether the proof condition
should distinguish "worker is public" from "worker is CF Access-protected." This is worth a single
sentence in the operator instructions: "If your Worker is behind CF Access, also copy
`CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET`."

**`SUBUMBRA_HMAC_KEY` role in auth:**
`subumbra-keys/app.py` validates requests using HMAC signatures (not just the bearer token). All
three reviews focus on the token; none explicitly name the HMAC key as a separate failure mode.
If `/opt/litellm/.env` has a correct `SUBUMBRA_ACCESS_TOKEN` but a stale or missing
`SUBUMBRA_HMAC_KEY`, requests to `subumbra-keys` will fail HMAC validation — a different error path
than `adapter_unknown`. The operator instructions must include both.

**Standalone config.yaml content:**
Claude's proposal covers this (Change 4: replace `/opt/litellm/config.yaml` with Subumbra's
version), but no review explicitly lists the config.yaml model format as a verification item. A
standalone operator who already has a custom `config.yaml` with regular `api_key: sk-...` entries
and adds only `model_list` entries with `api_key: subumbra:...` format but omits the
`litellm_settings.callbacks:` stanza will hit the same 401. The approved plan should require
verifying the `callbacks:` stanza is present.

---

## Phased Plan

### Do now (41.7 scope)

1. **Product change — stable internal network name.**
   Add `name: subumbra_internal` to the `internal` network block in `docker-compose.yml`.
   Rationale: enables standalone projects to join the named network without widening `subumbra-keys`'
   exposure.

2. **Operator change — standalone `/opt/litellm/docker-compose.yml`.**
   - Join both `subumbra_internal` (external reference) and a local external network
   - Mount `./custom_callbacks.py:/app/custom_callbacks.py:ro`
   - Set env vars: `SUBUMBRA_ACCESS_TOKEN`, `SUBUMBRA_HMAC_KEY`, `CF_WORKER_URL`, and optionally CF
     Access client credentials
   - Pin the image to the same digest as the bundled service

3. **Operator change — `/opt/litellm/.env` token sync.**
   Copy from `/opt/subumbra/.env`: `SUBUMBRA_TOKEN_LITELLM` (as `SUBUMBRA_ACCESS_TOKEN`),
   `SUBUMBRA_HMAC_KEY`, `CF_WORKER_URL`.
   Instruction must explicitly name `SUBUMBRA_TOKEN_LITELLM`, not `SUBUMBRA_TOKEN_PROXY`.
   Must also confirm the `subumbra_litellm` adapter's `allowed_keys` covers all referenced key IDs.

4. **Operator change — `/opt/litellm/config.yaml` alignment.**
   Replace or update to include `subumbra:<key_id>` model entries AND the
   `litellm_settings.callbacks: custom_callbacks.proxy_handler_instance` stanza.

5. **Proof.**
   - Restart standalone LiteLLM, confirm startup logs show callback loaded (no `ModuleNotFoundError`)
   - Make a Subumbra-backed request end-to-end: `subumbra:<key_id>` → callback intercept → 
     `subumbra-keys` fetch → CF Worker → upstream → HTTP 200 streaming response
   - Artifact both the startup log excerpt and the curl/response output

### Defer to Round 42

- `_wire_transport_once()` provider coverage audit (requires post-fix runtime evidence)
- `post-bootstrap.sh` / preflight script operator automation
- OpenWebUI / N8N operatorization
- Broad operator template polish or standalone setup wizard
- Systematic 10-provider validation

---

## Approved Plan Recommendation

The synthesis supports a clean consensus: **use Claude's proposal as the base, adopt Gemini's
startup-log verification addition, add explicit `allowed_keys` and `SUBUMBRA_HMAC_KEY` to the
operator instruction requirements, and exclude all of Gemini's proposed code changes (network
attachment, transport wiring expansion, post-bootstrap sync).**

There are no unresolved technical disagreements blocking the approved plan. The round has clear
scope, clear proof criteria, and a single product change that is small and safe.
