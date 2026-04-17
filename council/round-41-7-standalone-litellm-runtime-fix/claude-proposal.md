# Claude Proposal — Round 41.7: Standalone LiteLLM Runtime Fix

---

## 1. Evidence

### What Round 41.6 proved — do not reopen

1. The Subumbra core is working: `r41-3` passes, transparent proxy path is
   stable at HTTP 200 on first attempt (`codex-verification.md:V5`).
2. The bootstrap import wizard correctly ingests `/opt/litellm/.env` keys:
   10 provider keys detected, `openai_prod` assigned, bootstrap completed
   (`manual-migration-proof.txt` lines 60–103).
3. V2 envelope records are correctly written and the CF Worker decrypts them.
4. The proof harness is self-contained: overlay is in the active round folder.

None of the above is in question for Round 41.7.

### The two distinct standalone LiteLLM failures

**Failure A — `401 Incorrect API key provided: subumbra...`**

LiteLLM is passing the literal string `subumbra:openai_prod` (or similar) as
the `Authorization: Bearer` header to the provider. This is the behavior when
the Subumbra callback has not run. Cause: the callback module is not loaded in
the standalone container. Without interception, LiteLLM treats `subumbra:key_id`
as a raw API key value and forwards it.

Evidence path: `litellm/custom_callbacks.py:364-366` — the callback only
replaces `kwargs["api_key"]` with `SUBUMBRA_ACCESS_TOKEN` at line 435
_after_ successfully fetching the record. If the module never loads, no
interception occurs and the raw `subumbra:key_id` value reaches the provider.

The standalone docker-compose at `/opt/litellm/docker-compose.yml` (per
`docs/testbed-install.md:160-203`) does not mount `custom_callbacks.py`.
Without that mount, the `litellm_settings: callbacks:
custom_callbacks.proxy_handler_instance` reference in `config.yaml` fails to
import. LiteLLM logs the import failure but continues serving — unsubumbra'd
models work, subumbra: models produce 401.

**Failure B — `500 subumbra-keys service is unreachable`**

When the callback does load (after partial alignment), it calls
`_fetch_subumbra_record(key_id)` at `custom_callbacks.py:390`, which issues an
HTTP GET to `SUBUMBRA_KEYS_URL` (`http://subumbra-keys:9090` by default). This
fails after 3 retries and raises "subumbra-keys service is unreachable" at
`custom_callbacks.py:415`.

Root cause: `subumbra-keys` is on the `internal` Docker network only
(`docker-compose.yml:45-46`):

```yaml
subumbra-keys:
  networks:
    - internal    # no subumbra-net
```

The `internal` network has `internal: true` (`docker-compose.yml:9`), meaning
it is a bridge with no outbound routing. Standalone LiteLLM at `/opt/litellm`
is on `subumbra-net` (per `testbed-install.md:162`), which is a different
bridge. There is no common network between the two.

The `subumbra-keys` container comment explicitly states: "Deliberately NOT
published to host ports" (`docker-compose.yml:47`). So the service is
intentionally unreachable from outside Subumbra's own networks.

This is the primary product-side blocker: the network topology does not
accommodate standalone apps joining the `internal` network.

### What a clean standalone path requires

For standalone LiteLLM to use `subumbra:<key_id>` model credentials, it needs:

| Requirement | Current state | Missing |
|------------|--------------|---------|
| `custom_callbacks.py` mounted at `/app/custom_callbacks.py` | Not in `/opt/litellm/docker-compose.yml` | Volume mount |
| `config.yaml` with `subumbra:key_id` api_key refs and `callbacks:` setting | Not in `/opt/litellm/config.yaml` | Operator update |
| `SUBUMBRA_ACCESS_TOKEN` (= `SUBUMBRA_TOKEN_LITELLM` from bootstrap) | Not in `/opt/litellm/.env` | Operator copy from `/opt/subumbra/.env` |
| `SUBUMBRA_HMAC_KEY` | Not in `/opt/litellm/.env` | Operator copy from `/opt/subumbra/.env` |
| `SUBUMBRA_KEYS_URL=http://subumbra-keys:9090` | Not set | Operator set |
| `CF_WORKER_URL` | Not in `/opt/litellm/.env` | Operator copy from `/opt/subumbra/.env` |
| Network route to `subumbra-keys` | No common network | Product change required |
| Compatible LiteLLM version | `main-stable` (unverified) | Needs confirmation |

---

## 2. Current vs Desired

### Current

Standalone LiteLLM at `/opt/litellm`:
- Image: `ghcr.io/berriai/litellm:main-stable` (per `testbed-install.md:168`)
- Networks: `subumbra-net` only
- No `custom_callbacks.py` mount
- `config.yaml` uses `api_key: os.environ/ANTHROPIC_API_KEY` etc.
- `.env` has raw provider keys (or had them, if bootstrap shredded them)
- No Subumbra env vars

Result: all model calls use raw API keys (if present) or fail 401 (if shredded
but callback not wired).

### Desired

Standalone LiteLLM at `/opt/litellm`:
- Runs the Subumbra callback on every `subumbra:key_id` model call
- Can reach `subumbra-keys` at `http://subumbra-keys:9090`
- Has the correct `SUBUMBRA_ACCESS_TOKEN` (LiteLLM-scoped, not proxy-scoped)
- `config.yaml` references `subumbra:key_id` for every model
- No raw provider keys remain in config (they are in CF Secrets only)
- A real completion request through standalone LiteLLM returns HTTP 200

---

## 3. Proposal

### Change 1 — Add a stable name to the `internal` network (product code)

**File:** `docker-compose.yml`

**Change:** Add `name: subumbra_internal` to the `internal` network declaration.

Current (lines 7-10):
```yaml
networks:
  internal:
    driver: bridge
    internal: true
```

After:
```yaml
networks:
  internal:
    driver: bridge
    internal: true
    name: subumbra_internal
```

**Why this is the correct fix:** The `internal: true` Docker property enforces
no outbound routing at the iptables level — it is not defeated by giving the
network a stable name. The name merely makes the network addressable by other
Docker Compose projects. Standalone LiteLLM can then declare it as an external
network and join it, getting Docker DNS access to `subumbra-keys` without any
internet exposure.

**Important:** The first time Subumbra is brought up after this change (if the
unnamed `internal` network already exists), Docker Compose will attempt to
recreate it with the explicit name. This may require a `docker compose down`
and `docker compose up` cycle. Operators with a running deployment must
plan for a brief restart.

### Change 2 — Operator: update `/opt/litellm/docker-compose.yml`

This is an operator change, not a product code change. The implementing agent
documents the exact update; the operator applies it.

Replace the existing `litellm` service definition in `/opt/litellm/docker-compose.yml`
with the following, preserving the operator's `litellm-db` service and volumes
unchanged:

```yaml
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-latest@sha256:7c311546c25e7bb6e8cafede9fcd3d0d622ac636b5c9418befaa32e85dfb0186
    container_name: litellm
    restart: unless-stopped
    ports:
      - "127.0.0.1:4000:4000"
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - /opt/subumbra/litellm/custom_callbacks.py:/app/custom_callbacks.py:ro
    env_file:
      - .env
    environment:
      SUBUMBRA_ACCESS_TOKEN: "${SUBUMBRA_TOKEN_LITELLM}"
      SUBUMBRA_HMAC_KEY: "${SUBUMBRA_HMAC_KEY}"
      SUBUMBRA_KEYS_URL: "http://subumbra-keys:9090"
      CF_WORKER_URL: "${CF_WORKER_URL}"
      CF_ACCESS_CLIENT_ID: "${CF_ACCESS_CLIENT_ID:-}"
      CF_ACCESS_CLIENT_SECRET: "${CF_ACCESS_CLIENT_SECRET:-}"
      DEEPSEEK_API_BASE: "https://api.deepseek.com/v1"
    command: ["--config", "/app/config.yaml", "--port", "4000", "--num_workers", "1"]
    depends_on:
      litellm-db:
        condition: service_healthy
    networks:
      - subumbra_internal
      - subumbra-net

networks:
  subumbra-net:
    external: true
  subumbra_internal:
    external: true
    name: subumbra_internal
```

**Key differences from the testbed baseline:**

- Image is pinned to the same digest as Subumbra's bundled LiteLLM
  (`docker-compose.yml:78`) to avoid version skew in `_wire_transport_once()`
  internals at `custom_callbacks.py:246`
- `/opt/subumbra/litellm/custom_callbacks.py` is mounted at `/app/custom_callbacks.py`
- Subumbra env vars are injected (read from `/opt/litellm/.env`)
- `subumbra_internal` network is joined to reach `subumbra-keys`
- `subumbra-net` is retained for testbed connectivity

### Change 3 — Operator: copy Subumbra tokens into `/opt/litellm/.env`

After bootstrap and `post-bootstrap.sh`, copy the relevant values from
`/opt/subumbra/.env` into `/opt/litellm/.env`:

```bash
# Run from /opt/subumbra
source .env
cat >> /opt/litellm/.env << EOF

# Subumbra integration — added after bootstrap
SUBUMBRA_TOKEN_LITELLM=${SUBUMBRA_TOKEN_LITELLM}
SUBUMBRA_HMAC_KEY=${SUBUMBRA_HMAC_KEY}
CF_WORKER_URL=${CF_WORKER_URL}
CF_ACCESS_CLIENT_ID=${CF_ACCESS_CLIENT_ID:-}
CF_ACCESS_CLIENT_SECRET=${CF_ACCESS_CLIENT_SECRET:-}
EOF
```

The `/opt/litellm/docker-compose.yml` (Change 2) maps `SUBUMBRA_TOKEN_LITELLM`
to `SUBUMBRA_ACCESS_TOKEN` in the container, which is what `custom_callbacks.py:71`
reads.

**Important:** The LiteLLM-scoped token is `SUBUMBRA_TOKEN_LITELLM`, not
`SUBUMBRA_TOKEN_PROXY`. Using the wrong token produces a valid-format token
that `_resolve_adapter()` at `subumbra-keys/app.py:323` rejects with
`adapter_unknown`, returning 401. This would then surface as a 500 from the
callback ("subumbra-keys returned 401 for key_id=...").

### Change 4 — Operator: update `/opt/litellm/config.yaml`

Replace the baseline `config.yaml` (from `testbed-install.md:92-151`) with
Subumbra's `litellm/config.yaml` from `/opt/subumbra`. This config:

- Uses `api_key: "subumbra:key_id"` for all models (`litellm/config.yaml:21-106`)
- Registers the callback at `litellm_settings: callbacks: custom_callbacks.proxy_handler_instance`
  (`litellm/config.yaml:118`)
- Disables the built-in LiteLLM spend database (`litellm/config.yaml:125`)

The operator may also maintain their own `config.yaml` as long as it includes:

```yaml
litellm_settings:
  callbacks: custom_callbacks.proxy_handler_instance

general_settings:
  no_database: true
```

and every `subumbra:`-backed model uses `api_key: "subumbra:<key_id>"` matching
exactly what was assigned during bootstrap.

**Note:** The `general_settings: no_database: true` setting at
`litellm/config.yaml:125` disables LiteLLM's PostgreSQL spend-tracking
database. This means the standalone litellm-db container from the testbed
install is no longer needed and its `depends_on` can be removed. If the
operator wants to retain spend tracking, they must remove `no_database: true`
and keep `database_url: os.environ/DATABASE_URL` in `general_settings` — but
this is operator choice, not a Subumbra requirement.

---

## 4. Failure Modes

| Failure | Symptom | Where to look | Root cause |
|---------|---------|---------------|------------|
| Callback not loading | `401 Incorrect API key provided: subumbra...` | LiteLLM startup logs | `custom_callbacks.py` not mounted, or import failed |
| Wrong token scope | `500 subumbra-keys returned 401 for key_id=...` in LiteLLM logs | `subumbra-keys` logs: `adapter_unknown` | `SUBUMBRA_TOKEN_PROXY` used instead of `SUBUMBRA_TOKEN_LITELLM` |
| `subumbra-keys` unreachable | `500 subumbra-keys service is unreachable` | LiteLLM logs | Network not joined, or `subumbra_internal` network name mismatch |
| Transport wiring failure | `RuntimeError: Subumbra transport wiring requires litellm.module_level_aclient.client` | LiteLLM callback logs | LiteLLM version mismatch — image not pinned to same digest |
| Key scope denied | `403 forbidden` from subumbra-keys, surfaces as `500 subumbra-keys returned 403` | `subumbra-keys` logs: `key_scope_denied` | Bootstrap ran but `LITELLM_ALLOWED_KEYS` not set to include the requested key_id |
| Config key_id mismatch | `subumbra-keys returned 404 for key_id=...` | `subumbra-keys` logs: `key_not_found` | Bootstrap used different key_id than what's in `config.yaml` |
| Token expired | `401` from subumbra-keys, `adapter_expired` in logs | `subumbra-keys` logs | Token TTL expired — re-run bootstrap |

**New failure modes introduced by this round:**

- The `name: subumbra_internal` change will cause Docker Compose to fail the
  first `up` if the unnamed `internal` network already exists with a different
  name. Operator must run `docker compose down` in `/opt/subumbra` first.
  Operator-visible signal: Docker Compose error at startup: "network
  subumbra_internal not found" or conflict.

- If `/opt/subumbra/litellm/custom_callbacks.py` is not present at the path
  expected by the mount, the litellm container fails to start with a volume
  mount error. Operator-visible signal: container exits immediately with
  "invalid mount config" or similar Docker error.

---

## 5. Exclusions

These must NOT be included in Round 41.7:

| Item | Reason |
|------|--------|
| OpenWebUI cutover or N8N cutover | Out of scope per kickoff.md; separate runtime path |
| Preflight automation or config validation scripts | Round 42 operator hardening scope |
| Warning UX in bootstrap for post-bootstrap config steps | Round 42 |
| Changes to `worker/src/worker.js` | Not involved in this failure path |
| Changes to `subumbra-keys/app.py` | Not involved; auth model is correct |
| Changes to `subumbra-proxy/app.py` | Not involved |
| Changes to `bootstrap/subumbra-bootstrap.py` | Bootstrap already correctly creates the right token scopes |
| Changes to `litellm/custom_callbacks.py` | The callback code is correct; the failure is deployment configuration |
| Changing `.env.bootstrap.example` | Leaks verifier assumptions; same exclusion as 41.6 |
| `temp/` workspace relocation for clean-run | Pre-existing deferred scope |
| Modifying `testbed-install.md` standalone LiteLLM compose template | The testbed doc describes the "before" state; it correctly shows the unsubumbra'd baseline. Changing it is documentation polish. If the implementing agent wants to add a "Phase 2: after Subumbra" section, that is acceptable only if it does not change the baseline template. |

---

## 6. Open Questions

**OQ-1 — LiteLLM image version**

`testbed-install.md:168` uses `ghcr.io/berriai/litellm:main-stable`. Subumbra's
bundled LiteLLM uses a pinned digest (`docker-compose.yml:78`). These may be
different versions. The `_wire_transport_once()` function at
`custom_callbacks.py:246` checks for `litellm.module_level_aclient.client`
and raises `RuntimeError` if it doesn't exist.

Open question: does `main-stable` expose `module_level_aclient.client`? If it
does not, transport wiring silently fails (the RuntimeError propagates through
`async_pre_call_deployment_hook` and surfaces as a 500 on the first
subumbra-backed call). The proposal recommends pinning the standalone image to
the same digest as the bundled one. An alternative is to test `main-stable` and
document the minimum required version.

**Position:** Pin to the same digest as the bundled LiteLLM for this round. If
the operator has a reason to use a different version, that's a Round 42 concern
(version compatibility matrix).

**OQ-2 — Token copy step automation**

Change 3 (copy Subumbra tokens to `/opt/litellm/.env`) is a manual operator
step. Should `post-bootstrap.sh` be updated to optionally write Subumbra
integration env vars to `/opt/litellm/.env` automatically?

**Position:** No for this round. `post-bootstrap.sh` writes to `/opt/subumbra/.env`
only. Cross-project env propagation is operator configuration, not a bootstrap
concern. Adding cross-project writes to `post-bootstrap.sh` opens scope toward
the operator-hardening work designated for Round 42.

**OQ-3 — `no_database: true` and the litellm-db service**

Subumbra's `config.yaml` sets `no_database: true` (`litellm/config.yaml:125`),
which disables spend tracking. The standalone testbed's `docker-compose.yml`
(per `testbed-install.md`) runs `litellm-db` (a PostgreSQL container). If the
operator copies Subumbra's config verbatim, the `litellm-db` container becomes
unused and its `depends_on` in the standalone compose should be removed to
avoid unnecessary container startup failures.

**Position:** The proposal documents this as a note in Change 4. It is the
operator's choice whether to retain spend tracking. The implementing agent must
not make that decision in code.

**OQ-4 — `LITELLM_MASTER_KEY` scope**

The standalone `/opt/litellm/.env` has its own `LITELLM_MASTER_KEY` (per
`testbed-install.md:71`). Subumbra's `.env` also has `LITELLM_MASTER_KEY` for
the bundled instance. These are separate values. The standalone LiteLLM reads
its own master key from its own `.env`. No conflict.

However, if the operator runs both bundled and standalone LiteLLM
simultaneously (which would conflict on port 4000), they need to resolve the
port conflict. This round assumes only standalone LiteLLM is active at
`/opt/litellm`. The bundled LiteLLM (Subumbra's `litellm` profile service)
is not activated (profile `litellm` not passed).

**Position:** Document the assumption. Not a blocker.

---

## Proof Required

Round 41.7 succeeds when a single curl through standalone LiteLLM returns an
actual completion response:

```bash
# Run from VPS host
LITELLM_MASTER_KEY="$(grep LITELLM_MASTER_KEY /opt/litellm/.env | cut -d= -f2)"
curl -s http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
  | jq .choices[0].message.content
```

Expected: a short string response (e.g. `"pong"` or similar). HTTP 200.

**The proof artifact must show:**
1. The request used a `subumbra:key_id` model (the model name maps to
   `api_key: "subumbra:openai_prod"` in `config.yaml`)
2. The subumbra-keys `get_key` audit log shows `verdict=allow` for `key_id=openai_prod`
   (confirming the callback successfully fetched the record)
3. HTTP 200 from the completion endpoint
4. Non-empty completion body

No clean-run harness change is needed. The proof is a manual VPS transcript
committed to `council/round-41-7-standalone-litellm-runtime-fix/runs/`.

---

## What Stays In Round 42

The following are correctly identified as Round 42 operator hardening:

- Preflight check for standalone LiteLLM wiring (`custom_callbacks.py` mounted,
  Subumbra env vars present)
- Warning UX in bootstrap completion message pointing to the post-cutover steps
- Version compatibility documentation between Subumbra's LiteLLM image pin and
  the standalone operator's preferred tag
- Automated token propagation from `/opt/subumbra/.env` to downstream app envs
- OpenWebUI and N8N cutover paths
- `testbed-install.md` "Phase 2 Subumbra integration" section
