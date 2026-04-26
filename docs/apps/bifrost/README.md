# Bifrost AI Gateway — Subumbra Integration

Bifrost (`maximhq/bifrost`) is an AI gateway that routes LLM requests across
providers. When routed through Subumbra, Bifrost sends requests to
`subumbra-proxy` which handles key decryption and provider forwarding.

**Proven path:** fresh install, single OpenAI provider routed via Subumbra
transparent sidecar. Round 43-3-bifrost (2026-04-22).

What this folder covers:

- [install.md](./install.md) — clean fresh install path
- [templates/](./templates/) — operator-facing config templates

The tracked `config-subumbra.json` template now includes ready-to-edit JSON
provider entries for the providers proven working in Round 43-6, excluding
Together and Gemini for Bifrost.

What is not yet proven:

- a full multi-provider fresh-install walkthrough beyond the included template
- Bifrost UI behind Cloudflare Access
- migration from a running non-Subumbra Bifrost install

**Critical operator note:** Bifrost appends its own `/v1/` segment to
`network_config.base_url`. The correct Subumbra entry point for Bifrost is
bare `http://subumbra-proxy:8090/t` — **not** `/t/v1`. Adding `/v1` causes a
double-path 404 (`/t/v1/v1/chat/completions`).
