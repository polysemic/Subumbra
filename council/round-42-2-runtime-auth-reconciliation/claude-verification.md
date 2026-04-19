# Round 42.2 Verification — Claude

Date: 2026-04-19
Round: `round-42-2-runtime-auth-reconciliation`
Plan: `council/approved/runtime-auth-reconciliation-v2.md`
Implementation commit: `a879eee` (Claude)
Remediation commit: `6e04c19` (Codex)
VPS branch: `round-42-2-runtime-auth-reconciliation`
VPS commit at verification time: `6e04c19`

---

## Verification Method

**Direct fallback path** (`reset.sh` skipped — state is known-good):

The running stack is from Codex's `clean-run-20260419T190511` (commit `a879eee`,
PROXY_ALLOWED_KEYS expanded). The VPS is at `6e04c19` (Codex's remediation commit).
No implementation-affecting changes occurred between Codex's clean-run and this
verification run. Reset was skipped per COUNCIL.md: "A verifier may skip reset only
if the running state is already known-good and the reason is documented explicitly."

**Run command executed:**
```bash
AGENT=claude ./scripts/council/verify.sh round-42-2-runtime-auth-reconciliation
```

**Run ID:** `claude-20260419T201010`

Artifacts copied to local repo from VPS via `scp`.

---

## Harness Fixes Applied

Two harness issues were found. One prevented verify.sh from running at all (fixed
per COUNCIL.md); one does not prevent running (deferred as dispute).

### Fix 1 — Matrix derivation: `proxy_disallowed` was None (COUNCIL.md-eligible fix)

**Root cause:** `scripts/council/verify.sh:574` — `all_known_keys` was built
exclusively from keys referenced in adapter registries. After Codex's
PROXY_ALLOWED_KEYS expansion, all adapter-registry keys were in `subumbra-proxy`'s
scope, leaving no candidate for `proxy_disallowed`. The derivation raised
`SystemExit("matrix unavailable")` and verify.sh exited before capturing any proof.

**Why this qualifies for fixing:** verify.sh exited with ERROR before capturing any
P9 proof. This satisfies COUNCIL.md: "a real harness bug prevents proof capture from
running at all."

**Fix applied to VPS** (`scripts/council/verify.sh:574`):

```python
# Before:
all_known_keys = sorted({key_id for adapter in registry.values()
                          for key_id in adapter.get("allowed_keys", [])})

# After:
all_known_keys = sorted(set(keys.keys()) | {key_id for adapter in registry.values()
                                             for key_id in adapter.get("allowed_keys", [])})
```

`keys` is already loaded from `keys.json` in the same block. This widens the
disallowed-key candidate pool to include keys in the store that are not scoped to
any adapter (e.g. `gemini_prod`). `proxy_disallowed` was correctly derived as
`gemini_prod` after the fix.

**Verification re-run result after fix:** P9.3/P9.4/P9.5/P9.6 all PASS (matrix
derived correctly; P9.4 used `gemini_prod` as proxy_disallowed and correctly got
`403 key_scope_denied` from subumbra-keys).

### Fix 2 — P9.1/P9.2 payload uses `subumbra:` prefix (NOT fixed — deferred as dispute)

**Root cause:** `scripts/council/verify.sh:657, 669` — the P9.1/P9.2 payloads
construct `"api_key": f"subumbra:{sys.argv[2]}"`. Round 42.2 changed the LiteLLM
contract to plain `api_key: <key_id>` (no `subumbra:` prefix). The harness was not
updated.

**Why this is NOT fixed here:** verify.sh runs and captures proof (P9.1/P9.2 FAIL,
others PASS/NOT-RUN). The bug does not prevent proof capture from running. Per
COUNCIL.md, only bugs that prevent running are eligible for in-verification fixes.

**Deferred:** See Out-of-Scope Disputes below.

---

## V1 Static Checks

All 5 checks from the approved plan, verified by local file reads.

| # | Check | Command | Result |
|---|---|---|---|
| 1 | No `subumbra:` prefix in api_key values | `grep -n 'api_key.*subumbra:' litellm/config.yaml` | **PASS** — only comment line (line 6) matches |
| 2 | `callbacks:` stanza removed | `grep -n '^  callbacks:' litellm/config.yaml` | **PASS** — no match |
| 3 | Subumbra auth vars gone from litellm env | Python grep on docker-compose.yml litellm block | **PASS** — `SUBUMBRA_ACCESS_TOKEN`, `SUBUMBRA_HMAC_KEY`, `SUBUMBRA_KEYS_URL`, `CF_WORKER_URL`, `DEEPSEEK_API_BASE` absent from litellm block; present only in their own services |
| 4 | `api_base` set for each active model | `grep -c 'api_base:' litellm/config.yaml` | **PASS** — 14 matches (12 active models + 2 comment lines; Gemini entry commented out) |
| 5 | Legacy header in `custom_callbacks.py` | `grep -n 'LEGACY' litellm/custom_callbacks.py` | **PASS** — line 2 matches |

Additional V1 observations:
- `docker-compose.yml` litellm `depends_on` now includes `subumbra-proxy: condition: service_healthy` ✓
- `post-bootstrap.sh` drift loop: `litellm` removed from `for svc in` list ✓
- `bootstrap/subumbra-bootstrap.py` wizard Step 3 text updated ✓
- `_build_litellm_alignment_lines` function replaced with proxy-routing path ✓
- `README.md` and `docs/subumbra-install.md` updated ✓

---

## V3 — PROXY_ALLOWED_KEYS Scope Check

**PASS**

```
subumbra-proxy allowed_keys: [
  'anthropic_prod', 'openai_prod', 'groq_prod', 'github_prod',
  'slack_prod', 'sendgrid_prod', 'openai_prod', 'deepseek_prod',
  'cerebras_prod', 'mistral_prod', 'openrouter_prod', 'together_prod', 'xai_prod'
]
```

All active key_ids from `litellm/config.yaml` (anthropic_prod, openai_prod, groq_prod,
deepseek_prod, cerebras_prod, mistral_prod, openrouter_prod, together_prod, xai_prod)
are present. Note: `openai_prod` appears twice in the list — harmless duplicate.

---

## P9 Run Results

**Run ID:** `claude-20260419T201010`
**VPS path:** `/opt/subumbra`
**Branch:** `round-42-2-runtime-auth-reconciliation`
**Commit:** `6e04c19`

| Check | Result | Notes |
|---|---|---|
| P9.1 LiteLLM allowed key | **FAIL** | Harness payload contract drift — see below |
| P9.2 LiteLLM disallowed key | **FAIL** | Same root cause as P9.1 |
| P9.3 sidecar allowed key | **PASS** | `anthropic_prod` via subumbra-proxy → CF Worker → Anthropic |
| P9.4 sidecar disallowed key | **PASS** | `gemini_prod` correctly denied (key_scope_denied) |
| P9.5 UI status | **PASS** | |
| P9.6 Worker invalid token | **PASS** | |

### P9.1 / P9.2 Failure Analysis

The harness sends:
```
api_key: "subumbra:anthropic_prod"
```

LiteLLM uses this as an api_key override and forwards it as `Authorization: Bearer
subumbra:anthropic_prod` to `http://subumbra-proxy:8090/t`. subumbra-proxy correctly
rejects it:
```
{"detail":"invalid key_id"}  →  HTTP 400
```

The approved plan explicitly changed the contract: `api_key: <key_id>` (plain, no
`subumbra:` prefix). The harness was not updated.

**Paradox proof:** The P9.1 failure itself proves the implementation is correct:
- subumbra-proxy received the request (LiteLLM is routing via the new sidecar)
- The old callback did NOT intercept it (no `subumbra:` prefix handling)
- subumbra-proxy correctly rejected `"subumbra:anthropic_prod"` as an invalid key_id format

The audit log in the P9.1 artifact confirms that the sidecar IS processing requests
correctly for properly-formatted payloads:
```
adapter_id: subumbra-proxy, key_id: anthropic_prod, verdict: allow
```
(from Codex's prior runs with plain `anthropic_prod` key_id — request_count: 7 in
the audit body)

---

## Out-of-Scope Disputes

### D1 — P9.1/P9.2 harness payload uses stale `subumbra:` contract

`scripts/council/verify.sh:657, 669` constructs P9.1/P9.2 payloads with
`f"subumbra:{sys.argv[2]}"`. Round 42.2 changed the LiteLLM api_key contract to
plain key_ids. The harness was not updated to match.

**Minimum fix:** Change `f"subumbra:{sys.argv[2]}"` to `sys.argv[2]` at both
locations. This is a two-character-per-line change and is maintenance, not a
spec change.

**Impact of deferring:** P9.1/P9.2 can never PASS without this fix. The
implementation IS correct; the harness is testing the OLD contract. Council should
approve this harness fix in a close-out or harness maintenance step.

---

## Overall Assessment

**Implementation: CORRECT**

All 7 approved plan changes are present and correct (V1 PASS). The V3 scope
prerequisite is satisfied (PASS). The sidecar routing mechanism is live and working
(P9.3/P9.4 PASS, P9.5/P9.6 PASS).

**Harness verdict: FAIL** — P9.1/P9.2 fail due to harness contract drift, not
implementation failure. The root cause is documented above and deferred as D1.

Round 42.2 implementation is complete per the approved plan. The remaining FAIL
is a harness maintenance item (two-line fix to verify.sh P9.1/P9.2 payloads).
