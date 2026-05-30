# Secret Vaults And Secret Managers

This page is not a ranking. Vaults and secret managers are mature infrastructure products with deep administration, access-control, compliance, and integration stories. Subumbra is narrower: it is trying to keep provider plaintext out of normal app runtime while still allowing apps to call providers through adapter tokens.

## Visual Matrix

| Capability | Subumbra | HashiCorp Vault | Akeyless | Infisical | Doppler | 1Password Secrets Automation | AWS Secrets Manager | GCP Secret Manager | Azure Key Vault |
|------------|----------|-----------------|----------|-----------|---------|------------------------------|---------------------|--------------------|-----------------|
| Secret storage at rest | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Authorized plaintext retrieval path | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| App/server receives plaintext secret in normal workflow | ✗ No | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes |
| App-facing config holds adapter token, not provider key | ✓ Yes | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No | ✗ No |
| Split custody / multi-party trust | ✓ Yes | ◑ Partial | ◑ Partial | ? Needs verification | ? Needs verification | ? Needs verification | ◑ Partial | ◑ Partial | ◑ Partial |
| Policy-hash AAD binding | ✓ Yes | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification | ? Needs verification |
| Per-key isolated vault option | ✓ Yes | ◑ Partial | ? Needs verification | ? Needs verification | ? Needs verification | ◑ Partial | ◑ Partial | ◑ Partial | ✓ Yes |
| Offline provider-secret rotation without changing app adapter tokens | ✓ Yes | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ? Needs verification | ◑ Partial | ◑ Partial | ◑ Partial |
| Dynamic short-lived secrets | ✗ No | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ? Needs verification | ◑ Partial | ✗ No | ✗ No |
| Enterprise RBAC/SSO/team controls | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Hosted SaaS convenience | ✗ No | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Secret scanning | ✗ No | ◑ Partial | ? Needs verification | ✓ Yes | ? Needs verification | ? Needs verification | ◑ Partial | ◑ Partial | ◑ Partial |
| Audit logging | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |
| Cloud dependency / self-hosting model | ◑ Partial | ◑ Partial | ◑ Partial | ◑ Partial | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes | ✓ Yes |

## Reality Notes

- Subumbra does not currently replace mature enterprise RBAC/SSO, team administration, hosted onboarding, compliance reporting, or broad integration catalogs.
- Subumbra's current Cloudflare dependency is real: provider plaintext is usable inside a Cloudflare Worker/Durable Object boundary during proxied requests. [src:subumbra-claude]
- Accidental `.env` commit protection means app-facing configs should hold adapter tokens and proxy routes instead of provider plaintext; it does not mean all committed secrets are harmless.
- HashiCorp Vault, Akeyless, Infisical, Doppler, AWS Secrets Manager, GCP Secret Manager, Azure Key Vault, and 1Password all have mature secret storage/access stories. Several also support rotation, dynamic credentials, SSH access, or secret-scanning families. [src:vault-docs] [src:akeyless-docs] [src:infisical-docs] [src:doppler-docs] [src:aws-secrets-docs] [src:gcp-secret-manager] [src:azure-key-vault] [src:1password-connect]
- Subumbra's AAD binding is a repo-backed claim: V3 records use `subumbra:v3:<key_id>:<policy_hash>` so ciphertext cannot be replayed cleanly under a different policy hash. [src:subumbra-claude] [src:subumbra-worker]

## Where Others Are Stronger

- Mature vaults generally have deeper identity integrations, SSO, team lifecycle controls, audit export, compliance posture, hosted control planes, SDKs, and operations documentation.
- HashiCorp Vault, Akeyless, Infisical, and Doppler document dynamic or rotated secret workflows that Subumbra does not currently provide as a general database/cloud credential engine.
- Cloud-native secret managers integrate tightly with their own IAM, logging, replication, and platform services.

## Where Subumbra Is Different

- The app is meant to receive an adapter token, not the provider key. The transparent proxy resolves the adapter token to a scoped adapter, fetches encrypted metadata, and sends the canonical proxy request onward. [src:subumbra-proxy]
- The provider key decrypt path is split: host-side storage has ciphertext/wrapped DEK, while the private key lives in the Durable Object custody boundary. [src:subumbra-claude] [src:subumbra-worker]
- Rotation can re-encrypt an existing provider secret against the on-disk public key without changing the app's adapter token. [src:subumbra-rotation]

## Current Subumbra Gaps

- No mature enterprise RBAC/SSO replacement.
- No hosted SaaS onboarding or managed support organization.
- No general-purpose dynamic database/cloud credential engine.
- No built-in secret scanning for repos, CI logs, or chat transcripts.
- Audit is useful for the current stack, but not a mature export/retention product yet.

