# SSH Agent Access

This page is not a ranking. Teleport, Boundary, Vault SSH, Akeyless, 1Password, and GitHub deploy keys solve different SSH access problems. Subumbra's SSH path is narrower: it keeps SSH private keys in Subumbra custody and exposes sign operations through the same session, policy, and audit model used for API keys.

## Visual Matrix

| Capability | Subumbra | Teleport | HashiCorp Vault SSH | HashiCorp Boundary | Akeyless SRA | 1Password SSH Agent | GitHub deploy keys |
|------------|----------|----------|---------------------|--------------------|--------------|---------------------|--------------------|
| SSH private key custody | ✓ Yes | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ✓ Yes | ◑ Partial |
| Sign-only workflow | ✓ Yes | ◑ Partial | ◑ Partial | ✗ No | ◑ Partial | ✓ Yes | ✗ No |
| SSH certificates | ✗ No | ✓ Yes | ✓ Yes | ✗ No | ✓ Yes | ✗ No | ✗ No |
| Remote access/session platform | ✗ No | ✓ Yes | ✗ No | ✓ Yes | ✓ Yes | ✗ No | ✗ No |
| Private key exportability in normal workflow | ✗ No | ◑ Partial | ◑ Partial | — N/A | ✗ No | ✗ No | ✓ Yes |
| Host binding / host fingerprint restriction | ✓ Yes | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial |
| Per-sign approval | ✓ Yes | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ✓ Yes | ✗ No |
| Per-session quota / max sign operations | ✓ Yes | ◑ Partial | ✗ No | ◑ Partial | ◑ Partial | ✗ No | ✗ No |
| Git push/deploy workflow fit | ✓ Yes | ✓ Yes | ◑ Partial | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes |
| Same policy/session/audit model as API keys | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No |
| Team RBAC/SSO | ⊙ Planned | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Hosted or managed option | ⊙ Planned | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |

## Reality Notes

- Teleport and Boundary are broader access platforms, not direct signing-agent equivalents. They bring infrastructure access workflows, identity, and session controls that Subumbra does not currently provide. [src:teleport-docs] [src:boundary-docs]
- Subumbra does not currently provide Teleport/Boundary-level access platform maturity.
- 1Password's SSH agent is a close conceptual neighbor for sign-only local SSH use: it documents that SSH clients cannot read the private key and requests require user authorization while 1Password is unlocked. [src:1password-ssh-agent]
- **Vault SSH per-sign approval: ◑ Partial** — HashiCorp Vault's Control Groups feature can require additional human authorization before an SSH certificate is issued, but Control Groups is an Enterprise-only feature. OSS Vault signing is policy-gated but not per-request human-approved. [src:vault-docs]
- **Vault SSH per-session quota: ✗ No** — Vault's resource quotas cover requests-per-second and total lease counts, not per-session SSH signing operation counters. [src:vault-docs]
- **Akeyless SRA per-session quota: ◑ Partial** — The Akeyless CLI changelog references transaction rate limiting thresholds, but official SRA documentation does not clearly describe per-session signing quotas. Marked partial pending clearer source coverage. [src:akeyless-docs]
- Subumbra's difference is API-key and SSH-key custody under one split-trust/session/audit model. [src:subumbra-ssh] [src:subumbra-session]

## Where Others Are Stronger

- Teleport and Boundary cover remote access architecture, identity-aware access, and fleet patterns far beyond Subumbra's current SSH agent.
- Vault and Akeyless have mature secrets/access platforms with SSH certificate or remote access workflows.
- 1Password has polished desktop UX and broad user adoption for SSH key custody.

## Where Subumbra Is Different

- Subumbra's SSH private key path is sign-only inside the Worker/Vault custody boundary, with metadata-only key listings exposed to the local agent. [src:subumbra-ssh]
- Restricted SSH keys can be bound to resolved host-key fingerprints, so the sign path can fail closed when the verified destination is missing or not allowed. [src:subumbra-manifest] [src:subumbra-ssh]
- Janus can require approval for sign operations, and session quota counts signatures at the Subumbra signing boundary. [src:subumbra-janus] [src:subumbra-ssh-quota]
- SSH and API key custody share one session, policy, and audit model — a compromised session has the same blast-radius controls regardless of whether it is making provider API calls or SSH signing requests.

## Current Subumbra Gaps

- No SSH CA.
- No RSA/ECDSA/GPG/git-commit signing yet.
- No broad access-platform fleet model.
- Hosted access service and team RBAC/SSO are planned, not current.
- UI is still read-only for key lifecycle until hardened management API work exists.
