# Subumbra UI/UX Security Audit — Threat Model & Hardening Proposal
## v2 — Council Ready
*Prepared by Claude Design · April 2026*
*For council review — proposed Round 46 (Security Hardening)*

---

## Preface — Blunt Assessment

Subumbra's core cryptographic model is sound. The split-decrypt architecture
(encrypted blobs local, private key in CF Secrets) is well-designed and the
V2 envelope encryption is correct. The threat model is coherent.

However, the **browser UI surface introduces attack vectors that the terminal
path does not have**, and several of them are currently unmitigated. This
document names them without softening and proposes concrete fixes, most of
which are low-complexity.

The good news: none of the gaps below break the cryptographic core.
An attacker who exploits every UI weakness still cannot recover a plaintext
API key from `keys.json` without the CF private key. The gaps are about
**operational security**, **audit integrity**, and **lateral movement** —
not about breaking AES-256-GCM.

---

## Section 1 — Known Weaknesses, Ranked by Severity

### CRITICAL-UI-1: No CSRF Protection on State-Changing Endpoints

**The hole:**
`POST /api/add-key`, `POST /api/rotate-key`, `POST /keys/<id>/pause`,
`DELETE /keys/<id>` — none of these require a CSRF token. The UI uses
Basic Auth (`UI_USERNAME` / `UI_PASSWORD`). Browsers automatically include
Basic Auth credentials on cross-origin requests if the user is already
authenticated. A malicious page the operator visits while logged into the
dashboard can issue forged requests that delete or pause keys.

This is not theoretical. It is a textbook CSRF vector against Basic Auth.

**Fix:**
Add a `SameSite=Strict` session cookie + CSRF token pattern, OR switch
from Basic Auth to a session cookie with `HttpOnly; Secure; SameSite=Strict`.
The CSRF token is a random value stored server-side per session and required
as a header (`X-CSRF-Token`) on all state-changing requests.

Alternatively: require a `Content-Type: application/json` header check
(browsers cannot set this on cross-origin form posts) as a minimal CSRF
mitigation for the API endpoints, since all our state-changing calls are
already JSON. This is not a complete defence but raises the bar significantly
with one line of code.

**Complexity:** Low. One decorator in `ui/app.py`.

---

### CRITICAL-UI-2: Session Keypairs Stored in Plain Python Dict

**The hole:**
The ephemeral RSA private keys generated for the Add Key / Rotate Key flow
live in `_sessions: dict` in `ui/app.py` process memory. Python's memory
model means:
- The GC decides when to actually free objects — zeroing is not guaranteed
- A memory dump of the `ui` container (e.g. via `docker inspect`, OOM killer
  dump, or a container escape exploit) could expose private keys
- `gc.collect()` does not guarantee immediate reclamation

This is acknowledged in `PROJECT_STATUS.md` as MEDIUM-1 but the UI proposal
introduces new instances of it beyond the bootstrap path.

**Fix — two layers:**
1. Use `cryptography`'s `generate_private_key` and immediately export to DER,
   store as a `bytearray` (mutable, can be zeroed), reconstruct at decrypt time
2. Set `SESSION_TTL_SECONDS = 120` (2 minutes not 5) to minimise window
3. After decrypt, explicitly call `del private_key` and `gc.collect()`

None of this is perfect in CPython — it is best-effort. The council should
accept this as a known limitation with the note that the exposure window is
~2 minutes per session and requires container memory access.

**Complexity:** Low-Medium.

---

### HIGH-UI-1: The Dashboard Has No Authentication By Default

**The hole:**
`UI_USERNAME` is optional. When unset, `_require_auth` is a no-op and the
dashboard is wide open to anyone who can reach port 8080. The compose file
binds to `127.0.0.1:8080` (localhost only), which is the correct default.

But:
- If an operator runs `ports: "8080:8080"` (dropping the `127.0.0.1:`
  binding) to expose it on their network, the dashboard is unauthenticated
- The new Add Key / Rotate Key endpoints would also be unauthenticated
- A misconfigured reverse proxy (nginx, Caddy, Traefik) that forwards
  without auth would expose everything

**Fix:**
Make authentication **mandatory**, not optional. Remove the `if not UI_USERNAME`
bypass. Require `UI_USERNAME` and `UI_PASSWORD` to be set at startup; fail
with a clear error if missing. The current behaviour that allows unauthenticated
localhost access is a footgun.

Additionally: document explicitly in `README.md` and `docker-compose.yml` that
`127.0.0.1:8080` must never be changed to `0.0.0.0:8080` without a reverse
proxy with TLS and auth in front.

**Complexity:** Trivial.

---

### HIGH-UI-2: No TLS Between Browser and UI

**The hole:**
The dashboard is served over plain HTTP on `127.0.0.1:8080`. The session
keypair public key, the encrypted API key ciphertext, and the Basic Auth
credentials all transit this connection. On localhost this is acceptable
(no network egress), but:
- If accessed via SSH port forwarding, the forwarded traffic is unencrypted
  on the remote end
- If a Cloudflare Tunnel or reverse proxy is added without TLS termination,
  the session becomes exploitable via network interception
- Basic Auth credentials in an HTTP request are base64-encoded, not encrypted
  — trivially decoded by any network observer

**Fix:**
Add a `SUBUMBRA_UI_BASE_URL` env var. If it starts with `https://`, the UI
sets `Secure` on cookies and adds `Strict-Transport-Security` headers. If
HTTP, it logs a startup warning. Document that any non-localhost exposure
requires TLS.

For operators who want HTTPS without a reverse proxy: document `mkcert` +
gunicorn TLS as an option. Do not add this to the default stack — it is
out of scope for POC.

**Complexity:** Low (documentation + warning). TLS in gunicorn: Medium.

---

### HIGH-UI-3: Content Security Policy Not Set

**The hole:**
The dashboard HTML has no `Content-Security-Policy` header. This means:
- If an XSS vector is found (e.g. in the audit log rendering — key IDs,
  adapter IDs, and remote IPs are all operator-controlled strings that
  flow into the DOM), injected scripts execute with full page access
- An XSS attacker could intercept the paste event handler, read the
  session keypair request, or exfiltrate the encrypted ciphertext

The current `esc()` function sanitises all server-rendered strings, which
is correct. But defence-in-depth requires CSP as a second layer.

**Fix:**
Add via Flask's `after_request`:
```python
@app.after_request
def set_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "       # no inline scripts
        "style-src 'self'; "        # no inline styles
        "connect-src 'self'; "      # SSE only to same origin
        "img-src 'none'; "
        "object-src 'none'; "
        "frame-ancestors 'none';"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
```

**Caveat:** `script-src 'self'` requires moving all JS to static files
(already done in the Round 44 proposal) and removing any inline `<script>`
blocks. The new dashboard has no inline scripts — this is already compliant.

**Complexity:** Trivial. Five lines in `ui/app.py`.

---

### HIGH-UI-4: Audit Log Is Append-Only But Not Tamper-Evident

**The hole:**
`audit.db` is a SQLite file on a Docker volume. An attacker with write access
to the volume (container escape, Docker socket exposure) can:
- Delete rows covering their activity
- Modify `verdict` from `deny` to `allow`
- Truncate the database entirely

There is no cryptographic chain or external attestation. An operator reviewing
the audit log after a suspected incident cannot know if it has been modified.

**Fix — two tiers:**

*Tier 1 (low complexity):* Hash-chain the audit log. Each row stores
`HMAC(previous_row_hash + current_row_data, SUBUMBRA_HMAC_KEY)`. Verification
is a sequential scan. A gap or hash mismatch indicates tampering. This is
detectable but not preventable.

*Tier 2 (medium complexity):* Periodic audit log export to an append-only
external store — syslog over TLS, a write-only S3 bucket, or a Cloudflare
Worker that accepts log entries and stores them in KV (already available
in the stack). An attacker who compromises the local container cannot
retroactively modify what was already shipped out.

For POC: Tier 1 is sufficient. Add a `audit_chain_hash` column to
`audit_events`. Expose a `GET /audit/verify` endpoint that checks the chain
and returns `{"valid": true}` or `{"valid": false, "first_broken_id": N}`.
Surface this in the dashboard as a `✓ Audit chain intact` / `⚠ Chain broken`
indicator.

**Complexity:** Tier 1 = Medium. Tier 2 = Medium-High.

---

### MEDIUM-UI-1: No Brute-Force Protection on the UI Auth Endpoint

**The hole:**
`GET /` and `GET /api/status` with `_require_auth` log a warning on failure
but do not rate-limit or lock out repeated bad attempts. An attacker with
network access to port 8080 can brute-force `UI_USERNAME` / `UI_PASSWORD`
at full HTTP speed. On localhost this requires local access — still a real
threat in a shared server environment.

**Fix:**
Add a simple in-memory failed attempt counter per remote IP:
```python
_auth_failures: dict[str, list[float]] = defaultdict(list)
MAX_AUTH_FAILURES = 10
AUTH_LOCKOUT_SECONDS = 300

def _check_auth_lockout(remote: str) -> bool:
    now = time.time()
    attempts = _auth_failures[remote]
    # Prune old attempts
    _auth_failures[remote] = [t for t in attempts if now - t < AUTH_LOCKOUT_SECONDS]
    return len(_auth_failures[remote]) >= MAX_AUTH_FAILURES
```

Return `429 Too Many Requests` with `Retry-After: 300` when locked out.
Log the lockout event.

**Complexity:** Low.

---

### MEDIUM-UI-2: `plaintext` Field Transits Internal Network as UTF-8 JSON

**The hole:**
In the Add Key / Rotate Key flow, `ui/app.py` decrypts the browser ciphertext
and sends the plaintext API key as a JSON field to `subumbra-keys`:
```json
POST /keys/add  {"key_id": "...", "provider": "...", "plaintext": "sk-ant-..."}
```

This transit is over the Docker internal network (no internet exposure) and is
authenticated by `X-Subumbra-Token`. However:
- The plaintext is in a JSON body — it will appear in any request logging
  middleware that logs bodies
- If `subumbra-keys` ever logs request bodies for debugging, the key leaks
- `httpx` connection error logs may include request bodies in some error paths

**Fix:**
1. Ensure `subumbra-keys` **never** logs request bodies (review all log calls —
   currently none log bodies, but add a comment documenting this constraint)
2. Consider a separate HMAC-authenticated internal protocol for the write path
   that does not use JSON body for the secret — e.g. pass the plaintext as a
   request header (`X-Subumbra-Plaintext`) so it is less likely to be logged
   by body-logging middleware. Headers are still cleartext but are less
   commonly logged wholesale.
3. Long term: encrypt the plaintext for the internal transit using the same
   RSA public key that bootstrap uses — then `subumbra-keys` receives a
   ciphertext it can decrypt locally, and the UI never holds the plaintext.
   This closes the transit gap entirely.

**Complexity:** Low (logging review). Medium (header approach). High (full
internal encryption — eliminates the transit gap).

---

### MEDIUM-UI-3: No Rate Limiting on Session Keypair Generation

**The hole:**
`GET /api/key-session` generates an RSA-2048 keypair on each call. RSA keygen
costs ~2–5ms of CPU. An authenticated attacker (or a bug that causes repeated
modal opens) can call this endpoint in a tight loop and consume significant CPU.
On a minimal server, 200 concurrent calls = ~1 second of CPU — not a DoS but
noticeable.

**Fix:**
Rate-limit `GET /api/key-session` per authenticated user (or per IP if
unauthenticated):
- Max 10 session requests per minute per remote IP
- Return `429` with `Retry-After` header when exceeded
- Use the same in-memory counter as the auth brute-force protection

Also: implement the keypair pool proposed in the Round 44 handoff document.
Pre-generated pairs are ready instantly and the pool refills in a background
thread — no per-request generation cost.

**Complexity:** Low (rate limit). Medium (pool).

---

### LOW-UI-1: Basic Auth Credentials Visible in Browser History / Logs

**The hole:**
Basic Auth credentials (`Authorization: Basic base64(user:pass)`) appear in:
- Browser developer tools network tab
- Nginx/proxy access logs (if a proxy is in front)
- Any HTTP debugging tool the operator uses

They are base64 — not encrypted. Anyone who can read the browser network tab
while the dashboard is open sees the credentials.

**Fix:**
Switch to a session cookie flow:
1. `POST /login` accepts `{username, password}` as JSON
2. On success: set `HttpOnly; Secure; SameSite=Strict` session cookie
3. All other endpoints check the cookie, not Basic Auth
4. `POST /logout` clears the cookie

The session token is a `secrets.token_urlsafe(32)` stored server-side.
This also enables proper session expiry and makes CSRF protection straightforward.

**Complexity:** Medium. This replaces Basic Auth entirely — a worthwhile
improvement but a larger change than the others.

---

### LOW-UI-2: No Key Provenance or Integrity Check at Add Time

**The hole:**
When an operator pastes an API key into the Add Key modal, Subumbra accepts
any string and encrypts it. There is no validation that:
- The string is the correct format for the claimed provider
  (e.g. Anthropic keys start with `sk-ant-`, OpenAI with `sk-`)
- The key is actually valid (i.e. accepted by the provider's API)
- The key belongs to the claimed provider

An operator who accidentally pastes the wrong key (or an attacker who
intercepts and substitutes the paste content via a browser extension) will
have the wrong key silently stored and encrypted.

**Fix — two layers:**

*Layer 1 (client-side format check):* After paste interception and before
encryption, validate the format against known provider patterns:
```javascript
const PROVIDER_PATTERNS = {
  anthropic: /^sk-ant-[a-zA-Z0-9_-]{40,}$/,
  openai:    /^sk-[a-zA-Z0-9]{48,}$/,
  groq:      /^gsk_[a-zA-Z0-9]{50,}$/,
};
```
If the format does not match, show a warning (not a block — patterns change).

*Layer 2 (optional live validation):* Add a `POST /api/validate-key` endpoint
that makes a minimal API call to the provider (e.g. `GET /v1/models` for
OpenAI) using the decrypted key and returns valid/invalid before storing.
This is optional and has the tradeoff of the plaintext key being used in a
live network request during validation — noisy but detectable compromise.

**Complexity:** Layer 1 = Low. Layer 2 = Medium + policy decision.

---

## Section 2 — Zero-Day Attack Surface

Honest enumeration of where a zero-day could hurt:

| Component | Zero-day impact | Blast radius |
|---|---|---|
| Flask (ui/app.py) | RCE in the UI container | Access to session private keys (~2min window), access to subumbra-keys internal API |
| Flask (subumbra-keys) | RCE in the keys container | Read `keys.json` (ciphertext only — still useless without CF private key) |
| Python `cryptography` library | Break RSA-OAEP or AES-GCM | Catastrophic — all stored keys compromised |
| Cloudflare Worker runtime | Break the DO isolation | CF private key exposed — catastrophic |
| Docker runtime | Container escape | Access to all container filesystems and volumes |
| SQLite | Unlikely — but memory corruption | Audit log corruption |
| Browser (SubtleCrypto) | Break WebCrypto | Session private key or plaintext exposed in browser |

**The most realistic zero-day vector is Flask or Python `cryptography`.**
Both are widely deployed, well-audited libraries. Pin versions and update
regularly. Add a `dependabot` config or equivalent to the repo.

**The catastrophic zero-day vector is the Cloudflare Worker runtime** — but
this is Cloudflare's problem, not ours. We have no mitigation path. It is
an accepted architectural dependency.

**The Docker container escape** is the most operationally realistic threat
for a self-hosted system. Mitigations:
- Run containers as non-root (add `user: 1000:1000` to compose services)
- Set `read_only: true` on filesystems where possible
- Drop Linux capabilities: `cap_drop: [ALL]`, add back only what's needed
- Use `no-new-privileges: true`

None of these are currently in `docker-compose.yml`.

---

## Section 3 — What the UI/UX Does Well

To be complete and fair:

- **Paste interception** — correct. `preventDefault()` before DOM write is
  the right approach. The `input.value` is always `""`.
- **Single-use sessions** — correct. The `used` flag prevents replay.
- **`keepalive` DELETE on close** — correct. Orphaned sessions self-destruct.
- **`esc()` on all server-rendered strings** — correct. XSS via the audit
  log is mitigated.
- **`X-Content-Type-Options: nosniff`** — set in the HTML meta tag. Should
  also be a response header (see HIGH-UI-3 fix above).
- **No CDN dependencies** (post Round 44) — correct. Container is air-gapped.
- **`extractable: false` on imported CryptoKey** — correct. The browser
  cannot export the public key back out of the WebCrypto context.
- **Session TTL** — correct. 5-minute window limits exposure.

---

## Section 4 — Recommended Hardening by Priority

### Do immediately (trivial, high impact)

1. **Set security headers** (CSP, X-Frame-Options, Referrer-Policy) — 5 lines
2. **Require auth always** — remove the `if not UI_USERNAME` bypass — 3 lines
3. **Log startup warning if HTTP and no TLS** — document the risk
4. **Run containers as non-root** — add `user:` to docker-compose.yml

### Do in Round 46 (low-medium complexity)

5. **CSRF token on state-changing endpoints** — or at minimum `Content-Type` check
6. **Auth brute-force rate limiting** — in-memory counter, 10 failures = 5min lockout
7. **Session keypair rate limiting** — max 10/minute per IP
8. **Audit log hash chain** (Tier 1) — detectable tampering
9. **Provider key format validation** (client-side only)

### Do before MVP (medium complexity)

10. **Replace Basic Auth with session cookie** — enables proper CSRF, session expiry
11. **Keypair pool** — eliminates per-request RSA generation cost
12. **Internal transit encryption** — UI → subumbra-keys using the RSA public key
13. **Docker hardening** — `cap_drop`, `no-new-privileges`, `read_only`

### Deferred (high complexity, post-MVP)

14. **Audit log external export** — Tier 2 tamper-evidence
15. **Worker-level enforcement** — KV state for pause/time restrictions
16. **Live key validation** — optional provider API check at add time

---

## Section 5 — Proposed `PROJECT_STATUS.md` Additions

New entries for the Known Limitations table:

| ID | Description | Rationale |
|----|-------------|-----------|
| UI-CSRF-1 | State-changing UI endpoints lack CSRF token protection; Basic Auth does not prevent CSRF | Mitigated by localhost-only binding; fix in Round 46 |
| UI-AUTH-1 | UI authentication is optional; unauthenticated access possible if `UI_USERNAME` unset | Fix: make mandatory in Round 46 |
| UI-CSP-1 | No Content-Security-Policy header on dashboard responses | Fix: trivial, Round 46 |
| UI-TRANSIT-1 | Plaintext API key transits UI→subumbra-keys as JSON over internal Docker network | Same boundary as bootstrap path; accepted for POC |
| UI-AUDIT-1 | Audit log is not tamper-evident; local write access allows undetected modification | Tier 1 hash-chain proposed for Round 46 |
| UI-CONTAINER-1 | Containers run as root with default Linux capabilities | Docker hardening deferred to pre-MVP |

---

*End of proposal*
