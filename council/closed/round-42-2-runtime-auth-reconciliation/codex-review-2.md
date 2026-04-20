# Round 42.2 Evidence-Based Review — Runtime Auth Reconciliation

Date: 2026-04-19
Author: Codex
Round: round-42-2-runtime-auth-reconciliation

## Findings Table

| Severity | Finding | Evidence |
|---|---|---|
| High | Your multi-app concern is real: the current product model already supports one `key_id` being authorized for multiple adapters, but the bootstrap UX and docs still frame LiteLLM and `subumbra-proxy` as separate scope choices. If 42.2 makes `subumbra-proxy` the authority for LiteLLM traffic, bootstrap prompts and docs must change with it. | [eric-questions.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/eric-questions.md#L1-L17), [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L574-L625), [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1045-L1056), [README.md](/home/eric/git/Subumbra/README.md#L221-L226), [subumbra-keys/app.py](/home/eric/git/Subumbra/subumbra-keys/app.py#L546-L561) |
| Medium | Reused provider keys do not need to be encrypted once per app. The stored ciphertext model is one record per `key_id`; multiple adapters can share that record by being scoped to the same `key_id`. Duplicate encryption happens only if operators create multiple `key_id`s for the same raw secret. | [eric-questions.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/eric-questions.md#L18-L18), [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1664-L1678), [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L574-L625) |
| Medium | Moving `post-bootstrap.sh` into a container is not a Round 42.2 requirement and does not match the current deployment model. Today the project intentionally expects a host-local repo `.env`, and `post-bootstrap.sh` is the host step that copies runtime values out of `runtime.env` and into that file. | [eric-questions.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/eric-questions.md#L10-L13), [docs/project-memory.md](/home/eric/git/Subumbra/docs/project-memory.md#L22-L31), [docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L134-L148), [post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L19-L27), [post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L65-L77) |
| Medium | “Read app env/config, replace secrets with encrypted values, then shred the old file” is not the current Subumbra contract. Apps are supposed to hold `key_id` references or call the proxy, not store ciphertext blobs directly in their native config files. That idea is future UX/orchestration work, not the right center for 42.2. | [eric-questions.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/eric-questions.md#L14-L17), [README.md](/home/eric/git/Subumbra/README.md#L166-L170), [README.md](/home/eric/git/Subumbra/README.md#L395-L402), [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1668-L1678) |
| Low | The localhost exposure of `subumbra-proxy` is already constrained to `127.0.0.1:8090`. That does not remove the need for container-to-container auth, but it means the current host exposure is deliberately narrow rather than “open to the host” in a broad sense. | [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L172-L190), [docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L186-L194) |

## Detailed Analysis

### 1. Your “not just a LiteLLM round” instinct is correct

The strongest relevant takeaway from your questions is that the real problem is
not “how do we babysit LiteLLM better,” but “what should the authority boundary
look like when the same provider keys are reused across multiple apps?”
[eric-questions.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/eric-questions.md#L1-L9)

The current code already has the right *security* shape for shared use: adapter
identity is separate from key identity. Bootstrap builds a single
`SUBUMBRA_ADAPTER_REGISTRY` where each adapter gets its own token and its own
`allowed_keys` list. [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L574-L625) `subumbra-keys` then enforces those per-adapter scopes at fetch time.
[subumbra-keys/app.py](/home/eric/git/Subumbra/subumbra-keys/app.py#L546-L561)

That means one `openai_prod` record can be reused by:

- LiteLLM
- `subumbra-proxy`
- future adapters

without duplicating the underlying encrypted record, as long as each adapter is
scoped to that same `key_id`.

Where the current product shape still falls down is UX truth. Bootstrap Step 3
still teaches:

- LiteLLM scope = `subumbra:` values in `litellm/config.yaml`
- proxy scope = “explicit/transparent sidecar”

[bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1045-L1056)

README repeats that split and still describes proxy scope as “direct non-LiteLLM
API calls.” [README.md](/home/eric/git/Subumbra/README.md#L221-L226)

So I think your question usefully sharpens the review: if 42.2 migrates LiteLLM
onto `subumbra-proxy`, the round must also update the bootstrap/docs truth for
multi-app/shared-key reality. Otherwise the security model is fine, but the
operator path is misleading.

### 2. Reused keys are not automatically duplicated per app

Your question 4 gets to an important data-model point. The encryption loop writes
one ciphertext record per `key_id` into `keys_payload`. [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1664-L1678)

So if the same raw OpenAI key is intended to be shared across LiteLLM, N8N,
Open WebUI, and future apps, the lowest-friction Subumbra-native way is:

- one `key_id` such as `openai_prod`
- multiple adapters authorized for that same `key_id`

The only time you get multiple encrypted copies is when the operator creates
multiple `key_id`s for the same raw secret. The code does not deduplicate by
secret value; it keys records by `key_id`. [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1664-L1678)

So the answer relevant to 42.2 is:

- shared-key reuse is already architecturally supported
- the friction point is adapter scoping and install UX, not mandatory duplicate
  encryption per app

### 3. Host-side `post-bootstrap.sh` is still part of the current install reality

Your question about moving `post-bootstrap.sh` into a container is reasonable,
but the current project truth is still host-centric:

- fresh installs are terminal-first
- the project expects a repo-local `.env`
- `post-bootstrap.sh` runs on the host after bootstrap

[docs/project-memory.md](/home/eric/git/Subumbra/docs/project-memory.md#L22-L31)

The install guide still says to run `./post-bootstrap.sh` on the host, and its
job is to copy generated runtime values into the repo-local `.env`.
[docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L134-L148)

The script itself does exactly that by reading `runtime.env` out of the
`subumbra-keys` container and writing keys into the host `.env`.
[post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L19-L27),
[post-bootstrap.sh](/home/eric/git/Subumbra/post-bootstrap.sh#L65-L77)

So my evidence-based answer is:

- yes, this is worth discussing later
- no, it is not the real decision point for 42.2

If 42.2 stays about LiteLLM decoupling and runtime-auth authority, moving
`post-bootstrap.sh` into a container would be a broader install/process redesign.

### 4. App-file ingestion and shredding is a future UX/orchestration round, not 42.2

Your idea of pointing Subumbra at existing `.env` or config files, ingesting
their secrets, encrypting them, and shredding the originals is a coherent
future direction. [eric-questions.md](/home/eric/git/Subumbra/council/round-42-2-runtime-auth-reconciliation/eric-questions.md#L14-L17)

But the current product contract is different:

- bootstrap takes secret input and writes encrypted records into `keys.json`
  (not app-owned config files) [bootstrap/subumbra-bootstrap.py](/home/eric/git/Subumbra/bootstrap/subumbra-bootstrap.py#L1668-L1678)
- adapters are supposed to consume `key_id` references or proxy routes
  [README.md](/home/eric/git/Subumbra/README.md#L166-L170)
- LiteLLM today still uses `subumbra:<key_id>` references in its config
  [README.md](/home/eric/git/Subumbra/README.md#L395-L402)

So I think this should influence the round in one narrow way:

- 42.2 should avoid deepening app-specific file mutation assumptions

That means the round should favor:

- Subumbra-owned proxy/key-id surfaces

over:

- app-file rewriting
- env ingestion/shredding automation
- config mutation across arbitrary third-party apps

Those can absolutely become future usability work, but they are a different
class of feature than the current runtime-auth reconciliation round.

### 5. Localhost proxy exposure is intentionally narrow, not broadly host-open

One nuance worth saying plainly: `subumbra-proxy` is currently published only on
`127.0.0.1:8090`, not on all interfaces. [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L182-L183) The install guide says the same. [docs/subumbra-install.md](/home/eric/git/Subumbra/docs/subumbra-install.md#L186-L194)

So your question about “opening Subumbra proxy to the host” is partly answered
by the current config:

- yes, it is host-accessible
- but only localhost-accessible by design

That does not make the auth boundary irrelevant; `subumbra-proxy` still carries
`SUBUMBRA_ACCESS_TOKEN` and `SUBUMBRA_HMAC_KEY` and still has to authenticate to
`subumbra-keys`. [docker-compose.yml](/home/eric/git/Subumbra/docker-compose.yml#L184-L190)

## Commands Run

### Command
```bash
nl -ba bootstrap/subumbra-bootstrap.py | sed -n '560,625p;1042,1056p;1664,1678p'
```

### Important output
```text
574 def _build_adapter_registry(
585         "litellm": {
593         "subumbra-proxy": {
1045 print("  Choose which key_ids each built-in adapter may fetch from subumbra-keys.")
1046 print("  1. LiteLLM: keys referenced by subumbra:key_id values in litellm/config.yaml")
1047 print("  2. subumbra-proxy: keys available through the explicit/transparent sidecar")
1664 for key_id, (provider, target_host, _auth_header, _auth_prefix, raw) in api_keys.items():
1668     keys_payload[key_id] = {
```

### Command
```bash
nl -ba docker-compose.yml | sed -n '172,190p'
```

### Important output
```text
172   subumbra-proxy:
182     ports:
183       - "127.0.0.1:8090:8090"
184     environment:
185       SUBUMBRA_ACCESS_TOKEN: ${SUBUMBRA_TOKEN_PROXY}
186       SUBUMBRA_HMAC_KEY: ${SUBUMBRA_HMAC_KEY}
187       SUBUMBRA_KEYS_URL: http://subumbra-keys:9090
188       CF_WORKER_URL: ${CF_WORKER_URL}
```

### Command
```bash
nl -ba docs/subumbra-install.md | sed -n '134,148p;186,194p'
```

### Important output
```text
134 ## 6. Run `post-bootstrap.sh`
137 ./post-bootstrap.sh
141 `SUBUMBRA_ADAPTER_REGISTRY`, `SUBUMBRA_TOKEN_*`, `SUBUMBRA_HMAC_KEY`, `CF_WORKER_URL`,
186 Expected services: `subumbra-keys` (healthy), `subumbra-proxy` (healthy),
192 - `subumbra-ui` — `127.0.0.1:8080` only
193 - `subumbra-proxy` — `127.0.0.1:8090` only
```

## Recommendations

1. Let your multi-app/shared-key concern influence the approved 42.2 plan in one concrete way: bootstrap prompts, bootstrap summary hints, and install docs should stop teaching LiteLLM-vs-proxy scope as separate worlds if LiteLLM is being moved behind the proxy.
2. Treat “one `key_id`, many adapters” as the preferred current answer for shared provider credentials across apps. It matches the current record model and avoids needless duplicate encryption.
3. Keep containerized `post-bootstrap`, app-file ingestion/shredding, config autodiscovery, and background file watching out of 42.2. They are plausible future usability rounds, but they are broader orchestration features, not the core runtime-auth reconciliation fix.
4. Minimal logging note only: if 42.2 makes proxy scope a first-order operator dependency for LiteLLM, the proxy or install docs should make `403 key_scope_denied` easy to distinguish from provider auth failure. No secret-bearing logging should be added.
