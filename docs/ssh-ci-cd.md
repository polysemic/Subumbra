# Subumbra SSH in Automated Pipelines & CI/CD

This guide covers using Subumbra-managed SSH keys from self-hosted CI/CD systems and non-interactive automation. 

---

## 💡 Choosing Your Workflow

Subumbra supports two pipeline architectures depending on your team size and security profile:

| Model | Setup Complexity | Security Profile | Ideal For |
|---|---|---|---|
| **1. Operator-Gate Flow (Recommended)** | 🟢 Trivial (2 mins) | 🛡️ **Maximum** (Zero static secrets on GitHub, fully locked down by default) | Single developers, small teams, personal repos, highly sensitive environments |
| **2. Autonomous Flow** | 🟡 Moderate (10 mins) | 🔒 **High** (Time-gated sessions, but requires Cloudflare credentials in GitHub Secrets) | Larger teams, standard corporate unattended pipelines |

---

## 🟢 Model 1: Operator-Gate Workflow (Recommended)

In the **Operator-Gate** workflow, your pipeline does **not** create sessions autonomously. Instead, the pipeline expects an active session already authorized manually by you (the operator) on the host. 

### Why it is superior:
* **Zero GitHub Secrets**: You do not store your root Cloudflare API tokens or account IDs inside third-party clouds (GitHub/GitLab).
* **Locked by Default**: If an attacker pushes a malicious commit out-of-band, the runner will fail securely at the checkout step because the Subumbra session is locked.

### Newcomer Quick Start (GitHub Actions)

Add this template directly to your repository's `.github/workflows/subumbra-ssh-test.yml` file:

```yaml
name: Subumbra SSH Deployment

on:
  push:
    branches: [main]

permissions:
  # Standard hardening: Force GITHUB_TOKEN to be strictly read-only
  contents: read

jobs:
  deploy:
    runs-on: [self-hosted, linux]
    env:
      # Direct runner to use Subumbra's socket path on the host VPS
      SSH_AUTH_SOCK: /run/user/1000/subumbra/ssh-agent.sock
      # Standard hardening: Force git to ignore raw static private key files on disk
      GIT_SSH_COMMAND: ssh -o IdentitiesOnly=yes -i /dev/null

    steps:
      - name: Confirm key is visible
        run: ssh-add -L

      - name: Trust github.com host key
        run: |
          mkdir -p ~/.ssh
          ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null
          chmod 600 ~/.ssh/known_hosts

      - name: Checkout repo via secure Subumbra agent
        # Standard hardening: Pin actions to specific commit SHAs to prevent supply-chain spoofing
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          ssh-key: '' # Leave blank; Subumbra agent handles authentication

      - name: Run your build/deploy steps
        run: |
          echo "Deploying securely via Subumbra..."
```

### Daily Usage Loop:
1. **Start Your Coding Session**: When you sit down to develop and test, open a session on the VPS (e.g. for a 2-hour window):
   ```bash
   ssh subumbra-via-agent "cd /opt/subumbra && ./bootstrap.sh --session start --ttl 2h --consumers sshtest --keys github_vps_test"
   ```
2. **Push to Trigger**: Commit and run `git push`. The runner will pick up the job and successfully check out your repository.
3. **Automatic Expiry**: Once the 2-hour TTL runs out, the VPS agent automatically locks down again.

---

## 🟡 Model 2: Autonomous Workflow (Enterprise)

For unattended deployment pipelines that must trigger completely independently of an active developer, Subumbra can open and close sessions on-demand inside the workflow.

### Setup (GitHub Actions)

1. **Add Cloudflare Credentials to GitHub Secrets**:
   Go to your repository -> **Settings** -> **Secrets and variables** -> **Actions** -> **Repository secrets**, and add:
   * `CF_API_TOKEN`: Your Cloudflare API Token
   * `CF_ACCOUNT_ID`: Your Cloudflare Account ID

2. **Configure Your Workflow**:
   Use this template to open and close sessions dynamically:

```yaml
name: Subumbra Autonomous Deployment

on:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  deploy:
    runs-on: [self-hosted, linux]
    env:
      SSH_AUTH_SOCK: /run/user/1000/subumbra/ssh-agent.sock
      GIT_SSH_COMMAND: ssh -o IdentitiesOnly=yes -i /dev/null

    steps:
      - name: Open Subumbra session
        env:
          CF_API_TOKEN: ${{ secrets.CF_API_TOKEN }}
          CF_ACCOUNT_ID: ${{ secrets.CF_ACCOUNT_ID }}
        working-directory: /opt/subumbra     # path to your Subumbra checkout on VPS
        run: |
          SESSION_START_OUTPUT=$(./bootstrap.sh --session start \
            --ttl 30m \
            --consumers sshtest \
            --keys github_vps_test \
            --max-sign-ops 20)
          printf '%s\n' "$SESSION_START_OUTPUT"
          SESSION_ID=$(printf '%s\n' "$SESSION_START_OUTPUT" | awk '/Started session /{print $NF}')
          echo "SESSION_ID=$SESSION_ID" >> "$GITHUB_ENV"

      - name: Confirm key is visible
        run: ssh-add -L

      - name: Trust github.com host key
        run: |
          mkdir -p ~/.ssh
          ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null
          chmod 600 ~/.ssh/known_hosts

      - name: Checkout repo via secure Subumbra agent
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          ssh-key: ''

      - name: Your deployment steps
        run: ...

      - name: Close Subumbra session
        if: always() # CRITICAL: Close session even if build fails
        env:
          CF_API_TOKEN: ${{ secrets.CF_API_TOKEN }}
          CF_ACCOUNT_ID: ${{ secrets.CF_ACCOUNT_ID }}
        working-directory: /opt/subumbra
        run: ./bootstrap.sh --session end "$SESSION_ID"
```

In shared-runner CI, do not use `--session end --all` as the default cleanup
path. It can close unrelated operator sessions on the same deployment.

---

## 📑 GitLab CI — Self-Hosted Runner Patterns

### GitLab Model 1: Operator-Gate
```yaml
# .gitlab-ci.yml
variables:
  SSH_AUTH_SOCK: "${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock"
  GIT_SSH_COMMAND: "ssh -o IdentitiesOnly=yes -i /dev/null"

deploy:
  tags:
    - self-hosted-subumbra
  script:
    - ssh-add -L
    - git clone git@gitlab.com:org/private-repo.git
```

### GitLab Model 2: Autonomous (Uses CI Variables for Cloudflare Creds)
```yaml
# .gitlab-ci.yml
variables:
  SSH_AUTH_SOCK: "${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock"
  GIT_SSH_COMMAND: "ssh -o IdentitiesOnly=yes -i /dev/null"

deploy:
  tags:
    - self-hosted-subumbra
  before_script:
    # CF_API_TOKEN and CF_ACCOUNT_ID are injected as masked CI/CD variables
    - |
      SESSION_START_OUTPUT=$(cd /opt/subumbra && ./bootstrap.sh --session start \
        --ttl 30m --consumers ci_runner --keys gitlab_deploy_key --max-sign-ops 20)
      printf '%s\n' "$SESSION_START_OUTPUT"
      SESSION_ID=$(printf '%s\n' "$SESSION_START_OUTPUT" | awk '/Started session /{print $NF}')
      printf '%s\n' "$SESSION_ID" > /tmp/subumbra-session-id
  script:
    - ssh-add -L
    - git clone git@gitlab.com:org/private-repo.git
  after_script:
    - cd /opt/subumbra && ./bootstrap.sh --session end "$(cat /tmp/subumbra-session-id)"
```

---

## 🔒 Session Hygiene & Security Hardening

| Feature | Best Practice | Rationale |
|---|---|---|
| **Explicit Permissions** | Always declare `permissions: contents: read` | Standard repository hygiene; prevents the workflow runner from writing back to the repo unless explicitly required. |
| **Commit SHA Pinning** | Always use `uses: actions/checkout@<sha>` | Protects your pipeline from supply-chain attacks if a third-party action tag is compromised. |
| **Short TTLs** | Match job length + small buffer (e.g., `--ttl 30m`) | Reduces the threat window if a runner environment is breached. |
| **Sign Quotas** | Add `--max-sign-ops 20` | Caps the number of successful cryptographic signatures generated per session. |
| **Bypass Static Keys** | Enforce `IdentitiesOnly=yes -i /dev/null` | Prevents SSH from falling back to raw private keys left on the runner's disk. |

---

## 🛠️ Troubleshooting CI/CD Failures

### 1. `Permission denied (publickey)`
* **Active session?** Run `./bootstrap.sh --session status` on the VPS to confirm an active session exists.
* **Key served?** Run `SSH_AUTH_SOCK=... ssh-add -L` on the VPS. The public key identifier must appear.
* **Deploy Key authorized?** Make sure the public key is registered in **GitHub Settings** -> **Deploy Keys** for that repository.
* **Destination allowed?** If using restricted keys, verify that the remote host key matches your `allow.hosts` settings.

### 2. UID Mismatch — `connect to agent: Permission denied`
The runner process executes under a different User ID than the `subumbra-agent` container. 
* Compare `id -u` in a runner step to the `user:` mapping in `docker-compose.yml` for the `subumbra-agent` service. Both must match.
