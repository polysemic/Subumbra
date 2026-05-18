# Opt-in Git Hooks - Security Integration Guide

This folder contains optional Git hook templates for contributors and operators
who want local security checks before committing or pushing changes.

The hooks are opt-in. They are not installed automatically for clones or forks.

---

## What Is Included

- [`pre-commit`](./pre-commit): runs on `git commit`, blocks local-only
  `council/` files, blocks non-canonical `scripts/council/` helpers, and runs
  `gitleaks protect --staged`.
- [`pre-push`](./pre-push): runs on `git push` and scans only the commit range
  being pushed with Gitleaks.
- Optional full pre-push mode: set `SUBUMBRA_GITHOOK_FULL_SCAN=1` to also run
  Bandit, pip-audit, and Trivy before pushing.
- Shannon is intentionally excluded from Git hooks because it is an interactive
  adversarial scanner and belongs in explicit security-test runs.

---

## Activation Guide

Install both hooks:

```bash
cp scripts/git-hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push

cp scripts/git-hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

Run the heavier pre-push suite for one push:

```bash
SUBUMBRA_GITHOOK_FULL_SCAN=1 git push
```

Git hooks live under `.git/hooks/` and do not affect other clones or forks
unless those users install them too.

---

## Tool Installation Reference

The Gitleaks checks use a native `gitleaks` binary when available and fall back
to Docker when Docker is available.

The optional full pre-push suite uses the repository security scripts under
`scripts/security/`. Those scripts may require native tools or Python packages
depending on your environment.

### 1. Gitleaks (Secret Scanner)

- Native binary: follow the [Gitleaks installation guide](https://github.com/gitleaks/gitleaks#installing).
- Docker fallback: the hooks run `zricethezav/gitleaks:latest` if native
  Gitleaks is unavailable.
- Config source: both hooks pass
  `scripts/security/gitleaks/.gitleaks.toml`, matching GitHub Actions and the
  repo security scripts.

### 2. Bandit (Python SAST)

Install Bandit globally or within your active development virtual environment:

```bash
pip install bandit
```

### 3. Pip-Audit (Dependency Vulnerabilities)

Install pip-audit via pip:

```bash
pip install pip-audit
```

### 4. Trivy (System Vulnerabilities)

Follow the [Trivy installation guide](https://aquasecurity.github.io/trivy/latest/getting-started/installation/)
to install it through your system package manager.

---

## Customizing Rules and False Positives

- Secrets: adjust allowlist behavior in
  `scripts/security/gitleaks/.gitleaks.toml`. If you use fingerprint-based
  ignores, keep `.gitleaksignore` at the repo root because Gitleaks looks for it
  there.
- SAST and CVEs: configure scan settings in the individual runner scripts under
  [`scripts/security/`](../security/).

## Expected Runtime

- `pre-commit`: usually fast because it scans staged changes only.
- default `pre-push`: usually fast because it scans only the pushed commit
  range with Gitleaks.
- `SUBUMBRA_GITHOOK_FULL_SCAN=1 git push`: slower by design; it can run full
  dependency and filesystem scans before the push is allowed.
