# Project Memory

*Shared fresh-session memory for Subumbra. This is not a round log. It is a
small set of durable project truths that fresh chats are likely to miss.*

Update this file only when a closed round changes something a new session would
otherwise misunderstand.

---

## 1. Product Identity

- Subumbra is a **universal zero-trust secret broker**, not a LiteLLM plugin.
- LiteLLM is Adapter #1, not the product boundary.
- The core product shape is:
  - `forge-keys` for encrypted record storage and limited metadata access
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
- `forge-keys` is Docker-internal only and is not the public app-facing API.
- The Worker is the only place where provider secrets become usable plaintext.
- The explicit sidecar/service path exists and is a real product direction, not
  just an experiment.
- Live provider validation has moved away from a purely bundled model; local
  repo metadata can still remain as operator/bootstrap seed material.

---

## 4. Deployment Reality

- The clean supported install path is still **terminal-first**.
- Bootstrap currently runs through `docker compose --profile bootstrap run ...`
  and performs Cloudflare-side provisioning work.
- `post-bootstrap.sh` runs on the host and writes runtime values into the
  repo-local `.env`.
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
- `docs/subumbra-fresh-install.md` for the clean reference install path
- `docs/operator-guide.md` for live registry / operational flows
- `docs/verification-policy.md` for harness and proof policy

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
