# LiteLLM — standalone example (not core stack)

This directory holds **`config.yaml`** for **standalone** LiteLLM installs that
talk to Subumbra over the transparent sidecar:

- `api_base: http://subumbra-proxy:8090/t/<key_id>/...` (Docker network) or
  `http://127.0.0.1:10199/t/<key_id>/...` (host-published proxy)
- `api_key: <adapter token>` from your Subumbra `.env` (for example
  `SUBUMBRA_TOKEN_LITELLM`)

Bundled LiteLLM is **not** part of the default `docker compose` core stack in
this repo. See [docs/apps/litellm/install.md](../docs/apps/litellm/install.md)
for the supported app-owned setup.
