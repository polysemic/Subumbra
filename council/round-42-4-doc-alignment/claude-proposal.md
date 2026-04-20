# Claude Proposal — Round 42.4: Documentation Truth Alignment

## Eric's Requests (Operator Directives)

These are non-negotiable scope items raised directly by the operator:

1. **No council docs in git.** Remove all council files from git tracking.
   Fix the `.gitignore` so the council directory is permanently excluded and
   `scripts/council/` is not accidentally caught by the same rule.
2. **Delete obsolete scripts.** Any script in the root, `scripts/`, or elsewhere
   that no longer serves a purpose should be deleted.
3. **No archive retention.** Old round history does not need to be preserved in
   an in-git archive. Anything worth reusing is in `scripts/`.
4. **Round 41.7 fully closed and moved.** Record it as closed in
   PROJECT_STATUS.md; not pending.
5. **Round 42-operator-hardening closed with this round.** The
   `council/round-42-operator-hardening` directory was superseded by 42.2/42.3;
   close it out as part of this round.
6. **README must align** with the docs folder and current system state.

---

## 1. Evidence

### 1.1 CLAUDE.md — Multiple stale sections

**Architecture diagram** (`CLAUDE.md:16-38`):
```
App / Adapter
    ↓
LiteLLM (Adapter #1) today
or future sidecar/service adapters
    ↓ fetch subumbra records, then call canonical POST /proxy
```
Reality: `subumbra-proxy` is the deployed sidecar and is the current primary
integration method. The callback-era LiteLLM path is superseded.

**Project structure tree** (`CLAUDE.md:80-82`):
```
├── litellm/                     ← LiteLLM integration
│   ├── custom_callbacks.py      ← intercepts calls, fetches from subumbra-keys, routes to CF
│   └── config.yaml              ← model config using "subumbra:key_id" references
```
Reality: `custom_callbacks.py` is legacy. `config.yaml` no longer uses
`subumbra:key_id` format — it uses plain key IDs with
`api_base: http://subumbra-proxy:8090/t` (verified Round 42.3 r42-3-5).
`subumbra-proxy/` is absent from the tree entirely.

**Docker Networking** (`CLAUDE.md:96`):
```
- `external` network: litellm, cloudflared, subumbra-proxy, subumbra-probe
```
`litellm` service removed in Round 42.3 (r42-3-1 proof: only three services).

**"LiteLLM Integration" section** (`CLAUDE.md:100-113`): Entire section
describes the callback path as the current integration.

**Adapter Contract** (`CLAUDE.md:167-170`): Says LiteLLM is Adapter #1,
sidecar is Adapter #2. This inverts the current model.

**Environment Variables** (`CLAUDE.md:212`): Lists `SUBUMBRA_TOKEN_LITELLM`
as a runtime var — removed in Round 42.3 (r42-3-2 proof).

---

### 1.2 PROJECT_STATUS.md — Stale Path Forward

**File date** (`PROJECT_STATUS.md:2`): `*Current state — updated 2026-04-16*`

**Path Forward** (`PROJECT_STATUS.md:117-218`): Treats Rounds 40–42.3 as
future work. Specific stale items:
- Round 40: listed without "Closed" marker
- Round 41.7 (`PROJECT_STATUS.md:151`): listed as "Open"
- Round 42-operator-hardening: not reflected as closed
- Rounds 42.2, 42.3: not marked closed
- No Round 43 direction

**Stale "Immediate investigation"** (`PROJECT_STATUS.md:163-173`): Still
describes the `subumbra:key_id` config alignment problem as open — solved by
Round 42.2/42.3 (sidecar contract replaces the callback path entirely).

---

### 1.3 .gitignore — Unanchored council/ rule

**`.gitignore:19`**: `council/`  
This is unanchored and matches `scripts/council/` as well as the top-level
`council/`. The intended behavior is to ignore only the top-level
`council/` directory. Current effect: attempts to add `scripts/council/` files
require `git add -f` too, creating friction.

**131 council files are currently tracked in git** (`git ls-files council/`).
These are working documents (proposals, reviews, runs, approved plans) that
should be local-only, not committed to source control.

---

### 1.4 Obsolete root script

**`test-check.sh`** (root): References `LITELLM_ALLOWED_KEYS` and parses
`api_key: "subumbra:<key_id>"` from `litellm/config.yaml`. Both concepts are
obsolete after Round 42.2. This script has no current use.

---

### 1.5 README.md — Quick start coverage

**`README.md`**: Updated in Round 42.3 to describe the app-owned model and
current stack shape. Content is accurate. However the "Next Docs" section and
quick-start links should be verified to cover the full current doc set,
including `docs/testbed-install.md` (for Round 43 context) and the operator
guide.

---

### 1.6 docs/project-memory.md — Minor framing drift

**Section 1** (`docs/project-memory.md:13-18`): "LiteLLM is Adapter #1" —
historically true but misleading for a fresh session after Round 42.3.

**Section 3** (`docs/project-memory.md:40-45`): Describes sidecar as "a real
product direction" — it is now the current reference integration.

---

## 2. Current vs Desired

| File | Current State | Desired State |
|------|--------------|---------------|
| `CLAUDE.md` arch diagram | LiteLLM-centric; sidecar "future" | App-owned sidecar as current model |
| `CLAUDE.md` project tree | `litellm/` = integration; `subumbra-proxy/` absent | `litellm/` = legacy; `subumbra-proxy/` present |
| `CLAUDE.md` docker network | includes `litellm` | `litellm` absent |
| `CLAUDE.md` LiteLLM section | callback path as current | superseded legacy section |
| `CLAUDE.md` adapter contract | callback #1, sidecar #2 | sidecar primary, callback legacy |
| `CLAUDE.md` env vars | includes `SUBUMBRA_TOKEN_LITELLM` | removed; matches `.env.example` |
| `PROJECT_STATUS.md` | dated Apr 16; 40–42.3 as future | dated current; arc closed; Round 43 direction |
| `.gitignore:19` | `council/` (unanchored) | `/council/` (anchored to root) |
| git tracking | 131 council files tracked | council/ fully untracked |
| `test-check.sh` | obsolete callback-era script in root | deleted |
| `docs/project-memory.md` sec 1, 3 | sidecar as "direction"; LiteLLM as "Adapter #1" | sidecar as reference integration |

---

## 3. Proposed Changes

### Change A — Rewrite CLAUDE.md architecture section

Replace the architecture diagram and intro paragraph to reflect the app-owned
sidecar model:

```
App (LiteLLM, OpenWebUI, N8N, etc.) — app-owned install
      ↓ api_base: http://subumbra-proxy:8090/t  (plain key_id as api_key)
subumbra-proxy  (core stack at /opt/subumbra)
      ↓ fetches V2 record; packages canonical /proxy call
subumbra-keys   (Docker-internal only)
      ↓ returns V2 envelope — useless without RSA private key in CF Secrets
Cloudflare Worker + Durable Object
      ↓ RSA-OAEP unwrap → AES-256-GCM decrypt → auth inject (~100ms)
Provider API (Anthropic, OpenAI, Groq, etc.)
      ↓ streams response back through Worker → proxy → app
```

Intro paragraph: `subumbra-proxy` is the current reference integration surface.
The legacy callback path (`litellm/custom_callbacks.py`) is retained as a
reference artifact only.

### Change B — Update CLAUDE.md project structure tree

Add `subumbra-proxy/` entry. Fix `litellm/` entry:

```
├── subumbra-proxy/              ← transparent sidecar (primary integration path)
│   ├── Dockerfile
│   ├── app.py                   ← FastAPI; /t transparent route; /health worker_auth; /v1/request
│   └── requirements.txt
│
├── litellm/                     ← legacy callback artifacts (reference only)
│   ├── custom_callbacks.py      ← superseded callback-era integration
│   └── config.yaml              ← standalone LiteLLM config example (plain key_id format)
```

### Change C — Update CLAUDE.md Docker Networking

Remove `litellm` from the external network list:
- `internal`: subumbra-keys, bootstrap, ui, subumbra-probe, subumbra-proxy
- `external`: cloudflared, subumbra-proxy, subumbra-probe

### Change D — Replace CLAUDE.md "LiteLLM Integration" section

Rename to "App-Owned Integration Contract" describing the `/t` sidecar
contract. Demote the callback description to a "Legacy (Superseded)" subsection.

The current contract:
- App runs in its own install (e.g. `/opt/litellm`, `/opt/openwebui`)
- `api_base: http://subumbra-proxy:8090/t`
- `api_key: <plain key_id>` — no `subumbra:` prefix
- `subumbra-proxy` owns the record fetch and the `/proxy` call
- App never sees the decrypted provider key

### Change E — Update CLAUDE.md Adapter Contract section

Replace numbered adapter framing:
- Primary: `subumbra-proxy` transparent sidecar (`/t` route)
- Legacy: `litellm/custom_callbacks.py` (callback-era Round 25, superseded)
- New integrations follow the sidecar contract; see `docs/standalone-litellm.md`

### Change F — Update CLAUDE.md environment variables

Remove `SUBUMBRA_TOKEN_LITELLM` from the runtime env var example. Match
`.env.example` post-Round-42.3:
```
SUBUMBRA_ADAPTER_REGISTRY=
SUBUMBRA_TOKEN_PROXY=
SUBUMBRA_TOKEN_UI=
SUBUMBRA_TOKEN_PROBE=
SUBUMBRA_HMAC_KEY=
CF_WORKER_URL=
CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=
```

### Change G — Rewrite PROJECT_STATUS.md

Update date to 2026-04-19. Restructure the document:

1. **Add closed-arc summary** before the Path Forward section:
   - Round 40: Broader Decoupling And Security Hardening — Closed
   - Round 41 (41.1 through 41.6): Real App Validation — Closed
   - Round 41.7: Standalone LiteLLM Runtime Fix — Closed (resolved in 42.x arc)
   - Round 42 (operator hardening): Closed — superseded by 42.2 and 42.3
   - Round 42.2: Runtime Auth Reconciliation — Closed 2026-04-19
   - Round 42.3: App-Owned Integrations — Closed 2026-04-19

2. **Replace the stale "Path Forward" section** with a clean forward-looking entry:
   - Round 42.4: Documentation Truth Alignment — current
   - Round 43: Other App Testing (OpenWebUI, N8N, non-AI service flows)
   - Remove the stale "candidates," "immediate investigation," and sub-round
     lists — that history lives in closed council directories locally

3. **Keep the Known Limitations table and Open Questions section unchanged.**

### Change H — Update docs/project-memory.md sections 1 and 3

Section 1: Replace "LiteLLM is Adapter #1" with:
"LiteLLM is the first proven app-owned integration. The canonical integration
path is the transparent sidecar (`subumbra-proxy /t`) with plain key IDs."

Section 3: Update "explicit sidecar/service path exists and is a real product
direction" to: "The transparent sidecar route (`subumbra-proxy /t`) is the
current reference integration. New apps connect via `api_base:
http://subumbra-proxy:8090/t`."

### Change I — Fix .gitignore and remove council/ from git tracking

1. Change `.gitignore:19` from `council/` to `/council/` (anchored to root).
   - This stops the rule from accidentally matching `scripts/council/`.
   - `scripts/council/` harness scripts remain tracked normally.

2. Remove all 131 currently-tracked council files from git:
   ```
   git rm --cached -r council/
   ```
   The files remain on disk locally. They will not appear in future commits.
   Future `git add` calls targeting council files will be ignored.

### Change J — Delete obsolete root script

Delete `test-check.sh` from the repository root. This script:
- Hard-codes `LITELLM_ALLOWED_KEYS="anthropic_prod,openai_prod,groq"`
- Parses `api_key: "subumbra:<key_id>"` format from `litellm/config.yaml`
Both concepts are obsolete after Round 42.2. The script has no current purpose.

### Change K — Close round-42-operator-hardening in PROJECT_STATUS.md

The `council/round-42-operator-hardening` directory existed as a pre-planning
workspace. Its intended scope — preflight checks, restart sync guidance,
standalone LiteLLM template — was delivered through Rounds 42.2 and 42.3.
Record it as superseded in PROJECT_STATUS.md. (The directory itself becomes
local-only after Change I removes council from git tracking.)

### Change L — README.md: verify and align with current docs

The README was updated in Round 42.3 and is substantially correct. This change:
- Verifies all doc links are current and the referenced files exist
- Adds `docs/testbed-install.md` and `docs/operator-guide.md` to the "Next Docs"
  section if not already present
- Confirms the Quick Start steps align with the install/testing guide sequence
- Removes or corrects any remaining LiteLLM-bundled-service language

---

## 4. Failure Modes

This round is doc-only (plus gitignore + file deletion). Risks:

1. **git rm --cached removes too broadly** — the command targets `council/`
   only; `scripts/council/` is under `scripts/` and is not affected.
2. **Over-removal in PROJECT_STATUS.md** — the Known Limitations table and Open
   Questions section must be preserved verbatim; they are still accurate.
3. **README link rot** — verify each `docs/*.md` link target exists before
   committing.

---

## 5. Exclusions

1. No source code changes of any kind.
2. No changes to `docs/adapter-contract.md`, `docs/standalone-litellm.md`,
   `docs/subumbra-install.md`, `docs/subumbra-testing.md`,
   `docs/operator-guide.md`, `docs/subumbra-developer.md`,
   `docs/vps-deployment.md`, or `docs/testbed-install.md` — these are current.
3. No changes to `council/COUNCIL.md`, `council/COUNCIL_PROMPT.md`, or
   `docs/council-memory.md`.
4. No architecture changes or roadmap decisions.
5. `scripts/subumbra-expire-adapter.sh` is retained — still used operationally.
6. All `scripts/council/` harness scripts are retained — operational tools.

---

## 6. Open Questions

1. **CLAUDE.md "Build Order" and "Testing" sections**: Describe the original
   callback integration test sequence. My default: leave unchanged this round
   as they describe general developer process. Flag if others disagree.

2. **PROJECT_STATUS.md verbosity**: The Known Limitations table has entries
   like `DASH-COUNT` referencing LiteLLM retry behavior that no longer applies.
   Should these be pruned? My default: clean up only items explicitly tied to
   the removed bundled service.

3. **"POC" language**: `PROJECT_STATUS.md` says "Keep project language as POC
   for now." Given the stack has been stress-tested and is running real API
   traffic across 13+ providers, should this evolve? Not blocking this round —
   raise in Round 43 kickoff.
