# Threat Model And Failure Modes

This page is not a ranking. It compares likely outcomes by product category, not every product's custom deployment. Real results depend on operator configuration, IAM, network controls, CI policy, and incident response.

This page describes defensive posture and residual operator responsibilities. It does not enumerate specific exploitation paths.

## Visual Matrix

| Scenario | Typical vault/secret-manager outcome | Typical gateway/broker outcome | Subumbra outcome | Residual operator responsibility |
|----------|--------------------------------------|--------------------------------|------------------|----------------------------------|
| Developer laptop malware reads `.env` | If provider plaintext is present, attacker may get a usable secret; if only a vault reference exists, the attacker still needs an authorized access path | If a gateway key is present, it may be used within gateway policy limits | App config should hold an adapter token and proxy route, not provider plaintext — a leaked token has significantly lower standalone value than a leaked provider key | Active session hygiene, endpoint security, and deploy-authority controls remain the operator's responsibility |
| CI log or GitHub Actions secret leak | A leaked plaintext secret is immediately usable until revoked; secret scanning may help detect it | A leaked gateway key may be usable within whatever policy the gateway enforces | A leaked adapter token should be treated as a credential requiring rotation; Subumbra controls constrain its reach | CI access controls, branch protection, and secret hygiene remain the operator's responsibility |
| Admin or support social-engineered into revealing a secret | If plaintext is revealed, rotation and audit are the response paths | If a virtual key is revealed, gateway policy limits impact | Revealing an adapter token is less damaging than revealing provider plaintext, but still serious — treat it as a full credential compromise | Social engineering of the operator, CF account, or source repository requires operator-layer controls Subumbra cannot enforce |
| Stolen gateway/app token used externally | Vault may not see use if plaintext was already copied out | Gateway can log and rate-limit use if the request reaches it | Adapter token use is constrained by session state and policy; network-level isolation is an operator-layer control | Credential rotation, session hygiene, and network controls are the operator's responsibility |
| Malicious config PR lands | Mature platforms depend on code review, CI, branch protection, and policy review | Gateway config can redirect or broaden access if merged | Manifest policy changes are explicit and `policy_hash` binds encrypted records to the policy in effect at encryption time | Repository integrity controls and PR review are the operator's responsibility |
| App server compromise | If the app holds plaintext secrets, an attacker may copy them; if using vault retrieval, the attacker may query the vault | An attacker may use a gateway key from the app environment | An app server should not hold provider plaintext — only an adapter token and route; Subumbra controls limit what a compromised app can reach | Endpoint security, session hygiene, and host hardening remain the operator's responsibility |
| Compromised SSH workflow | Static private key theft can grant access until revoked | Not usually covered | SSH private keys remain in custody; the local agent requests sign operations rather than possessing the key | Active session hygiene and Janus approval settings are the operator's responsibility |
| Cloud/control-plane compromise | Depends on provider architecture and IAM segmentation | Hosted gateway compromise is high impact for that gateway | Cloudflare Worker/Durable Object is Subumbra's trust boundary today — its integrity is critical | CF account security, Worker deploy controls, and Wrangler token hygiene are the operator's responsibility |
| Active session abused by local malware | — N/A | — N/A | Session, adapter, and key policy narrow what can be done; Janus approval adds friction for designated operations | Endpoint security and session lifecycle management remain the operator's responsibility |

## Reality Notes

- Subumbra reduces key-theft blast radius; it does not make host malware, compromised endpoints, or insider threats harmless.
- Active sessions represent a trust window. Enforce session hygiene — short TTLs, minimum key scope, and Janus approval on sensitive operations.
- Janus can add runtime approval friction, but it is not a replacement for branch protection, code review, CI policy, or IAM.
- Vaults and gateways can also reduce blast radius when configured with short-lived credentials, strict policies, identity controls, rotation, audit, and network restrictions. [src:vault-docs] [src:cloudflare-ai-gateway] [src:portkey-docs]
- This document describes defensive posture. Specific bypass techniques, exploitation paths, and detection-gap details are intentionally omitted.

## Where Others Are Stronger

- Secret managers and enterprise access platforms often have mature IAM, SSO, approval workflows, audit export, and incident-response tooling.
- Gateway platforms can offer analytics, rate limits, spend controls, and provider routing that help investigate abuse patterns.
- Source-control platforms and security scanners may catch credential leaks before runtime; Subumbra does not currently include secret scanning.

## Where Subumbra Is Different

- The normal app runtime is not supposed to contain provider plaintext. A leaked app config exposes an adapter token, not a provider credential. [src:subumbra-claude]
- Policy enforcement happens at the Worker boundary with adapter/key binding, method/path/content-type/body/header controls, velocity, and response checks. [src:subumbra-manifest] [src:subumbra-worker]
- SSH private keys and API provider secrets share one custody posture and one session/audit model. [src:subumbra-ssh]

## Operator Security Checklist

The following are the operator's responsibility and are outside Subumbra's enforcement boundary:

- Endpoint security and malware protection on all machines that access the Subumbra stack or CF credentials
- CF account security: strong auth, minimal API token scope, Wrangler token hygiene, and Worker deploy controls
- Repository controls: branch protection, required reviews, and CI secret hygiene
- Session lifecycle: short TTLs, minimum adapter/key scope per session, and prompt rotation of any suspected compromised credential
- Network controls: restrict which hosts and networks can reach the Worker and subumbra-proxy
- Janus approval configured on sensitive operations where human review adds meaningful friction
- Regular audit log review and incident response planning

## Current Subumbra Gaps

- No built-in repo or CI secret scanning.
- No mature SIEM/export pipeline for audit data.
- Cloudflare account and Worker integrity are critical boundary conditions with no current in-stack verification beyond the bootstrap worker-bundle hash.
