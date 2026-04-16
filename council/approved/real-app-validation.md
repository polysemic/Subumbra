# Approved Plan — Round 41: Real App Validation

## Consensus Basis

All three syntheses (Claude, Codex, Gemini) agree on the core approach and all
implementation items in this plan. Two minor disagreements were raised by Codex
in `codex-disputes.md` and are resolved here:

- **Python shred fallback (Dispute 2):** Resolved by evidence. Gemini's own
  investigation confirmed `shred` is present at `/usr/bin/shred` in
  `node:20-slim`. The synthesis preference for a Python fallback predates that
  finding. No fallback required this round.

- **Truth-alignment doc updates (Dispute 1):** Treated as cleanup, not required
  implementation scope. `PROJECT_STATUS.md` and `CLAUDE.md` updates will be
  noted in `cleanup.md` at close-out. This is consistent with how documentation
  drift has been handled in prior rounds.

**Scope anchor:** Subumbra installs alongside existing standalone apps (LiteLLM,
OpenWebUI, N8N) and hardens them in place. The target operator has working apps
with raw API keys and wants those keys replaced with Subumbra records without
rebuilding their stack.

This is a bolt-on round. It is NOT a fresh-install bundled-stack proof.

---

## Phase 0 — Coexistence Prerequisites

Four changes to `docker-compose.yml`. These are atomic prerequisites — do not
start Phase 1 or Phase 2 before Phase 0 is verified.

### Change 1 — Profile-gate bundled LiteLLM

**File:** `docker-compose.yml`  
**Location:** `litellm` service definition, immediately after `container_name: litellm` (line 73)  
**Add:**
```yaml
    profiles:
      - litellm
```

This prevents the bundled `litellm` service from starting on a plain
`docker compose up`, eliminating the port 4000 conflict with the standalone
testbed's LiteLLM (`127.0.0.1:4000` on the host).

Operators who want the bundled LiteLLM explicitly opt in:
`docker compose --profile litellm up -d`

### Change 2 — Add subumbra-net to networks block

**File:** `docker-compose.yml`  
**Location:** `networks:` block (lines 7–12), append after the `external:` entry  
**Add:**
```yaml
  # Join pre-existing testbed network created by: docker network create subumbra-net
  # external: true is the Compose property meaning "do not create; find by name"
  # (unrelated to the local 'external' bridge network above)
  subumbra-net:
    external: true
    name: subumbra-net   # Required: prevents subumbra_subumbra-net project-prefix collision
```

The `name: subumbra-net` field is required. Without it, Compose looks for
`subumbra_subumbra-net` (project-prefixed), which does not exist.

**Pre-condition:** The operator must have run `docker network create subumbra-net`
before `docker compose up`. This is documented in `docs/testbed-install.md`.

### Change 3 — Add subumbra-net to subumbra-proxy service

**File:** `docker-compose.yml`  
**Location:** `subumbra-proxy` service `networks:` list (lines 168–170)  
**Before:**
```yaml
    networks:
      - internal
      - external
```
**After:**
```yaml
    networks:
      - internal      # reaches subumbra-keys
      - external      # reaches CF Worker
      - subumbra-net  # reachable from standalone OpenWebUI, N8N, LiteLLM
```

`subumbra-keys` service: **no change**. It remains on `internal` only.
`subumbra-ui` service: **no change**. Accessed via nginx at `127.0.0.1:8080`.

### Change 4 — Add restart policy to subumbra-proxy

**File:** `docker-compose.yml`  
**Location:** `subumbra-proxy` service, immediately after `container_name: subumbra-proxy` (line 167)  
**Add:**
```yaml
    restart: unless-stopped
```

Every other long-running service already has this policy. The proxy is the
dependency for all standalone apps after cutover; a crash without restart
silently breaks OpenWebUI, N8N, and LiteLLM simultaneously.

### Phase 0 Verification

```bash
# Apply changes, recreate affected services
docker compose up -d --force-recreate

# Verify subumbra-proxy joined subumbra-net
docker network inspect subumbra-net | grep subumbra-proxy

# Verify subumbra-keys is NOT on subumbra-net (air-gap preserved)
docker network inspect subumbra-net | grep subumbra-keys
# Expected: no output

# Verify litellm is not running (profile-gated)
docker ps | grep litellm
# Expected: no output (unless explicitly started with --profile litellm)

# Verify all other services healthy
docker compose ps
# Expected: subumbra-keys, subumbra-proxy, subumbra-ui all Up/healthy
```

---

## Phase 1 — Bootstrap Import Loop

New implementation in `bootstrap/subumbra-bootstrap.py`. This is new work —
not existing product capability. The scratch parser (`scratch/test_parser.py`)
is supporting evidence only.

### 1a — Provider import whitelist constant

Add the following constant near the top of `subumbra-bootstrap.py`, after the
`KNOWN_PROVIDERS` block (after line 193):

```python
# Maps both Subumbra canonical env var names AND common standalone-app aliases
# to their provider_id. Both sides must be supported so that migration from a
# standard LiteLLM .env (which uses ANTHROPIC_API_KEY) and the CI path (which
# uses ANTHROPIC_KEY) both work.
IMPORT_PROVIDER_WHITELIST: dict[str, str] = {
    # Subumbra canonical names (from providers.json env_var field)
    "ANTHROPIC_KEY":        "anthropic",
    "OPENAI_KEY":           "openai",
    "GROQ_KEY":             "groq",
    "DEEPSEEK_KEY":         "deepseek",
    "CEREBRAS_API_KEY":     "cerebras",
    "GEMINI_API_KEY":       "gemini",
    "MISTRAL_API_KEY":      "mistral",
    "OPENROUTER_API_KEY":   "openrouter",
    "TOGETHER_AI_API_KEY":  "together",
    "XAI_API_KEY":          "xai",
    "GITHUB_KEY":           "github",
    "SLACK_KEY":            "slack",
    "SENDGRID_KEY":         "sendgrid",
    # Common standalone-app aliases (LiteLLM .env, OpenWebUI, etc.)
    # 7 providers have mismatched names vs. Subumbra canonical
    "ANTHROPIC_API_KEY":    "anthropic",
    "OPENAI_API_KEY":       "openai",
    "GROQ_API_KEY":         "groq",
    "DEEPSEEK_API_KEY":     "deepseek",
    "TOGETHER_API_KEY":     "together",
    "GITHUB_TOKEN":         "github",
    "SLACK_BOT_TOKEN":      "slack",
    "SENDGRID_API_KEY":     "sendgrid",
}

# Vars to explicitly skip — app-internal secrets that must never be imported
# as provider keys. If detected, skip silently (do not warn or shred).
IMPORT_EXCLUSION_LIST: frozenset[str] = frozenset({
    "LITELLM_MASTER_KEY",
    "LITELLM_SALT_KEY",
    "WEBUI_SECRET_KEY",
    "N8N_ENCRYPTION_KEY",
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "REDIS_URL",
    "SECRET_KEY",
    "JWT_SECRET",
})
```

### 1b — New function: `_parse_env_file`

Add this function after the `_default_key_id` function (after line 321):

```python
def _parse_env_file(path: str) -> list[tuple[str, str, str]]:
    """
    Parse a .env file and return detected provider key entries.

    Returns a list of (env_var_name, provider_id, raw_value) tuples.
    Only includes vars that appear in IMPORT_PROVIDER_WHITELIST.
    Skips blank lines, comments, and IMPORT_EXCLUSION_LIST vars.
    Returns empty list if file does not exist or cannot be read.

    Rules:
    - If zero entries are detected, the file must NOT be added to the shred queue.
    - Duplicate env var names: last occurrence wins (standard .env behavior).
    - Values may be quoted (single or double); quotes are stripped.
    """
    results: dict[str, tuple[str, str, str]] = {}  # env_var -> (env_var, provider_id, value)

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()

                # Strip surrounding quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]

                if not value:
                    continue
                if key in IMPORT_EXCLUSION_LIST:
                    continue
                if key in IMPORT_PROVIDER_WHITELIST:
                    provider_id = IMPORT_PROVIDER_WHITELIST[key]
                    results[key] = (key, provider_id, value)
    except OSError:
        return []

    return list(results.values())
```

### 1c — New function: `_run_import_screen`

Add this function after `_parse_env_file`:

```python
def _run_import_screen(
    api_keys: dict,
    existing_keys: dict,
) -> dict[str, tuple[str, str, str, str, str]]:
    """
    Interactive import loop: operator specifies one or more .env file paths,
    wizard detects provider keys, operator confirms each, keys are added to
    api_keys. Operator may re-run the loop for multiple files.

    Returns updated api_keys dict (same shape as run_interactive_wizard's
    api_keys: {key_id: (provider, target_host, auth_header, auth_prefix, raw_secret)}).

    SHRED QUEUE: populated only by the caller after all records are confirmed
    and bootstrap completes. This function returns a separate shred_paths list.
    """
    shred_paths: list[str] = []

    while True:
        print("\n" + "─" * 70)
        print("  Import from .env file")
        print("  (In-container path — mount host files with -v /opt/...:/host_...:ro)")
        print("─" * 70)
        path = input("  Path to .env file (or Enter to skip): ").strip()
        if not path:
            break

        detected = _parse_env_file(path)

        if not detected:
            print(f"  ✗  No recognised provider keys found in {path}.")
            print("     (App-internal secrets like LITELLM_MASTER_KEY are excluded by design.)")
            print("     File will NOT be shredded. Add keys manually below if needed.")
            continue

        print(f"\n  Detected {len(detected)} provider key(s) in {path}:\n")
        for env_var, provider_id, value in detected:
            masked = value[:4] + "..." + value[-4:] if len(value) > 8 else "****"
            print(f"    {env_var:30s}  provider={provider_id}  value={masked}")

        confirm = input("\n  Import these keys? [Y/n]: ").strip().lower()
        if confirm == "n":
            print("  Skipped. File will NOT be shredded.")
            continue

        # Per-key confirmation and key_id assignment
        for env_var, provider_id, raw_value in detected:
            target_host = PROVIDER_HOSTS.get(provider_id, "")
            if not target_host:
                print(f"  ✗  Unknown target_host for provider '{provider_id}'. Skipping {env_var}.")
                continue

            provider_entry = BUILTIN_PROVIDER_BY_ID.get(provider_id, {})
            auth_header = provider_entry.get("auth_header", "authorization")
            auth_prefix = provider_entry.get("auth_prefix", "Bearer ")

            default_key_id = _default_key_id(provider_id)
            while True:
                key_id_input = input(
                    f"\n  Key ID for {env_var} (provider={provider_id}) [{default_key_id}]: "
                ).strip()
                key_id = key_id_input or default_key_id

                if not KEY_ID_RE.match(key_id):
                    print(f"  ✗  Invalid key_id. Must match ^[a-z0-9][a-z0-9_-]{{2,63}}$")
                    continue
                if key_id in api_keys:
                    print(f"  ✗  key_id '{key_id}' already added. Choose a different name.")
                    continue
                if key_id in existing_keys:
                    ex_provider = existing_keys[key_id].get("provider", "unknown")
                    if ex_provider != provider_id:
                        print(f"\n  ⚠  WARNING: key_id '{key_id}' already exists under provider '{ex_provider}'.")
                        overwrite = input("     Overwrite? [y/N]: ").strip().lower()
                        if overwrite != "y":
                            print("  Cancelled. Choose a different key_id.")
                            continue
                break

            api_keys[key_id] = (provider_id, target_host, auth_header, auth_prefix, raw_value)
            ok(f"{provider_id:12s}  →  {key_id}  (from {env_var}, key hidden)")

        # Only add to shred queue after all keys from this file are accepted
        shred_confirm = input(
            f"\n  Shred source file {path} after bootstrap completes? [y/N]: "
        ).strip().lower()
        if shred_confirm == "y":
            shred_paths.append(path)
            print(f"  ✓ {path} queued for shredding after successful bootstrap.")
        else:
            print(f"  Skipped shredding. Raw keys remain in {path}.")

        another = input("\n  Import from another file? [y/N]: ").strip().lower()
        if another != "y":
            break

    # Attach shred_paths to api_keys as a side-channel (caller unpacks)
    # Use a sentinel key to pass shred list without changing return signature
    if shred_paths:
        api_keys["__shred_paths__"] = shred_paths  # type: ignore[assignment]

    return api_keys
```

### 1d — Integrate import screen into `run_interactive_wizard`

**Location:** `run_interactive_wizard` function, at the start of Screen 2
(Provider API Keys), before the `while True:` loop that prompts for provider
selection (before line 754).

Add this block immediately after the Screen 2 header print statements:

```python
    # ── Import from .env file (migration path) ────────────────────────────────
    print("  Option: import provider keys from an existing .env file.")
    print("  Run bootstrap with: -v /opt/litellm:/host_litellm:ro")
    print("  then enter the in-container path (e.g. /host_litellm/.env)\n")
    do_import = input("  Import from .env file(s)? [y/N]: ").strip().lower()
    if do_import == "y":
        api_keys = _run_import_screen(api_keys, existing_keys)
        # Unpack shred_paths sentinel if present
        shred_paths: list[str] = api_keys.pop("__shred_paths__", [])  # type: ignore[arg-type]
    else:
        shred_paths = []
```

### 1e — Shred execution after successful bootstrap

**Location:** At the end of the `run_interactive_wizard` call site in `main()`
(or wherever bootstrap completion is signaled), add shred execution:

```python
    # Execute queued shreds after all records confirmed and bootstrap complete
    if shred_paths:
        import subprocess
        print("\n" + "─" * 70)
        print("  Shredding source .env files...")
        for shred_path in shred_paths:
            try:
                result = subprocess.run(
                    ["shred", "-u", shred_path],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    ok(f"Shredded: {shred_path}")
                else:
                    warn(f"shred failed for {shred_path}: {result.stderr.strip()}")
                    print(f"  ⚠  Manual deletion required: rm -P {shred_path}")
            except FileNotFoundError:
                warn(f"shred not found. Manual deletion required: rm -P {shred_path}")
```

### Phase 1 Notes

**Zero-detected-keys rule:** `_parse_env_file` returning an empty list means
no shred — this is enforced in `_run_import_screen` by the `continue` after
the "No recognised provider keys found" message. Never shred a file from which
zero keys were imported.

**Shred availability:** `shred` is confirmed present at `/usr/bin/shred` in
`node:20-slim` (Gemini investigation). No Python fallback required for this
round.

**What NOT to change:**
- The automation / CI path (`run_automation_mode`) — no import loop there
- The existing manual key-entry flow in `run_interactive_wizard` — remains
  unchanged; operators who skip the import offer continue to the manual flow
- `providers.json` — do not add `env_var_aliases` to this file; the whitelist
  lives in bootstrap code only (implementation decision: keeps the registry
  clean; aliases are a migration concern, not a runtime concern)
- `KNOWN_PROVIDERS` — do not change; canonical names remain the CI path source

### Migration command (must appear in operator docs)

```bash
docker compose --profile bootstrap run --rm \
  -v /opt/litellm:/host_litellm:ro \
  -it bootstrap
```

In-container path for LiteLLM's `.env`: `/host_litellm/.env`

To mount multiple app directories:
```bash
docker compose --profile bootstrap run --rm \
  -v /opt/litellm:/host_litellm:ro \
  -v /opt/open-webui:/host_openwebui:ro \
  -it bootstrap
```

---

## Phase 2 — App Cutover

These are operator steps, not code changes. The approved plan must document the
exact values so operators don't guess.

### LiteLLM cutover

After bootstrap completes and Subumbra records exist:

1. Edit `/opt/litellm/config.yaml`. For each model entry, change the `api_key`:
   ```yaml
   # Before:
   api_key: os.environ/ANTHROPIC_API_KEY

   # After:
   api_key: "subumbra:anthropic_litellm"
   ```
   The key_id (`anthropic_litellm`) must match what was assigned during import.

2. The `.env` keys (`ANTHROPIC_API_KEY`, etc.) are now unused by LiteLLM config,
   but remain in the file until the operator shreds (done by bootstrap if queued).

3. Restart LiteLLM:
   ```bash
   cd /opt/litellm && docker compose restart litellm
   ```

4. Verify: send a test completion request through LiteLLM.

### OpenWebUI cutover

Edit `/opt/open-webui/.env`:
```bash
# Before:
OPENAI_API_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...

# After (direct path — OpenAI-format providers only):
OPENAI_API_BASE_URL=http://subumbra-proxy:8090/t
OPENAI_API_KEY=openai_prod   # key_id, NOT a raw API key
```

Restart OpenWebUI:
```bash
cd /opt/open-webui && docker compose restart open-webui
```

**Critical verification:**
```bash
docker inspect open-webui | grep OPENAI_API_KEY
# Expected: "OPENAI_API_KEY=openai_prod"  (or chosen key_id)
# NOT:      "OPENAI_API_KEY=sk-..."
```

If the output contains `sk-`, the misconfiguration will produce a cryptic
502 from the proxy (key_id format check passes but record lookup fails).
The verification command above is the operator-facing mitigation.

**Anthropic constraint (documented boundary):**
The transparent proxy returns whatever the upstream provider returns.
OpenWebUI only parses OpenAI-format responses. For Anthropic-keyed records,
route: OpenWebUI → standalone LiteLLM → Subumbra (LiteLLM handles format
translation). This is not a limitation to fix in this round.

**Supported direct-path providers:** openai, groq, deepseek, mistral,
openrouter, together, xai, cerebras, gemini (OpenAI-compatible APIs only).

### N8N cutover

N8N stores credentials in its SQLite database (not `.env` files), so it is a
**workflow re-pointing proof**, not an import target.

1. In N8N UI: open each HTTP Request credential that uses a raw API key.
2. Change the base URL to `http://subumbra-proxy:8090/t` (or `/v1/request`
   for the key-lookup path).
3. Change the API key field to the relevant key_id (e.g., `openai_prod`).
4. Delete the old raw-key credentials from N8N's credential store.
5. Import `docs/n8n-workflows/test-llm-via-subumbra.json` as proof workflow.
6. Run the workflow; confirm successful execution.

**N8N is not an import target** — no bootstrap changes apply here.

---

## Phase 3 — Proof Capture

Minimal manual artifacts. No harness rewrite required.

### Required proof artifacts

1. **Subumbra UI screenshot:** Subumbra dashboard at `http://127.0.0.1:8080`
   showing per-app key_ids (`anthropic_litellm`, `openai_openwebui`, etc.)
   with non-zero request counts.

2. **OpenWebUI screenshot:** Successful chat completion response visible in
   the OpenWebUI interface, routing through Subumbra.

3. **N8N workflow execution log:** `docs/n8n-workflows/test-llm-via-subumbra.json`
   workflow run showing success in N8N execution history.

4. **LiteLLM curl proof:**
   ```bash
   curl -s http://127.0.0.1:4000/v1/chat/completions \
     -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
     -H "Content-Type: application/json" \
     -d '{"model": "anthropic/claude-3-5-haiku", "messages": [{"role": "user", "content": "ping"}]}' \
     | jq .choices[0].message.content
   ```

5. **Absence proof:** Confirm no raw provider keys remain in LiteLLM config:
   ```bash
   grep -E "(ANTHROPIC|OPENAI|GROQ|DEEPSEEK)_API_KEY" /opt/litellm/config.yaml
   # Expected: no matches (only subumbra:key_id refs remain)
   ```

### Per-app key naming (recommended, not required)

The council recommends per-app key naming to enable audit trail, per-app
revocation, and spend visibility:

- `anthropic_litellm` — Anthropic key used by LiteLLM
- `openai_openwebui` — OpenAI key used by OpenWebUI direct path
- `openai_n8n` — OpenAI key used by N8N workflows

This is a recommendation. Operators may use simpler naming (`anthropic_prod`,
`openai_prod`) if per-app separation is not needed.

---

## Logging and Error Handling

### Required (operator-visible signals)

| Scenario | Signal | Location |
|----------|--------|----------|
| Zero keys detected in .env file | Print to wizard terminal: "No recognised provider keys found in {path}" | `_run_import_screen` |
| shred fails or binary missing | Print to wizard terminal: "shred failed ... Manual deletion required" | Phase 1e |
| OpenWebUI sends raw key as key_id | Proxy returns 502 "subumbra record fetch failed" | Existing behavior; no change |
| subumbra-net does not exist at startup | Docker Compose startup failure with network not found error | Docker layer; no code change |

### Must not log

- Raw provider key values (at any log level)
- `SUBUMBRA_ACCESS_TOKEN` or `SUBUMBRA_TOKEN_*` values
- Full `Authorization` header values
- Imported `.env` file contents

### Deferred

- Better 502 error message distinguishing "key_id not found" from upstream errors
  (E5 from review-4) — deferred to a future round; verification steps above
  mitigate the operator impact for this round

---

## What NOT to Change

- `subumbra-keys/app.py` — no changes
- `subumbra-proxy/app.py` — no changes
- `worker/src/worker.js` — no changes
- `worker/src/providers.json` — no `env_var_aliases` field; alias mapping lives
  in bootstrap only
- `litellm/custom_callbacks.py` — no changes
- `bootstrap/subumbra-bootstrap.py` automation path (`run_automation_mode`) —
  no import loop; CI path unchanged
- Existing manual key-entry flow in `run_interactive_wizard` — import is an
  opt-in offer at the start of Screen 2; the rest of the wizard is unchanged
- `docs/testbed-install.md` — already documents the baseline; no changes needed
  for this round's proof

---

## Known Limitations Carried Forward

1. **OpenAI-only direct path:** Transparent proxy returns upstream responses
   unchanged. OpenWebUI direct path works only for OpenAI-format providers.
   Anthropic users must route through standalone LiteLLM. Documented above;
   not a fix target for this round.

2. **No inbound caller authentication on subumbra-proxy:** Network membership
   in `subumbra-net` is the access boundary. All containers on `subumbra-net`
   can reach the proxy. This is the accepted Round 41 operating assumption,
   consistent with the existing Docker-network-placement security model.
   Per-caller auth is a future round concern.

3. **key_id format validation only:** `validate_transparent_key_id` checks
   `^[A-Za-z0-9_-]+$` but not key_id existence. A misconfigured raw API key
   passes the regex and produces a 502 with no helpful message. Mitigation:
   verification step `docker inspect open-webui | grep OPENAI_API_KEY`.
   Better error messaging is deferred.

4. **N8N credential migration is manual UI work:** No bootstrap import path
   for N8N's SQLite credential store. This is by design — N8N is a
   re-pointing proof, not an import target.

5. **Import loop is interactive only:** No CI/automation path for env file
   import. Operators needing headless migration must pre-stage Subumbra records
   manually via `run_automation_mode` with explicit env vars.

---

## Close-Out

**Status:** Closed — 2026-04-16

**Implementing agent:** Codex

**Verifying agents:** Codex (verification 1), Claude (verification 2)

**Final PASS commit:** `98f4206`

**Official proof run:**
- Clean-run ID: `clean-run-20260416T183708`
- Verify run ID: `claude-20260416T183754`
- Overall: PASS
- Artifacts: `council/closed/round-41-real-app-validation/runs/clean-run-20260416T183708/`

### Verification Summary

All five proof checks passed:

| Check | Status |
|-------|--------|
| r41-1: subumbra-net membership (proxy present, keys absent) | PASS |
| r41-2: bundled litellm absent (profile gate working) | PASS |
| r41-3: transparent proxy direct (HTTP 200, real OpenAI response) | PASS |
| P9.5: UI status (`subumbra_keys_healthy` present) | PASS |
| P9.6: Worker invalid-token rejection | PASS |

### Issues found and fixed during verification

1. **PROXY_ALLOWED_KEYS empty** — `.env.bootstrap.example` defaults to empty,
   giving the proxy zero key scope. Fixed via `--bootstrap-overlay` with
   `PROXY_ALLOWED_KEYS=openai_prod`. Overlay file committed at
   `council/closed/round-41-real-app-validation/bootstrap-overlay.env`.

2. **Stale subumbra-ui image** — VPS image was built pre-rebrand, returned
   `forge_healthy` instead of `subumbra_keys_healthy`. Fixed by passing
   `--build subumbra-ui` to the clean-run command.

3. **verify_run_id null in result.json on failure** — `export_round_runs_if_present`
   (cleanup trap path) copied run folders but never set `verify_run_id`. Fixed in
   `scripts/council/clean-run.sh` to resolve the run ID from copied folders.

4. **Workflow doc gaps** — three precondition notes added to
   `docs/subumbra-developer.md` Lane B/C section: stack-down requirement,
   `--build` requirement for stale images, and failing-verify-still-produces-artifacts.

### Deferred items

See `council/closed/round-41-real-app-validation/cleanup.md` and `council/cleanup.md`
for items carried forward:

- `PROJECT_STATUS.md` and `CLAUDE.md` truth-alignment updates (bolt-on
  architecture and Round 41 "universality" goal) — deferred from Round 41 scope
