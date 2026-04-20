# Council Cleanup Log

Minor upkeep items that do not justify a full council round on their own.
Use this log for truth-alignment, prompt hygiene, naming cleanup, and other
small maintenance findings.

Do not use this file for architecture changes, runtime behavior changes,
security decisions, or implementation-scope decisions. Those still require a
normal council round.

---

## Round 34 — 2026-04-10 (post-verification smoke test fixes)

**Transport bypass for OpenRouter, Mistral, xAI** (`litellm/custom_callbacks.py`, `_wire_transport_once()`)  
LiteLLM dispatches OpenRouter, Mistral, and xAI through `base_llm_http_handler` → `AsyncHTTPHandler` rather than the OpenAI SDK path used by Together/Cerebras/Gemini. The `AsyncHTTPHandler` instances for these three providers were never added to the `_wire_transport_once()` wiring loop, so requests bypassed `KeyVaultTransport` entirely and hit providers directly with `FORGE_ACCESS_TOKEN` as auth. Additionally, the params key must be `{"ssl_verify": None}` (not `None`) to match the handler instance that `BaseLLMHTTPHandler` acquires at call time. Fixed: added openrouter, mistral, xai to the wiring loop with correct params.

**xAI model `grok-2-latest` deprecated** (`litellm/config.yaml`)  
The approved plan specified `xai/grok-2-latest`; LiteLLM resolves this to `grok-2-1212`, which xAI's API rejects as "Model not found." The grok-2 series has been deprecated. Updated to `xai/grok-3`.

**Cerebras model `llama-3.3-70b` not accessible** (`litellm/config.yaml`)  
The approved plan specified `cerebras/llama-3.3-70b`. Cerebras returns "does not exist or you do not have access to it" with the real API key. The transport chain is correct (fake key returns "Wrong API Key"). Updated to `cerebras/llama3.1-8b` which is accessible on the operator's tier.

These are out-of-spec runtime findings not coverable by static spec checks. The approved plan's provider catalog (`providers.json`) and LiteLLM route structure remain correct; only the model string values in `config.yaml` and transport wiring in `custom_callbacks.py` changed.

## Round 34 — 2026-04-09

**Incorrect reset command in approved plan** (`council/approved/provider-flexibility.md`, Verification Steps §3)  
The plan specifies `./scripts/council/reset.sh --build litellm`. The reset script does not support `--build litellm` and its help text explicitly says litellm config changes (including `config.yaml`) are bind-mounted and require recreate-only. Correct command is `./scripts/council/reset.sh`. Does not affect runtime behavior; verification passed with plain reset. Update the approved plan to remove `--build litellm`.

## Entry Format

| Date | Finding | Affected Files | Severity | Disposition | Status |
|------|---------|----------------|----------|-------------|--------|

## Round 36 — 2026-04-11 (post-verification findings)

**`run_push_registry` uses `cwd=DATA_DIR` instead of `cwd=WORKER_SRC`** (`bootstrap/keyvault-bootstrap.py`, `run_push_registry()`)  
The approved plan specifies `cwd=WORKER_SRC` for the `wrangler kv key put` call in `run_push_registry`. The implementation uses `cwd=DATA_DIR`. Functionally equivalent when `--namespace-id` is provided directly (wrangler does not need to locate the namespace via wrangler.toml), confirmed by P36.3 PASS. Non-blocking; implementation is correct at runtime.

**`--name` flag omitted from `wrangler kv key put` calls** (`bootstrap/keyvault-bootstrap.py`, `deploy_worker()` and `run_push_registry()`)  
The approved plan includes `--name <worker_name>` on both KV push calls. The implementation omits it. When `--namespace-id` is provided, `--name` is redundant for namespace addressing. Confirmed working by P36.2 and P36.3 PASS. Non-blocking deviation noted per council rule.

**`docs/verification-policy.md` updated without Round 36 scope** (`docs/verification-policy.md`)  
The implementation updated `docs/verification-policy.md` to document `clean-run.sh` as the preferred verification path. This file was not listed in the Round 36 approved plan. The change is accurate and helpful (it mirrors the COUNCIL_PROMPT.md language) but was made outside the spec. No functional impact.

**`reset.sh --build` starts the bootstrap container** (`scripts/council/reset.sh`)  
When run as `reset.sh --build bootstrap`, `docker compose up -d --force-recreate --build bootstrap` starts the bootstrap container, causing bootstrap to re-run in the background. This invalidates the running token state and requires `post-bootstrap.sh` + `docker compose up -d --force-recreate` to re-sync before verification. The P36.5 initial run failed with 401 for this reason. Workaround: always run `post-bootstrap.sh` and `docker compose up -d --force-recreate` after `reset.sh --build bootstrap`. Longer fix: `reset.sh --build` should not start profiled one-shot containers.

## Round 41 — 2026-04-16 (verification pass findings)

**`PROJECT_STATUS.md` and `CLAUDE.md` truth-alignment deferred** — Gemini's
Round 41 synthesis listed these doc updates as consensus scope, but Claude and
Codex syntheses did not include them. Resolved in the approved plan as cleanup
scope, not implementation scope. Both files should be updated at close-out to
reflect the bolt-on architecture and Round 41 "universality" goal.

**Bootstrap image logs `FORGE_TOKEN_LITELLM`** (`bootstrap/subumbra-bootstrap.py`)
— The VPS had a cached `bootstrap` image built before Round 41.4 (full rebrand).
It logs `FORGE_TOKEN_LITELLM generated` instead of `SUBUMBRA_TOKEN_LITELLM
generated`. `post-bootstrap.sh` handles both names with a fallback at line 31.
Non-blocking. Fix: rebuild bootstrap image (`--build bootstrap` in clean-run).

**Clean-run `--build` should be documented as required after image-built service
changes** (`docs/subumbra-developer.md`) — Added note to Lane B/C sections.
The precondition was discovered during Round 41 verification pass.

## Open Items

| Date | Finding | Affected Files | Severity | Disposition | Status |
|------|---------|----------------|----------|-------------|--------|
| 2026-04-09 | `docs/operator-guide.md` now has duplicate top-level section numbering after the Round 32 Recovery Playbook insertion: `## 6. Adapter Authority Expiry And Emergency Expiry` and `## 6. Slack Host-Only Trust Tradeoff`. This is doc-only and does not affect proof semantics. | `docs/operator-guide.md` | Low | Fixed in Round 37 cleanup | Closed |
| 2026-04-07 | `docs/provider-catalog.md` omits `user-agent` as a required header for GitHub. GitHub returns HTTP 403 with "Request forbidden by administrative rules" if no User-Agent is present. Fixed directly in catalog; curl example updated. | `docs/provider-catalog.md` | Low | Fixed directly | Closed |
| 2026-04-07 | `keyvault-proxy` sidecar response contains `server: uvicorn` (uvicorn's own Server header). P8-sidecar checks for `server` absence in CF-header context, but uvicorn adds its own header. The CF `server: cloudflare` header is correctly stripped from Worker responses. The uvicorn header has been present since Round 25. Consider adding `ServerHeader(False)` or equivalent in a future round. | `keyvault-proxy/app.py` | Low | Deferred | Open |
| 2026-04-07 | `worker/src/worker.js` URL parse error logging (`catch (e) { console.error("keyvault: URL parse error", e); }`) was originally noted as an uncommitted pre-Round-30 working-tree change, but it is now committed in the current repo state. The cleanup follow-up is complete. | `worker/src/worker.js` | Low | Already committed; cleanup item closed | Closed |
| 2026-04-08 | `scripts/council/reset.sh` does not set `DOCKER_BUILDKIT=0` for `--build` variants. The BuildKit container driver fails DNS resolution for `registry-1.docker.io` in this network environment; the legacy builder (host daemon) works correctly. Applied manually as `DOCKER_BUILDKIT=0 ./scripts/council/reset.sh --build ...` during Round 31 verification. Should be fixed in `reset.sh` before next round requiring a rebuild. | `scripts/council/reset.sh` | Low | Fixed in Round 37 cleanup (separate build/up + DOCKER_BUILDKIT=0) | Closed |

## Closed Items

| Date | Finding | Affected Files | Severity | Disposition | Status |
|------|---------|----------------|----------|-------------|--------|
| 2026-04-06 | `CLAUDE.md` still says forge-keys is only reachable from the LiteLLM container, but the current Compose topology places `ui`, `adapter-probe`, and `keyvault-proxy` on the `internal` network too. | `CLAUDE.md`, `docker-compose.yml` | Medium | Fixed directly by Gemini | Closed |
| 2026-04-06 | `CLAUDE.md` still frames the explicit sidecar/service as the next adapter form, while `PROJECT_STATUS.md` records Round 25 as completed. | `CLAUDE.md`, `PROJECT_STATUS.md` | Medium | Fixed directly by Gemini | Closed |
| 2026-04-06 | `council/COUNCIL_PROMPT.md` proposal/review prompts explicitly discuss minimal error handling and logging, but the approval prompt could require that decision to be carried into the approved plan more explicitly. | `council/COUNCIL_PROMPT.md` | Low | Fixed directly by Gemini | Closed |
## 2026-04-07

- `scripts/council/reset.sh --build <service>` currently relies on Docker's active BuildKit builder. In this environment the buildx docker-container driver cannot resolve Docker Hub metadata, while the host daemon and legacy builder can. Prefer a future harness maintenance patch to force the reliable non-BuildKit path for image rebuilds (for example `DOCKER_BUILDKIT=0`, optionally with `--pull=false`) so verification rebuilds are less sensitive to builder-DNS drift.
- `council/harness-usage-alignment/kickoff-prompts.md`: kickoff prompt asked
  later participants to read `codex-proposal.md` before writing their own
  proposal. Future planning-round kickoff prompts should avoid proposal-specific
  pre-read bias or label it explicitly as reference context only.
| 2026-04-08 | Harness bug in \`scripts/council/verify.sh\`: Docker Compose prioritizes shell environment variables over \`.env\` file edits. Expiry simulation failed when \`FORGE_ADAPTER_REGISTRY\` was pre-set in the environment. | \`scripts/council/verify.sh\` | Medium | Fixed in verification harness | Closed |
| 2026-04-08 | Transport wiring bug in `litellm/custom_callbacks.py`: `_wire_transport_once()` used a `_transport_wired` once-only flag. LiteLLM's `in_memory_llm_clients_cache` has a 3600-second TTL; after expiry, `get_async_httpx_client()` returns a new unwired handler. The guard prevented re-wiring, causing the forge adapter token to reach providers directly as the API key (rejected as invalid). Fix: remove the flag, create `_keyvault_client` once but re-apply `handler.client = _keyvault_client` on every forge call. Applied to `litellm/custom_callbacks.py`; `docker compose restart litellm` picks it up. | `litellm/custom_callbacks.py` | High | Fixed directly | Closed |
| 2026-04-09 | `scripts/council/clean-run.sh` `run_step()` routes all step stdout/stderr to `/dev/null` (line 108). On failure, the operator sees `ERROR: failed bootstrap` but no step output — no diagnostic information survives. Spec does not require capturing step output; this is within v1 scope. Consider capturing step output to a per-step log file in v2 so failures are debuggable. | `scripts/council/clean-run.sh:108` | Low | Deferred to v2 | Open |
| 2026-04-10 | Round 35 `verify.sh` P35.1 block uses a hardcoded absolute path: `python3 - /home/eric/git/Subumbra/.env.bootstrap.example` (line 1355). Other proofs use relative paths or variables. Works on this machine; breaks portability if the repo moves. Should use `"$(pwd)/.env.bootstrap.example"` or `"${SCRIPT_DIR}/../../.env.bootstrap.example"`. | `scripts/council/verify.sh:1355` | Low | Fixed in Round 37 cleanup (all three absolute paths replaced with relative paths; now at line 1392 as `.env.bootstrap.example`) | Closed |
| 2026-04-09 | Round 33 transparent sidecar makes 2 forge record fetches per successful transparent request: once in `handle_transparent_request()` (line 296) to get `target_host` for `build_transparent_target_url()`, and again in `proxy_via_worker()` (line 200) to build the proxy payload. The approved spec created this ambiguity by requiring the transparent route to call `fetch_record()` before `proxy_via_worker`, while `proxy_via_worker` also fetches internally. No security or correctness impact; forge audit shows a doubled `get_key → allow` entry per transparent call. A future refactor could pass the record as a parameter to `proxy_via_worker()` to eliminate the redundant fetch. | `keyvault-proxy/app.py:200,296` | Low | Deferred | Open |

## 2026-04-11

- `scripts/council/verify.sh` can record a failed `preflight.txt` with `HARNESS-ERROR` in some Codex-local runs even when a direct subsequent `./scripts/council/preflight.sh` passes and the same workspace is healthy. This did not block Round 37 close-out because Claude and Gemini produced official PASS artifacts and Codex confirmed spec compliance manually. Keep as a non-blocking harness observation for later investigation if it repeats. Affected files: `scripts/council/verify.sh`, `scripts/council/preflight.sh`, `council/closed/round-37-cleanup-review/runs/codex-20260411T104609/`, `council/closed/round-37-cleanup-review/runs/codex-20260411T104717/`.
- The same Codex-local preflight anomaly recurred during Round 38: `AGENT=codex ./scripts/council/verify.sh round-38-system-review` wrote `HARNESS-ERROR` with `preflight.txt` reporting all default services unavailable, but an immediate direct `docker compose ps` and `./scripts/council/preflight.sh` both showed the stack healthy. Non-blocking because Claude and Gemini produced PASS verification artifacts for Round 38. Affected files: `scripts/council/verify.sh`, `scripts/council/preflight.sh`, `council/closed/round-38-system-review/runs/codex-20260411T121821/`.
- The same Codex-local preflight anomaly recurred during Round 39: repeated `AGENT=codex ./scripts/council/verify.sh round-39-poc-deployment-hardening` runs wrote `HARNESS-ERROR` with `preflight.txt` reporting all default services unavailable, while direct `./scripts/council/preflight.sh` passed and the clean-run diagnostic API status showed the new `worker_reachable` field working. Non-blocking because Claude and Gemini both produced PASS verification artifacts for Round 39. Affected files: `scripts/council/verify.sh`, `scripts/council/preflight.sh`, `council/closed/round-39-poc-deployment-hardening/runs/codex-20260411T150602/`, `council/closed/round-39-poc-deployment-hardening/runs/codex-20260411T150713/`, `council/clean-run-harness/runs/clean-run-20260411T145540/diag-api-status.json`.
- Round 40 close-out found a small approved-plan verification erratum: `council/approved/broader-decoupling-and-security-hardening.md` listed `./scripts/council/reset.sh --build forge-keys keyvault-proxy adapter-probe`, but `scripts/council/reset.sh` does not support `adapter-probe` as a `--build` target. Verified fallback was `./scripts/council/reset.sh --build forge-keys keyvault-proxy`, with separate probe rebuild only when needed. This did not affect the product implementation. Affected files: `council/approved/broader-decoupling-and-security-hardening.md`, `scripts/council/reset.sh`.
- The same Codex-local preflight anomaly recurred during Round 40: the first `AGENT=codex ./scripts/council/verify.sh round-40-broader-decoupling-and-security-hardening` run wrote `HARNESS-ERROR` with `preflight.txt` showing all default services unavailable, while direct subsequent `./scripts/council/preflight.sh` passed and the stack was healthy. Non-blocking because Claude and Gemini produced PASS verification artifacts for Round 40. Affected files: `scripts/council/verify.sh`, `scripts/council/preflight.sh`, `council/closed/round-40-broader-decoupling-and-security-hardening/runs/codex-20260411T162208/`.

## 2026-04-13

- **Bootstrap wizard: adapter key_id validation is deferred to final submission, not at point of entry.** During VPS testing (Round 41.2), entering a mistyped key_id (e.g. `openai` instead of `openai_test`) for an adapter scope was accepted without error at that step. The error only surfaces after completing the entire wizard: `ERROR: keyvault-proxy requested unknown allowed key_id(s): openai`. The operator must restart the wizard from the beginning. Fix candidate for Round 41.3: validate each adapter's allowed key_id list immediately after input against the key_ids collected in the current bootstrap run, re-prompting that adapter on mismatch instead of continuing. Codex had also proposed replacing free-text name entry with numbered selection from the available list to eliminate typo errors entirely — that UX upgrade is a companion candidate for 41.3. Affected file: `bootstrap/keyvault-bootstrap.py` (adapter scope collection step). Priority: Medium. **Status: Fixed 2026-04-13** — `_prompt_allowed_keys()` now validates inline and re-prompts immediately on unknown key_id input. Post-wizard `_validate_allowed_keys()` retained as a backstop for the automation/CI path. `_get_push_registry_cf_creds()` also updated to add empty-check loops for its three interactive prompts (previously would reach the CF API before failing on empty values).

## 2026-04-14 — Round 41.3 Rebrand post-verification

- **`worker/.dev.vars` and `worker/.dev.vars.example` not updated**: These wrangler local-dev files still reference `FORGE_ACCESS_TOKEN` and `FORGE_HMAC_KEY`. Not used in production (bootstrap overrides all CF Secrets). Affected files: `worker/.dev.vars`, `worker/.dev.vars.example`. Priority: Low.

- **`scripts/forge-expire-adapter.sh` not renamed**: The helper script retains the old name; `scripts/council/verify.sh` references it at lines 1081, 1207, 1211 and still works. A future pass can rename to `subumbra-expire-adapter.sh` and update `verify.sh` references. Priority: Low.

- **2026-04-14 - LiteLLM Config Preflight Validation Script**: The proposed validation script in `gemini-investigation.md` (which parses `litellm/config.yaml` using native bash arrays and validates against `LITELLM_ALLOWED_KEYS`) was omitted from the Round 41.3 Approved Plan for `post-bootstrap.sh`. This should be integrated in an upcoming cleanup round to prevent downstream operators from experiencing silent 403 `key_scope_denied` upstream auth errors triggered by config typoes.

## 2026-04-14 — Round 41.4 Full Rebrand post-verification

- **Internal Python variables in bootstrap retain legacy `forge` naming**: In `bootstrap/subumbra-bootstrap.py`, the variable `forge_hmac_key` was used in five locations (Lines 978, 1100, 1449, 1493, 1541) despite the output key being renamed to `SUBUMBRA_HMAC_KEY`. **Status: Fixed 2026-04-14** (renamed to `subumbra_hmac_key`).

## 2026-04-19 — Round 42.2 close-out harness fix

- **`scripts/council/verify.sh` P9.1/P9.2 payload uses stale `subumbra:` contract** (`scripts/council/verify.sh:657,669`)  
  `verify.sh` constructed P9.1/P9.2 LiteLLM payloads as `"api_key": f"subumbra:{sys.argv[2]}"`. Round 42.2 changed the LiteLLM contract to plain `api_key: <key_id>` (no prefix). Fix applied in close-out commit. **Status: Fixed 2026-04-19**.

- **`scripts/council/verify.sh` P9.1/P9.2 architecturally incompatible with Round 42.2** (`scripts/council/verify.sh:742, 764`)  
  After applying the prefix fix, P9.1/P9.2 still FAIL due to deeper architectural drift:
  - **P9.1 fallback** (`verify.sh:742`): checks for `adapter_id: litellm` in the audit log. Round 42.2 removed the litellm direct-adapter path — all subumbra-keys calls now go through `adapter_id: subumbra-proxy`. The `litellm` adapter_id never appears in the audit, so this condition can never be satisfied.
  - **P9.2 disallowed key** (`verify.sh:764`): matrix derives `litellm_disallowed_key: cerebras_prod`. After Codex's `PROXY_ALLOWED_KEYS` expansion, `cerebras_prod` is in subumbra-proxy scope and is not denied. The harness expects 403 (scope denied) but gets 401 (provider auth failure — wrong key for provider). Scope denial via LiteLLM path no longer exists in the new architecture.
  
  **Requires redesign** of P9.1/P9.2 proof logic to target the sidecar-routing architecture. P9.3–P9.6 (which PASS) cover the equivalent proof for the sidecar path. **Status: Open — harness maintenance item for a future round**.

## 2026-04-19 — Round 42.2 verification note

- `council/approved/runtime-auth-reconciliation-v2.md` V1 static check 1 uses
  `grep -n 'api_key.*subumbra:' litellm/config.yaml`, which now false-positives
  on the explanatory header comment in [litellm/config.yaml](/home/eric/git/Subumbra/litellm/config.yaml#L4-L6) even when no active model entry still uses the
  `subumbra:` prefix. The round failure was not caused by this, but the approved
  plan should tighten that grep to active model lines only before the next reuse.

- **`scripts/council/preflight.sh` uses legacy `forge_error` key**: The preflight script attempted to parse `forge_error` from the UI response, but the UI was rebranded to `subumbra_keys_error`. **Status: Fixed 2026-04-14** (now parses `subumbra_keys_error`).

- **Legacy headers and comments in documentation**: `CLAUDE.md:172` contained `### Forge Key Service` and `docs/subumbra-install.md:204` contained `# Forge health`. **Status: Fixed 2026-04-14**.

- **`scripts/council/verify.sh` round-mapping for rebrand**: The round name `round-41-4-full-rebrand` is not enabled for P30-P36 test suites in `verify.sh`, causing the automated harness to skip validation of critical rebrand surfaces like the KV provider registry and custom tokens. Priority: Medium (Harness maintenance). Status: Open (Non-blocking).

## 2026-04-15 — Round 41 Real App Validation planning cleanup

- **Truth alignment deferred from implementation scope**: The approved Round 41 real-app-validation plan intentionally treated architecture/status doc alignment as cleanup rather than blocking implementation scope. `PROJECT_STATUS.md` and `CLAUDE.md` should be updated in a follow-up doc-maintenance pass to reflect the standalone-app / bolt-on validation direction and the post-Round-39 operating model. Affected files: `PROJECT_STATUS.md`, `CLAUDE.md`. Priority: Low.

## Round 41.6 — 2026-04-16 (post-verification harness fixes)

- **`scripts/council/preflight.sh` LiteLLM poll requires running container**: The preflight script unconditionally polls LiteLLM on port 4000. In Round 41 coexistence flows where LiteLLM is not bundled, this caused a 60s timeout failure in `clean-run.sh`. 

- **`.gitignore` rule `council/` ignores `scripts/council/`**: The top-level `council/` ignore rule in `.gitignore` is not anchored to the root (`/council/`), which causes git to ignore the `scripts/council/` directory as well. This makes harness maintenance harder to track. **Status: Open** (Non-blocking).

## Round 42.3 — 2026-04-19 (post-verification docs maintenance)

- **`docs/standalone-litellm.md` missing explicit api_base suffixes for Grok/Cerebras/OpenRouter**: The approved spec shifted standalone models to use `http://subumbra-proxy:8090/t/v1` for OpenAI, but did not note that LiteLLM natively enforces explicit version path suffixes for other specific providers as well. Tested models configuring `cerebras/` or `xai/` aliases returned 404 blockages when terminating at `/t/chat/completions`, and `openrouter/` generated HTML 404 responses. 
  - **Status: Fixed 2026-04-19**. Applied updates directly to `docs/standalone-litellm.md` post-verification to formally specify `/v1` for Cerebras/Grok and `/api/v1` for OpenRouter routes. This is doc-only and does not affect the proxy architecture proof semantics.
