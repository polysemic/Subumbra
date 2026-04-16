# Cleanup Notes — Round 41: Real App Validation

## Deferred: PROJECT_STATUS.md and CLAUDE.md truth-alignment

**What:** Both files should be updated to reflect the bolt-on standalone-app
architecture and the Round 41 "universality" goal (Subumbra installs alongside
existing apps without replacing them).

**Why deferred:** Gemini's synthesis listed these as consensus scope, but
Claude and Codex syntheses did not include them. The approved plan preamble
resolves this as a scope disagreement, treating doc updates as cleanup rather
than implementation scope. Consistent with how documentation drift has been
handled in prior rounds.

**Specific updates needed:**

- `PROJECT_STATUS.md` — add bolt-on architecture description; note that Round 41
  validated the coexistence model with LiteLLM, OpenWebUI, and N8N on the same
  host; update "current state" to reflect Phase 0–3 complete
- `CLAUDE.md` — add note to Architecture section about the `subumbra-net` Docker
  network and the transparent proxy path (`/t/{path}`) for OpenWebUI-style
  direct connections; note the `PROXY_ALLOWED_KEYS` scope setting and its role
  in coexistence deployments

**Suggested round:** Next available truth-alignment or documentation round.

---

## Harness note: --bootstrap-overlay required for transparent proxy check

The `verify-round.sh` r41-3 check requires `PROXY_ALLOWED_KEYS=openai_prod` to
be set at bootstrap time. The `.env.bootstrap.example` default is empty (by
design — operators set their own scope). The `bootstrap-overlay.env` file in
this archive folder must be passed as `--bootstrap-overlay` whenever a future
verifier reruns the Round 41 clean-run from scratch.

```bash
./scripts/council/clean-run.sh \
  --round round-41-real-app-validation \
  --agent <name> \
  --build bootstrap subumbra-ui \
  --bootstrap-overlay council/closed/round-41-real-app-validation/bootstrap-overlay.env
```

Note: `--build bootstrap subumbra-ui` can be omitted once those images have
been rebuilt from current source on the target host.

---

## Harness improvement: verify_run_id on failure path

Fixed in `scripts/council/clean-run.sh`: `export_round_runs_if_present` now
sets `verify_run_id` from the workspace run folders when the cleanup trap fires
(i.e., when verify fails before `copy_proof_artifacts` runs). This ensures
`result.json` captures the run ID even on a failed clean-run. Committed in
`98f4206`.
