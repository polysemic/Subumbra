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

## SSH config

Start with a host-scoped block, not `Host *`:

```sshconfig
Match host github.com
    IdentityAgent ${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock
    IdentitiesOnly no
```

Repeat that pattern for other SSH targets you trust with this key.

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

## Day-2 SSH lifecycle

These commands are for an already-initialized deployment.

### Add a new generated SSH key

```bash
./bootstrap.sh --add-ssh-key verify_vps_key --adapters sshtest
```

### Rotate an existing generated SSH key

```bash
./bootstrap.sh --rotate-ssh-key verify_vps_key
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

## Match exec note

Subumbra supports a terminal-first workflow today. If you want synchronous shell-side checks in the future, OpenSSH `Match exec` blocks until the helper exits, which makes it viable for session-open ergonomics. This round does not ship a helper script for that pattern.

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
