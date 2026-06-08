# Subumbra 1.2.0-alpha Release Report

This report summarizes the Shannon-assisted security assessments performed during the `1.2.0-alpha` release cycle, the blocked attack paths and remediated findings they surfaced, the accepted residual risks, and the operational upgrades implemented between `1.1.0-alpha` and `1.2.0-alpha`.

---

## 1. Shannon Scan Execution Metrics

The following statistics were extracted from the selected completed Shannon workspaces and their published assessment artifacts. Turn metrics represent active LLM interactions across all agent phases for the selected completed runs, with the final column showing the maximum turn number reached during the active exploitation scanning phase.

Scope note: the table intentionally counts the completed baseline and kept follow-up runs that produced the security reports used for release review. Earlier failed, retried, or superseded workspaces were retained as internal audit trail but are not counted in these totals. `subumbra-auth-20260516T171500Z` predates `r75` and is included as the `r71` security-hardening baseline.

| Scan Profile | Focus Area | Active Duration | Total LLM Turns | Exploitation Phase Turns |
| :--- | :--- | :--- | :--- | :--- |
| `subumbra-ssrf-worker-lite-20260521T060437Z` | Server-Side Request Forgery | 67.1 mins | 795 turns | 415 turns |
| `subumbra-response-injection-lite-20260520T175534Z` | Header/Pattern Injection | 57.5 mins | 757 turns | 232 turns |
| `subumbra-auth-worker-lite-20260519T192813Z` | Worker Authentication | 42.8 mins | 494 turns | 132 turns |
| `subumbra-keys-auth-lite-20260521T214849Z` | Keys Service Baseline | 68.9 mins | 383 turns | 236 turns |
| `subumbra-authz-worker-lite-20260520T052928Z` | Worker Authorization | 60.2 mins | 384 turns | 138 turns |
| `subumbra-keys-auth-lite-20260522T060759Z` | Keys Hardening Post-R80 | 63.9 mins | 307 turns | 74 turns |
| `subumbra-auth-20260516T171500Z` | Auth Baseline (R71) | 54.0 mins | 346 turns | 90 turns |
| **Total** | | **414.4 mins** | **3,466 turns** | **1,317 turns** |

---

## 2. Blocked Exploitations & Invariant Protections

While scans identified specific implementation flaws, Subumbra's layered controls defended key assets and prevented full compromise across several attack vectors. The format below mirrors the remediation section: each entry names the scan coverage, the blocked attack path, the protection that held, and the caveat or follow-up.

### Cloudflare Access Edge Gate
* **Scan Profiles**: `auth-worker-lite`, `authz-worker-lite`, `response-injection-lite`, and `ssrf-worker-lite`.
* **Blocked Exploit**: Anonymous external requests could not reach Worker application code on `/proxy`, `/auth-ping`, `/setup`, `/manage`, or `/health`.
* **Protection That Held**: Cloudflare Access returned edge-level HTTP 403 before the Worker executed. Shannon's documented bypass attempts included forged JWTs, empty Access headers, spoofed IP headers, method changes, and direct workers.dev access; all failed.
* **Impact**: Worker-side findings remained exploitable only for an attacker who first obtained valid Cloudflare Access identity or service-token credentials.
* **Caveat**: The evidence showed no mTLS/client-certificate bypass path (`mtls_status: NONE`). This should be described as CF Access identity/service-token protection, not client-certificate protection.

### Cryptographic Enclosure Invariants
* **Scan Profiles**: `auth-worker-lite`, `authz-worker-lite`, and keys-service auth scans.
* **Blocked Exploit**: Shannon did not extract plaintext provider API keys, even when it identified the pre-r75 client-controlled `policy_hash` AAD integrity gap.
* **Protection That Held**: Provider secrets remained V3 envelope records: AES-GCM ciphertext plus RSA-wrapped DEKs on the keys side, with the matching RSA private key retained in Cloudflare Durable Object SQLite custody. Neither side alone exposed usable plaintext.
* **Impact**: The r75 finding weakened server-authoritative policy replay binding, but it did not give Shannon the private key or a direct plaintext key dump.
* **Caveat**: The DEK wording must stay precise: wrapped DEKs are stored with key records; the Durable Object stores the RSA private key used to unwrap them.

### Keys-Service HMAC and Adapter Gates
* **Scan Profiles**: `keys-auth-lite` pre-hardening and post-r80/r81 follow-up.
* **Blocked Exploit**: Current keys-service reads require adapter authorization plus a valid timestamped HMAC signature; unauthorized callers cannot retrieve encrypted records or management metadata.
* **Protection That Held**: Rounds 79-81 scoped list/stat/audit reads, normalized denied/nonexistent key responses, enforced global nonce uniqueness, adopted length-prefixed HMAC payloads, bound HMAC to `consumer_id`, and collapsed staged HMAC error oracles.
* **Impact**: Shannon's initial keys-service reconnaissance paths were converted from confirmed vulnerabilities into denied or scoped responses in the release branch.
* **Caveat**: The first keys scan did find real HMAC and metadata-scope flaws. The release report should present HMAC validation as a post-remediation invariant, not as a control that was already complete during the initial scan.

### SSRF Isolation
* **Scan Profiles**: `authz-worker-lite`, `response-injection-lite`, and `ssrf-worker-lite`.
* **Blocked Exploit**: Generic localhost, private-range, alternate-scheme, host-spoofing, path traversal, and redirect-chain SSRF attempts did not produce internal host access.
* **Protection That Held**: The Worker requires `https://`, compares `target_url.hostname` to the live registry target host, enforces method/path/content-type/body policy before Durable Object dispatch, and now uses `redirect: "manual"` for upstream fetches.
* **Impact**: After r76 and r78, the Worker blocks both redirect-follow SSRF and non-default-port SSRF on otherwise allowlisted provider hosts.
* **Caveat**: This was not fully true before r78: Shannon correctly found that a non-443 port on an allowlisted hostname bypassed the hostname-only check. The release report should credit the current invariant to the r78 port-validation patch, not to Cloudflare edge routing alone.

### Request Guardrail Verification
* **Scan Profiles**: Worker authz/SSRF/response-injection scans plus r78 verification.
* **Blocked Exploit**: Requests outside declared adapter capabilities were rejected before provider dispatch.
* **Protection That Held**: The live policy model enforces `allow.methods`, `allow.path_prefixes`, `allow.content_types`, and `allow.max_body_bytes`; r77 added explicit request/response header allowlists where policy supplies them.
* **Impact**: Shannon's successful findings shifted from broad policy bypasses to narrow implementation gaps, which were patched in r75-r81.
* **Caveat**: Query-parameter authority remains deferred; the report should not claim complete query-string policy enforcement.

---

## 3. Remediated Vulnerabilities (Shannon Scan Findings)

These assessments used the Shannon security testing harness to evaluate both the externally facing Cloudflare Worker and the internal-facing keys management service across the release hardening arc.

* **Scan Execution Controller**: [run-shannon-vps.sh](../scripts/security/run-shannon-vps.sh)
* **Scan Configuration Catalog**: [shannon/](../scripts/security/shannon)

### Worker Authentication & Cryptographic Bindings Pass
* **Scan Profile**: [auth-worker-lite.yaml](../scripts/security/shannon/auth-worker-lite.yaml)
* **Operational Gate**: All active exploitation attempts were blocked at the edge by Cloudflare Access (enforcing RS256 JWT validation prior to application execution).
* **Patched Vulnerability**:
  * **Quoting Shannon**: `"AUTH-VULN-04: Client-Controlled policy_hash Overrides Server-Authoritative Cryptographic AAD Binding"` (Medium Severity).
  * **Finding**: The decryption pipeline accepted client-supplied policy hashes, allowing requests to decrypt envelopes using stale or modified policy values.
  * **Remediation**: Patched in v1.1.1-alpha. The Worker was updated to ignore client-provided hashes and retrieve the authoritative policy hash directly from the server-side provider registry.

### Worker Authorization & Content Filtering Pass
* **Scan Profiles**: [authz-worker-lite.yaml](../scripts/security/shannon/authz-worker-lite.yaml), [response-injection-lite.yaml](../scripts/security/shannon/response-injection-lite.yaml)
* **Operational Gate**: Edge JWT validation prevented external exposure.
* **Patched Vulnerabilities**:
  * **Quoting Shannon**: `"AUTHZ-VULN-03 / INJ-VULN-03: SSRF via Redirect Following"` (Medium Severity).
  * **Finding**: The upstream request client followed HTTP redirects without re-validating the redirect URL against the hostname allowlist, enabling SSRF.
  * **Remediation**: Patched in Round 76. Outbound request redirection was set to `manual`, forcing connection failure on 3xx responses.
  * **Quoting Shannon**: `"INJ-VULN-01: deny_patterns Content-Filter Bypass"` (High Severity).
  * **Finding**: Content filters matched blocked patterns case-sensitively, and the buffered-response scan gate was too narrow. Streaming responses also bypassed body scanning.
  * **Remediation**: Patched in Round 76. Deny-pattern scanning was updated to match case-insensitively, the buffered-response scan gate was broadened, and the SSE streaming bypass was explicitly documented as an accepted contract limit.
  * **Quoting Shannon**: `"INJ-VULN-02: Response Header Injection"` (Medium-High Severity) and `"INJ-VULN-05: Request Header Injection to Upstream"` (Low-Medium Severity).
  * **Finding**: Blacklist-only header filters allowed critical request/response headers (such as CORS overrides, cookies, and beta feature gates) to pass unfiltered.
  * **Remediation**: Patched in Round 77. Replaced blacklist filters with strict, whitelist-based header allowlists defined in the provider templates.

### Server-Side Request Forgery (SSRF) Pass
* **Scan Profile**: [ssrf-worker-lite.yaml](../scripts/security/shannon/ssrf-worker-lite.yaml)
* **Patched Vulnerability**:
  * **Quoting Shannon**: `"SSRF-VULN-01 / AUTHZ-VULN-07: Missing Port Validation in Hostname Allowlist Check"` (High Severity).
  * **Finding**: The hostname checker stripped port designations, allowing connections to non-standard TCP ports on allowlisted API domains.
  * **Remediation**: Patched in Round 78. Added port-level validation to reject any target port other than default/explicit HTTPS (443).

### Keys Service Internal Authentication Pass
* **Scan Profile**: [keys-auth-lite.yaml](../scripts/security/shannon/keys-auth-lite.yaml)
* **Patched Vulnerabilities**:
  * **Quoting Shannon**: `"AUTH-VULN-01: Absence of Rate Limiting and Account Lockout"` (High Severity).
  * **Finding**: The keys service lacked application-layer rate limiting on tokens or admin routes.
  * **Remediation**: Patched in Round 80. Implemented a persistent, SQLite-backed IP rate limiter.
  * **Quoting Shannon**: `"AUTH-VULN-03: Sensitive Responses Lack Cache-Control Headers"` (High Severity).
  * **Finding**: API responses returning key configurations did not specify cache constraints, risking persistence in intermediate caches.
  * **Remediation**: Patched in Round 79. Added `Cache-Control: no-store` to all keys service endpoints.
  * **Quoting Shannon**: `"AUTH-VULN-04: Key-Existence Oracle via 403/404 Status Divergence"` (High Severity).
  * **Finding**: Distinct status codes (403 vs 404) leaked whether an unauthorized key existed in the registry.
  * **Remediation**: Patched in Round 79. Normalized all unauthorized or missing key responses to an identical HTTP 403 Forbidden payload.
  * **Quoting Shannon**: `"AUTH-VULN-05: /keys Returns All Key Metadata Regardless of allowed_keys Scope"` (High Severity).
  * **Finding**: The metadata list endpoint did not filter keys by the querying adapter's scope.
  * **Remediation**: Patched in Round 79. Scoped metadata listings strictly to the adapter's authorized key subset.
  * **Quoting Shannon**: `"AUTH-VULN-06: Cross-Adapter Data Leakage via GET /stats and GET /audit"` (High Severity).
  * **Finding**: Low-privilege adapters could fetch audit logs and usage statistics for keys they were not permitted to access.
  * **Remediation**: Patched in Rounds 79 and 81. Stats and audit trails are now scoped to the adapter's authorized key subset.
  * **Quoting Shannon**: `"AUTH-VULN-07: Cross-Key Nonce Reuse (Composite Primary Key Flaw)"` (High Severity).
  * **Finding**: Replay protection allowed reusing nonces across distinct key endpoints due to composite database indexes.
  * **Remediation**: Patched in Round 80. Migrated nonce tracking to enforce global, single-column uniqueness.
  * **Quoting Shannon**: `"AUTH-VULN-08: HMAC Canonical String Collision via Delimiter-Only Construction"` (Medium Severity).
  * **Finding**: Ambiguous token canonicalization allowed signatures to be transferred between requests containing colons.
  * **Remediation**: Patched in Round 80. Adopted length-prefixed HMAC canonical strings across all services.

### Keys Service Internal Robustness Pass
* **Scan Profile**: [keys-auth-lite.yaml](../scripts/security/shannon/keys-auth-lite.yaml) (Round 81 verification)
* **Patched Vulnerabilities**:
  * **Quoting Shannon**: `"AUTH-VULN-01 (Internal): Fail-Open Rate Limiter on Database Failure"` (Medium Severity).
  * **Finding**: SQL database exceptions caused the rate limiter to silently skip checks.
  * **Remediation**: Patched in Round 81. Hardened the rate checker to fail-closed on DB errors.
  * **Quoting Shannon**: `"AUTH-VULN-02 (Internal): get_key Bypass of Key Paused State"` (Medium Severity).
  * **Finding**: The keys database query did not validate key pause states, bypassing the Worker-level pause checks.
  * **Remediation**: Patched in Round 81. Paused-state verification was integrated directly into database key resolution.
  * **Quoting Shannon**: `"AUTH-VULN-03 (Internal): HMAC Payload Excludes consumer_id"` (Medium Severity).
  * **Finding**: The signing string omitted the adapter ID, allowing signatures to be reused across different credentials.
  * **Remediation**: Patched in Round 81. Bound HMAC validation to the requesting adapter identity.
  * **Quoting Shannon**: `"AUTH-VULN-04 (Internal): Cross-Adapter Activity Disclosure via /audit NULL key_id Rows"` (Medium Severity).
  * **Finding**: Non-list-all readers could still observe audit rows without a `key_id`, exposing cross-adapter activity metadata.
  * **Remediation**: Patched in Round 81. Non-list-all `/audit` reads now exclude `key_id IS NULL` rows and scope results to the caller's allowed keys.
  * **Quoting Shannon**: `"AUTH-VULN-05 (Internal): /stats recent_log Returns Unscoped Cross-Adapter Events"` (Medium Severity).
  * **Finding**: `/stats` aggregate scoping did not fully cover the recent-log tail, allowing low-privilege adapters to view unrelated activity.
  * **Remediation**: Patched in Round 81. `recent_log` is now scoped for non-list-all adapters and excludes unrelated or NULL-key rows.
  * **Quoting Shannon**: `"AUTH-VULN-06 (Internal): Staged Oracle for HMAC Errors"` (Medium Severity).
  * **Finding**: Validation steps leaked whether a failure was due to format errors (400) or semantic signature failure (401).
  * **Remediation**: Patched in Round 81. Consolidated all signature and payload verification errors to a uniform 401 response.

---

## 4. Accepted Residuals & Deferred Follow-Ups

The following Shannon-raised or council-confirmed items should not be described as fully remediated in 1.2.0-alpha:

* **Worker token TTL / in-band revocation**: Static Worker token lifecycle was deferred in r75. Rounds 82-83 add bounded operator sessions and Worker-side active-adapter gates for decrypt/proxy use, but they do not convert all static tokens into expiring credentials.
* **IP-only Cloudflare-side auth rate limiting**: Distributed IP rotation against Worker auth/admin rate limits remains a known limitation of per-IP throttling, not a 1.2.0-alpha fix claim.
* **Docker-internal plaintext HTTP for `subumbra-keys`**: Shannon flagged no TLS/HSTS on the internal keys service. This remains an accepted Docker-internal-network design tradeoff unless a future round adds mTLS or internal TLS. The 1.2.0-alpha remediations instead harden HMAC, nonce, scoping, cache, and rate-limit behavior on that internal channel.

---

## 5. Operational Upgrades & Enhancements

Significant improvements to platform lifecycle automation, validation tooling, and session controls were introduced between v1.1.0-alpha and v1.2.0-alpha.

### Cloudflare Lifecycle Automation
* Added support for Bring Your Own Credentials (BYOC) Cloudflare Tunnels and Access apps.
* Introduced commands to update credentials in place: `./bootstrap.sh --update-tunnel` and `./bootstrap.sh --update-access`.
* Added automated creation and teardown (`./bootstrap.sh --nuke-cloudflare`) of all DNS, Tunnel, Access, and policies using a single token.

### Source & Preflight Verification
* Created `scripts/subumbra-verify` to ensure code integrity, check file drift, and verify KV/Worker states before container startup.
* Automated preflight checks inside `bootstrap.sh` to halt execution on stale configurations or key residues.

### Session Lockdown (v1.2.0-alpha Foundation)
* Added a persistent `sessions.db` to enforce global lockdown mode across the system.
* All decryption capabilities (`GET /keys/<id>` and `/proxy`) remain locked and fail-closed until an operator opens a bounded session using `./bootstrap.sh --session start ...`.
* Provided read-only session monitoring endpoints (`GET /sessions`) and dashboard rendering.

### Multi-Session Isolation (v1.2.0-alpha Release)
* Expanded session capabilities to support concurrent active sessions.
* Enforces disjoint mapping: overlapping scopes (duplicate adapter-to-key associations) are rejected at session initialization to prevent tenant interference.
* Propagates session status to the edge via shadow KV keys (`session_token:<session_id>:<consumer_id>`) and aggregate gates (`active_consumer:<consumer_id>`), laying the groundwork for edge-level session revocation.
