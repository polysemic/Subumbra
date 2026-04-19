# Round 42.2 Revised Proposal — Decouple LiteLLM From Subumbra Auth Authority

Date: 2026-04-19

## 1. Positions I Accept

### Accept from Claude: the current recurring failure is caused by LiteLLM owning Subumbra runtime auth state

I accept Claude's strongest evidence and framing:

- `litellm/custom_callbacks.py` freezes Subumbra auth material at module import,
  so any bootstrap rerun that regenerates `SUBUMBRA_TOKEN_LITELLM` or
  `SUBUMBRA_HMAC_KEY` leaves LiteLLM stale until the container is recreated.
  [custom_callbacks.py](/home/eric/git/Subumbra/litellm/custom_callbacks.py#L71-L76)
- bundled LiteLLM is explicitly given Subumbra runtime auth values in
  `docker-compose.yml`, which is the structural reason drift keeps recurring.
  [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L98-L109)
- current LiteLLM model entries are activated through the `subumbra:` prefix and
  global callback wiring, which makes the callback path the live authority today.
  [config.yaml](/home/eric/git/Subumbra/litellm/config.yaml#L17-L107),
  [config.yaml](/home/eric/git/Subumbra/litellm/config.yaml#L111-L118)
- the transparent sidecar already owns record fetch, HMAC signing, Worker
  forwarding, and provider auth injection for the path it supports.
  [app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L135-L163),
  [app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L266-L315),
  [providers.json](/home/eric/git/Subumbra/worker/src/providers.json#L1-L59)

This is the best concrete explanation for why the same break keeps resurfacing:
LiteLLM is still carrying Subumbra auth authority it should not need to carry.

### Accept from Gemini: the strategic direction should be real decoupling, not better callback maintenance

I accept Gemini's core direction:

- LiteLLM should become a consumer of a Subumbra-owned surface, not a co-owner
  of Subumbra's auth bundle.
- removing `subumbra:`-prefix dependence is strategically better than improving
  env-sync tribal knowledge around `custom_callbacks.py`.

That direction matches the broader Round 42 outcome too: Subumbra-owned
surfaces should grow while app-specific glue shrinks.

### Accept from my first proposal: this exposed a runtime auth bundle problem, not just one stale variable

I still accept the underlying diagnosis from my first proposal:

- `post-bootstrap.sh` reads a multi-value runtime bundle from `runtime.env`.
  [post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L19-L45)
- its drift check currently compares only `SUBUMBRA_ACCESS_TOKEN`, not the full
  auth bundle used by callback-based consumers.
  [post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L89-L107)
- the standalone LiteLLM doc already documents manual sync for both access token
  and HMAC, which is evidence that the current model is operationally brittle.
  [standalone-litellm.md](/home/eric/git/Subumbra/docs/standalone-litellm.md#L153-L194)

So the problem statement remains valid even if the best answer is now
"stop making LiteLLM hold that bundle" rather than "sync the bundle better."

## 2. Positions I Reject

### Reject from my first proposal: standalone reconciliation tooling as the main outcome

I reject the strongest version of my earlier path:

- a dedicated standalone LiteLLM sync script
- expanded callback-era env reconciliation as the center of the round

Why:

1. It solves the symptom while preserving the trust shape we want to reduce.
2. It keeps LiteLLM in possession of Subumbra auth material.
3. It makes a brittle operator pattern feel official instead of transitional.

If a temporary sync path still exists during migration, it should be treated as
compatibility scaffolding, not the strategic output of `42.2`.

### Reject from Claude: broad provider-family scope in this same round

I reject the broadest version of Claude's implementation scope:

- moving all currently working LiteLLM providers in one pass
- including Anthropic and other non-OpenAI-shaped flows as if the same sidecar
  contract is already equally proven for all of them

Why:

- Round 42 established the transparent sidecar as the primary documented path
  for OpenAI-compatible / low-mutation app flows, not as a universal translation
  surface for every provider family.
  [round-42-operator-hardening.md](/home/eric/git/Subumbra/council/approved/round-42-operator-hardening.md#L7-L15)
- the current LiteLLM config still mixes provider families with different path
  and request-shape expectations.
  [config.yaml](/home/eric/git/Subumbra/litellm/config.yaml#L17-L107)
- `subumbra-proxy` preserves inbound path and derives upstream host from the key
  record; it does not add Anthropic-specific body translation logic.
  [app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L182-L190),
  [app.py](/home/eric/git/Subumbra/subumbra-proxy/app.py#L307-L315)

So I agree with Claude on the mechanism, but not on stretching the round wider
than the already-approved sidecar contract.

### Reject from Claude: the claim that `docs/standalone-litellm.md` does not exist

Claude's proposal says the standalone LiteLLM doc does not exist. That is
incorrect. The file exists and already documents the current callback-era sync
burden. [standalone-litellm.md](/home/eric/git/Subumbra/docs/standalone-litellm.md#L1-L245)

That matters because the round should update or explicitly supersede the real
operator guidance, not assume there is none.

## 3. Path That Resolves the Disagreement

### 3A. Narrow the round to decoupling LiteLLM from Subumbra auth authority for the supported transparent-surface slice

The round should explicitly target:

- LiteLLM as a consumer of the transparent sidecar for the provider/request
  families already compatible with the Round 42 sidecar contract
- not universal LiteLLM/provider-family decoupling in one pass

This resolves the disagreement cleanly:

- it accepts Claude and Gemini's architectural direction
- it avoids backsliding into callback maintenance
- it preserves the Round 42 scope guardrail against overclaiming universality

### 3B. Make `subumbra-proxy` the authority for the supported LiteLLM path

For the supported slice, the implementation should move to:

- LiteLLM `api_base` pointing at `subumbra-proxy`
- LiteLLM `api_key` carrying plain `key_id`
- `subumbra-proxy` owning:
  - record fetch
  - HMAC signing
  - Worker auth
  - provider auth injection

That means LiteLLM no longer needs to hold, sync, or restart around:

- `SUBUMBRA_ACCESS_TOKEN`
- `SUBUMBRA_HMAC_KEY`
- `SUBUMBRA_KEYS_URL`
- `CF_WORKER_URL`
- CF Access credentials

for the migrated path.

### 3C. Treat callback-based LiteLLM flow as legacy, not the center

I agree with Claude that `custom_callbacks.py` should not be deleted in this
round. But its status should become explicit:

- legacy compatibility path
- not the preferred integration
- not the authority for the supported path going forward

That lets us move forward without pretending every old path vanishes
immediately.

### 3D. Update operator guidance to match the new authority boundary

Because the standalone LiteLLM doc exists, the round should update the docs to
say clearly:

- callback-era runtime auth syncing is legacy behavior
- the supported decoupled path routes through `subumbra-proxy`
- LiteLLM should not hold Subumbra runtime auth material for that path

The round should also remove dead or misleading drift checks once LiteLLM is no
longer expected to carry those auth values.

## 4. Revised Round Shape

The best merged path for `42.2` is:

1. **Primary goal**
   - decouple LiteLLM from Subumbra runtime auth authority

2. **Preferred mechanism**
   - use `subumbra-proxy` transparent routing with plain `key_id`
   - remove `subumbra:` callback dependence for the supported slice

3. **Scope guardrail**
   - do not claim universal provider-family migration unless the sidecar
     contract is already proven for that family

4. **Config / compose direction**
   - move migrated LiteLLM models to proxy-owned `api_base`
   - remove Subumbra auth envs from LiteLLM where they are no longer needed
   - drop dead LiteLLM drift-check expectations

5. **Docs / operator direction**
   - update the real standalone LiteLLM documentation
   - mark callback flow as legacy compatibility

6. **Carry-forward**
   - any remaining non-compatible provider families get a future round rather
     than being forced into this one

## 5. Practical Implication

The center of `42.2` should no longer be:

- "how do we keep LiteLLM's Subumbra auth bundle synchronized better?"

It should become:

- "how do we stop requiring LiteLLM to own that auth bundle for the supported
  path at all?"

That is the clearer long-term direction, and Claude's proposal materially
strengthens the evidence for taking it.
