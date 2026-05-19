# Changelog

All notable changes to Subumbra should be summarized here.

This file is intentionally concise. For longer release writeups, operator notes,
and release-specific context, see `docs/releases/`.

## 1.1.0-alpha - 2026-05-19

### Added

- Optional Cloudflare BYOC runtime credential support for `TUNNEL_TOKEN`,
  `CF_ACCESS_CLIENT_ID`, and `CF_ACCESS_CLIENT_SECRET`
- Day-2 runtime credential update commands:
  `./bootstrap.sh --update-tunnel` and `./bootstrap.sh --update-access`
- Optional bootstrap-managed Cloudflare auto-provisioning for Tunnel, DNS, and
  Access resources
- `./bootstrap.sh --nuke-cloudflare` teardown for tracked Cloudflare-managed
  resources
- `scripts/subumbra-verify` for source-trust and pre-bootstrap integrity checks
- Published release-signing public key at `docs/release-signing-key.pub`

### Changed

- Bootstrap now runs a preflight verifier automatically before reading
  `.env.bootstrap` or prompting for secrets
- Cloudflare lifecycle is now treated as a completed optional capability rather
  than an active product theme
- Install, security, and developer docs now cover signed-release verification
  and release-signing trust roots

### Security

- Added stricter signed-tag verification path through
  `SUBUMBRA_REQUIRE_SIGNED_TAG=1`
- Added public security reporting layout and VPS-oriented public scan tooling

## v1.0.0-alpha - 2026-05-17

### Added

- Worker hardening headers for JSON/auth/error responses
- Worker-side rate limiting for non-proxy auth/admin surfaces
- Active default `velocity` controls in built-in signed provider templates

### Changed

- `/setup/keygen` and `/internal/*` now reject before request body parsing
- Worker-edge setup-token equality checks were hardened

## v0.0.1-alpha

- Initial public alpha baseline
