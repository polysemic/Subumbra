# Subumbra UI — Design & Integration Proposal
*Prepared by Claude Design · April 2026*
*For council review — proposed Round 44 (UI Modernisation)*

---

## Overview

This document covers two parallel workstreams:

1. **UI Modernisation** — replace the Bootstrap-based `ui/templates/index.html` with a production-quality vanilla HTML/CSS/JS dashboard (no CDN dependencies, mobile-first, full CSS custom properties, zero inline styles)
2. **Secure Browser Key Management** — add Add Key and Rotate Key flows to the dashboard using ephemeral RSA session keypairs + SubtleCrypto paste interception, so non-terminal users can safely manage keys via the browser

Both streams are designed to leave the Subumbra security boundary (split-decrypt, Cloudflare Worker, HMAC nonce chain) completely untouched.

---

## Part 1 — UI Modernisation

### What is being replaced

`ui/templates/index.html` (22,714 bytes) currently:
- Loads Bootstrap 5.3.3 CSS + JS from `cdn.jsdelivr.net` at browser runtime
- Uses Bootstrap grid classes, `d-none`/`d-flex` toggles, and `bootstrap.Modal` JS
- Has all styles inline in a `<style>` block
- Polls `/api/status` every 30 seconds unconditionally
- Uses inline `onclick` attributes with Bootstrap Modal API

This is flagged in `PROJECT_STATUS.md` as `LOW-5`:
> *"Dashboard loads Bootstrap CSS/JS from public CDN — Browser-only fetch; container is air-gapped"*

### What replaces it

Four files — no build step, no npm, no bundler. Drop into `ui/`:

```
ui/
├── static/
│   ├── template.css     ← all CSS custom properties / design tokens
│   ├── main.css         ← all component styles (no inline styles anywhere)
│   └── dashboard.js     ← vanilla JS, no framework, SubtleCrypto only
└── templates/
    └── index.html       ← semantic HTML shell, loads from /static/
```

### Design system summary

All values live in `template.css` as `:root` custom properties. Change a token, it propagates everywhere:

**Colour scale**
```css
--bg-page:       #061027   /* page background */
--bg-surface:    #1e293b   /* cards, panels */
--bg-raised:     #263044   /* panel headers */
--bg-sunken:     #0f172a   /* inputs, code blocks */
--border-subtle: #334155

--color-green:   #22c55e
--color-red:     #ef4444
--color-yellow:  #f59e0b
--color-blue:    #3b82f6
```

**Provider badge palette** (all four current providers + unknown fallback)
```css
--provider-anthropic-bg: #451a03   --provider-anthropic-fg: #fb923c
--provider-openai-bg:    #052e16   --provider-openai-fg:    #4ade80
--provider-groq-bg:      #1e1b4b   --provider-groq-fg:      #a78bfa
--provider-deepseek-bg:  #082f49   --provider-deepseek-fg:  #38bdf8
```

**Typography, spacing, radius, z-index** — all tokenised. No magic numbers anywhere in `main.css`.

### API compatibility

`GET /api/status` in `ui/app.py` already returns the exact shape the new dashboard expects:

```json
{
  "subumbra_keys_healthy": true,
  "subumbra_keys_error": null,
  "worker_reachable": true,
  "worker_error": null,
  "stats_available": true,
  "audit_available": true,
  "audit_error": null,
  "keys_loaded": 3,
  "keys": [
    {
      "key_id": "anthropic_prod",
      "provider": "anthropic",
      "created_at": "2026-04-01T09:00:00Z",
      "request_count": 142,
      "last_access": "2026-04-22T18:30:00Z"
    }
  ],
  "recent_log": [
    {
      "timestamp": "2026-04-22T18:30:00Z",
      "adapter_id": "subumbra-proxy",
      "endpoint": "get_key",
      "key_id": "anthropic_prod",
      "verdict": "allow",
      "reason_code": "allowed",
      "remote": "172.20.0.4"
    }
  ],
  "dashboard_time": "2026-04-22T18:30:05Z"
}
```

**Zero changes to `ui/app.py` are needed for the static file replacement.** The dashboard renders live the moment the files are in place.

### Resolving LOW-5

By serving CSS/JS from `ui/static/` instead of a CDN, the browser makes zero external requests. The container's air-gapped network constraint is fully respected. Bootstrap is removed entirely — the new CSS is ~1,200 lines of pure custom-properties-driven styles with no framework dependency.

---

## Part 2 — Live Push (SSE) vs. Polling

### Problem with current polling

The existing dashboard polls `/api/status` every 30 seconds unconditionally. This means:
- CPU and network load on every poll regardless of whether anything changed
- Stale data for up to 30 seconds after a state change
- A countdown timer in the topbar that creates false urgency

### Proposed replacement: Server-Sent Events

Replace the polling loop with a persistent SSE connection. The server pushes a `status` event only when state actually changes. The browser's native `EventSource` handles reconnection automatically.

**Browser side** (already implemented in `dashboard.js`):
```javascript
const es = new EventSource("/api/events");
es.addEventListener("status", (e) => {
  applyStatus(JSON.parse(e.data));
});
// EventSource auto-reconnects on drop — no client retry logic needed
```

The topbar countdown is replaced by a live connection indicator:
```
● live          (green, pulsing)
● reconnecting… (red, on drop)
```

A manual `↺ now` button remains for operator sanity.

**Server side — `GET /api/events` in `ui/app.py`:**

```python
import queue, threading, hashlib, json
from flask import Response, stream_with_context

_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()
_last_status_hash: str = ""

def _broadcast_status(payload: dict) -> None:
    data = json.dumps(payload, separators=(",", ":"))
    h = hashlib.sha256(data.encode()).hexdigest()
    global _last_status_hash
    if h == _last_status_hash:
        return          # nothing changed — no push
    _last_status_hash = h
    msg = f"event: status\ndata: {data}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)

def _status_watcher():
    """Background thread: polls subumbra-keys every 3s, broadcasts on change."""
    while True:
        try:
            payload = _build_status_payload()   # same logic as /api/status
            _broadcast_status(payload)
        except Exception:
            pass
        time.sleep(3)

threading.Thread(target=_status_watcher, daemon=True).start()

@app.get("/api/events")
@_require_auth
def api_events():
    q = queue.Queue(maxsize=10)
    with _sse_lock:
        _sse_clients.append(q)

    def generate():
        try:
            # Send current state immediately on connect
            payload = _build_status_payload()
            yield f"event: status\ndata: {json.dumps(payload)}\n\n"
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    yield ": keepalive\n\n"   # prevent proxy timeout
        finally:
            with _sse_lock:
                _sse_clients.remove(q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering if present
        },
    )
```

**Concurrency note:** Each connected browser tab gets its own `Queue`. The watcher thread only polls subumbra-keys (internal network), not the browser. For a small-team POC (2–20 users), a 3-second internal poll interval is indistinguishable from real-time and imposes negligible load. This can be tuned via an env var if needed.

---

## Part 3 — Secure Browser Key Management

### Threat model

The goal is a browser-based key entry path that is equivalent in security to the existing terminal bootstrap path — not weaker. A browser is inherently less trusted than a terminal, so the design compensates with:

1. **Ephemeral session keypairs** — each modal open generates a fresh RSA-2048-OAEP keypair server-side. The private key never leaves the UI container memory. If the server restarts, all pending sessions are invalidated.

2. **Paste interception** — the `paste` event is intercepted with `preventDefault()` before the clipboard value lands in `input.value`. The plaintext exists as a JS string for ~1ms, then is encrypted via `SubtleCrypto.encrypt()`. The intermediate `Uint8Array` is zeroed before GC. The input field's `.value` is always `""` — devtools inspection returns nothing.

3. **Single-use sessions** — each `sessionId` is consumed atomically on first use (`used: true`). Replay of captured ciphertext → `410 Gone`.

4. **Session TTL** — sessions expire after 5 minutes server-side regardless of client behaviour. A background sweep cleans up orphaned sessions every 30 seconds.

5. **`keepalive` DELETE on close** — when the modal closes or the tab unloads, the client fires `DELETE /api/key-session/:id` with `keepalive: true`, ensuring the private key is destroyed even on tab crash.

### Browser-visible attack surface after submit

| What an attacker inspects | What they see |
|---|---|
| `document.querySelector('input').value` | `""` — always empty |
| Network tab body | RSA-OAEP ciphertext (base64) — useless without server private key |
| Replay the captured POST | `410 Gone` — session already consumed |
| Guess sessionId | 2¹²⁸ space — computationally infeasible |
| Orphaned session after tab close | Destroyed within 5 minutes by TTL sweep |
| JS heap dump after submit | Ciphertext ArrayBuffer — no plaintext |

### Modal UX

Both modals have three internal states driven by CSS `.modal-state.active`:

```
akm-loading   → spinner while session keypair is generated
akm-form      → paste field + session TTL indicator + submit
akm-success   → confirmation screen + close
```

The **Rotate Key modal** additionally shows the existing CLI instruction path (docker compose --rotate) above a divider, so terminal-capable operators still have their reference and non-terminal users get the browser path. Both paths are presented clearly; neither is hidden.

---

## Part 4 — Backend Implementation

### Files changed

```
ui/requirements.txt                    ← add: cryptography
ui/static/template.css                 ← new (design tokens)
ui/static/main.css                     ← new (component styles)
ui/static/dashboard.js                 ← new (runtime JS)
ui/templates/index.html                ← replace (shell only)
ui/app.py                              ← add SSE + session + proxy endpoints
subumbra-keys/app.py                   ← add POST /keys/add, POST /keys/rotate/<id>
bootstrap/subumbra-bootstrap.py        ← add can_write_keys to subumbra-ui registry
docker-compose.yml                     ← add SUBUMBRA_PROXY_URL to subumbra-ui env
```

**Files not touched:** `worker/`, `subumbra-proxy/`, `subumbra-probe/`, `litellm/`, `scripts/`, `bootstrap/Dockerfile`, `bootstrap/requirements.txt`, `post-bootstrap.sh`

---

### `ui/app.py` additions

#### Session store (module-level)

```python
import secrets, threading, time
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization

_sessions: dict[str, dict] = {}   # sessionId → {private_key, expires_at, used}
_sessions_lock = threading.Lock()
SESSION_TTL_SECONDS = 300          # 5 minutes

def _session_sweep():
    while True:
        now = time.time()
        with _sessions_lock:
            expired = [sid for sid, s in _sessions.items()
                       if s["used"] or s["expires_at"] < now]
            for sid in expired:
                del _sessions[sid]
        time.sleep(30)

threading.Thread(target=_session_sweep, daemon=True).start()
```

#### `GET /api/key-session`

```python
@app.get("/api/key-session")
@_require_auth
def api_key_session():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    public_jwk = _rsa_public_key_to_jwk(public_key)   # see below

    session_id = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_TTL_SECONDS

    with _sessions_lock:
        _sessions[session_id] = {
            "private_key": private_key,
            "expires_at":  expires_at,
            "used":        False,
        }

    return jsonify({
        "sessionId":    session_id,
        "publicKeyJwk": public_jwk,
        "expiresAt":    datetime.fromtimestamp(expires_at, tz=timezone.utc)
                                .isoformat(timespec="seconds"),
    })
```

#### `DELETE /api/key-session/<session_id>`

```python
@app.delete("/api/key-session/<session_id>")
@_require_auth
def api_delete_key_session(session_id: str):
    with _sessions_lock:
        _sessions.pop(session_id, None)
    return "", 200
```

#### `POST /api/add-key`

```python
@app.post("/api/add-key")
@_require_auth
def api_add_key():
    body = request.get_json(silent=True) or {}
    session_id = body.get("sessionId", "")
    provider   = body.get("provider", "").strip()
    key_id     = body.get("keyId", "").strip()
    ciphertext = body.get("ciphertext", "")

    if not all([session_id, provider, key_id, ciphertext]):
        return jsonify({"error": "missing fields"}), 400

    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None or session["used"]:
            return jsonify({"error": "session not found or already used"}), 410
        if session["expires_at"] < time.time():
            del _sessions[session_id]
            return jsonify({"error": "session expired"}), 401
        session["used"] = True   # atomic claim

    try:
        ct_bytes  = base64.b64decode(ciphertext)
        plaintext = session["private_key"].decrypt(
            ct_bytes,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception:
        return jsonify({"error": "decryption failed"}), 400
    finally:
        with _sessions_lock:
            _sessions.pop(session_id, None)

    try:
        resp = _http.post(
            f"{SUBUMBRA_KEYS_URL}/keys/add",
            json={"key_id": key_id, "provider": provider,
                  "plaintext": plaintext.decode("utf-8")},
        )
        resp.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"subumbra-keys write failed: {e}"}), 502
    finally:
        # Best-effort zero — Python limitation noted in PROJECT_STATUS MEDIUM-1
        if isinstance(plaintext, (bytearray, memoryview)):
            plaintext[:] = b"\x00" * len(plaintext)

    return jsonify({"status": "ok"}), 201
```

#### `POST /api/rotate-key` — identical flow, different subumbra-keys endpoint

```python
@app.post("/api/rotate-key")
@_require_auth
def api_rotate_key():
    # ... same session claim + decrypt as above ...
    resp = _http.post(
        f"{SUBUMBRA_KEYS_URL}/keys/rotate/{key_id}",
        json={"provider": provider, "plaintext": plaintext.decode("utf-8")},
    )
    # ...
```

#### JWK helper

The browser imports the public key via `SubtleCrypto.importKey("jwk", ...)` with `{ name: "RSA-OAEP", hash: "SHA-256" }`. Python's `cryptography` library gives us the raw RSA numbers; we need to convert to JWK:

```python
import base64 as _b64

def _rsa_public_key_to_jwk(pub_key) -> dict:
    pub_numbers = pub_key.public_key().public_numbers() \
        if hasattr(pub_key, "public_key") else pub_key.public_numbers()
    n_bytes = pub_numbers.n.to_bytes(
        (pub_numbers.n.bit_length() + 7) // 8, "big"
    )
    e_bytes = pub_numbers.e.to_bytes(
        (pub_numbers.e.bit_length() + 7) // 8, "big"
    )
    def _b64url(b: bytes) -> str:
        return _b64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")
    return {
        "kty": "RSA",
        "alg": "RSA-OAEP-256",
        "use": "enc",
        "n":   _b64url(n_bytes),
        "e":   _b64url(e_bytes),
    }
```

---

### `subumbra-keys/app.py` additions

Two new write endpoints. Both require the adapter token and a new `can_write_keys` scope check. The encryption logic is identical to what bootstrap does — the same DEK + RSA-OAEP + AES-256-GCM pattern, using `public_key.pem` already on the data volume.

```python
@app.post("/keys/add")
def add_key():
    adapter_result = _resolve_adapter()
    if isinstance(adapter_result, _AdapterDenial):
        return _err("unauthorized", 401)
    if not adapter_result.get("can_write_keys"):
        return _err("forbidden", 403)

    body     = request.get_json(silent=True) or {}
    key_id   = body.get("key_id", "").strip()
    provider = body.get("provider", "").strip()
    plain    = body.get("plaintext", "")

    if not all([key_id, provider, plain]):
        return _err("missing fields", 400)

    keys = _load_keys()
    if key_id in keys:
        return _err("key_id already exists", 409)

    record = _encrypt_key_v2(key_id, provider, plain)  # see below
    keys[key_id] = record
    _write_keys_atomic(keys)   # temp file + os.replace

    log.info("add_key: wrote key_id=%s provider=%s", key_id, provider)
    _record_audit(adapter_id=adapter_result["adapter_id"], key_id=key_id,
                  endpoint="add_key", verdict="allow", reason_code="allowed",
                  remote=request.remote_addr or "")
    return jsonify({"status": "ok"}), 201


@app.post("/keys/rotate/<key_id>")
def rotate_key(key_id: str):
    # ... same auth checks ...
    keys = _load_keys()
    if key_id not in keys:
        return _err("key not found", 404)

    record = _encrypt_key_v2(key_id, keys[key_id]["provider"], plain)
    keys[key_id] = record
    _write_keys_atomic(keys)
    return jsonify({"status": "ok"}), 200
```

**`_encrypt_key_v2`** — reuses the bootstrap crypto pattern:

```python
def _encrypt_key_v2(key_id: str, provider: str, plaintext: str) -> dict:
    from cryptography.hazmat.primitives.asymmetric import padding as _padding
    from cryptography.hazmat.primitives import hashes as _hashes, serialization as _ser
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64, os, hashlib

    pub_key_path = DATA_DIR / "public_key.pem"
    with pub_key_path.open("rb") as fh:
        pub_key = _ser.load_pem_public_key(fh.read())

    der = pub_key.public_bytes(_ser.Encoding.DER, _ser.PublicFormat.SubjectPublicKeyInfo)
    pub_key_fp = "sha256:" + hashlib.sha256(der).hexdigest()

    dek = os.urandom(32)
    wrapped_dek = base64.b64encode(
        pub_key.encrypt(dek, _padding.OAEP(
            mgf=_padding.MGF1(algorithm=_hashes.SHA256()),
            algorithm=_hashes.SHA256(), label=None,
        ))
    ).decode("ascii")

    nonce  = os.urandom(12)
    aesgcm = AESGCM(dek)
    aad    = f"subumbra:v2:{key_id}".encode()
    ct     = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
    ciphertext = base64.b64encode(nonce + ct).decode("ascii")

    return {
        "key_id":      key_id,
        "enc_version": 2,
        "pub_key_fp":  pub_key_fp,
        "wrapped_dek": wrapped_dek,
        "ciphertext":  ciphertext,
        "provider":    provider,
        "target_host": PROVIDER_HOSTS.get(provider, ""),
        "created_at":  _now_iso(),
        "label":       key_id,
    }
```

**`_write_keys_atomic`** — temp file + `os.replace` (same pattern bootstrap uses):

```python
def _write_keys_atomic(keys: dict) -> None:
    tmp = KEYS_FILE.with_suffix(".tmp")
    with tmp.open("w") as fh:
        json.dump(keys, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, KEYS_FILE)
```

---

### `bootstrap/subumbra-bootstrap.py` change

In `_build_adapter_registry()`, add `can_write_keys` to the `subumbra-ui` entry:

```python
"subumbra-ui": {
    "token":          adapter_tokens["subumbra-ui"],
    "allowed_keys":   [],
    "can_list_keys":  True,
    "can_read_stats": True,
    "can_write_keys": True,    # ← new
    "issued_at":      issued_at,
    "expires_at":     expires_at,
},
```

And in `_load_adapter_registry()` parsing, add `can_write_keys` as an optional boolean that defaults to `False` if absent (backward-compatible with existing registry JSON):

```python
can_write_keys = config.get("can_write_keys", False)
if not isinstance(can_write_keys, bool):
    raise RuntimeError(f"SUBUMBRA_ADAPTER_REGISTRY[{adapter_id!r}].can_write_keys must be true/false")
```

This is backward-compatible — existing registry JSON without `can_write_keys` parses as `False`, so no re-bootstrap is required for operators who don't use the browser key entry path.

---

### `docker-compose.yml` change

```yaml
subumbra-ui:
  environment:
    SUBUMBRA_ACCESS_TOKEN: ${SUBUMBRA_TOKEN_UI}
    SUBUMBRA_KEYS_URL: http://subumbra-keys:9090
    CF_WORKER_URL: ${CF_WORKER_URL}
    SUBUMBRA_PROXY_URL: http://subumbra-proxy:8090   # ← add this line
```

`SUBUMBRA_PROXY_URL` is already referenced in `ui/app.py` (`_proxy_get`) but is missing from `docker-compose.yml`. This is a pre-existing gap; the Worker reachability check falls back gracefully but logs a warning.

---

## Part 5 — Security Properties of the New Path

### New trust path introduced

The `POST /api/add-key` flow introduces:

```
Browser → (RSA-OAEP ciphertext over HTTPS/localhost) → ui container
ui container → (plaintext over Docker internal network) → subumbra-keys container
```

This is a new path not present in the original architecture. Its properties:

- **Transit is Docker internal only** — `subumbra_internal` network with `internal: true` enforced by Docker. No host exposure, no outbound routing.
- **Transit duration** — plaintext exists in UI memory for the duration of one HTTP POST to subumbra-keys (~1ms over loopback).
- **Equivalent risk to terminal path** — during bootstrap, the plaintext API key exists in the bootstrap container's RAM and transits to the subumbra-keys volume via the same internal network. This is the same boundary.
- **Python memory zeroing** — best-effort only. Noted in `PROJECT_STATUS.md` as `MEDIUM-1`. Applies equally to the bootstrap path. No new risk introduced.

### Recommended council notation

Suggest adding to `PROJECT_STATUS.md` Known Limitations table:

| ID | Description | Rationale |
|----|-------------|-----------|
| UI-KEY-TRANSIT | Browser add/rotate path: plaintext transits from UI container to subumbra-keys over Docker internal network for ~1ms | Same boundary and duration as bootstrap RAM path; accepted for POC |

---

## Part 6 — Implementation Work Order for Claude Code

Paste the following prompt into Claude Code (Antigravity):

---

> Read `design_handoff_subumbra/README.md` and `claude-design-proposal.md` in full before writing any code.
>
> Implement the Subumbra UI modernisation and secure browser key management described in `claude-design-proposal.md`. Work in this exact order:
>
> **Step 1 — Static files**
> Create `ui/static/` and copy `template.css`, `main.css`, `dashboard.js` from `design_handoff_subumbra/` into it. Update `ui/templates/index.html` to be a minimal shell that serves Flask's template (keep `_require_auth`) but loads CSS and JS from `/static/` instead of Bootstrap CDN. Flask's default static handler at `/static/` works without any route changes.
>
> **Step 2 — `ui/requirements.txt`**
> Add `cryptography` (pin to same version used in `subumbra-keys/requirements.txt` for consistency).
>
> **Step 3 — `ui/app.py` — SSE**
> Add `GET /api/events` as described in Part 2 of the proposal. Extract the status-building logic from `api_status()` into a shared `_build_status_payload()` function so both the REST endpoint and the SSE watcher use the same code path.
>
> **Step 4 — `ui/app.py` — session endpoints and key proxies**
> Add `GET /api/key-session`, `DELETE /api/key-session/<session_id>`, `POST /api/add-key`, `POST /api/rotate-key` as specified in Part 4. Include the JWK helper, session store, TTL sweep thread, and RSA-OAEP decrypt logic exactly as specified.
>
> **Step 5 — `subumbra-keys/app.py`**
> Add `POST /keys/add`, `POST /keys/rotate/<key_id>`, `_encrypt_key_v2()`, and `_write_keys_atomic()` as specified. Add `can_write_keys` scope check. Do not touch any existing endpoint logic.
>
> **Step 6 — `bootstrap/subumbra-bootstrap.py`**
> Add `can_write_keys: true` to `subumbra-ui` in `_build_adapter_registry()`. Add `can_write_keys` as an optional boolean (default `False`) in `_load_adapter_registry()` parsing. These changes must be backward-compatible — existing registry JSON without the field must parse without error.
>
> **Step 7 — `docker-compose.yml`**
> Add `SUBUMBRA_PROXY_URL: http://subumbra-proxy:8090` to the `subumbra-ui` environment block.
>
> After each step, verify the changed file has no syntax errors before moving to the next. Do not modify `worker/`, `subumbra-proxy/`, `subumbra-probe/`, `litellm/`, `scripts/`, or any Dockerfile except to add the `cryptography` dep to `ui/requirements.txt`.
>
> When all steps are complete, summarise: which files changed, what new endpoints exist, and what the operator must do to activate the browser key management path (re-bootstrap to get `can_write_keys` in the adapter registry, or manually add it to the existing `SUBUMBRA_ADAPTER_REGISTRY` env var JSON).

---

## Appendix — Frontend Files

The four frontend files are in `design_handoff_subumbra/` in this project:

| File | Size | Purpose |
|---|---|---|
| `Subumbra Dashboard.html` | shell | HTML structure, ARIA roles, modal markup |
| `template.css` | ~150 lines | CSS custom properties (design tokens only) |
| `main.css` | ~870 lines | All component styles |
| `dashboard.js` | ~560 lines | Runtime JS — SSE, session lifecycle, SubtleCrypto |

These are the exact files to copy into `ui/static/`. No modifications needed.

---

*End of proposal*
