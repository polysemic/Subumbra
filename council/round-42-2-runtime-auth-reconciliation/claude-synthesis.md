# Round 42.2 Synthesis — Runtime Auth Reconciliation

Date: 2026-04-19
Author: Claude
Input: all proposals, reviews, investigations, and the two existing syntheses
(codex-synthesis.md, gemini-synthesis.md)

---

## 1. Consensus Items

All three reviewers converge on the following, all verifiable by code:

**C1 — Root cause is correct.**
Module-level env freeze in `custom_callbacks.py:71-76` is why token drift
produces opaque failures: `SUBUMBRA_ACCESS_TOKEN`, `SUBUMBRA_HMAC_KEY`,
`CF_WORKER_URL`, `SUBUMBRA_KEYS_URL` are read once at import time. No amount of
`.env` rewriting repairs a running container. The transparent sidecar owns all of
this at its boundary instead.

**C2 — Mechanism is correct.**
Route LiteLLM through `subumbra-proxy /t/` using `api_base:
http://subumbra-proxy:8090/t` + plain `api_key: <key_id>`. The sidecar extracts
the key_id from the Authorization header, HMAC-fetches from subumbra-keys, and
forwards to the CF Worker. `subumbra-proxy/app.py:266-315` handles this today
with no code changes required.

**C3 — `/t` format is the only correct api_base.**
`subumbra-proxy/app.py:307` builds the upstream URL as `target_host` (from the
record) + the captured `{path:path}` suffix. Any prefix embedded in `api_base`
beyond `/t` becomes part of the captured path and gets appended directly to the
provider host. Adding a provider prefix corrupts the upstream URL.
Gemini's early proposal variant (`/t/<provider_prefix>/`) was wrong;
`/t` only is correct.

**C4 — PROXY_ALLOWED_KEYS is the gating operational prerequisite.**
`subumbra-keys/app.py:546` enforces per-adapter key scope on every record fetch.
The wizard at `bootstrap/subumbra-bootstrap.py:1053-1057` prompts for litellm
and subumbra-proxy scope separately. A typical deployment that scoped keys to
LiteLLM only will get `403 key_scope_denied` on every sidecar fetch until the
proxy scope is expanded. This must be a named prerequisite step, not buried in
operator notes.

**C5 — DEEPSEEK_API_BASE must be removed.**
`docker-compose.yml:100`. Per-model `api_base` takes precedence over this env
var, so it would not silently override the sidecar route. But it is misleading
cargo-cult config that points at DeepSeek's raw endpoint, and it would become
a footgun if any model entry is added without `api_base` set. All three
reviewers and the investigation agree: remove it.

**C6 — Anthropic is in scope.**
Resolved by direct evidence. LiteLLM passes `api_base` as `base_url` to the
Anthropic SDK client constructor. The SDK sends to `{api_base}/v1/messages`.
The sidecar captures `v1/messages` and routes to `api.anthropic.com`. No
`openai/` prefix needed. The existing callback's explicit transport wiring
(which bypassed this) is what this round replaces.

**C7 — Bootstrap/docs updates are required in-scope work, not optional polish.**
The wizard text, alignment hints, README adapter-scope section, README model
section, and `docs/subumbra-install.md` Section 7 all still teach the
callback-era pattern. Following them correctly after this round produces a
broken deployment. All three reviewers named this a correctness requirement.
I named 5 specific locations in the investigation; both other syntheses confirm
they are all required.

**C8 — `custom_callbacks.py` becomes legacy-labeled; LiteLLM drift check removed.**
`custom_callbacks.py` is retained with a legacy header — callback path still
works for operators who haven't migrated, and removing the file now would leave
no record of how the callback path worked. The `litellm` case in
`post-bootstrap.sh:92` becomes a no-op (LiteLLM no longer carries
`SUBUMBRA_ACCESS_TOKEN`) and should be removed.

**C9 — `depends_on` for litellm must be updated.**
LiteLLM now depends on `subumbra-proxy` being healthy before it can serve any
model. `subumbra-proxy` has a healthcheck at `app.py:195`. Add `subumbra-proxy:
service_healthy` to litellm's `depends_on` block.

---

## 2. Disagreements

### Disagreement A — Bootstrap auto-merge of LiteLLM scope into proxy scope

**Gemini's position** (gemini-proposal.md, gemini-proposal-2.md):
Modify bootstrap so the proxy automatically inherits LiteLLM's key pool —
reduces operator friction by eliminating the separate wizard prompt.

**Claude's position** (claude-proposal-2.md, claude-investigation.md):
Reject auto-merge. Per-adapter scope is a security property: the proxy token
and the LiteLLM token have different blast radii. Collapsing them removes a
meaningful isolation boundary. Fix the operator guidance instead, not the
isolation model.

**Codex's position** (codex-review.md, codex-synthesis.md):
Agrees auto-merge is not technically required. Prompt/doc truth fixes are
sufficient for correctness.

**My position: auto-merge is not in scope and should not be.**

Evidence: `bootstrap/subumbra-bootstrap.py:574-625` — the adapter registry is
designed around independent per-adapter token + allowed_keys pairs. The proxy
having broad scope and LiteLLM having narrow scope (or no scope, post-migration)
is a legitimate security configuration. Merging them automatically would silently
grant all apps routing through the proxy the same key access that previously
required an explicit LiteLLM token. The correct fix is to update wizard text so
operators understand that `subumbra-proxy` scope IS the LiteLLM scope now — that
guidance change achieves the same usability outcome without collapsing the
security model.

### Disagreement B — Gemini's `api_base` path prefix proposal

**Gemini's position** (gemini-proposal.md, gemini-proposal-2.md):
Use `http://subumbra-proxy:8090/t/<provider_prefix>/` in `api_base`, e.g.
`/t/v1/` for OpenAI-compatible models.

**Claude + Codex position**: Mechanically wrong. Verified by
`subumbra-proxy/app.py:182-190, 307`: captured path is appended directly to
`record["target_host"]`. Provider prefix in `api_base` would produce
`api.openai.com/v1/v1/chat/completions` — a broken URL.

**My position: `/t` only is correct.**

This is now resolved by evidence. The one legitimate exception is Gemini's Google
API endpoint, which requires the path `https://generativelanguage.googleapis.com/
v1beta/openai/chat/completions`. Setting `api_base: http://subumbra-proxy:8090/t/
v1beta/openai` correctly threads this: LiteLLM appends `/chat/completions`,
sidecar captures `v1beta/openai/chat/completions`, appends to
`generativelanguage.googleapis.com`. This is a per-model path prefix in `api_base`
that is correct for this specific provider's URL structure — not a general pattern.

### Disagreement C — Was Gemini's doc-truth position adequately resolved?

Both Codex and Gemini's syntheses agree doc updates are required. But Gemini's
synthesis lists only 3 action items under "Update Operator Truth" without
specifying the exact replacement text. Codex's synthesis is similarly
action-level without specifying exact changes.

**My position:** The 5 specific locations and their replacement text are
specified in `claude-investigation.md:181-246`. The approved plan must reference
these exactly, not just say "update the docs." The risk of underprescribed doc
changes is that they get implemented partially or inconsistently.

---

## 3. What the Other Two Missed or Underspecified

**What Gemini missed:**
- Gemini's synthesis does not address the Gemini model's special `api_base` path.
  The `gemini-2.0-flash` model currently uses
  `api_base: https://generativelanguage.googleapis.com/v1beta/openai/` — a
  direct Google URL with a non-standard prefix. After removing the callback,
  this model needs `api_base: http://subumbra-proxy:8090/t/v1beta/openai` (not
  plain `/t`) or it will route to the wrong URL path.
- Gemini treated the key-scope issue as an operational note rather than a
  wizard-text correctness requirement until late in the review cycle.

**What Codex missed:**
- Codex's synthesis notes the `depends_on` gap (from my review F8) under
  "What We Missed" in Gemini's synthesis but does not include it in Codex's
  own phased plan. It belongs in the approved implementation list.
- Codex's phased plan says "verify Anthropic, OpenAI, Groq, DeepSeek, Mistral"
  but does not call out that PROXY_ALLOWED_KEYS must be verified as a
  prerequisite to live tests, not just noted somewhere in the docs.

**What I underspecified early:**
- My initial proposal (claude-proposal.md) noted DEEPSEEK_API_BASE as a risk
  to investigate but did not immediately call for removal. The investigation
  resolved this; the approved plan correctly includes removal.
- I initially deferred the `_build_litellm_alignment_lines` replacement hint
  format as BQ4 — it took until the investigation to fully specify the
  replacement. Both other syntheses absorbed this correctly from the
  investigation output.

---

## 4. Consensus Status

**APPROVED for implementation.**

All three reviewers and both other syntheses agree on the core mechanism,
removal list, and doc-truth requirements. The only open disagreement
(bootstrap auto-merge) is settled: deferred, not required. The Gemini model's
special-case `api_base` is the one implementation-detail addition beyond the
standard pattern and is addressed in the approved plan.

The approved plan (`council/approved/runtime-auth-reconciliation.md`) is
consistent with this synthesis and with both Codex and Gemini syntheses.

---

## 5. Phased Plan (Synthesis View)

### Phase 1 — Implementation (this round)

1. `litellm/config.yaml`: full replacement with proxy-routing pattern; plain
   key_ids; no callback stanza; Gemini uses `/t/v1beta/openai` api_base
2. `docker-compose.yml`: remove 7 env vars from litellm block; update
   `depends_on` to add `subumbra-proxy: service_healthy`
3. `litellm/custom_callbacks.py`: legacy header only — no logic changes
4. `post-bootstrap.sh`: remove `litellm` from drift check loop
5. `bootstrap/subumbra-bootstrap.py`: update Step 3 wizard text + replace
   `_build_litellm_alignment_lines` with proxy-routing hints
6. `README.md`: update adapter scope section + "Adding Models" section
7. `docs/subumbra-install.md`: update Section 7

### Phase 2 — Verification (prerequisite before live tests)

V3 first: verify `PROXY_ALLOWED_KEYS` in running registry covers all
`litellm/config.yaml` key_ids.

V1: static grep checks — no `subumbra:` prefix, no `callbacks:` stanza, no
Subumbra auth vars in litellm block.

V2: live per-provider completions through LiteLLM → sidecar path. Minimum: 
Anthropic, OpenAI, Groq. Aspirational: DeepSeek, Mistral, Gemini.

### Phase 3 — Future rounds (explicitly deferred from 42.2)

- Credential file import / "swap & shred" (`bootstrap --import /path/to/.env`)
- Post-bootstrap containerization
- Removal of legacy `litellm` adapter registry entry + `LITELLM_ALLOWED_KEYS`
  dead write
- Removing `custom_callbacks.py` from repo
- Removing `internal` network from `litellm` service (no longer contacts
  subumbra-keys directly)
