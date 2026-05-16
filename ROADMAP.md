# Subumbra roadmap

This is the **operator- and contributor-facing** backlog: planned work, open ideas, and long-range possibilities. **Nothing here is a fixed sequence**—order shifts with real installs, incidents, and feedback. Items are grouped so similar work can be scheduled together when you pick the next round.

---

## Near-term priority candidates

_Work that tends to reduce operator time-to-diagnosis or closes obvious foot-guns._

### Cloudflare Tunnel and Cloudflare Access (higher priority)

_Operator goal: expose the dashboard (and optionally other services) through Cloudflare without shipping the UI on `0.0.0.0`, with strong edge auth. Full "one-click" Access policy creation is account-specific; plan for **repeatable docs first**, then **optional helpers** where the API is stable._

- **End-to-end guide + checklist** — create or extend an operator-facing doc (e.g. `docs/subumbra-install.md` or a dedicated `docs/cloudflare-tunnel-access.md`): Cloudflare Zero Trust → Tunnel → route to **`http://subumbra-ui:8080`** on the Docker **internal** network (not host `127.0.0.1:6563` — see `docs/subumbra-developer.md` tunnel routing note); `TUNNEL_TOKEN` in `.env`; `docker compose --profile tunnel up -d`; verify UI and Worker paths independently.
- **CF Access for the UI** — document Access application (hostname), allowed IdPs, and **leave `UI_USERNAME` / `UI_PASSWORD` unset** so the container does not double-auth; align with `README.md` / `docs/operator-guide.md` matrix rows.
- **CF Access in front of the Worker (optional)** — when used, operators need `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` on **proxy** (and probe) so service tokens are sent; cross-link CRITICAL-3 behavior (`PROJECT_STATUS.md`) so misconfiguration is diagnosable.
- **Automate what is safe to automate** — optional script or `make`/compose target that: validates `TUNNEL_TOKEN` / Access-related env vars are set; prints a generated **public hostname** checklist; emits **config snippets** (ingress rules, env block) for copy-paste. Reserve **API-driven** Access app or policy creation for a later round only if you accept maintaining Cloudflare account/zone assumptions.
- **Proof / smoke path** — add a short "Tunnel + Access verification" subsection to testing docs: expected headers, 401 vs 200 on `/api/status`, and a note to tail `cloudflared` logs.

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
- **Wrangler / CF CLI collision** — detect existing installs/credentials to avoid overwrite surprises.
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
- **`ui/templates/README.md`** — refresh or archive; align with read-only UI + management-token story.
- **Redirects** — thin `docs/standalone-*.md` stubs: keep, merge, or remove after link grep.
- **`README.md` nuance** — clarify shared Worker deployment vs per-app adapter identity where readers confuse them.
- **`PROJECT_STATUS.md`** — optional density trim; keep as history + high-level status, not a second backlog.
- **`Website/Pages.md`** — expand or trim placeholder.
- **Link automation** — optional `scripts/check-links.sh` + CI (repo-relative links, no broken anchors).
- **Troubleshooting / Mermaid** — dedicated troubleshooting doc and diagrams when worth the maintenance.
- **SEC-2 / SEC-4 doc nits** — proxy `KEY_ID_RE` vs validator wording; container env exposure completeness.

---

## Cloudflare, registry, and cost visibility

- **Registry layout** — split monolithic KV vs per-provider namespaces for add/delete/telemetry (large design + migration).
- **Per-provider bootstrap add** — add single provider using same trust model as initial bootstrap.
- **Workers Observability** — document when to enable logs/traces and cost links ([Workers logs pricing](https://developers.cloudflare.com/workers/observability/logs/workers-logs/), [Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/)).
- **Analytics Engine** — optional metrics sink for request volumes / errors.
- **Real-time logs** — operator playbooks using CF real-time log tail.
- **Future billing hints** — correlate log volume to rough cost alerts (vision-level).
- **Workers VPC** — multi-server topology: one Subumbra footprint, many app hosts ([Workers VPC](https://developers.cloudflare.com/workers-vpc/)).
- **Durable Objects platform notes** — Data Studio availability; watch **Cloudflare Actors** (`cloudflare/actors`) for roadmap fit.

---

## Networking, topology, and "where Subumbra runs"

- **Split topology** — apps on VPS, Subumbra on laptop (or reverse), Termux phones, Oracle VPS tests—documented patterns with realistic security notes.
- **Alternatives** — Tailscale, Kubernetes, Docker Swarm as *integration recipes*, not supported products, unless adopted later.
- **`.env` on host** — reduce accidental `git push` exposure (architecture tension with compose bind mounts; needs a designed story, not a one-line fix).
- **Rename / obfuscate** — service names, ports, deleting `bootstrap.sh` after first run: security-through-obscurity tradeoffs vs operational clarity.

---

## Ecosystem: apps and integration breadth

- **Endpoint breadth** — beyond chat-completions-shaped paths; may need richer app↔manifest communication or custom bootstrap flows.
- **Decouple LiteLLM file edits** — prefer sidecar `/t` contract over repo-patching LiteLLM where possible.
- **Non-LLM providers** — Slack, Stripe, GitHub, SendGrid-style patterns as recipes or first-class examples when ready.
- **OAuth and rotation keys** — broker OAuth client secrets and rotation material with the same split-trust model (research).
- **Token storage** — npm/GitHub-style PAT handling patterns.
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

_Items completed after the 1.0.0-alpha release. Add entries here as work ships._

---

*Last updated: 2026-05-15 — cleaned for 1.0.0-alpha release; removed all pre-release completed items; added post-release completed section at bottom.*
