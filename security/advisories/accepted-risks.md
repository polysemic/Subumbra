# Accepted Risks and Reviewed Findings

This page tracks security findings that were reviewed and intentionally not
treated as immediate product defects.

The goal is transparency, not dismissal. Some items are accepted because they
are platform-controlled, some because they are explicit alpha tradeoffs, and
some because they belong in a later design round rather than an urgent fix.

## Categories

### Platform-Controlled

These are real observations, but they are owned primarily by an external
platform rather than by Subumbra source code.

- Cloudflare-managed TLS/cipher behavior on `*.workers.dev`
- Cloudflare dashboard-only switches such as "Always Use HTTPS"

### Operator / Packaging Tradeoffs

These are conscious tradeoffs in the current alpha deployment model.

- Bootstrap container running as root for one-shot administrative workflows
- Compose-level healthchecks existing even when scanner rules prefer Dockerfile
  `HEALTHCHECK`
- Terminal-first install and admin workflows instead of a fully sealed
  DockerHub/Portainer-native packaging model

### Deferred Design Work

These are known issues or limitations that deserve future review, but are not
being misrepresented as fully solved.

- Adapter `/proxy` velocity behavior under omitted, tight, and high-capacity
  policy variants
- Worker-side TTL/authority lifecycle behavior beyond current documented limits
- Broader secret-storage and host-env encapsulation improvements

## R71 Security Hardening Notes

During the `r71-security-hardening` round, the project fixed public-edge
header hardening, auth-first internal/setup behavior, Worker-edge setup-token
comparison, auth/admin throttling, and shipped template `velocity` defaults.

The main items intentionally left out of that round were:

- Cloudflare-native rate limiting instead of source-controlled Worker throttles
- rootless/bootstrap packaging redesign
- stale local `.env` setup-token cleanup as a product requirement
- raw Shannon workspace publication

Those were deferred deliberately so the round could stay focused on the
security behaviors that materially changed runtime enforcement.

## Public Reporting Rule

When a report is published under `security/reports/`, findings that fall into
one of the categories above should be called out clearly in that report's
Notes section rather than silently ignored.
