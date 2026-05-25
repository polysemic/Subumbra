# Subumbra SSH Guide

This guide covers daily-use SSH with Subumbra after the stack is already running. It assumes:

- `subumbra-agent` is enabled in your Compose stack
- you have an SSH key record such as `github_vps_test`
- you open Subumbra sessions before using the key

For initial install, start with [subumbra-install.md](subumbra-install.md). For GitHub-specific setup, see [docs/apps/github/install.md](apps/github/install.md).

## Socket path

Subumbra now uses a user-scoped runtime socket:

```bash
export SSH_AUTH_SOCK="${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock"
```

Important:

- this is the **host-side** path you export in your shell
- inside the container, the agent still listens on `/run/subumbra/ssh-agent.sock`
- `XDG_RUNTIME_DIR` must exist, so run Subumbra as a regular logged-in user

On the standard Subumbra VPS, the operator user is UID/GID `1000`, and `docker-compose.yml` pins `subumbra-agent` to `user: "1000:1000"`. If your system uses a different UID/GID, adjust that Compose value to match your operator account.

### How to find your host UID and GID

Run the following commands on your host server to find the correct numbers:
```bash
id -u  # prints your current User ID (UID)
id -g  # prints your current Group ID (GID)
```

### Security Architecture: UID/GID matching & isolation

For the seamless daily operation of SSH tools like `git`, `rsync`, and `ssh`, **the `subumbra-agent` container's UID/GID must match the exact host UID/GID of the user executing the SSH or Git commands.**

#### Why? (Unix Socket Permissions)
An SSH agent communicates via a Unix domain socket file (`ssh-agent.sock`). To prevent unauthorized local processes from accessing your keys, Unix domain sockets are protected by standard filesystem permissions. When the `subumbra-agent` creates the socket, only processes owned by the **exact same UID** can read or write to it. If you ran the agent container under a different user (e.g. a separate `subumbra` system account) and tried to run `git clone` or `ssh` from your normal account, the client would get a `Permission denied` error.

#### Best Practice for High-Security Deployments
If you are highly security-conscious and want maximum local isolation, **do not share your primary personal user account with Subumbra**. Instead:

1. **Create a dedicated host user account** (e.g., `subumbra-ops`):
   ```bash
   sudo adduser subumbra-ops
   ```
2. **Log in to that account** to run your deployments, automation scripts, and Git workflows.
3. Find that user's UID and GID (`id -u` / `id -g`).
4. Pin the `subumbra-agent` container in `docker-compose.yml` to that exact UID/GID (e.g., `user: "1001:1001"`).
5. All SSH-gated processes (like backup cron jobs or Git-sync workflows) will run cleanly inside that isolated user's session.


## SSH config

Start with a host-scoped block, not `Host *`:

```sshconfig
Match host github.com
    IdentityAgent ${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock
    IdentitiesOnly no
```

Repeat that pattern for other SSH targets you trust with this key. Restricted
SSH keys rely on native OpenSSH destination binding, so keep the SSH client on
OpenSSH 8.9 or newer when you opt into host restrictions.

Important: if you force `IdentitiesOnly yes`, OpenSSH expects an explicit `IdentityFile` and may skip agent-backed keys. Keep `IdentitiesOnly no` unless you have a very specific reason to constrain the client differently.

## Opening a session

Subumbra is locked by default. Before SSH use, open a scoped session:

```bash
./bootstrap.sh --session start --ttl 8h --adapters sshtest --keys github_vps_test
```

Recommended daily-use pattern:

- `--ttl 8h` for a normal workday
- `--adapters sshtest` or your specific SSH adapter
- `--keys <ssh_key_id>` for the minimum key scope you need

Check status with:

```bash
./bootstrap.sh --session status
```

Close access when you are done:

```bash
./bootstrap.sh --session end --all
```

## Verifying the agent

List the SSH public keys the agent is serving:

```bash
SSH_AUTH_SOCK="${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock" ssh-add -L
```

If a matching session is open, the configured Subumbra SSH key should appear in that output.

## Exporting and Authorizing the Public Key

Subumbra-managed private keys are kept secure inside the Cloudflare vault. To use them for authentication, you need to extract the **public key** and authorize it on your destination services (e.g. GitHub or a remote VPS).

### How to extract the public key

There are three ways to get the public key string:

#### Option A: From the active agent (Recommended)
If a Subumbra session is open and the agent is running, list all served public keys:
```bash
SSH_AUTH_SOCK="${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock" ssh-add -L
```
Copy the string starting with `ssh-ed25519 ...`.

#### Option B: Directly from `keys.json` on the server
You can extract the plain-text public key from your server's data volume using `jq`:
```bash
# Replace 'verify_vps_key' with your actual key ID
jq -r '.keys.verify_vps_key.public_key' /opt/subumbra/data/keys.json
```

#### Option C: From the dashboard UI
Open the read-only dashboard at `http://127.0.0.1:6563`. Go to the keys listing to view and copy the public key.

---

### Authorizing the key on GitHub

To use the key for checking out repositories or deploying actions on GitHub:

1. **For a single repository (Best Practice - Deploy Key)**:
   - Go to your repository on **GitHub** -> **Settings** -> **Deploy keys**.
   - Click **Add deploy key**.
   - Paste your public key string into the **Key** field and give it a descriptive **Title** (e.g., `Subumbra VPS Deploy Key`).
   - If your server needs to push changes (e.g., tags or releases), check **Allow write access**.
   - Click **Add key**.
2. **For account-wide access**:
   - Go to **GitHub Settings** -> **SSH and GPG keys**.
   - Click **New SSH key**, paste the public key string, and save.

To ensure your SSH client trusts GitHub, make sure it is in your `known_hosts` (you can run `ssh-keyscan github.com >> ~/.ssh/known_hosts` if needed).

GitHub's published host keys are also available from:

```bash
curl -sS https://api.github.com/meta | jq '.ssh_keys'
```

---

### Authorizing the key on a Remote VPS / Target Server

To allow Subumbra to connect to another remote server via SSH (e.g. for backups, syncs, or deployment steps):

1. **Manual addition**:
   Append the public key string directly to the target user's `authorized_keys` file on the remote server:
   ```bash
   # Run this on the target remote server:
   mkdir -p ~/.ssh && chmod 700 ~/.ssh
   echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... verify_vps_key" >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```
2. **Piped addition from the host**:
   If a Subumbra session is active on your host, you can pipe the public key directly over SSH using password auth:
   ```bash
   SSH_AUTH_SOCK="${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock" ssh-add -L | ssh username@remote-vps-ip 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys'
   ```

To inspect a non-GitHub host's live SSH host keys before adding restrictions:

```bash
ssh-keyscan your-host.example.com
```

## Restricted destinations

SSH records may optionally carry `allow.hosts` restrictions. Subumbra resolves
the operator-facing hostnames or IP literals into SSH host-key fingerprints at
provision or policy-publish time, and enforcement at sign time is based on the
verified host key, not the literal hostname string.

Restricted keys require native OpenSSH destination binding:

- OpenSSH 8.9 or newer for the client that is talking to the agent
- a normal SSH handshake that reaches host-key verification before auth
- the Subumbra agent path, not a direct `curl` to `/t/<key_id>/ssh/sign`

If verified destination context is missing, a restricted key fails closed. In
practice OpenSSH will surface the usual `Permission denied (publickey)` while
the deny reason is recorded in Subumbra logs.

Unrestricted legacy keys remain unchanged.

## Day-2 SSH lifecycle

These commands are for an already-initialized deployment.

### Add a new generated SSH key

```bash
./bootstrap.sh --add-ssh-key verify_vps_key --adapters sshtest
```

Add a restricted key for one or more SSH destinations:

```bash
./bootstrap.sh --add-ssh-key verify_vps_key --adapters sshtest --allow-hosts github.com,127.0.0.1
```

### Rotate an existing generated SSH key

```bash
./bootstrap.sh --rotate-ssh-key verify_vps_key
```

Replace the allowed destination set during rotation:

```bash
./bootstrap.sh --rotate-ssh-key verify_vps_key --allow-hosts github.com
```

If `--allow-hosts` is omitted during rotate, the existing restriction is preserved.

### Manifest-owned SSH restrictions

Manifest-managed SSH records may also declare optional restrictions:

```yaml
keys:
  - key_id: github_vps_test
    type: ssh_key
    key_source: generated
    adapters: [sshtest]
    unique_vault: false
    allow:
      hosts:
        - github.com
```

After editing a manifest-owned SSH restriction, publish it with:

```bash
./bootstrap.sh --publish-policy github_vps_test
```

### Revoke an SSH key

```bash
./bootstrap.sh --revoke-ssh-key verify_vps_key
```

### Security note for add/rotate

`--add-ssh-key` and `--rotate-ssh-key` require Cloudflare operator credentials:

- `CF_API_TOKEN`
- `CF_ACCOUNT_ID`

Bootstrap temporarily reissues `SUBUMBRA_SETUP_TOKEN`, performs the SSH key operation against the Worker setup route, then deletes the setup token again. This keeps SSH lifecycle privileged without leaving a standing setup credential behind.

### Redeploying the Cloudflare Worker

If a `git pull` brings down a round that changes Cloudflare Worker code (`worker/src/worker.js`), `./bootstrap.sh --upgrade` will rebuild your local Docker images but will **not** push the new Worker code to Cloudflare. Follow `--upgrade` with:

```bash
./bootstrap.sh --deploy-worker
```

This redeploys the Worker bundle and re-injects the live `PROVIDER_REGISTRY_KV` binding. All existing Worker secrets and Durable Object state are preserved.

The symptom of missing this step on a round that changed Worker code: SSH sign requests (and other Worker-routed operations) return `HTTP 503 {"error":"worker not configured"}`.

If a round changes Worker secrets (rare — usually announced in the changelog), run a full `./bootstrap.sh` instead.

## Direct sign note

Restricted SSH keys are enforced only when the request comes through the agent
with verified destination binding. A direct POST to `/t/<key_id>/ssh/sign`
without agent-supplied binding is denied with `403 {"error":"host_required"}`
for restricted keys.

## Troubleshooting

### `XDG_RUNTIME_DIR` missing

Run the stack as a regular logged-in user. `bootstrap.sh` now fails clearly if it needs to recreate the stack and `XDG_RUNTIME_DIR` is unset.

### `ssh-add -L` does not show the key

Check:

```bash
echo "$SSH_AUTH_SOCK"
ls -l "${XDG_RUNTIME_DIR}/subumbra"
docker compose ps
docker logs subumbra-agent | tail -30
```

### Sign requests return `system_locked`

No matching Subumbra session is open for that adapter/key pair. Re-open a scoped session.

### GitHub works but another host does not

Verify your `authorized_keys` entry or deploy-key setup on the remote host. A successful sign only proves the agent path is working; the remote host must still trust the current public key.
