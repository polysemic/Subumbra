# Threat Model And Failure Modes

This page is not a ranking. It compares likely outcomes by product category, not every product's custom deployment. Real results depend on operator configuration, IAM, network controls, CI policy, and incident response.

## Visual Matrix

| Scenario | Typical vault/secret-manager outcome | Typical gateway/broker outcome | Subumbra outcome | Still not solved |
|----------|--------------------------------------|--------------------------------|------------------|------------------|
| Developer laptop malware reads `.env` | If provider plaintext is present, attacker may get usable secret; if only vault reference exists, attacker needs access path | If gateway key is present, attacker may use that key within gateway policy | App config should hold adapter token and proxy route, not provider plaintext | Active session, local token theft, browser compromise, and deploy authority still matter |
| CI log or GitHub Actions secret leak | Plain leaked secret can be used until revoked; secret scanning may help detect | Gateway key can be abused if policy permits | Leaked adapter token still needs session, adapter/key binding, and policy path to succeed | CI authority can still deploy bad config or leak other credentials |
| Admin or support social-engineered into revealing a secret | If plaintext is revealed, rotation and audit are response paths | If virtual key is revealed, gateway policy limits impact | Revealing an adapter token is less useful than revealing provider plaintext, but still serious | Social engineering of Cloudflare, repo, or operator machine remains serious |
| Stolen gateway/app token used off-site | Vault may not see use if plaintext secret was already copied | Gateway can log and rate-limit use if request reaches it | Adapter token use is constrained by session state and policy; off-site use may still reach the Worker if network controls allow | Subumbra does not yet have canary-token call-back telemetry |
| Malicious config PR lands | Mature platforms depend on code review, CI, branch protection, and policy review | Gateway config can redirect or broaden access if merged | Manifest policy changes are explicit and `policy_hash` affects encrypted records | Malicious repo authority can still ship dangerous code or config |
| App server compromise | If app holds plaintext secret, attacker may copy it; if using retrieval, attacker may query vault | Attacker may use gateway key from app environment | App server should not have provider plaintext, only adapter token and route | During an active session, malware can still cause authorized-looking calls |
| Compromised SSH workflow | Static private key theft can grant access until revoked | Not usually covered | SSH private key remains in custody; local agent requests sign operations | Local malware can attempt sign operations while session and Janus state permit |
| Cloud/control-plane compromise | Depends on provider architecture and IAM segmentation | Hosted gateway compromise is high impact for that gateway | Cloudflare Worker/Durable Object compromise is high impact for Subumbra | Subumbra's Cloudflare dependency is an accepted current risk |
| Active Subumbra session abused by local malware | — N/A | — N/A | Session/adapter/key policy narrows what can be done | Host malware remains meaningful during active sessions |

## Reality Notes

- Subumbra reduces key-theft blast radius; it does not make host malware harmless.
- Active sessions, compromised browsers, malicious deploy authority, or infrastructure-level access still matter.
- Janus can add runtime approval friction, but it is not a replacement for branch protection, code review, CI policy, or IAM.
- Vaults and gateways can also reduce blast radius when they are configured with short-lived credentials, strict policies, identity controls, rotation, audit, and network restrictions. [src:vault-docs] [src:cloudflare-ai-gateway] [src:portkey-docs]

## Where Others Are Stronger

- Secret managers and enterprise access platforms often have mature IAM, SSO, approval workflows, audit export, and incident-response tooling.
- Gateway platforms can offer analytics, rate limits, spend controls, and provider routing that help investigate abuse patterns.
- Source-control platforms and security scanners may catch credential leaks before runtime; Subumbra does not currently include secret scanning.

## Where Subumbra Is Different

- The normal app runtime is not supposed to contain provider plaintext. If a local `.env` is committed, the intended leaked value is an adapter token rather than the provider credential. [src:subumbra-claude]
- Policy enforcement happens at the Worker boundary with adapter/key binding, method/path/content-type/body/header controls, velocity, and response checks. [src:subumbra-manifest] [src:subumbra-worker]
- SSH private keys and API provider secrets share one custody posture instead of living in unrelated workflows. [src:subumbra-ssh]

## Current Subumbra Gaps

- No automated canary-token or honeypot callback system.
- No local EDR or malware containment.
- No repo secret scanning.
- No mature SIEM/export pipeline.
- Cloudflare compromise remains a serious boundary condition.

