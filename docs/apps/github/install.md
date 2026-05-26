# GitHub SSH with Subumbra

This guide shows how to use a Subumbra-managed SSH key with a private GitHub repository.

## Prerequisites

- the Subumbra stack is already running
- `subumbra-agent` is healthy
- you have a generated SSH key in Subumbra, for example `github_vps_test`
- your shell exports:

```bash
export SSH_AUTH_SOCK="${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock"
```

## 1. Open a scoped session

```bash
./bootstrap.sh --session start --ttl 8h --adapters sshtest --keys github_vps_test
```

## 2. Confirm the key is visible through the agent

```bash
SSH_AUTH_SOCK="${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock" ssh-add -L
```

You should see the public key line for `github_vps_test`.

## 3. Add the public key to GitHub

For a repo-scoped fixture, add the Subumbra public key as a deploy key on the target repository:

1. Open the repository on GitHub
2. Go to `Settings`
3. Open `Deploy keys`
4. Add the public key
5. Enable write access only if you want to test pushes

Use the public key printed during bootstrap, or extract it from `keys.json`.

## 4. Use a host-scoped SSH config block

```sshconfig
Match host github.com
    IdentityAgent ${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock
    IdentitiesOnly no
```

Do not use `Host *` for this path.

## 5. Test against the repo

Read-only test:

```bash
mkdir -p ~/.ssh
ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null
chmod 600 ~/.ssh/known_hosts
SSH_AUTH_SOCK="${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock" \
git ls-remote git@github.com:polysemic/Subumbra-SSH-Test.git
```

If you enabled write access for the deploy key, you can also test normal Git operations such as fetch and push against your own branch workflow.

## Notes

- GitHub still validates the remote public key independently; a successful Subumbra signature is necessary but not sufficient
- if a session closes, GitHub SSH auth will fail because Subumbra will stop signing
- if you rotate the Subumbra SSH key, update the GitHub deploy key to the new public key before expecting auth to succeed again
