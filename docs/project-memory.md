# Project Memory

*Shared fresh-session memory for Subumbra. This is not a round log. It is a
small set of durable project truths that fresh chats are likely to miss.*

Update this file only when a closed round changes something a new session would
otherwise misunderstand.

---

## 1. Product Identity

- Subumbra is a **universal zero-trust secret broker**, not a LiteLLM plugin.
- LiteLLM is a proven app-owned example, not the product boundary.
- The core product shape is:
  - `subumbra-keys` for encrypted record storage and limited metadata access
  - Cloudflare Worker for decrypt/proxy enforcement
  - adapters/sidecars for app-facing integration

---

## 2. Core Security Invariants

These should be treated as hard constraints unless a round explicitly reopens
them.

- Split-decrypt boundary must remain intact.
- No durable host-local decrypt power on operator-controlled systems.
- Worker-side hostname/provider validation must remain fail-closed.
- Secrets, tokens, decrypted material, auth headers, and raw sensitive payloads
  must not be logged.
- Operator-visible logging should stay minimal and diagnostic, not expansive.

---

## 3. Current Architecture Reality

- The canonical core API is `POST /proxy`.
- `subumbra-keys` is Docker-internal only and is not the public app-facing API.
- Provider secrets become usable plaintext only inside the Cloudflare Worker
  runtime, currently within the SQLite-backed `SubumbraVault` Durable Object isolate.
- The transparent sidecar route (`subumbra-proxy` / `/t/<key_id>/...`) is the
  current reference integration path.
- App-facing transparent traffic now authenticates with adapter token in
  `Authorization` or `X-API-Key`; legacy raw-`key_id` transparent auth is gone.
- The legacy sidecar `/v1/request` surface is no longer a supported app-facing
  contract.
- Live provider validation has moved away from a purely bundled model; local
  repo metadata can still remain as operator/bootstrap seed material.

---

## 4. Deployment Reality

- The clean supported install path is still **terminal-first**.
- Bootstrap currently runs through `docker compose --profile bootstrap run ...`
  and performs Cloudflare-side provisioning work.
- Full bootstrap now uses a one-shot Cloudflare `/setup/keygen` flow so the RSA
  private key is generated and retained in the vault DO rather than on the VPS.
- The interactive bootstrap wizard is now manual RAM-only entry; machine-readable
  env-driven input belongs to automation mode rather than an in-wizard import prompt.
- `subumbra-probe` is an optional diagnostic profile now; baseline bootstrap and
  runtime bring-up do not require probe provisioning.
- `bootstrap.sh` now runs on the host, mounts repo-local `.env` into the
  bootstrap container, and shreds `.env.bootstrap` after a successful run.
- Automation-mode app imports use `IMPORT_PATH_<n>` plus required
  `IMPORT_APP_LABEL_<n>` entries in `.env.bootstrap`.
- The project expects a **project-local `.env` in the repo root**.
- Fresh installs should use a dedicated checkout path such as `/opt/subumbra`
  rather than sharing a directory with unrelated services.
- Portainer/Dockge may be acceptable for day-2 management later, but they are
  not the primary reference install path.

---

## 5. Timekeeping And Ops Defaults

- Use UTC on servers, logs, and scheduled operations.
- Localized time belongs in presentation/UI only, not in stored operational
  timestamps.
- Verification and deployment guidance should prefer reproducible, explicit
  steps over convenience magic.

---

## 6. Current Documentation Shape

These docs are the main fresh-session anchors:

- `README.md` for current install/use flow
- `CLAUDE.md` for architecture overview
- `docs/vps-deployment.md` for generic Ubuntu 24.04 VPS baseline
- `docs/subumbra-install.md` for the clean reference install path
- `docs/operator-guide.md` for live registry / operational flows
- `docs/subumbra-testing.md` for harness and proof policy

---

## 7. Known Recurring Misreads

- Do not collapse “Subumbra” back into “the LiteLLM project.”
- Do not assume any shorthand line in `PROJECT_STATUS.md` overrides a more
  specific approved roadmap or round-approved plan.
- Do not treat optional future deployment modes as current reference install
  paths.
- Do not assume coexistence with pre-existing host services is already solved
  just because the clean install path works.
- Do not silently turn deferred mechanics into settled design decisions.

---

## 8. When To Update This File

Update only when a closed round changes one of these:

- product identity
- security invariants
- deployment/install reality
- operational defaults
- recurring gotchas that repeatedly mislead fresh sessions

If a round adds only local implementation detail, do not add it here.
