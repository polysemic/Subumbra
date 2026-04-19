# Round 42.3 Synthesis — App-Owned Integrations

Author: Claude
Date: 2026-04-19
Round: `round-42-3-app-owned-integrations`

Documents read:
- All three proposals and alignment passes
- All three first and second reviews
- All three investigations

---

## 1. Consensus Items

All three investigations and reviews agree without qualification on the following:

### C1 — Full LiteLLM removal is the correct direction
Bundled LiteLLM should be removed from `docker-compose.yml` entirely (not profile-renamed,
not demoted — removed). Codex explicitly rejected Gemini's "rename profile to `example-litellm`"
twice. Gemini's investigation dropped its own demotion proposal after investigating. Claude's
position was full removal from proposal-2 onward.

### C2 — The `/t` route is a shared proxy identity model, not per-app identity
All three investigations confirmed independently: the transparent route at
`subumbra-proxy/app.py:266-315` injects `SUBUMBRA_ACCESS_TOKEN` (the proxy's own token) into
every Worker-bound request, regardless of which app issued the `key_id`. The Worker at
`worker.js:449-454` authenticates the proxy, not the originating app. This means:
- custom adapter tokens in bootstrap have no effect on the `/t` runtime path
- apps using `/t` are authenticated as `subumbra-proxy` at the Worker level
- this is not a limitation to fix in 42.3; it is the current and correct universal model

Gemini's investigation conclusion matches: "Sidecar path is anonymous to the key store;
shared identity is the current reality."

### C3 — Custom application loop in bootstrap wizard is rejected for 42.3
All three LLMs reject Gemini's original §3.3 (interactive custom app loop) as a required
42.3 change. Evidence: the runtime doesn't use per-app bootstrap identities on the `/t` path.
Adding the wizard prompt without changing the runtime would create a false mental model.
Gemini's investigation concedes: "Remove the 'Custom Application Loop' from the 42.3 bootstrap
changes."

### C4 — Operator truth cleanup scope is wider than `docs/subumbra-install.md`
All three investigations confirm the same files need updating. Codex's investigation Q3
provides the most complete list:
- `README.md` (lines 311-349, 374-419, 481-492)
- `docs/subumbra-install.md` (lines 118-220)
- `docs/subumbra-testing.md` (lines 24-48)
- `docs/adapter-contract.md` (lines 212-220)
- `post-bootstrap.sh` (lines 19-80)
- `bootstrap/subumbra-bootstrap.py` summary text (lines 1818-1840)

### C5 — Worker-auth visibility requires a probe, not body inspection alone
All three agree that `/auth-ping` on the Worker (or equivalent explicit probe) is necessary
for proactive stale-token detection. All three agree the sidecar `/health` should be updated
with a tri-state `worker_auth` field. Gemini's investigation makes the key architectural
point: "The UI could attempt to use its own token to probe the Worker, but that would only
prove the UI's token is valid, not the Gateway's." — confirming the sidecar must own the
probe.

### C6 — "Subumbra Gateway" rename is out of scope
All three LLMs agree the service name stays `subumbra-proxy`; "gateway" is descriptive prose
in documentation only. Gemini's investigation concedes this as a "human policy decision" and
aligns with Claude and Codex.

### C7 — Per-app identity model is future-round work
If distinct Worker-level identities for N8N, LibreChat, LiteLLM, etc. are ever wanted, that
requires a runtime contract change (separate token per app on `/t` route or separate endpoints).
It is not a 42.3 deliverable.

---

## 2. Disagreements and Resolutions

### D1 — Change D: Is body inspection reliable?

**Codex position (both reviews and investigation):** Body inspection for `{"error":"unauthorized"}`
is "not a technically strong boundary." The Worker forwards provider responses unchanged
(`worker.js:379-392`), so the proxy "does not currently have any explicit marker proving that
a given `401` is Worker-originated."

**Gemini position (review-1):** Reliable, because the Worker's auth logic is the only path
returning that specific string with a 401.

**Claude position (investigation):** Gemini is right about current reliability; Codex's
concern is valid only as a future-brittleness note.

**Evidence that resolves this:**

The Worker's `jsonError("unauthorized", 401)` at `worker.js:454` produces `{"error":"unauthorized"}`.
This is the ONLY place in the Worker that produces a 401 — all other `jsonError` calls use 400,
404, 405, 502, or 503. Provider 401 bodies from all 12 supported providers use nested error
structures:
- Anthropic: `{"type":"error","error":{"type":"authentication_error",...}}`
- OpenAI/Groq/DeepSeek/etc.: `{"error":{"message":"...","type":"invalid_request_error",...}}`
- None match the flat `{"error":"unauthorized"}` pattern

**The real resolution is architectural:** Codex's concern is that body inspection is the
*sole* reliable mechanism. But it is not. Change E (`/auth-ping` probe) is the proactive,
authoritative detection mechanism. Change D (body inspection) is per-request classification
on live requests. These are complementary:
- Change E catches stale tokens before the first request fails
- Change D classifies failures on live requests (belt-and-suspenders)

The approved plan should frame Change D as secondary to Change E and note that body
inspection degrades gracefully: if the Worker format changes, the classification produces no
`reason_code` rather than a false positive. This satisfies Codex's correctness concern while
keeping both changes.

**My position:** Both changes belong in the approved plan. Change D is reliable today for
all supported providers. Change E makes body inspection a secondary signal, not the primary
one.

### D2 — Bootstrap litellm adapter removal: phase now or phase later?

**Codex investigation:** Suggests a possible "Phase B" to decide whether LiteLLM-specific
runtime sync variables in `post-bootstrap.sh` are "fully removed in the same round or retained
temporarily as legacy compatibility."

**Claude investigation:** Removal is safe (confirmed by tracing `SUBUMBRA_ADAPTER_TOKENS`
flow). `post-bootstrap.sh:43` guard can be removed with four specific line changes. No break
to sidecar operation.

**My position:** Remove in the same round. Retaining `SUBUMBRA_TOKEN_LITELLM` in
`post-bootstrap.sh` as "legacy compatibility" serves no operator. There is no service
consuming it after Change A. The token is only used by the old callback path, which 42.3
explicitly supersedes. Phasing the cleanup creates a window where `post-bootstrap.sh` writes
a token to `.env` that has no consumer, which is confusing rather than compatible. The
evidence from Q1 and Q2 of my investigation confirms this is a clean, safe removal in one
pass.

---

## 3. Items Found Exclusively in Each Investigation

### Found by Claude (missed by Codex and Gemini):

**`post-bootstrap.sh:43` blocking dependency:** The required-values guard will cause
`post-bootstrap.sh` to exit fatally if `SUBUMBRA_TOKEN_LITELLM` is absent from
`runtime.env`. This was the critical missing link between "remove litellm from bootstrap"
and "operator can successfully run post-bootstrap.sh after the change." Neither Codex nor
Gemini traced this specific guard in their investigations.

**Four specific `post-bootstrap.sh` changes identified:** lines 29-31 (read), 43 (guard),
68 (write), 80 (verification loop).

### Found by Codex (missed by Claude and Gemini):

**`docs/adapter-contract.md:212-220`** — the normative adapter contract still describes
`litellm/custom_callbacks.py` as the current Subumbra adapter implementation. Claude's
review missed this until the secondary round; Gemini did not raise it at all. Codex added
it in review-2 and confirmed it in their investigation. This is a required Change B
companion item — the normative contract document cannot be left describing removed
functionality.

### Found by Gemini (missed by Claude and Codex):

**UI `/health` probe architecture:** `ui/app.py:154` probes `CF_WORKER_URL/health` directly
with no auth. Gemini's investigation makes the key point: the UI's token would only prove the
UI's token is valid, not the Gateway's. The UI should consume the sidecar `/health` endpoint
instead. This is a correct, precise architectural observation.

**Specific `ui/app.py` update:** Change the `_worker_get("/health")` call to probe the
sidecar instead. The UI can reach the sidecar on the shared `internal` network as
`http://subumbra-proxy:8090`. This closes the contradiction where `worker_auth: "stale"` in
sidecar health but the UI still shows Worker reachable.

---

## 4. Approved Plan Scope

Based on full synthesis, the approved plan must include:

### Phase 1 — De-bundling and operator truth alignment (complete in this round)

| Change | Files | Key detail |
|---|---|---|
| A | `docker-compose.yml:73-106` | Delete `litellm:` service block entirely |
| B | `docs/subumbra-install.md`, `README.md`, `docs/subumbra-testing.md`, `.env.example`, `docs/adapter-contract.md:212-220` | Remove LiteLLM from expected services, replace `docker exec litellm` health checks, update callback-era contract description |
| C | `docs/standalone-litellm.md` (new) | Normative standalone app integration doc; close Round 41.7 as superseded |
| R2a | `bootstrap/subumbra-bootstrap.py:104-111,1062-1078,1741-1749,1818-1840` | Remove `litellm` adapter from `ADAPTER_SCOPE_VARS`, wizard prompt, `runtime.env` output, and success summary text |
| R2b | `post-bootstrap.sh:29-31,43,68,80` | Remove `SUBUMBRA_TOKEN_LITELLM` read, required-values guard, `.env` write, and post-write verification |

### Phase 2 — Worker auth visibility (complete in this round)

| Change | Files | Key detail |
|---|---|---|
| D | `subumbra-proxy/app.py` | 4xx-branch buffering: buffer Worker error responses, inspect for `{"error":"unauthorized"}`, return `reason_code: worker_auth_failure` |
| E-worker | `worker/src/worker.js` | Add `GET /auth-ping` endpoint: validate `X-Subumbra-Token` only, return 200/401, no KV/crypto, User-Agent `Subumbra-HealthProbe/1.0` |
| E-sidecar | `subumbra-proxy/app.py` | Extend `/health` with `worker_auth: "ok"\|"stale"\|"unreachable"` tri-state; probe via `/auth-ping`; cached result (60s TTL) or 1-2s timeout |
| E-ui | `ui/app.py` | Replace `_worker_get("/health")` with `_proxy_get("/health")` to surface sidecar's aggregated `worker_auth` state |
| F | `scripts/council/verify.sh` | Round-42-3 hook: assert `worker_auth == "ok"` before proof run |

### Explicitly deferred

- Per-app Worker identity model (requires runtime contract change to `/t` route)
- Custom adapter bootstrap wizard prompts (premature without per-app runtime)
- CF Logs API integration in verify harness
- Broad observability expansion
- `litellm/` directory removal (keep as reference material)
- P9.1/P9.2 harness redesign (open R42.2 item)

---

## 5. Implementation Spec Notes

**Change D buffering requirement** (missed in all proposals; clarified in my review):
`proxy_via_worker` at `subumbra-proxy/app.py:227` calls `CLIENT.send(worker_req, stream=True)`.
For 4xx responses, the implementation must branch: use `await worker_resp.aread()` to buffer
the full body (error responses are small — `{"error":"..."}`) before inspecting; for 2xx/3xx,
keep `aiter_raw()` streaming. `aread()` and `aiter_raw()` are mutually exclusive on an httpx
streaming response.

**Change E probe caching** (required to prevent sidecar instability):
Docker's healthcheck at `docker-compose.yml:185-189` polls every 10 seconds. A synchronous
unbounded `/auth-ping` call per Docker health poll risks sidecar health instability during
CF network blips and generates ~8,640 CF Worker requests/day from health traffic alone. The
implementation must specify: probe cached for 60 seconds, OR probe with a hard 1-2s timeout.

**`BUILTIN_TOKEN_SUFFIXES` at `bootstrap/subumbra-bootstrap.py:111`:**
Currently `{"LITELLM", "PROXY", "UI", "PROBE"}`. After removing `litellm` from
`ADAPTER_SCOPE_VARS`, `"LITELLM"` should be removed from this set and from
`BUILTIN_ADAPTER_IDS` to prevent the guard at line 538-542 from rejecting a hypothetical
future custom adapter named "litellm".

---

## 6. Consensus Statement

All three LLMs agree on the approved path:

1. Remove bundled LiteLLM from `docker-compose.yml` entirely
2. Update the full operator truth surface (not just install docs)
3. Document standalone app integration as the normative model with explicit shared-proxy-scope identity note
4. Add Worker auth visibility via sidecar `/health` tri-state and Worker `/auth-ping`
5. Remove the `litellm` adapter from bootstrap and `post-bootstrap.sh` in the same pass
6. Keep per-app identity as future-round work

No blocking technical disputes remain. The disagreement on Change D (body inspection
reliability) is resolved by evidence — no current provider uses the flat `{"error":"unauthorized"}`
format — and is made moot by Change E being the primary detection mechanism.

The round is ready for an approved plan.
