# Security Overview

> **Alpha software.** Subumbra has not been independently audited. Do not rely
> on it as your only control for protecting high-value credentials in production
> environments. Read this document before deploying.

---

## What Subumbra protects

Subumbra is designed to prevent API keys from being stored in plaintext on your
server or inside app configuration files. It does this by splitting custody:

- **Your server** holds AES-256-GCM ciphertext and a wrapped key — useless
  without the private key.
- **Cloudflare** holds the RSA-4096 private key inside a Durable Object — it
  can decrypt but never sees your plaintext key at rest.

Neither side alone can recover a provider API key. A decrypted key exists only
inside Cloudflare Durable Object memory for approximately 100ms during a
proxied request, and is never written to disk or returned to your server.

---

## What Subumbra does not protect

Be explicit with yourself about these before deploying:

**Cloudflare is in the trust boundary.** The private key is generated and
stored inside Cloudflare infrastructure. If Cloudflare is compromised,
subpoenaed, or misconfigured, the split-custody guarantee does not hold.
Subumbra reduces your attack surface; it does not eliminate Cloudflare as a
dependency.

**Root access to your server breaks the model.** An attacker with root on
your VPS can read container environment variables (adapter tokens, HMAC key)
from running process memory. They cannot directly extract provider API keys,
but they can make authenticated requests through the proxy. Treat your server
as a security boundary.

**Billing and spend are not capped.** Subumbra enforces which paths and
methods an adapter can call, but it does not limit total spend or request
volume at the provider level. Set spend limits and rate alerts directly with
your API providers.

**The UI is read-only and alpha-quality.** The dashboard has no write
capability in the current release. It is not hardened for public internet
exposure — use Cloudflare Access or host-level controls to restrict access.

**Streaming responses are not scanned.** `response.deny_patterns` (if
configured) applies only to buffered response types (`application/json`,
`text/plain`). Server-sent event streams pass through without pattern
matching. This is a known limitation.

**Local source must be trusted before bootstrap.** Split custody does not help
if a compromised checkout changes `bootstrap.sh`, the bootstrap Python code, or
the Worker before you enter Cloudflare and provider credentials. Run
`./scripts/subumbra-verify --verbose` before bootstrap; the host wrapper also
runs `./scripts/subumbra-verify --preflight` before reading `.env.bootstrap` or
prompting for secrets. The verifier checks Git state, selected sensitive files,
local state shape, and optional Worker deployment drift. It cannot prove its
own honesty without an external trust root such as a signed release tag,
trusted commit, or separately verified release artifact.

---

## Release signing and trust roots

Subumbra has two different signing roles, and they should stay separate:

- **Template catalog signing** uses the offline Ed25519 key for the signed
  provider template catalog.
- **Release signing** should use a separate dedicated key for Git release tags.

Do **not** reuse the template catalog private key for Git release tags. Keeping
those trust roots separate limits the blast radius if one signing key is ever
compromised.

The intended release model is:

- maintainers sign release tags with the **private release-signing key**
- operators verify release tags with the corresponding **public key**

For operator convenience, the release public key should be published both:

- outside the repo, such as GitHub signing-key settings
- inside the repo at [docs/release-signing-key.pub](release-signing-key.pub)

The in-repo copy is a convenience, not the only trust anchor. For higher
assurance, operators should compare the repo copy against an out-of-band source
they already trust before relying on strict signed-tag verification.

---

## Threat model

The following threats are structurally addressed in the current release:

| Threat | Mitigation |
|--------|-----------|
| Compromised adapter token requests any key | `allow.adapters` binds each token to specific `key_id`s; `capability_class` limits what APIs can be called |
| Prompt-injection causes an LLM app to call unintended APIs | `capability_class` + `allow.path_prefixes` and `allow.methods` enforce scope at the Worker boundary |
| Stolen Cloudflare deploy token replaces the Worker | Bootstrap captures a SHA-256 of the deployed bundle; `subumbra-verify-deploy` detects drift |
| Local source modified before bootstrap | `scripts/subumbra-verify` checks sensitive source files and bootstrap runs it before reading secrets |
| Ciphertext replayed across different keys or policies | V3 AAD binding (`subumbra:v3:<key_id>:<policy_hash>`) — decryption fails if key or policy don't match |
| Policy tampered with in Cloudflare KV | AAD binding causes decryption to fail if the stored policy hash no longer matches the ciphertext seal |
| Response body exfiltrates a secret | `response.deny_patterns` (optional) scans buffered responses; streaming is not scanned |

The following are known gaps or deferred work:

| Gap | Status |
|-----|--------|
| Streaming response scanning | Explicitly deferred — SSE and chunked streams pass through unscanned |
| UI write access / management API | UI is read-only; a hardened management API is planned but not implemented |
| Independent security audit | Not yet performed |
| Token expiry end-to-end validation | TTL enforcement is implemented but has not been validated against an actual expiry event |

---

## Sensitive files — never commit these

| File | Why |
|------|-----|
| `.env` | Contains adapter tokens and HMAC key |
| `.env.bootstrap` | Contains provider API keys and Cloudflare credentials — shred after use |
| `subumbra.yaml` | Contains `key_id` labels and `secret_ref` names — low sensitivity but gitignored by convention |
| `keys.json` | Encrypted ciphertext — safe cryptographically, but no reason to expose |
| `public_key*.pem` | RSA public keys — not secret, but no reason to commit |

All of the above are covered by `.gitignore`. Do not force-add them.

---

## Reporting security issues

This project is in alpha and has not been audited. If you find a vulnerability,
please report it privately before disclosing publicly:

**Email:** eric@polysemic.email

Include a description of the issue, steps to reproduce, and your assessment of
impact. There is no formal bug bounty at this time.

---

## Disclaimer

Subumbra is provided **as-is** under the Mozilla Public License 2.0 with no
warranty of any kind. The authors make no representations about the
suitability of this software for any purpose, including production use. You
are responsible for evaluating whether Subumbra meets your security
requirements before deployment.

See [LICENSE](../LICENSE) for the full terms.
