# Claude Review — Round 42.4: Documentation Truth Alignment

## Findings Table

| # | File | Line(s) | Issue | In Proposal? | Severity |
|---|------|---------|-------|-------------|----------|
| 1 | `CLAUDE.md` | 18 | `"LiteLLM (Adapter #1) today"` in arch diagram | Yes (Change A) | High |
| 2 | `CLAUDE.md` | 80–82 | `litellm/` described as active integration; `subumbra:key_id` format; `subumbra-proxy/` absent from tree | Yes (Change B) | High |
| 3 | `CLAUDE.md` | 96 | `litellm` listed on external network — service removed | Yes (Change C) | High |
| 4 | `CLAUDE.md` | 100–113 | Entire LiteLLM Integration section describes superseded callback path | Yes (Change D) | High |
| 5 | `CLAUDE.md` | 167–170 | Adapter Contract: callback = #1, sidecar = #2 — inverted | Yes (Change E) | High |
| 6 | `CLAUDE.md` | 211 | `SUBUMBRA_TOKEN_LITELLM` in runtime env vars | Yes (Change F) | High |
| 7 | `CLAUDE.md` | 234 | Build Order step 5: `litellm/custom_callbacks.py (LiteLLM integration)` | No | Medium |
| 8 | `PROJECT_STATUS.md` | 2 | Dated `2026-04-16` | Yes (Change G) | Medium |
| 9 | `PROJECT_STATUS.md` | 151, 175, 184 | Round 41.7 listed as `(Open)` in three places | Yes (Change G) | High |
| 10 | `PROJECT_STATUS.md` | 182–193 | Round 42 operator hardening listed as future, not closed | Yes (Change K) | High |
| 11 | `PROJECT_STATUS.md` | 60, 62–64 | Known Limitations: 4 entries reference bundled LiteLLM service | No | Medium |
| 12 | `.gitignore` | 19 | `council/` unanchored — matches `scripts/council/` | Yes (Change I) | High |
| 13 | git index | — | 131 council files tracked in git | Yes (Change I) | High |
| 14 | `test-check.sh` | root | Obsolete callback-era script: `LITELLM_ALLOWED_KEYS`, `subumbra:key_id` parsing | Yes (Change J) | High |
| 15 | `README.md` | 60, 66, 72, 100–105 | All 9 doc links use absolute `/home/eric/git/Subumbra/...` paths | **No** | **Critical** |
| 16 | `README.md` | 98–106 | "Next Docs" missing `docs/testbed-install.md`, `docs/vps-deployment.md`, `docs/provider-catalog.md` | Partial (Change L) | Low |
| 17 | `docs/project-memory.md` | 13–18, 40–45 | "LiteLLM is Adapter #1"; sidecar framed as "direction" | Yes (Change H) | Medium |

---

## Verification Output

```
$ grep -n "litellm" docker-compose.yml
(no output)                    # litellm service confirmed absent

$ grep -rn "SUBUMBRA_TOKEN_LITELLM" .env.example bootstrap/subumbra-bootstrap.py post-bootstrap.sh
(no output)                    # token references confirmed removed

$ grep -n "subumbra:" litellm/config.yaml | head -3
6:#   api_key:  <key_id>   (plain, no "subumbra:" prefix)
                               # comment confirms legacy format is gone

$ git ls-files council/ | wc -l
131                            # 131 files currently tracked

$ grep -n "council" .gitignore
19:council/                    # unanchored; matches scripts/council/ too

$ ls test-check.sh
test-check.sh                  # file present, 596 bytes
```

---

## Detailed Analysis

### Finding 15 — README absolute paths (CRITICAL, not in proposal)

Every doc link in `README.md` uses the hardcoded absolute path
`/home/eric/git/Subumbra/...`:

```markdown
README.md:60  - [docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md)
README.md:66  - [docs/standalone-litellm.md](/home/eric/git/Subumbra/docs/standalone-litellm.md)
README.md:72  - [docs/subumbra-testing.md](/home/eric/git/Subumbra/docs/subumbra-testing.md)
README.md:100 - [docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md)
...
```

These links work only on this machine. Anyone who clones the repo to
`/opt/subumbra`, `/home/other-user/`, or any other path will have broken links
throughout the README. This is a portability regression that Round 42.3
introduced and must be fixed in this round.

**Required fix**: Change all 9 doc links from absolute paths to relative paths:
```markdown
- [docs/subumbra-install.md](docs/subumbra-install.md)
```

### Finding 7 — CLAUDE.md Build Order step 5

`CLAUDE.md:234`:
```
5. litellm/custom_callbacks.py (LiteLLM integration)
```

The Build Order section describes the original construction sequence for
the repo. Step 5 presents the callback as a first-class build artifact.
Since `subumbra-proxy` now exists and is the primary integration path, this
step should be updated. The Error / Logging Check subsection (`CLAUDE.md:239`)
and the Testing section (`CLAUDE.md:247-251`) are generic and accurate — they
do not need changes.

Suggested replacement for step 5:
```
5. subumbra-proxy/app.py (transparent sidecar)
```
Optionally add a step 6a:
```
6a. litellm/ (example app-owned integration — see docs/standalone-litellm.md)
```

### Finding 11 — Known Limitations table (PROJECT_STATUS.md)

Four entries reference the bundled LiteLLM service that was removed in
Round 42.3:

| ID | Stale reference | Status |
|----|----------------|--------|
| `DASH-COUNT` | "Likely silent LiteLLM retry" | Bundled LiteLLM removed; root cause may not apply |
| `PROVIDER-COUPLING` | "LiteLLM model declaration duplication in `litellm/config.yaml`" | This is now an app-owner concern, not a core coupling |
| `LITELLM-UI` | "LiteLLM admin UI login non-functional (no DB)" | Bundled LiteLLM removed; limitation is now moot for the core stack |
| `TOKEN-SYNC` | "detects and warns on stale container tokens for `litellm`" | LiteLLM no longer a token-sync target |

Recommended action:
- `DASH-COUNT`: Remove the parenthetical "Likely silent LiteLLM retry" or close the entry since bundled LiteLLM is gone
- `PROVIDER-COUPLING`: Update to reflect that `litellm/config.yaml` is now app-owned, not a core coupling concern
- `LITELLM-UI`: Close or remove — the standalone LiteLLM UI situation is now an operator concern, not a core stack limitation
- `TOKEN-SYNC`: Remove the `litellm` reference from the token-sync entry; it was closed by Round 13 and the entry already says so

### Finding 16 — README "Next Docs" missing entries

`docs/testbed-install.md` is the guide for setting up LiteLLM, OpenWebUI, and
N8N as the app-owned integration targets for Round 43. It is absent from the
README. `docs/vps-deployment.md` is the baseline host setup guide (referenced
by `docs/subumbra-install.md` as a prerequisite) but is not in the README.
`docs/provider-catalog.md` documents the 13+ supported providers.

`docs/council-memory.md` and `docs/project-memory.md` are internal fresh-session
anchors and should NOT appear in the user-facing README.

Recommended additions to "Next Docs":
```markdown
- [docs/vps-deployment.md](docs/vps-deployment.md)
- [docs/provider-catalog.md](docs/provider-catalog.md)
- [docs/testbed-install.md](docs/testbed-install.md)
```

### Confirmed correct items (no change needed)

- `.env.example`: `SUBUMBRA_TOKEN_LITELLM` already removed (`grep` returns no match)
- `bootstrap/subumbra-bootstrap.py`: no `SUBUMBRA_TOKEN_LITELLM` references
- `post-bootstrap.sh`: no `SUBUMBRA_TOKEN_LITELLM` references
- `litellm/config.yaml`: no `subumbra:` prefix in active model entries
- `docker-compose.yml`: `litellm` service fully absent
- `scripts/council/` harness scripts: all present and correct
- `scripts/subumbra-expire-adapter.sh`: still needed, not a deletion candidate
- `CLAUDE.md:95`: subumbra-proxy correctly shown on internal network (it's on both — dual-homed by design)
- `docs/adapter-contract.md`, `docs/standalone-litellm.md`, `docs/subumbra-install.md`,
  `docs/subumbra-testing.md`, `docs/operator-guide.md`: all current, no changes needed

---

## Operator Directive Compliance Check

| Directive | Proposal Coverage | Notes |
|-----------|------------------|-------|
| No council docs in git | Change I — gitignore + `git rm --cached` | ✓ Correct. Must be `/council/` (anchored) not `council/` |
| Delete obsolete scripts | Change J — `test-check.sh` | ✓ Confirmed obsolete. No other obsolete root/scripts found |
| No archive retention needed | Covered by Change I | Council/closed/ files untracked after `git rm --cached -r council/` |
| Round 41.7 closed | Change G / K | ✓ Confirmed open at lines 151, 175, 184 |
| Round 42-operator-hardening closed | Change K | ✓ Confirmed at lines 182–193 as future work |
| README must align | Change L + Finding 15 | ⚠ Proposal missed absolute path bug — critical addition |

---

## Recommendations for the Approved Plan

**Must add to the plan:**

1. **README absolute path fix** (not in Claude's proposal): Change all 9 doc
   links from `/home/eric/git/Subumbra/docs/...` to `docs/...` relative paths.
   Affects lines 60, 66, 72, 100, 101, 102, 103, 104, 105 of `README.md`.

2. **Known Limitations cleanup** (not in proposal): Remove or update the four
   stale bundled-LiteLLM references in PROJECT_STATUS.md Known Limitations:
   `DASH-COUNT`, `PROVIDER-COUPLING`, `LITELLM-UI`, `TOKEN-SYNC`.

3. **CLAUDE.md Build Order step 5**: Replace `litellm/custom_callbacks.py
   (LiteLLM integration)` with `subumbra-proxy/app.py (transparent sidecar)`.

**Endorsements from Claude's proposal:**

All Changes A–K are verified and correct. The gitignore anchor fix (I) and
test-check.sh deletion (J) are confirmed. The PROJECT_STATUS.md rewrite scope
(G + K) is correct — lines 151, 175, 184 (Round 41.7), and lines 182–193
(Round 42 operator hardening) are all confirmed stale.

**Open questions from the proposal:**

- Q1 (Build Order section): Fix it — step 5 is actively wrong, not just stale.
- Q2 (Known Limitations pruning): Yes — prune the four identified entries.
- Q3 ("POC" language): Agree this belongs in Round 43 kickoff, not here.
