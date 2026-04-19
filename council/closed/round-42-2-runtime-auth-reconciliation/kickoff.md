# Round 42.2 Kickoff — Runtime Auth Reconciliation

Date: 2026-04-18

## Why This Round Exists

Round 42.1 fixed the narrow Worker auth recovery scope and proved that the
Cloudflare Worker acceptance path can be checked explicitly after bootstrap.

However, live post-round use exposed a second recurring runtime break:

- standalone LiteLLM can hold a stale `SUBUMBRA_HMAC_KEY` or other derived
  runtime auth value even after `SUBUMBRA_ACCESS_TOKEN` is current
- this causes `subumbra-keys` request-signature failures before the request
  reaches the Worker
- current docs treat standalone sync as a manual operator step, and
  `post-bootstrap.sh` only checks `SUBUMBRA_ACCESS_TOKEN` drift in bundled
  containers

This makes the system vulnerable to a realistic operator failure mode after
bootstrap reruns, partial restarts, or recovery work.

## Scope

In scope:

1. Define the runtime auth bundle that must stay coherent across downstream
   consumers
2. Harden bundled-stack drift detection so it covers all required runtime auth
   values, not only `SUBUMBRA_ACCESS_TOKEN`
3. Add a supported reconciliation path for standalone LiteLLM as the first
   external consumer
4. Add one acceptance check that proves downstream signed requests to
   `subumbra-keys` are valid after reconciliation

Out of scope:

- reopening Round 42.1 Worker auth recovery design
- broader multi-app automation for every future external integration
- token architecture redesign
- changing Cloudflare Worker auth semantics

## Starting Assumptions

- Round 42.1 remains valid and should still be closed on its own terms
- the new problem is broader than Worker token drift
- the missing product behavior is runtime auth reconciliation for downstream
  consumers that depend on both access token and HMAC

## Success Condition

Round 42.2 succeeds if:

1. downstream consumers can be resynced from the canonical Subumbra runtime
   source of truth without hand-editing individual values
2. bundled drift checks catch stale HMAC alongside stale access tokens
3. standalone LiteLLM can be reconciled and restarted by a supported flow
4. one real LiteLLM request succeeds after that reconciliation flow
