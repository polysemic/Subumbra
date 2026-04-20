# Round 42.2 Proposal — Runtime Auth Reconciliation

Date: 2026-04-18

## 1. Evidence

The current system already treats runtime auth as a bundle, but its recovery
paths are inconsistent.

- `post-bootstrap.sh` reads a full runtime bundle from `runtime.env`, including
  `SUBUMBRA_TOKEN_LITELLM`, `SUBUMBRA_TOKEN_PROXY`, `SUBUMBRA_TOKEN_UI`,
  `SUBUMBRA_TOKEN_PROBE`, `SUBUMBRA_HMAC_KEY`, and `CF_WORKER_URL`, then writes
  them into `.env`.
  [post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L19-L45)
- But its current drift check only compares `SUBUMBRA_ACCESS_TOKEN` in running
  containers, not `SUBUMBRA_HMAC_KEY`.
  [post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L89-L107)
- Bundled services clearly consume both values, not just the token:
  - bundled LiteLLM uses `SUBUMBRA_ACCESS_TOKEN` and `SUBUMBRA_HMAC_KEY`
    [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L98-L109)
  - `subumbra-proxy` also uses both values
    [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L183-L186)
- The standalone LiteLLM docs already document that both
  `SUBUMBRA_ACCESS_TOKEN` and `SUBUMBRA_HMAC_KEY` must be copied from
  `/opt/subumbra/.env` into `/opt/litellm/.env`.
  [standalone-litellm.md](/home/eric/git/Subumbra/docs/standalone-litellm.md#L153-L176)
- The same doc also says, plainly, that no automated drift detection exists for
  the standalone path.
  [standalone-litellm.md](/home/eric/git/Subumbra/docs/standalone-litellm.md#L180-L194)
- The callback path explains why stale HMAC causes an early failure before
  Cloudflare: `subumbra-keys` HTTP failures raise immediately during record
  fetch in the LiteLLM callback.
  [custom_callbacks.py](/home/eric/git/Subumbra/litellm/custom_callbacks.py#L392-L408)
- The failure table in the standalone LiteLLM doc already has token-specific
  and network-specific cases, but it does not yet cover stale-HMAC-driven
  signed-request failures as a first-class recovery item.
  [standalone-litellm.md](/home/eric/git/Subumbra/docs/standalone-litellm.md#L230-L245)

Taken together, the repo already shows the real issue:

- runtime auth is multi-value
- bundled drift detection is only partial
- standalone reconciliation is manual
- callback failures from `subumbra-keys` are expected if the signed request path
  is out of sync

## 2. Current vs Desired

### Current

- canonical runtime auth values are generated centrally
- bundled stack gets partial drift detection
- standalone LiteLLM relies on manual copy/restart steps
- the system can appear mostly healthy while signed `subumbra-keys` requests are
  already broken

### Desired

- runtime auth is treated as one reconciled bundle
- bundled checks validate both access token and HMAC where required
- standalone LiteLLM has a supported sync/restart path from canonical runtime
  state
- one signed downstream request check proves the full `subumbra-keys` auth path
  works after reconciliation

## 3. Proposal

### 3A. Treat runtime auth as a bundle

For this round, the runtime auth bundle should explicitly include:

- `SUBUMBRA_ACCESS_TOKEN`
- `SUBUMBRA_HMAC_KEY`
- `CF_WORKER_URL`
- optional CF Access values when present

The key design point is not to special-case HMAC forever; it is to stop treating
“token sync” as the whole problem.

### 3B. Extend bundled drift detection in `post-bootstrap.sh`

Update the bundled-container drift check so services that consume
`SUBUMBRA_HMAC_KEY` are also checked for stale HMAC, not just stale
`SUBUMBRA_ACCESS_TOKEN`.

Exact target behavior:

1. keep the existing token-drift check
2. for services that use `SUBUMBRA_HMAC_KEY` in `docker-compose.yml`, also
   compare the running `SUBUMBRA_HMAC_KEY` against the new value from
   `runtime.env`
3. if either value is stale, emit a recreate warning
4. do not log raw HMAC values

This stays narrow and aligned with current architecture. It improves bundled
correctness without changing token semantics.

### 3C. Add a supported standalone reconciliation script

Add a small operator script for standalone LiteLLM, for example:

- `scripts/sync-standalone-litellm.sh`

Responsibilities:

1. read canonical values from `/opt/subumbra/.env`
2. update `/opt/litellm/.env` for:
   - `SUBUMBRA_ACCESS_TOKEN`
   - `SUBUMBRA_HMAC_KEY`
   - `CF_WORKER_URL`
   - optional `CF_ACCESS_CLIENT_ID`
   - optional `CF_ACCESS_CLIENT_SECRET`
3. preserve unrelated LiteLLM env entries
4. recreate standalone LiteLLM
5. print a short success/failure summary without secrets

This should be presented as the supported post-bootstrap reconciliation path for
standalone LiteLLM, instead of leaving users to remember manual copy commands.

### 3D. Add one signed-request acceptance check

Round 42.1 added a Worker acceptance probe. Round 42.2 should add one
additional downstream acceptance check for the signed `subumbra-keys` path.

For scope control, this can be LiteLLM-specific:

1. after standalone reconciliation, run one minimal request that forces the
   callback to fetch a record from `subumbra-keys`
2. success condition: no `401` from `subumbra-keys`
3. if the request fails with a `401`, the operator output should identify the
   problem as downstream runtime auth mismatch, not provider auth

This does not require a broad new observability system. It just proves the path
that `42.1` intentionally did not cover.

### 3E. Update standalone docs from “manual recipe” to “supported flow”

Keep the existing manual explanation in
[standalone-litellm.md](/home/eric/git/Subumbra/docs/standalone-litellm.md),
but add:

1. a short “preferred reconciliation command” section using the new script
2. a failure mode row for stale HMAC / invalid signed request to `subumbra-keys`
3. a reminder that bootstrap reruns can invalidate both token and HMAC

## 4. Failure Modes

| Failure mode | Why it matters | Minimal handling |
|---|---|---|
| Access token updated but HMAC stale | Signed `subumbra-keys` requests fail before Cloudflare | Reconciliation script updates both values together |
| Standalone `.env` updated but container not recreated | Runtime still uses old values | Script recreates LiteLLM as part of sync |
| CF Access values required but missing | Worker path can still fail later | Sync script copies them when present, does not invent them |
| Bundled service stale HMAC | Partial auth mismatch remains hidden | `post-bootstrap.sh` drift check warns on stale HMAC too |

## 5. Exclusions

This round should explicitly avoid:

- changing Worker token architecture
- redesigning HMAC semantics
- automating every future external app integration
- making `post-bootstrap.sh` manage arbitrary external stacks directly
- broad logging or secret-bearing diagnostics

Standalone LiteLLM should be the first supported external consumer, not the
beginning of a giant general-purpose orchestration system.

## 6. Open Questions

1. Should the standalone reconciliation script live in `scripts/` as a general
   operator utility, or under `scripts/council/` as a testing/verification
   helper? My recommendation is `scripts/`, because this is a user-facing
   runtime maintenance path, not just a council artifact.
2. Should the signed-request acceptance check live inside the reconciliation
   script, or remain a documented follow-up command? My recommendation is to
   keep the first version simple: sync + recreate in the script, acceptance
   check in docs/verification, unless the other reviews strongly prefer a fully
   integrated check.
3. Should `post-bootstrap.sh` eventually support optional downstream hook calls
   for external consumers? That feels like a later round. `42.2` should first
   make the supported standalone LiteLLM path correct and explicit.
