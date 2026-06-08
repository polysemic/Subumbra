# Secret Vaults And Secret Managers

This page is not a ranking. Vaults and secret managers are mature infrastructure products with deep administration, access-control, compliance, and integration stories. Subumbra is narrower and different in a specific way: it is designed so that provider plaintext never leaves the Cloudflare Durable Object custody boundary — not even to an authorized operator. Apps call providers through consumer tokens and a proxy; the key is used on their behalf but never returned to them.

## Visual Matrix

| Capability | Subumbra | HashiCorp Vault | Akeyless | Infisical | Doppler | 1Password Secrets Automation | AWS Secrets Manager | GCP Secret Manager | Azure Key Vault |
|------------|----------|-----------------|----------|-----------|---------|------------------------------|---------------------|--------------------|-----------------|
| Secret storage at rest | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Authorized plaintext retrieval path | ✗ No | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Plaintext key extractable by authorized operator | ✗ No | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| App/server receives plaintext secret in normal workflow | ✗ No | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes |
| App-facing config holds consumer token, not provider key | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No |
| Split custody / multi-party trust | ✓ Yes | ◑ Partial | ◑ Partial | ◑ Partial | ✗ No | ? Needs verification | ◑ Partial | ◑ Partial | ◑ Partial |
| Policy-hash AAD binding | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No |
| Per-call human approval gate | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No |
| Per-key isolated vault option | ✓ Yes | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial | ◑ Partial | ◑ Partial | ✓ Yes |
| Offline provider-secret rotation without changing app consumer tokens | ✓ Yes | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial |
| Dynamic short-lived secrets | ✗ No | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ◑ Partial | ◑ Partial | ◑ Partial | ✗ No |
| Enterprise RBAC/SSO/team controls | ⊙ Planned | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Hosted SaaS convenience | ⊙ Planned | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Secret scanning | ✗ No | ◑ Partial | ◑ Partial | ✓ Yes | ✗ No | ? Needs verification | ◑ Partial | ◑ Partial | ◑ Partial |
| Audit logging | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Cloud dependency / self-hosting model | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |

## Reality Notes

- Subumbra does not currently replace mature enterprise RBAC/SSO, team administration, hosted onboarding, compliance reporting, or broad integration catalogs. Both are planned.
- **"Authorized plaintext retrieval path: ✗ No"** — This is by design, not a gap. There is no Subumbra API call that returns a provider key in plaintext. The Durable Object decrypts the key inside its memory boundary and injects auth directly into the upstream provider request. The key never exits the custody boundary to any caller — not the proxy, not the app, not the operator. Vaults are designed to give you your secret when you need it; Subumbra is designed to use it for you while keeping it from you.
- **"Plaintext key extractable by authorized operator: ✗ No"** — Follows from the above. An operator with full CF access, full Docker access, and full host access still cannot retrieve provider plaintext in the normal custody path. The private RSA key lives non-extractably in the Durable Object; the ciphertext on disk is useless without it.
- **"Per-call human approval gate: ✓ Yes"** — Janus can hold individual proxy requests behind an operator approval step before the Worker submits them to the upstream provider. No vault product gates individual secret retrievals this way; access is granted at the policy level, not per-call. [src:subumbra-janus]
- Subumbra's current Cloudflare dependency is real: provider plaintext is usable inside the Cloudflare Worker/Durable Object boundary during proxied requests. [src:subumbra-claude]
- Accidental `.env` commit protection means app-facing configs should hold consumer tokens and proxy routes instead of provider plaintext; it does not mean all committed secrets are harmless.
- HashiCorp Vault, Akeyless, Infisical, Doppler, AWS Secrets Manager, GCP Secret Manager, Azure Key Vault, and 1Password all have mature secret storage/access stories. Several also support rotation, dynamic credentials, SSH access, or secret-scanning families. [src:vault-docs] [src:akeyless-docs] [src:infisical-docs] [src:doppler-docs] [src:aws-secrets-docs] [src:gcp-secret-manager] [src:azure-key-vault] [src:1password-connect]
- Subumbra's AAD binding is a repo-backed claim: V3 records use `subumbra:v3:<key_id>:<policy_hash>` so ciphertext cannot be replayed cleanly under a different policy hash. [src:subumbra-claude] [src:subumbra-worker]

## Where Others Are Stronger

- Mature vaults are designed to return plaintext secrets to authorized callers — that is their primary job. They have deep identity integrations, SSO, team lifecycle controls, audit export, compliance posture, hosted control planes, SDKs, and operations documentation to support this model.
- HashiCorp Vault, Akeyless, Infisical, and Doppler document dynamic or rotated secret workflows that Subumbra does not currently provide as a general database/cloud credential engine.
- Cloud-native secret managers integrate tightly with their own IAM, logging, replication, and platform services.
- If your use case requires an application to receive a plaintext secret at runtime (e.g. a database password, a signing certificate), Subumbra is not the right primary tool today.

## Where Subumbra Is Different

- Subumbra's model inverts the vault pattern: instead of giving an authorized caller their secret, it uses the secret for them. The provider key exits the custody boundary only as an auth header in a proxied HTTPS request, not as a value returned to any application or operator.
- The app receives a consumer token and a proxy URL. Its config never contains a provider key. A leaked config leaks a scoped token, not a provider credential. [src:subumbra-proxy]
- The provider key decrypt path is split across two systems that cannot decrypt alone: host-side storage holds ciphertext and a wrapped data-encryption key, while the Cloudflare Durable Object holds the RSA private key. [src:subumbra-claude] [src:subumbra-worker]
- Rotation can re-encrypt an existing provider secret against the on-disk public key without changing the app's consumer token or touching Cloudflare. [src:subumbra-rotation]
- Individual API calls can be held behind a Janus approval gate before the key is used — a control that no vault product applies at the per-call level. [src:subumbra-janus]

## Current Subumbra Gaps

- Enterprise RBAC/SSO and multi-user team controls are planned, not current.
- Hosted SaaS deployment option is planned, not current.
- No general-purpose dynamic database/cloud credential engine.
- No built-in secret scanning for repos, CI logs, or chat transcripts.
- Audit is useful for the current stack, but not a mature export/retention product yet.
