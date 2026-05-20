# Shannon R75 Summary — 2026-05-20

## Scope

Sanitized public summary of the Shannon-assisted auth/authz review that fed
`r75-shannon-patch` and the `1.1.1-alpha` patch release.

Primary review focus:

- staging Worker auth and authz surfaces
- direct Worker `/proxy` behavior under valid and invalid auth
- policy-binding behavior around client-supplied `policy_hash`

This report is intentionally public-facing and high signal. It does not include
private council notes, raw exploit payloads, or sensitive environment details.

## Result

- Status: `PATCHED`
- Release: `1.1.1-alpha`
- Overall outcome: external auth-bypass attempts were blocked; one real runtime
  integrity issue was confirmed and patched

## What Was Tested

The Shannon-assisted review concentrated on the externally reachable staging
Worker path and the trust boundaries immediately behind it.

Covered areas:

- Worker route access behavior
- Cloudflare Access enforcement at the edge
- Worker token handling for valid and invalid requests
- direct `/proxy` request behavior
- runtime policy-binding behavior for V3 decrypt-time requests

Supporting public scan context from the same release window:

- Semgrep baseline
- Trivy filesystem and misconfiguration review
- ZAP passive baseline
- Nuclei low-rate web scan
- Bandit, pip-audit, gitleaks, and Semgrep secrets scans

## How It Was Tested

Testing used a mix of:

- static code review of the Worker request path
- adversarial runtime checks against a staging Cloudflare-backed deployment
- direct valid/invalid request validation on the patched staging stack
- comparison of pre-patch and post-patch behavior for the specific
  `policy_hash` integrity issue

The practical goal was not just to enumerate theoretical issues, but to answer
three questions:

1. Can an external attacker reach the Worker auth surface unauthenticated?
2. If not, what still matters if an outer control is bypassed or misused?
3. Which findings are real enough to patch immediately?

## High-Level Outcome

The overall result was reassuring:

- Cloudflare Access blocked the externally attempted auth-bypass paths
- the review did **not** produce a confirmed unauthenticated compromise
- the main credible runtime issue was narrower than an auth bypass
- that issue was patched in `1.1.1-alpha`

This is the kind of result we want from an alpha hardening cycle: aggressive
testing, a small real finding, a prompt patch, and clear documentation of what
was and was not confirmed.

## What Was Blocked

During the auth/authz review, the outer Cloudflare Access layer consistently
prevented unauthenticated requests from reaching the Worker’s protected runtime
paths.

Public takeaway:

- the external attack surface was not found to be openly reachable
- the review did not demonstrate a live external auth bypass
- several candidate findings remained dependent on having legitimate upstream
  access or privileged credentials before they could matter

That does **not** mean “nothing was found.” It means the strongest live
external control held, and the remaining actionable issue was inside the Worker
logic rather than an exposed bypass.

## Confirmed Finding

### Worker `policy_hash` authority bug

The Worker accepted a client `policy_hash` value when constructing the
decrypt-time payload sent to the Durable Object for V3 `/proxy` requests. The
runtime should instead rely on the live registry `policy_hash`.

Impact:

- weakened the intended server-authoritative policy binding for decrypt-time AAD
- did **not** become a confirmed unauthenticated auth bypass in the Shannon run
- was still worth patching because it weakened an integrity guarantee the system
  was supposed to enforce centrally

Fix shipped in `1.1.1-alpha`:

- the Worker now uses the live registry `policy_hash` as the decrypt-time
  authority

## Patch Verification Summary

Staging verification after the patch confirmed:

- transparent proxy baseline still succeeds
- direct Worker `/proxy` baseline still succeeds with valid auth
- tampering the client `policy_hash` no longer changes decrypt-time behavior
- invalid adapter tokens still return `401 unauthorized`

This is the key release outcome: the patch removed client control over the
decrypt-time `policy_hash` decision without breaking the normal operator path.

## Other Findings and Disposition

The wider review also surfaced several lower-confidence or non-blocking themes.
These matter as engineering backlog, but they were not release blockers for
`1.1.1-alpha`.

- `/proxy` pre-auth throttling remains a defense-in-depth hardening item
- some auth/authz findings were valid only after passing outer access controls
- the SSRF lane hit Shannon harness/container-network limits and did not yield a
  confirmed product finding
- no confirmed unauthenticated compromise emerged from the completed auth/authz
  work

## Transparency Notes

This summary is intentionally more transparent than a one-line “scan passed”
statement:

- it states what was tested
- it states what held
- it states what was actually confirmed
- it states what was patched
- it distinguishes real findings from tooling or environmental limits

That is the reporting posture we want to keep: regular testing, clear public
writeups, and prompt patches when a real issue is found.

## Related Public Reports

- [Semgrep Baseline](./semgrep-baseline.md)
- [Trivy](./trivy.md)
- [ZAP Baseline](./zap-baseline.md)
- [Nuclei Web Lite](./nuclei-web-lite.md)
- [Bandit](./bandit.md)
- [pip-audit](./pip-audit.md)
- [gitleaks](./gitleaks.md)
- [Semgrep Secrets](./semgrep-secrets.md)
