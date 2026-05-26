# Subumbra SSH in Automated Pipelines

This guide covers using Subumbra-managed SSH keys from CI/CD systems and other
non-interactive automation. Read [ssh-guide.md](ssh-guide.md) first for daily
manual use.

## Architecture choices

Subumbra exposes SSH signing through a Unix socket (`$SSH_AUTH_SOCK`) served by
the `subumbra-agent` container. That socket is a host-scoped file, not a network
service, so a pipeline process must either:

**Option A — Self-hosted runner on the same machine** (recommended)

The pipeline agent runs on the same VPS as the Subumbra stack. It shares the
socket path directly. This is the simplest and most secure arrangement and works
identically for GitHub Actions, GitLab CI, Bitbucket Pipelines, and Jenkins.

**Option B — Remote runner via Cloudflare Tunnel**

For cloud-hosted runners (GitHub-hosted Actions, GitLab SaaS shared runners,
etc.), you must expose `subumbra-proxy` via a Cloudflare Tunnel. The proxy
accepts HTTP requests from the internet and forwards them to the Worker. See
[cloudflare-setup.md](cloudflare-setup.md) for tunnel setup.

When using Option B, the remote runner calls the proxy's `/t/<key_id>/ssh/sign`
HTTP endpoint with an adapter token instead of speaking the SSH agent protocol.
This requires a custom SSH wrapper or ProxyCommand; that tooling is not currently
bundled with Subumbra. Option A is strongly preferred.

---

## Option A: Self-hosted runner

### Prerequisites

- `subumbra-agent` is running and healthy
- the runner process user has the same UID as `subumbra-agent` (see
  [ssh-guide.md § UID/GID matching](ssh-guide.md#security-architecture-uidgid-matching--isolation))
- the runner user's shell can reach the socket at
  `${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock`

### Session management for automation

For CI, open a scoped session before any job that needs SSH, and close it when
the job finishes. Use short TTLs and tight key scoping:

```bash
# Open a session scoped to this pipeline run
./bootstrap.sh --session start \
  --ttl 30m \
  --adapters ci_runner \
  --keys github_deploy_key \
  --max-sign-ops 20
```

Close the session in a cleanup step regardless of job success or failure:

```bash
./bootstrap.sh --session end --all
```

`--max-sign-ops 20` is a hard cap on signatures for the session. A denied sign
request (wrong host, locked system, expired session) does not consume the quota;
the counter only increments when a signature is actually produced.

### GitHub Actions — self-hosted runner

Add the runner to the same machine as your Subumbra stack following the standard
GitHub self-hosted runner install. Then use this job template:

```yaml
jobs:
  deploy:
    runs-on: self-hosted
    steps:
      - name: Open Subumbra session
        working-directory: /opt/subumbra     # path to your Subumbra checkout
        run: |
          ./bootstrap.sh --session start \
            --ttl 30m \
            --adapters ci_runner \
            --keys github_deploy_key \
            --max-sign-ops 20

      - name: Configure SSH agent
        run: |
          echo "SSH_AUTH_SOCK=${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock" >> "$GITHUB_ENV"

      - name: Verify key is visible
        run: ssh-add -L

      - name: Checkout private repo via SSH
        uses: actions/checkout@v4
        with:
          ssh-key: ""          # leave blank; agent handles auth
          repository: org/private-repo

      - name: Your deployment steps here
        run: ...

      - name: Close Subumbra session
        if: always()           # run even on failure
        working-directory: /opt/subumbra
        run: ./bootstrap.sh --session end --all
```

Add the Subumbra public key as a deploy key on any repository the runner needs to
reach. See [apps/github/install.md](apps/github/install.md) for how to extract
the public key and add it to GitHub.

### GitLab CI — self-hosted runner

Register a GitLab runner on the same VPS (shell or Docker executor). For the
shell executor:

```yaml
# .gitlab-ci.yml
deploy:
  tags:
    - self-hosted-subumbra    # tag matching your runner registration
  before_script:
    - cd /opt/subumbra && ./bootstrap.sh --session start
        --ttl 30m --adapters ci_runner --keys gitlab_deploy_key --max-sign-ops 20
    - export SSH_AUTH_SOCK="${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock"
    - ssh-add -L
  script:
    - git clone git@gitlab.com:org/private-repo.git
    - # ... deployment steps
  after_script:
    - cd /opt/subumbra && ./bootstrap.sh --session end --all
```

Add the Subumbra public key as a deploy key on the GitLab project:

1. Open the project → **Settings** → **Repository** → **Deploy keys**
2. Paste the public key (extracted via `ssh-add -L` with an open session)
3. Enable **Write access** only if the runner needs to push

For GitLab host-key trust:

```bash
ssh-keyscan gitlab.com >> ~/.ssh/known_hosts
```

GitLab's current host keys are also published at
`https://docs.gitlab.com/ee/user/gitlab_com/index.html#ssh-host-keys-fingerprints`.

### Bitbucket Pipelines / Jenkins / other

The same pattern applies for any CI system that supports self-hosted agents:

1. Open a scoped Subumbra session in a pre-step or setup stage
2. Export `SSH_AUTH_SOCK` to point at the Subumbra socket
3. Run git/ssh operations as normal
4. Close the session in a post/cleanup step with `if: always` semantics

For Bitbucket: add the public key under **Repository settings → Access keys**.
For any host-based automation (cron, systemd timer): wrap the script in a session
open/close and export `SSH_AUTH_SOCK` in the script header.

---

## Session hygiene for pipelines

| Concern | Recommendation |
|---------|----------------|
| TTL | Match the expected job duration plus a small buffer (e.g. `--ttl 30m` for a 15-minute job). Avoid long-lived sessions for automation. |
| Key scope | `--keys <key_id>` to the minimum set the job needs. Never scope to all keys. |
| Adapter | Use a dedicated adapter per CI system (e.g. `ci_runner`, `gitlab_runner`). This makes audit logs readable and lets you revoke CI access without affecting human sessions. |
| Sign quota | Set `--max-sign-ops` to a small multiple of the expected number of SSH auth events per job. If a job should clone 3 repos, cap at 10-20 signs. |
| Cleanup | Always close the session in a step marked `if: always` / `when: always` so it closes even if the job fails. |
| Orphaned sessions | Sessions have TTLs and expire automatically, but closing early reduces the window if a runner is compromised. |

---

## Restricted keys in CI

If your SSH key has `allow.hosts` restrictions, the self-hosted runner path works
unchanged — the normal OpenSSH SSH handshake provides the required verified
destination binding automatically. No special configuration is needed.

The restriction is enforced at the Worker boundary: if the runner attempts to SSH
to a host not in the `allow.hosts` list, the sign request is denied
(`host_not_allowed`, 403) and the SSH client receives `Permission denied
(publickey)`. The denial is logged in Subumbra with the verified host fingerprint.

---

## Audit trail for pipeline activity

Query SSH audit rows scoped to CI activity after a run:

```bash
docker compose exec -T subumbra-keys \
  curl -sS "http://127.0.0.1:9090/audit?endpoint=ssh_sign" \
  -H "X-Subumbra-Token: ${SUBUMBRA_TOKEN_UI}"
```

Each row includes the adapter ID, key ID, timestamp, and (for restricted keys)
the verified host fingerprint. Using a dedicated CI adapter makes it
straightforward to filter for pipeline-only activity.

---

## Troubleshooting

### `Permission denied (publickey)` in CI

Check in this order:

1. Is a session open? `./bootstrap.sh --session status`
2. Is the key visible? `SSH_AUTH_SOCK=... ssh-add -L`
3. Is the public key added to the remote service (deploy key / authorized_keys)?
4. For restricted keys: is the destination in `allow.hosts`?

### Session not found / `system_locked`

The session TTL expired or was never opened. Check that the `before_script` /
pre-step ran successfully. Review `docker logs subumbra-agent` for details.

### UID mismatch — `connect to agent: Permission denied`

The runner process is running as a different UID than the `subumbra-agent`
container. Check `id -u` in the runner step and compare it to the `user:` field
in `docker-compose.yml` for the `subumbra-agent` service.
