# Changelog

All notable changes to Subumbra should be summarized here.

This file is intentionally concise. For longer release writeups, operator notes,
and release-specific context, see `docs/releases/`.

## 1.2.1-alpha - 2026-05-29
## 1.3.0-alpha - 2026-06-03

### Changed

- Renamed the app-facing caller vocabulary from `adapter` to `consumer` across
  bootstrap/session flows, registry env wiring, UI routes, and Worker log
  fields.
- Renamed the approval subsystem from Gate to Janus across Worker bindings,
  operator commands, and dashboard references, with a Durable Object rename
  migration in `worker/wrangler.toml`.
- Standardized the operator manifest name on `manifest.yaml` and retired the
  active `subumbra.json` fallback from bootstrap and verification helpers.
- Renamed the local encrypted record store from `data/keys.json` to
  `data/endpoint.json` across runtime code, compose wiring, and proof helpers.
- Updated install/operator/app docs and website copy to use split-trust,
  consumer, Janus, `manifest.yaml`, and `endpoint.json` terminology.

## 1.2.1-alpha - 2026-05-29

### Changed

- Vault drawer sub-tabs now swap content panes (Overview/Policy/Allow/Velocity/Audit for API keys; Overview/Hosts/Quota/Audit for SSH keys) instead of toggling CSS classes only.
- Adapter proxy URL snippets now show two topology entries per key — Docker-internal (`http://subumbra-proxy:8090/t/<key_id>`) and host-local (`http://127.0.0.1:10199/t/<key_id>`) — replacing the incorrect CF Worker URL.
- SSH key list endpoint (`GET /keys`) now includes `public_key` and `algorithm` fields, enabling real fingerprint display in the SSH vault drawer.
- Vault, SSH vault, policies, and adapters pages accept `?select=<id>` query parameter to pre-select a key, policy, or adapter on load.
- Cross-page navigation links added: audit log key/adapter columns link to vault and adapters pages; overview recent activity stream links key IDs and adapter names.
- Removed stale "coming Q3" and "quota lands Q3" placeholder strings from the SSH vault drawer.

## 1.1.1-alpha - 2026-05-20

### Changed

- The Worker now uses the live registry `policy_hash` as the decrypt-time
  authority for V3 `/proxy` requests instead of trusting a client-supplied
  value.
- Security, project-memory, and adapter-contract docs now explicitly describe
  the server-authoritative `policy_hash` behavior.

### Security

- Patched a policy-binding integrity gap identified during Shannon-assisted
  review of the staging Worker path.
- Verified on staging that tampering the client `policy_hash` no longer affects
  decrypt-time behavior; valid requests still succeed and invalid adapter tokens
  still return `401 unauthorized`.
- Published a sanitized public Shannon summary at
  `security/reports/2026-05/shannon-r75-summary.md`, including tested scope,
  high-level method, blocked external auth-bypass attempts, the confirmed
  runtime finding, and the shipped patch outcome.

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
