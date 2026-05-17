# Project Memory

*Shared fresh-session memory for Subumbra. This is not a round log. It is a
small set of durable project truths that fresh chats are likely to miss.*

Update this file only when a closed round changes something a new session would
otherwise misunderstand.

---

## 1. Product Identity

- Subumbra is a **policy-bound secret proxy**: it brokers access to provider API keys without ever exposing them in plaintext to apps. Not a LiteLLM plugin.
- LiteLLM is a proven app-owned example, not the product boundary.
- The core product shape is:
  - `subumbra-keys` for encrypted record storage and limited metadata access
  - Cloudflare Worker for decrypt/proxy enforcement
  - adapters/sidecars for app-facing integration

---

## 2. Core Security Invariants

These should be treated as hard constraints unless a round explicitly reopens
them.

- Split-decrypt boundary must remain intact.
- No durable host-local decrypt power on operator-controlled systems.
- Worker-side hostname/provider validation must remain fail-closed.
- Secrets, tokens, decrypted material, auth headers, and raw sensitive payloads
  must not be logged.
- Operator-visible logging should stay minimal and diagnostic, not expansive.

---

## 3. Current Architecture Reality

- The canonical core API is `POST /proxy`.
- `subumbra-keys` is Docker-internal only and is not the public app-facing API.
- Provider secrets become usable plaintext only inside the Cloudflare Worker
  runtime, currently within the SQLite-backed `SubumbraVault` Durable Object isolate.
- The transparent sidecar route (`subumbra-proxy` / `/t/<key_id>/...`) is the
  current reference integration path.
- Transparent-sidecar callers can now supply optional request-side intent
  metadata via `X-Subumbra-Intent-Source`,
  `X-Subumbra-Intent-Initiators`, and
  `X-Subumbra-Intent-Content-Sources`; the proxy converts those into the
  canonical Worker `/proxy` `intent` field and strips them before upstream
  provider requests.
- App-facing transparent traffic now authenticates with adapter token in
  `Authorization` or `X-API-Key`; legacy raw-`key_id` transparent auth is gone.
- The legacy sidecar `/v1/request` surface is no longer a supported app-facing
  contract.
- Live provider validation has moved away from a purely bundled model; local
  repo metadata can still remain as operator/bootstrap seed material.
- **Signed provider templates (r51):** Bootstrap ships a release-signed catalog
  under `/app/templates/` (`catalog.json` + `catalog.sig` + per-provider
  templates). A manifest key may use `"template": "<name>"` instead of an inline
  `policy` object; bootstrap verifies signature and SHA-256 before expanding into policy.
- **Internal State Authority (R48-3)**: Day-2 management commands (`--push-registry`, `--provision`, `--rotate`) source all authority from internal "Fat Records" in `keys.json` instead of external manifests. Re-bootstrap is the only supported path for changing embedded authority fields.
- **Management Authority (R50)**: Worker management mutations now use a distinct `SUBUMBRA_MANAGEMENT_TOKEN`; browser/UI routes remain read-only, while pause/unpause writes happen through Worker `/manage/*` endpoints and durable audit rows in the existing vault DO.
- **R65 (2026-05-13):** The repo ships **tracked** `subumbra.minimal.yaml` and `subumbra.example.yaml`. **Minimal** is one OpenAI key via `template` only (smallest valid manifest). **Example** lists every signed catalog template plus one inline “gold” policy. The operator working file **`subumbra.yaml` is gitignored** — copy a template (`cp subumbra.minimal.yaml subumbra.yaml` or `cp subumbra.example.yaml subumbra.yaml`), edit, then bootstrap; README and `docs/subumbra-install.md` document this. Example `curl` tables for `/t` moved to `docs/integration-recipes.md` (replacing removed `docs/provider-catalog.md`).
- **r67 (2026-05-13):** Bootstrap now accepts **`subumbra.yaml`** (preferred) or `subumbra.json` — both gitignored; `subumbra.minimal.yaml` is the new tracked YAML starter. `bootstrap.sh` auto-discovers the manifest and mounts it extension-free at `/app/manifest`. Operators may place local `./templates/<name>.yaml` files that take precedence over the signed built-in catalog (not signature-verified; operator-owned). Bootstrap image must be rebuilt (`./bootstrap.sh --upgrade`) after pulling this round to pick up `pyyaml`.
- **r68 (2026-05-13):** Template-backed policy normalization failures now name
  the originating template in bootstrap errors, and the docs/starter wording
  was aligned to the tracked multi-provider `subumbra.minimal.yaml`.
- **r69 (2026-05-13):** Built-in provider and adapter templates were migrated to
  YAML under the signed catalog, `./bootstrap.sh --status` was added as a
  read-only manifest-vs-record drift check, and `--add-adapter` /
  `--revoke-adapter` gained bounded canonical `adapters: [...]` manifest sync.
- **r71 (2026-05-17):** Worker-generated JSON/auth/error responses now carry
  hardening headers (`Cache-Control: no-store`, `Pragma: no-cache`,
  `X-Content-Type-Options: nosniff`, `Cross-Origin-Resource-Policy:
  same-origin`, and HSTS). Non-proxy auth/admin surfaces (`/auth-ping`,
  `/setup/keygen`, `/internal/*`, `/manage/key/*`) now use Worker-side
  per-IP throttling with `429 rate_limit_exceeded_auth`, and built-in signed
  provider templates now ship active default `velocity` controls.

---

## 4. Deployment Reality

- The clean supported install path is still **terminal-first**.
- Bootstrap currently runs through `docker compose --profile bootstrap run ...`
  and performs Cloudflare-side provisioning work.
- Full bootstrap now uses a one-shot Cloudflare `/setup/keygen` flow so the RSA
  private key is generated and retained in the vault DO rather than on the VPS.
- Bootstrap now self-heals a stale saved Cloudflare KV namespace ID through the
  existing account list/title-scan path instead of requiring manual
  `kv-config.json` deletion as the normal recovery step.
- Bootstrap now treats app identity as a per-key input: direct-provider
  automation slots require matching `*_ADAPTERS` values, non-empty values bind
  the key to named app adapters, and blank `*_ADAPTERS=` is an explicit
  compatibility/simple-mode choice that binds that key to `subumbra-proxy`.
- Bootstrap now accepts optional `SUBUMBRA_POLICY_PATH` input for policy-backed
  bootstrap ingestion, while built-in direct-provider secrets retain a narrow
  in-memory auto-compat fallback when no explicit policy entry is supplied.
- **R62 (CLOSED 2026-05-12):** Interactive bootstrap is again a first-class **manifest-era** TTY path when `.env.bootstrap` is absent: `run_interactive_wizard` loads `subumbra.yaml` (or `subumbra.json`), prompts for CF + per-`secret_ref` secrets into RAM (`_WIZARD_SECRETS`), and returns the same credential bundle shape as automation; `_resolve_manifest_secret` consults that cache before `os.environ`.
- `subumbra-probe` is an optional diagnostic profile now; baseline bootstrap and
  runtime bring-up do not require probe provisioning.
- `bootstrap.sh` now runs on the host, mounts repo-local `.env` into the
  bootstrap container, and shreds `.env.bootstrap` after a successful run.
- The repo-local `.env` retains `SUBUMBRA_SETUP_TOKEN` as an operator reference
  value after bootstrap completes. The corresponding transient Cloudflare Worker
  secret is deleted by bootstrap before it exits; `.env`'s `SUBUMBRA_SETUP_TOKEN`
  value is therefore stale and does not authenticate to Cloudflare after bootstrap.
- R45-3 moves runtime registry state off the monolithic
  `subumbra_registry_v1` blob and onto structured KV keys
  (`policy:<id>`, `key:<id>`, `registry_version`).
- R45-3 V3 records bind ciphertext with
  `subumbra:v3:<key_id>:<policy_hash>`, where `policy_hash` is computed from
  the baseline-bound policy object rather than the full policy document.
- Worker-side V2 records remain readable only through the R45-4 grace window;
  `--rotate-policy` performs Worker-mediated re-encryption without host
  plaintext recovery.
- After R46, `--rotate` is supported only for existing V3 records and
  `--rotate-policy` refuses V2 inputs locally; V2 migration now means full
  re-bootstrap rather than in-place upgrade repair.
- R47 completed the Alpha mixed-vault contract: shared vault remains the
  default, `UNIQUE_KEY_<key_id>=true/false` can opt individual keys into
  dedicated `vault-<key_id>` instances, runtime records now carry
  `vault_instance`, and bootstrap recovery can repair one missing key with
  `--provision <key_id>` without rewriting successful records.
- R48 activates optional request-side `intent.trust` guardrails for
  `allowed_initiators` and `allowed_content_sources`, but missing `intent`
  still remains accepted by default. Response-side `response.deny_patterns`
  enforcement is still deferred beyond R48.
- Automation-mode bootstrap is manifest-driven: author `subumbra.yaml` (or `subumbra.json`) and
  provide only the referenced secrets in `.env.bootstrap`. The legacy
  `IMPORT_PATH_<n>` / `IMPORT_APP_LABEL_<n>` wizard-era input slots are no
  longer used in the primary path (removed in R48-2).
- Full bootstrap now writes `/app/data/system-integrity.json`, and
  `scripts/subumbra-verify-deploy` compares that recorded deploy hash against
  the current live Cloudflare Worker content.
- **Fat Records (R48-3)**: `keys.json` stores the full `policy` document, `adapters` list, and routing metadata (`auth_header`, `auth_prefix`, `template_name`) per key. All management commands verify that the embedded policy matches the `policy_hash` before publication. (R61 removed legacy `bootstrap-checkpoint.json` plaintext resume; repair uses `subumbra.yaml` / `subumbra.json` + host env + on-disk `public_key*.pem` only.)
- **Paused-State Durability (R50)**: live `key:<id>` structured KV entries may now carry `paused: true`, Worker runtime denies those keys with `key_paused`, and `--push-registry` must preserve that live paused flag instead of clearing it from rebuilt KV entries.
- **Read-Only Policy UI (R52)**: the dashboard read path now surfaces per-key V3 metadata from `subumbra-keys` through `/api/status`, including `policy_id`, `policy_hash`, `capability_class`, auth metadata, target host/base path, and allowlist adapter/method/path data, while still excluding ciphertext, wrapped DEKs, fingerprints, and raw policy blobs.
- **Dashboard Freshness (R52)**: the promoted UI now serves the local dashboard bundle at `/`, uses heartbeat-only `/api/events` SSE for connection visibility, and keeps a 30-second `/api/status` polling fallback so operators still get updates even though this round does not implement server-side event fan-out. **R63 (CLOSED 2026-05-12):** per-key `request_count` and `last_access` on the dashboard come from **`subumbra-keys` SQLite `audit_events`** (via `/stats` and `/keys`), not from per-worker in-memory counters, so multi-worker Gunicorn in `subumbra-keys` no longer causes divergent stats between polls. **R64 (CLOSED 2026-05-13):** `GET /audit` supports optional **`key_id`** and **`verdict`** query parameters (bounded `LIMIT 100`); the dashboard worker card labels **`worker_auth`** (`ok` / `stale` / `token_mismatch` / `unreachable`) instead of implying raw TCP reachability; `scripts/fresh-start.sh` names the real Compose audit volume **`subumbra_subumbra_audit_data`** in teardown messaging and the manual volume-removal loop; `subumbra-keys` Gunicorn runs with **`--no-control-socket`** to suppress control-socket noise on no-home users.
- **Propagation Ceiling (R50)**: management pause/unpause proofs should allow for up to 90 seconds of worst-case Cloudflare KV propagation before declaring failure on a stale read path.
- The project expects a **project-local `.env` in the repo root**.
- Fresh installs should use a dedicated checkout path such as `/opt/subumbra`
  rather than sharing a directory with unrelated services.
- Portainer/Dockge may be acceptable for day-2 management later, but they are
  not the primary reference install path.
- **Operator cadence (R58):** Polling, healthcheck, SSE heartbeat, proxy worker-auth cache TTL, and related timeouts are summarized in `docs/operator-guide.md` ("Heartbeat, polling, and health cadence") with file/line pointers; SEC-4 documents Compose-injected env visibility inside containers.
- **Log rotation (R58):** `subumbra-proxy`, `subumbra-probe`, and `bootstrap` use Docker `json-file` logging with `max-size: 50m` and `max-file: 3` (same pattern as other stack services).
- **Volume migration naming (R58):** Operator docs use host/project volume name `subumbra_keys_data` for `docker volume create` / migration examples; in-container mount remains `keys_data:/app/data` per Compose.
- **Legacy callback artifact:** `litellm/custom_callbacks.py` was removed in R58; callback-era behavior is reference-only in prose docs.
- **Harness existing-stack (R60):** `scripts/council/vps-proof-run.sh` may auto-export `SUBUMBRA_PROXY_HOST_PORT` from `docker compose port subumbra-proxy 8090` after `docker compose up` when the value is unset; operators may pass **`--deploy-worker`** with a non-empty **`CF_API_TOKEN`** for an optional Wrangler deploy on that path (fail-closed if the flag is set without a token). **`scripts/council/verify.sh`** P9.5 records **SKIP** (non-failing) when **`SUBUMBRA_UI_CONTAINER`** is set, instead of host-curling the UI port. Round hooks that call `verify.sh` for the same round should set **`VERIFY_SKIP_ROUND_HOOK=1`** to avoid re-entrant `verify-round.sh` invocation.
- **R71 deploy-worker proof note (2026-05-17):** when `scripts/council/vps-proof-run.sh --deploy-worker`
  runs a temporary Wrangler deploy from the bootstrap image, it must append the
  live KV namespace binding from `/app/data/kv-config.json` into the copied
  `wrangler.toml` before deploy. Otherwise Worker smoke proofs can fail with a
  missing `PROVIDER_REGISTRY_KV` binding even when the product code is correct.
- **UI Gunicorn worker model (R59):** `subumbra-ui` runs Gunicorn with **`--workers 1 --threads 4`** so in-process Basic Auth failure counting is not split across processes. SSE `/api/events` still holds a worker thread between heartbeats; Docker-bridge `remote_addr` semantics for host-published UI are documented in `docs/operator-guide.md` (R59 subsection).
- **R61 (2026-05-12, CLOSED):** Bootstrap no longer writes plaintext `bootstrap-checkpoint.json`; manifest `secret_ref` resolves only in the encrypt phase; phase-1 `/setup/keygen` runs per vault instance before encryption; `run_provision_key` uses host env + manifest only; `_sync_host_env_file` fails closed without `/app/host-env`. VPS `fresh-install` proof run `codex-vps-20260512T174950Z` (see `PROJECT_STATUS.md`).
- **round-cleanup (CLOSED 2026-05-13):** Bootstrap uses a **pre-mutation gate** on `kv-config.json` before runtime token generation / `deploy_worker()`; successful full bootstrap **zeros `SUBUMBRA_SETUP_TOKEN` in host `.env`** after the CF secret is deleted; the Worker exposes **`HEAD /health`** (200, no body); the UI sets **CSP** and default **`Cache-Control: no-store`** (SSE keeps `no-cache`); **`subumbra-verify-deploy`** infers **`CF_WORKER_NAME` from `CF_WORKER_URL`** when unset. Proof runs `codex-vps-20260513T143105Z`, `Gemini-vps-20260513T142722Z` (SHA `05083d1`).

---

## 5. Timekeeping And Ops Defaults

- Use UTC on servers, logs, and scheduled operations.
- Localized time belongs in presentation/UI only, not in stored operational
  timestamps.
- Verification and deployment guidance should prefer reproducible, explicit
  steps over convenience magic.

---

## 6. Current Documentation Shape

These docs are the main fresh-session anchors:

- `README.md` for current install/use flow
- `CLAUDE.md` for architecture overview
- `docs/vps-deployment.md` for generic Ubuntu 24.04 VPS baseline
- `docs/subumbra-install.md` for the clean reference install path
- `docs/operator-guide.md` for live registry / operational flows
- `docs/subumbra-testing.md` for harness and proof policy

---

## 7. Known Recurring Misreads

- Do not collapse “Subumbra” back into “the LiteLLM project.”
- Do not assume any shorthand line in `PROJECT_STATUS.md` overrides a more
  specific approved roadmap or round-approved plan.
- Do not treat optional future deployment modes as current reference install
  paths.
- Do not assume coexistence with pre-existing host services is already solved
  just because the clean install path works.
- Do not silently turn deferred mechanics into settled design decisions.

---

## 8. When To Update This File

Update only when a closed round changes one of these:

- product identity
- security invariants
- deployment/install reality
- operational defaults
- recurring gotchas that repeatedly mislead fresh sessions

If a round adds only local implementation detail, do not add it here.
