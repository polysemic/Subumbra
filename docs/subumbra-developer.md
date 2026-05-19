# Subumbra Developer & Contributor Guide

*This document is the deep-dive reference for developers, contributors, and self-hosted operators looking to extend Subumbra, write custom adapters, contribute to the core codebase, or verify deployment integrity.*

---

## 1. Branch and Contribution Strategy

Subumbra follows strict semantic versioning and a clean-history development model.

### Branch Conventions
* **`main`**: The stable, production-ready branch. All releases are tagged commits from `main`.
* **Feature Branches (`feature/<name>` or `fix/<name>`)**: All development work, bug fixes, and feature additions must happen in short-lived feature branches branched off `main`.

Avoid direct commits to `main` for complex upgrades. Instead, submit a Pull Request (PR) to facilitate peer review and allow automated integration testing to pass before merging.

---

## 2. Local → Server Deployment Workflow

### 1. Local Development (Where Code Changes Happen)

```bash
git checkout main
git pull --ff-only
git checkout -b feature/your-feature-name

# Make your changes, edit code, and write tests
git add <files>
git commit -m "feat: short description of your change"
git push -u origin feature/your-feature-name
```

Before submitting a Pull Request, ensure that:
* **Python tests and linting** are passing locally.
* **Workflows are verified** with no static analysis warnings (e.g. no CodeQL, Bandit, or dependency alerts).
* Your feature branch contains no uncommitted temporary files or local `.env` secrets.

### 2. Isolated Deployment Testing (Clean Run Harness)

Subumbra includes an automated integration testing harness under `scripts/council/` that simulates a completely fresh, zero-state installation from scratch to verify setup and bootstrap stability.

To run the full clean-state integration test suite:

```bash
# ⚠️ Precondition: Stop any active containers first
docker compose down -v

# Run the clean install integration suite
./scripts/council/clean-run.sh --build all
```

The harness does the following:
1. Provisions a clean, isolated Docker environment.
2. Builds the containers fresh from your current local source files.
3. Runs the complete `./bootstrap.sh` pipeline (generating mock API keys and deploying a test Worker).
4. Verifies transparent routing, secure vault isolation, and policy enforcement checks.
5. Outputs a detailed timestamped audit report.

---

## 3. Rebuild / Restart Decision Tree

When testing local edits on your development stack, use this reference to decide how to apply changes:

| What Changed | Action Required | Command |
|---|---|---|
| **Documentation Only** | None | N/A |
| **Mounted configuration, rules, or `.env` values** | Force Recreate | `docker compose up -d --force-recreate` |
| **Service code (`proxy`, `ui`, `keys`, `probe`)** | Rebuild + Recreate | `docker compose up -d --build --force-recreate` |
| **Bootstrap code, tokens, or core RSA key pairs** | Full Re-bootstrap | `./bootstrap.sh` |

---

## 4. Full Bootstrap Sequence

A full bootstrap is required for a new installation, after token expiration, or when modifying core encryption configurations:

```bash
# 1. Copy the example manifest and configure your keys
cp subumbra.example.yaml subumbra.yaml
nano subumbra.yaml

# 2. Rebuild the bootstrap container if bootstrap code changed
docker compose --profile bootstrap build bootstrap

# 3. Run the bootstrap runner
./bootstrap.sh

# 4. Spin up the runtime services
docker compose up -d --force-recreate
```

`./bootstrap.sh` runs `./scripts/subumbra-verify --preflight` before it reads
`.env.bootstrap` or prompts for secrets. Development branches and council
branches usually are not signed release tags, so the verifier warns rather than
failing by default when no signed annotated tag is present. To require a signed
tag in a release workflow, set `SUBUMBRA_REQUIRE_SIGNED_TAG=1`.

For local development only, you can bypass the bootstrap preflight:

```bash
SUBUMBRA_ALLOW_UNVERIFIED_SOURCE=I_ACCEPT_RISK ./bootstrap.sh
```

Use that override only when you intentionally changed local source. It disables
the pre-secret source check for the bootstrap path and prints a warning because
secrets entered during that run could be exposed if local source is compromised.

---

## 5. Day-2 Operational Commands

Subumbra's `./bootstrap.sh` host wrapper supports several direct CLI commands to facilitate targeted Day-2 administrative tasks without needing a full state teardown.

### 1. Adding a New Provider Key
To provision an additional provider key without impacting existing encrypted keys:
1. Open `subumbra.yaml` and configure/uncomment the new provider block.
2. Run the bootstrap wrapper:
   ```bash
   ./bootstrap.sh
   ```
   *The bootstrap script detects existing encrypted state, keeps current keys completely intact, and only provisions the new key.*

### 2. Rotating Provider Secrets (Offline)
To rotate a provider's underlying API secret without interacting with Cloudflare (uses the local public key to re-encrypt):
```bash
./bootstrap.sh --rotate
```
*You will be prompted to enter the new secret key. The target record in `keys.json` is updated atomically with a new AES-256-GCM data encryption key (DEK) wrapped by the active RSA public key.*

### 3. Modifying Adapter Access Rules
To add or remove an adapter's access to a specific key without re-encrypting the key:
* **Add Adapter**:
  ```bash
  ./bootstrap.sh --add-adapter <key_id> <adapter_id>
  ```
* **Revoke Adapter**:
  ```bash
  ./bootstrap.sh --revoke-adapter <key_id> <adapter_id>
  ```
* **Re-publish Policy Updates**:
  ```bash
  ./bootstrap.sh --publish-policy <key_id>
  ```

### 4. Direct Registry Synchronization
If you need to push local policies directly to the Cloudflare KV database without rebuilding or restarting:
```bash
./bootstrap.sh --push-registry
```

### 5. Revoking a Provider Key Entirely
To delete a key and permanently block access to it at both the local sidecar and Worker boundaries:
```bash
./bootstrap.sh --revoke-key <key_id>
```

---

## 6. Full Reset (Clean Install from Scratch)

To wipe all local development state and return the repository to a completely pristine, first-time-install state:

```bash
# 1. Stop all containers and delete named volumes
docker compose down --remove-orphans -v

# 2. Clean cached images and bootstrap caches
docker compose down --rmi all 2>/dev/null || true
docker rmi subumbra-bootstrap 2>/dev/null || true

# 3. Shred local credential and environment configuration files
rm -f .env .env.bootstrap subumbra.yaml
```

> [!NOTE]
> **Cloudflare state is NOT deleted by a local reset.** 
> The Cloudflare Worker script, Durable Object custody database, and KV namespace will remain intact on your Cloudflare account. However, running a new `./bootstrap.sh` after a reset will completely overwrite the KV namespace policies and generate fresh SQLite-backed vault instances, rendering previous encrypted ciphertexts safely unrecoverable.

---

## 7. Security Hardening and PR Checks

All code contributions must meet our strict repository safety rules. The following automated scans are executed on every pull request to protect the supply chain:

### 1. Token Permissions (`contents: read`)
All GitHub Actions workflows are restricted to read-only scopes. Workflows must never be granted global write permissions. If a job requires writing to Code Scanning (`security-events: write`), it must be declared explicitly at the **individual job level**.

### 2. Dependency Pinning
* **GitHub Actions**: Actions must be pinned to specific tags/commits.
* **Pip Packages**: All installations in Dockerfiles and workflows must use exact semantic version pinning (e.g. `bandit==1.7.8` or via `requirements.txt`).

### 3. Static Security Analysis (SAST)
Before pushing commits, run local static scans to ensure no secrets, plaintext keys, or high-risk standard library calls are included:
```bash
# Run Bandit on the Python codebase
bandit -r bootstrap/ subumbra-proxy/ ui/ -x tests/
```

### 4. Opt-in Pre-push and Pre-commit Hooks

To prevent accidental leaks before code leaves a workstation, Subumbra provides
opt-in Git hook templates under `scripts/git-hooks/`.

* **Default checks**: staged Gitleaks secret scanning on commit, plus
  commit-range Gitleaks scanning on push.
* **Optional full pre-push suite**: set `SUBUMBRA_GITHOOK_FULL_SCAN=1` to also
  run Bandit, pip-audit, and Trivy before pushing.
* **Setup**: see the opt-in guide in [scripts/git-hooks/README.md](../scripts/git-hooks/README.md).

---

## 8. Related Documentation

* **[Architecture Overview](../CLAUDE.md)**: Conceptual diagrams, design invariants, and threat mitigations.
* **[Adapter Contract](./adapter-contract.md)**: Specifications for building or connecting downstream clients to the transparent proxy.
* **[Testing Taxonomy](./subumbra-testing.md)**: Harness taxonomy, evidence capture, and diagnostic commands.
