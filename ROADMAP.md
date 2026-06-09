# Subumbra roadmap

This is the **operator- and contributor-facing** backlog: planned work, open ideas, and long-range possibilities. **Nothing here is a fixed sequence**—order shifts with real installs, incidents, and feedback. Items are grouped so similar work can be scheduled together when you pick the next round.

Current planning direction after `1.1.1-alpha`:

- **Core Subumbra hardening and product simplification** are the active priority:
  observability, policy lifecycle, management API, secure UI.
- Cloudflare is now a **completed optional capability** — see the appendix for
  the shipped `r72`/`r73` lifecycle work and deferred/watch items.
- Future Cloudflare work is **debug- or request-driven only** unless a new round
  explicitly reopens it.

---

## Major epics

_Large, multi-part arcs that span several sub-rounds. Each is an **epic**: one
parent objective with a definition of "done," implemented as merged sub-rounds
(`r10N-1`, `r10N-2`, …) and closed as a single named release. Each gets a
tracker at `council/epics/<epic>.md` (untracked) when it becomes active. The
sections below this one are the finer-grained per-area backlog that epics and
one-off rounds draw from._

- **Multi-user / single-org model** — user attribution, per-user grants, admin
  approval, opaque tokens. Introduces durable records: `org_id`, `principal_id`,
  `token_id`, `consumer_id`, `key_id`, `role_id`, `grant_id`, `session_id`,
  `approval_id`. Keep the app-facing contract to proxy URL + opaque token; put
  complexity in server-side metadata. Do **not** bake identity into
  `consumer_id` / `key_id` / URL paths / token strings. Starts with recon on the
  current consumer-token, session-lockdown, Janus, audit, and UI data models.
- **Structured control-plane → Go + libSQL/Turso-ready data layer** — converge
  on a schema-first control plane (durable records above), a narrow storage
  abstraction over today's SQLite/state files, then optional libSQL/Turso and a
  pilot Go service (identity/token/audit first). Staged, many rounds; keep
  Python while contracts move. Database-per-org as the default tenancy boundary.
  Evidence-only recon before any backend commitment.
- **Agent-blast-radius security hardening** *(HIGH)* — guardrails must be
  enforced and verified **outside the agent's blast radius**: out-of-band Worker
  integrity verification (so a compromised host cannot disable the check),
  runtime CF-token de-scoping (runtime needs no deploy authority), Janus-required
  defaults for irreversible actions, and explicit blast-radius assumptions in
  every endpoint template. Discrete unfixed findings that pair with this work are
  tracked privately in `council/SECURITY_FINDINGS.md` (untracked).
- **Operator command palette (⌘K)** — structured command-dispatch API (no shell
  passthrough), operator-mode session flag, SSE output. Stage 1 (verify / session
  / pause / revoke) needs no CF creds; Stage 2 (rotate / add-consumer / provision)
  is blocked on the management API trust boundary.
- **`.env` secrets-at-rest hardening** — `age`-encrypted `.env` with a
  tmpfs unlock lifecycle (laptop `$XDG_RUNTIME_DIR`, VPS dedicated tmpfs), plus
  CF-token scope minimization and expiry. Removes long-lived plaintext authority
  from the host disk.
- **Credential-abuse telemetry & canaries** — stolen consumer tokens are not
  provider secrets, so attempted use against Subumbra surfaces is a
  high-confidence detection signal. Canary tokens, source-context logging,
  bounded defensive friction. Evidence-only recon first; no deception/enforcement
  before threat modeling.
- **Session-bound git / repo workflow automation** — sessions carry repo+branch
  policy; a local helper enforces checkout/pull/push within policy with optional
  Janus gating on push/merge/deploy. Authorization stays in Subumbra; command
  execution stays in the local helper (never the Worker / secret path).

---

## Near-term priority candidates

_Work that tends to reduce operator time-to-diagnosis or closes obvious foot-guns._

### Observability, health, and logging

- **Cross-service `/health` contract** — one clear matrix (keys, proxy, worker, UI): field names, degraded semantics, where to curl from host vs Docker.
- **Bootstrap messaging** — when `.env.bootstrap` contains keys for providers **not** declared in `subumbra.yaml`, warn at start/end which material was ignored.
- **End-of-bootstrap summary** — print successful `key_id` list for copy/paste (operator ergonomics).
- **Silent failure logging (Worker)** — upstream `fetch` connect/DNS/TLS: rate-limited `console.warn` with host only (no path/query).
- **Silent failure logging (Worker)** — invalid JSON on `POST /proxy`: warn without logging sensitive body.
- **Proxy `fetch_record` errors** — split timeout vs HTTP status vs connect in logs (no response bodies).
- **Proxy auth-ping** — throttled `DEBUG` or transition logging for `get_worker_auth_status` failures (balance vs `/health` spam).
- **Keys `/stats` deny path** — log parity with `/keys` denials for audit correlation.
- **Keys `/health` vs corrupt `keys.json`** — product choice: `degraded` flag vs `503` vs status quo (healthcheck behavior).
- **UI dashboard** — `WARNING` in container logs when `subumbra_keys_error` is non-null on poll.
- **Log volume** — demote happy-path per-request `INFO` on keys/proxy (env-gated `DEBUG` / sampling); keep denials loud.
- **Vault decrypt errors** — log reason class, not raw exception text that may echo fingerprint-sensitive material.
- **Proxy streaming responses** — if mid-stream read/teardown errors lack operator-visible logs, add minimal `key_id`-only hooks (low priority; only if reproduced).

---

## Policy, intent, and abuse resistance

- **Three-level intent** (existence / initiator / content source) — align transport, `subumbra.yaml`, Worker enforcement, and docs (extends **R48** direction).
- **Model allowlist** — restrict which models a given key may use (`policy.allow.models` or equivalent).
- **Streaming/SSE response scanning** — only if explicitly scoped (currently deferred by design).
- **NONCE-STORE / WAL contention** — evidence-gated; reproduce under load before design changes (`PROJECT_STATUS` watch item).
- **Body size / buffering** — `G-MEDIUM-3` Worker full-body buffer limits; revisit if large-payload providers matter.
- **Optional directory scan** — warn if user-selected paths contain obvious unencrypted API key material.

---

## Bootstrap, wizard, and day-two operations

- **Import vs RAM-only bootstrap modes** — clearer compose entrypoints or flags so "import from mounted `.env`" vs "type keys only" is obvious; single env-ingest story (paths + inline secrets).
- **Post-bootstrap Docker / host** — whether post-bootstrap steps should run inside a container when Docker touches the host (design).
- **Wizard copy** — de-confuse "Step 2 of 4" import path text; optional numerical selection for keys; sensible defaults (e.g. hide Cloudflare Account ID where safe).
- **No-restart / low-friction verbs** — e.g. `--add-provider`, `--add-key`, `--remove-key`, `--remove-provider`, `--scope-to-apps` (needs KV/manifest authority design, not slogans).
- **`--delete-key` / lifecycle** — after registry + KV story is pinned.
- **Hot reload** — optional reload of `keys.json` (or equivalent) without full stack restart (risk vs simplicity).
- **Wrangler / CF CLI collision** — detect existing installs/credentials to avoid overwrite surprises; if `r72` moves more Cloudflare lifecycle to direct API + runtime-managed state, revisit whether this remains relevant.
- **`subumbra-clean-run` / proof harness** — ensure KV binding/content parity where proofs expect registry material.
- **Bootstrap dead-code / tombstones** — remove or wire unreachable stubs in `subumbra-bootstrap.py`; optional removal of legacy callback artifacts pending three-way recon (`litellm/custom_callbacks.py` policy).
- **`scripts/subumbra-env-ingest.py`** — signed-template defaults integration.
- **`fresh-start.sh` / volume naming** — any remaining `keys_data` vs `subumbra_keys_data` doc or script drift (grep periodically).

---

## UI, management API, and operator dashboards

_The live Flask app is **read-only** today (`GET` health, dashboard, status); write routes from the handoff JS are **not** implemented._

### Prerequisite (no plaintext relay)

- **Hardened management API first** — browser-visible key material and rotation must go through the **Worker management surface**, not ad-hoc Flask POSTs that make `subumbra-ui` a second plaintext authority.
- **Adapter scope** — today the UI adapter is metadata-only in bootstrap; expanding to `can_write_keys` (or equivalent) is an explicit trust-boundary change, not a template swap.

### Secure dashboard shell (before or in parallel with writes)

- **Mandatory edge or container auth** — for production-style exposure: either **Cloudflare Access** in front of the hostname (preferred with Tunnel; leave `UI_USERNAME` / `UI_PASSWORD` unset) or **HTTP Basic** on the container. Pure open dashboard on `0.0.0.0` remains out of scope. Local `127.0.0.1:6563` dev may stay relaxed only when documented as such. Default compose bind stays **`127.0.0.1:6563`** (`docker-compose.yml`).
- **CSRF** — token or double-submit cookie pattern **before** any mutating `POST`/`DELETE` from the browser (Basic Auth alone is insufficient for CSRF safety).
- **Rate limits** — auth and expensive read endpoints.

### Key lifecycle in the UI (after API + CSRF)

- **Pause / resume / block** — surface state; enforce at **subumbra-keys** (no ciphertext) and **Worker** (defence in depth).
- **Rotate** — secure flow using management API + envelope semantics (`/api/rotate-key` route must be implemented against the real API, not Flask stubs).
- **Add key** — wire handoff assets to **management** endpoints only after paste/redaction UX is reviewed.
- **Delete** — confirmation UX + `DELETE` semantics on keys store + registry/KV consistency.
- **Rate limits and time windows** — per-key policy; clarify Worker vs keys enforcement in operator copy.
- **Fix handoff implementation bugs** — e.g. secure-paste path in `ui/templates/dashboard.js` (paste handler vs `clipboardData`) before trusting the file as implementation truth.

### Dashboard UX (read path and filters)

- **Management mutations** — until API lands, keep documenting CLI/bootstrap paths; then promote pause / resume / rotate / delete in UI.
- **Audit and stats UX** — filters (date, model, key, provider, verdict, adapter); `GET /audit?key_id=&verdict=` already implemented on `subumbra-keys`; remaining gap is UI wiring for those filters.
- **Audit retention** — export / archival path (`AUDIT-RETENTION` limitation today is local-only cap).
- **Adapter allowlist UX** — per-app adapter names with clearer UI copy.
- **Per-app health** — when apps expose health, show linkage in dashboard (best-effort).
- **Scripted pause/resume** — owner hooks (time, billing) tied to pause semantics.
- **Basic Auth rate-limit** — reliable 429 under multi-worker Gunicorn if operators need strict semantics.

---

## Documentation and information architecture

- **`docs/README.md`** — audience map (operator / integrator / developer) and canonical "start here" links.
- **Comparison atlas (`docs/comparisons/`)** — seeded in `r91-doc-updaes`; refresh primary-source notes and matrix cells before public launch or release promotion.
- **`ui/templates/README.md`** — refresh or archive; align with read-only UI + management-token story.
- **Redirects** — thin `docs/standalone-*.md` stubs: keep, merge, or remove after link grep.
- **`README.md` nuance** — clarify shared Worker deployment vs per-app adapter identity where readers confuse them.
- **`PROJECT_STATUS.md`** — optional density trim; keep as history + high-level status, not a second backlog.
- **`Website/Pages.md`** — expand or trim placeholder.
- **Link automation** — optional `scripts/check-links.sh` + CI (repo-relative links, no broken anchors).
- **Troubleshooting / Mermaid** — dedicated troubleshooting doc and diagrams when worth the maintenance.
- **SEC-2 / SEC-4 doc nits** — proxy `KEY_ID_RE` vs validator wording; container env exposure completeness.

---

## Networking, topology, and "where Subumbra runs"

- **Split topology** — apps on VPS, Subumbra on laptop (or reverse), Termux phones, Oracle VPS tests—documented patterns with realistic security notes.
- **Alternatives** — Tailscale, Kubernetes, Docker Swarm as *integration recipes*, not supported products, unless adopted later.
- **`.env` on host** — reduce accidental `git push` exposure (architecture tension with compose bind mounts; needs a designed story, not a one-line fix).
- **Rename / obfuscate** — service names, ports, deleting `bootstrap.sh` after first run: security-through-obscurity tradeoffs vs operational clarity.
- **Endpoint / authority modularization (later)** — long-term direction: make Cloudflare one supported authority/exposure backend among several, but only after the current Cloudflare lifecycle is finished and the core trust boundary is better stabilized. This is a separate architecture arc, not part of `r72`.

---

## Ecosystem: apps and integration breadth

- **Endpoint breadth** — beyond chat-completions-shaped paths; may need richer app↔manifest communication or custom bootstrap flows.
- **Decouple LiteLLM file edits** — prefer sidecar `/t` contract over repo-patching LiteLLM where possible.
- **Non-LLM providers** — Slack, Stripe, GitHub, SendGrid-style patterns as recipes or first-class examples when ready.
- **OAuth and rotation keys** — broker OAuth client secrets and rotation material with the same split-trust model (research).
- **npm publish brokering (r92, closed)** — `type: npm_token` V3 envelope, Worker tarball inspection, package identity enforcement, scope allowlist, `--rotate-npm-token`.
- **npm professional workflow (r93, active)** — CI/CD path (`NODE_AUTH_TOKEN` / GitHub Actions), `allow.npm_operations` to block deprecate/unpublish/dist-tag, `deny.max_tarball_bytes` size gate, GitHub Packages documentation.
- **Token storage** — npm/GitHub-style PAT handling patterns beyond r92/r93 scope (multi-hop registries, read-path install brokering, regex credential patterns).
- **Import from existing `.env` / `config.yaml`** — guided migration that encrypts, updates app configs, shreds source file; far future optional file-watcher sync (high complexity, multi-tenant caution).
- **App validation queue** (examples, not commitments): AnythingLLM, LibreChat, Dify, Chatwoot, Langfuse, Documenso, Plausible CE, Swetrix, Directus, Bolt.diy, Trigger.dev, Homepage, DronaHQ, Retool self-hosted, EmailEngine — each needs its own proof doc when prioritized.

---

## Harness, CI, and contributor process

- **`vps-proof-run.sh`** — default `--build` when container sources change; env forwarding docs.
- **`VERIFY_MODE`** — remove or wire into `verify.sh` (no orphan exports).
- **Round hooks** — avoid probe scripts that mutate production stack state during verify.
- **Half-open / breaker proofs** — optional scripted clean PASS if still flaky (`verify-round.sh` scenarios).
- **Three-LLM verification** — restore verifier files discipline when practical.
- **`COUNCIL.md` vs hooks** — reconcile "no compose" language with read-only `docker compose exec` usage.

---

## Research and long bets

- **Android / Google API key escalation** — public-format keys gaining LLM scope silently; whether Subumbra mediation fits mobile distribution models.
- **Redis / shared counters** — multi-instance rate limits or state (explicitly deferred in past rounds).
- **Hardware attestation / TEE binding** — intent tied to platform attestation (vision).
- **Rename `bootstrap.sh` → `install.sh`** — only with compatibility shim and broad doc churn acceptance (not a silent sub-bullet).

---

## Accepted or watch-only (see `PROJECT_STATUS.md`)

These are **tracked as limitations or watch items**, not a promise to "fix soon": Python best-effort memory scrubbing; internal-network `/health` metadata exposure; CF Access strip at Worker edge only; `npm audit` on dev tooling; local-only audit retention unless export is built; `NONCE-STORE` without reproduction.

---

## Completed (post-release)

_Items completed after the 1.1.1-alpha release. Add entries here as work ships._

---

*Last updated: 2026-06-09 — added the **Major epics** section (strategic arcs
migrated out of `council/cleanup.md`); security blind-spot findings moved to the
untracked `council/SECURITY_FINDINGS.md`. Core hardening and product
simplification remain the active direction.*

---

## Appendix: Cloudflare integration (completed, planned, and deferred)

_Cloudflare is a completed optional deployment path. Active development here is
debug- or request-driven only. The items below are tracked for reference._

### Completed in r72-cloudflare-runtime-ux

- Wizard and `.env.bootstrap` ingestion of `TUNNEL_TOKEN`,
  `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`
- Bootstrap writes runtime Cloudflare credentials to `.env` automatically
- Day-2 commands `./bootstrap.sh --update-tunnel` and
  `./bootstrap.sh --update-access`
- `docs/cloudflare-tunnel-access.md` — BYOC operator guide
- `docs/subumbra-install.md` updated to remove pre-bootstrap copy workaround

### Completed in r73-cloudflare-autoprovision

- API-driven Tunnel provisioning (create tunnel + DNS CNAME) behind explicit
  wizard opt-in
- API-driven CF Access provisioning (app + policy + service token) behind opt-in
- `data/cf-resources.json` idempotency manifest
- `./bootstrap.sh --nuke-cloudflare` teardown command
- Expanded CF API token scope for auto-provisioning, with explicit operator
  opt-in
- New `cf-api-provision` verification harness lane (must be defined before
  live Cloudflare lifecycle proof runs

### Deferred indefinitely (not scheduled)

- Workers VPC (multi-server topology)
- Cloudflare Actors / Durable Objects platform notes (research watch)
- Workers Observability cost tracking and log-tail playbooks
- Analytics Engine / real-time logs
- CF-native auth/admin rate limiting (R71 follow-up; reopen if needed)
- Registry layout (split monolithic KV vs per-provider namespaces)
- Per-provider bootstrap add

### Accepted limitations (not re-litigated)

- **CRITICAL-3**: CF Access header strip applies at the Worker edge only
  (inside the CF network); Worker does not strip CF Access headers before
  upstream provider calls. Accepted architectural constraint.
