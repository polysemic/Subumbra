# Security Policy

Subumbra is alpha software and has not been independently audited.
A complete security overview, threat model, and known-gap list lives in
[docs/security-overview.md](docs/security-overview.md) — please read it
before deploying.

## Supported versions

Subumbra is pre-1.0 and has not yet cut tagged releases. The `main`
branch is the only supported version; security fixes land there.

| Version | Supported |
|---------|-----------|
| `main`  | ✅        |
| pre-release tags (if any) | ❌ |

## Reporting a vulnerability

Please report privately before any public disclosure.

**Preferred:** Open a [private security advisory](https://github.com/polysemic/Subumbra/security/advisories/new)
on this repository. GitHub notifies maintainers without making the report
public until a fix is published.

**Alternative:** Email **eric@polysemic.email**

Please include:

1. A description of the vulnerability and its potential impact
2. Steps to reproduce, or a minimal proof of concept
3. Any suggested mitigations or known workarounds

There is no formal bug bounty at this time. Acknowledgment will be sent
within 7 days. A coordinated public disclosure timeline will be agreed
based on severity and remediation complexity.

## Out of scope

The following are documented design choices, not vulnerabilities — please
read [docs/security-overview.md](docs/security-overview.md#what-subumbra-does-not-protect)
before reporting:

- Cloudflare is in the trust boundary (split-custody, not zero-trust to CF)
- Root access to the host breaks the runtime model
- Streaming response bodies are not scanned by `response.deny_patterns`
- The dashboard UI is read-only and alpha-quality (not hardened for public exposure)
- Spend and rate limits are enforced by upstream providers, not by Subumbra

## Disclaimer

Subumbra is provided **as-is** under the Mozilla Public License 2.0 with
no warranty. See [LICENSE](LICENSE) for full terms.
