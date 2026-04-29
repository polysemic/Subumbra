# Bifrost AI Gateway — Install

## Scope

This install path proves:

- Bifrost running as a Docker service on the `subumbra-net` network
- OpenAI-compatible requests routed through the secure Subumbra transparent path
- `config_store` enabled with SQLite for persistent configuration on the mounted
  data path

## Host Vs Docker-Internal Ports

Use the host-published port only for operator checks from the VPS host:

- host health check: `http://127.0.0.1:10199/health`

Use the Docker-internal service address from app containers on `subumbra-net`.

## Secure Contract

Bifrost now uses:

- a shared Bifrost adapter token as the credential value presented to
  `subumbra-proxy`
- the requested Subumbra `key_id` embedded in each provider `base_url`

For the single-provider OpenAI path:

```text
BIFROST_SUBUMBRA_TOKEN=<subumbra adapter token>
network_config.base_url=http://subumbra-proxy:8090/t/openai_prod
```

Bifrost appends `/v1/chat/completions` after that base URL.

## Prerequisites

Standard Subumbra readiness:

```bash
cd /opt/subumbra
docker compose ps
curl -sS http://127.0.0.1:10199/health
```

Expected proxy health:

```json
{"status":"ok","worker_auth":"ok"}
```

## Supported Env Shape

The single-provider fresh install flow only requires:

```text
BIFROST_SUBUMBRA_TOKEN=<subumbra adapter token>
```

The promoted JSON template carries the target `key_id` in each provider
`base_url`.

## Cut-Over Steps

1. Create the Bifrost data directory.
2. Copy `templates/config-subumbra.json` to `/opt/bifrost-data/config.json`.
3. Copy `templates/bifrost.env` to `/opt/bifrost.env`.
4. Start Bifrost with the tracked compose file.

## Operator Notes

- The Bifrost credential is now the adapter token, not a Subumbra key ID.
- The target `key_id` lives in `network_config.base_url`.
- OpenAI uses bare `.../t/openai_prod`, not `.../t/openai_prod/v1`, because
  Bifrost appends the provider path itself.
- Groq and OpenRouter still keep their extra upstream suffixes after the
  embedded `key_id`.

## Fail-Closed Check

Bifrost with an invalid adapter token should fail closed when it attempts to
route through `subumbra-proxy`.

## Operator Checklist

- [ ] Subumbra proxy health returns `{"status":"ok","worker_auth":"ok"}`
- [ ] `BIFROST_SUBUMBRA_TOKEN` is set to the Bifrost adapter token
- [ ] `network_config.base_url` embeds the intended Subumbra `key_id`
- [ ] Bifrost UI returns HTTP 200 at the configured host port
