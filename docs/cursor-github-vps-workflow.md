# Cursor → GitHub → VPS Workflow

*Practical cheat sheet for developing Subumbra locally, pushing to GitHub, and
testing on the VPS without losing track of what branch or commit is under test.*

This workflow is separate from the council prompt flow. It is the practical
day-to-day path for:

- local editing in Cursor
- pushing changes to GitHub
- pulling the exact branch on the VPS
- running verification there
- reporting findings back into the local/Cursor workflow

---

## 1. Core Rule

Treat each layer like this:

- **Cursor/local** = where code changes happen
- **GitHub branch** = handoff/source of truth
- **VPS** = pull + run + verify

Do not use the VPS as the normal place to edit source files.

---

## 2. Branch Strategy

Use one branch per round or test effort.

Examples:

- `round-40-broader-decoupling-security-hardening`
- `round-41-real-app-validation`
- `round-42-vps-conflict-checks`

Keep:

- `main` = stable / known-good
- `round-*` branch = active work under test

Avoid using one long-lived generic `dev` branch if you can. Round branches make
it much easier to answer:

- what code is on the VPS right now?
- what exactly did the verifier test?
- what commit passed?

---

## 3. Local / Cursor Flow

Start from `main`:

```bash
git checkout main
git pull --ff-only
```

Create the round branch:

```bash
git checkout -b round-40-broader-decoupling-security-hardening
```

Do the work in Cursor, then commit:

```bash
git status
git add .
git commit -m "Round 40: initial implementation"
```

Push the branch:

```bash
git push -u origin round-40-broader-decoupling-security-hardening
```

Useful quick checks:

```bash
git branch --show-current
git rev-parse --short HEAD
git status
```

---

## 4. VPS Pull-And-Test Flow

SSH into the VPS and go to the repo:

```bash
ssh subumbra
cd /opt/subumbra
```

Fetch the latest remote state:

```bash
git fetch origin
```

Switch to the round branch:

```bash
git checkout round-40-broader-decoupling-security-hardening
git pull --ff-only
```

Confirm what is under test:

```bash
git branch --show-current
git rev-parse --short HEAD
git status
```

Expected:

- correct round branch
- correct commit SHA
- clean working tree

---

## 5. Rebuild / Restart Guidance

After pulling the branch, choose the lightest correct action.

### Docs only

No runtime action needed.

### Config / mounted file changes

Often enough:

```bash
docker compose up -d --force-recreate
```

### Image-built service code changes

Usually:

```bash
docker compose up -d --build --force-recreate
```

### Bootstrap / token / env changes

Use the project’s documented bootstrap and recreate flow:

```bash
docker compose --profile bootstrap run --rm -it bootstrap
./post-bootstrap.sh
docker compose up -d --force-recreate
```

If the round uses the council verification harness, follow the approved-plan
instructions for reset/build/clean-run instead.

---

## 6. Verification Flow

### Quick manual verification

Examples:

```bash
export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' .env)"
export CF_WORKER_URL="$(sed -n 's/^CF_WORKER_URL=//p' .env)"
docker compose ps
curl -sS "$CF_WORKER_URL/health"
curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://127.0.0.1:4000/health
curl -sS http://127.0.0.1:8090/health
curl -sS http://127.0.0.1:8080/api/status
```

### Official council verification

Preferred fresh-state proof:

```bash
./scripts/council/clean-run.sh --round round-40-broader-decoupling-security-hardening --agent codex
```

Fallback:

```bash
./scripts/council/reset.sh
AGENT=codex ./scripts/council/verify.sh round-40-broader-decoupling-security-hardening
```

If image-built services changed:

```bash
./scripts/council/reset.sh --build <services>
AGENT=codex ./scripts/council/verify.sh round-40-broader-decoupling-security-hardening
```

---

## 7. Reporting Back

When you or an LLM reports VPS results, always include:

- branch name
- commit SHA tested
- VPS path
- commands run
- result
- whether the finding requires a code fix, doc fix, or no change

Suggested template:

```text
Branch: round-40-broader-decoupling-security-hardening
Commit: abc1234
VPS path: /opt/subumbra
Commands:
- git fetch origin
- git checkout round-40-broader-decoupling-security-hardening
- git pull --ff-only
- docker compose up -d --build --force-recreate
- ./scripts/council/verify.sh ...
Result:
- PASS / FAIL
Findings:
- ...
```

This makes it much easier to keep Cursor, GitHub, and the VPS aligned.

---

## 8. Fix Loop

If verification finds a bug:

1. go back to Cursor/local
2. fix the code there
3. commit
4. push to the same round branch
5. pull the updated branch on the VPS
6. rerun the required checks

Example:

```bash
git add .
git commit -m "Round 40: fix sidecar host validation"
git push
```

Then on VPS:

```bash
git pull --ff-only
docker compose up -d --build --force-recreate
```

Repeat until green.

---

## 9. Merge To Main

Only merge after the branch passes the required local/VPS/council checks.

Local:

```bash
git checkout main
git pull --ff-only
git merge --ff-only round-40-broader-decoupling-security-hardening
git push origin main
```

Optionally update the VPS back to `main` after merge:

```bash
ssh subumbra
cd /opt/subumbra
git checkout main
git pull --ff-only
```

---

## 10. Things To Avoid

- editing source directly on the VPS as normal practice
- testing uncommitted local changes and then forgetting what the VPS actually ran
- using one long-lived `dev` branch for many unrelated rounds
- merging to `main` before the VPS has tested the actual branch commit
- letting multiple agents make uncontrolled competing edits on different copies
  of the same round work

---

## 11. Recommended Division Of Labor

### You

- create branches
- review diffs
- decide what gets committed and pushed
- decide when a branch is ready to merge

### Implementing LLM

- helps write code/docs locally in Cursor workflow
- works on the active round branch

### Verifying LLMs

- SSH into VPS
- pull the branch under test
- run manual checks and/or council verification scripts
- report findings with branch + SHA

Best practice:

- one branch owns the round implementation
- verifiers test and report
- fixes go back through the implementation branch

---

## 12. Tired-Brain Checklist

Before you test on the VPS, ask:

1. What branch am I on locally?
2. Did I commit my changes?
3. Did I push them?
4. What commit SHA do I expect the VPS to test?
5. Did the VPS pull that exact branch and SHA?
6. Did I rebuild/recreate if runtime code changed?
7. Did I write down what actually passed or failed?

If you can answer those seven questions, you are probably in a good state.
