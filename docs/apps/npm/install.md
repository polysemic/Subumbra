# npm — Install

## Scope

This guide covers the proven npm publish path for Subumbra:

- `type: npm_token` manifest records
- path-scoped `.npmrc` auth to `subumbra-proxy`
- package publish with Worker-side identity and tarball deny checks
- day-2 npm token rotation with `./bootstrap.sh --rotate-npm-token <key_id>`

Deferred from this guide:

- `npm deprecate`, `dist-tag` mutation commands, and 2FA/account flows
- other registries such as GitHub Packages or PyPI
- regex-grade publish content matching

## Prerequisites

Run the standard readiness checks on the host that already has Subumbra deployed:

```bash
cd /opt/subumbra
docker compose ps
curl -sS http://127.0.0.1:10199/health
grep '^SUBUMBRA_TOKEN_' .env | cut -d= -f1
```

Expected proxy health:

```json
{"status":"ok","worker_auth":"ok"}
```

You also need:

- a manifest key with `type: npm_token`
- an adapter token for the adapter name in that key's `adapters:` list
- an npm package name that matches the key's allowed scope policy

## Supported Env Shape

Use `./bootstrap.sh --show npm` to print a paste-ready `.npmrc` snippet for the
first key authorized for the `npm` adapter.

The approved `.npmrc` shape is path-scoped:

```ini
registry=http://subumbra-proxy:8090/t/<key_id>/
//subumbra-proxy:8090/t/<key_id>/:_authToken=<SUBUMBRA_ADAPTER_TOKEN>
```

The real npm registry token does not belong in `.npmrc` on the publishing host.
Subumbra decrypts it inside the Worker and forwards it upstream.

## Cut-Over Steps

1. Add an npm token record to `subumbra.yaml`.

Example:

```yaml
keys:
  - key_id: npm_publish
    type: npm_token
    provider: npmjs
    secret_ref: NPM_TOKEN
    adapters: [npm]
    unique_vault: false
    policy:
      key_id: npm_publish
      policy_id: npm-publish-policy
      protocol: http_rest
      capability_class: custom_rest
      source: env
      target:
        host: registry.npmjs.org
      auth:
        scheme: bearer
      allow:
        adapters: [npm]
        methods: [GET, PUT]
        path_prefixes: [/@your-scope]
        scopes: ["@your-scope"]
        content_types: [application/json]
        max_body_bytes: 10485760
      deny:
        publish_path_patterns: [.env, .pem, .key, .npmrc, credentials.json]
        publish_content_patterns: [AKIA, npm_, PRIVATE KEY]
```

2. Bootstrap or republish according to your state:

```bash
./bootstrap.sh
```

3. Print the adapter snippet and copy the values into the publishing user's
   `.npmrc`:

```bash
./bootstrap.sh --show npm
```

4. Publish through the transparent route:

```bash
npm publish
```

For a scoped package, the package name in `package.json`, the npm request path,
and the configured `allow.scopes` prefix must all agree.

## Operator Notes

- npm issues metadata `GET` requests before the publish `PUT`. Both go through
  the same `/t/<key_id>/...` path-scoped auth rule.
- Subumbra inspects the `_attachments[*].data` tarball embedded in the npm
  publish JSON body before forwarding the request upstream.
- Publish deny checks are safe-literal substring matches, not regexes.
- A denied publish returns `403` with one of:
  - `publish_identity_mismatch`
  - `publish_scope_not_allowed`
  - `publish_deny_pattern_match`
  - `publish_invalid_packument`

## Persistence and Purge

`.npmrc` changes on the publishing machine take effect immediately for the next
CLI invocation. No local npm state purge is required just to rotate the
Subumbra adapter token or change the registry URL.

If you change persisted npm auth in another config layer, remove or correct the
conflicting entry before re-testing so npm resolves the path-scoped token you
intend.

## Fail-Closed Check

These are the expected fail-closed behaviors for the approved publish path:

- wrong or missing path-scoped adapter token: npm fails auth before publish
- package name mismatch between path and packument body: `403 publish_identity_mismatch`
- package outside `allow.scopes`: `403 publish_scope_not_allowed`
- forbidden file path or content in the tarball: `403 publish_deny_pattern_match`

## Rotation

Rotate the upstream npm token without a full bootstrap:

```bash
./bootstrap.sh --rotate-npm-token <key_id>
```

This rewrites only the selected `npm_token` record in `keys.json` using the
existing public key and does not rotate adapter tokens.

## Operator Checklist

- manifest record uses `type: npm_token`
- policy allows `GET` and `PUT`
- policy `allow.scopes` matches the package scope you publish
- `.npmrc` uses `registry=http://subumbra-proxy:8090/t/<key_id>/`
- `.npmrc` uses `//subumbra-proxy:8090/t/<key_id>/:_authToken=...`
- `./bootstrap.sh --show npm` matches the live `.env` token
- `curl http://127.0.0.1:10199/health` returns `{"status":"ok","worker_auth":"ok"}`
