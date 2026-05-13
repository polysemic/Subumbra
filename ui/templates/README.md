# Handoff: Subumbra Dashboard — Secure Key Management UI

## Overview

This is a **high-fidelity browser UI** for [Subumbra](https://github.com/your-repo/subumbra) — a zero-knowledge API key proxy that keeps secrets isolated and encrypted at rest. The dashboard lets administrators:

- Monitor key health, usage counts, and last-access times
- View a live structured audit log of all proxied requests
- **Add new API keys** via a browser with zero plaintext exposure
- **Rotate existing API keys** via the same secure browser flow, or via the CLI

The UI is built in vanilla HTML/CSS/JS with no framework dependencies. The task for Claude Code is to **implement the backend endpoints** that the frontend calls. The frontend is complete and production-ready — it just needs a server to talk to.

---

## About the Design Files

The files in this bundle are **the actual production frontend** — not throwaway prototypes. They are vanilla HTML/CSS/JS and should be served as static files by your Docker service. No build step required.

The design is **dark-themed**, mobile-first, and uses CSS custom properties throughout (`template.css` → `main.css`). Do not modify the frontend files unless fixing a bug — all backend work is API-only.

---

## Fidelity

**High-fidelity.** The frontend is pixel-complete with final colors, typography, spacing, interactions, and all UI states. Recreate nothing — serve it as-is and implement the APIs it calls.

---

## Architecture Overview

```
Browser (dashboard.js)
  │
  ├── GET  /api/status            → poll every 30s, drives all dashboard state
  ├── GET  /api/key-session       → open Add Key or Rotate modal
  ├── DELETE /api/key-session/:id → close modal or page unload
  ├── POST /api/add-key           → submit new key (encrypted)
  └── POST /api/rotate-key        → submit replacement key (encrypted)

Docker service (you implement)
  │
  ├── In-memory session store (Map<sessionId, SessionEntry>)
  ├── RSA-OAEP keypair pool (pre-generated, replenished in background)
  ├── TTL sweep (every 30s, removes expired/used sessions)
  └── Existing subumbra-keys DEK encryption pipeline
```

---

## Security Model — Read This First

The browser **never transmits a plaintext API key**. The flow for both Add Key and Rotate Key is:

1. **Modal opens** → browser calls `GET /api/key-session`
2. **Server** generates an ephemeral RSA-2048-OAEP keypair in memory, stores `{ sessionId → privateKey }` with a TTL, returns `{ sessionId, publicKeyJwk, expiresAt }`
3. **Browser** imports the public key as `extractable: false` via `SubtleCrypto.importKey()` — the key cannot be read back from JS
4. **User pastes** API key → `paste` event is intercepted with `preventDefault()` before the value lands in the input's `.value` → plaintext is passed to `SubtleCrypto.encrypt(RSA-OAEP)` → intermediate `Uint8Array` is zeroed → only the `ArrayBuffer` ciphertext remains in memory
5. **User submits** → `POST` body contains `{ sessionId, ciphertext: base64 }` — no plaintext anywhere
6. **Server** looks up `sessionId` → decrypts with private key → passes plaintext to existing DEK pipeline → zeroes plaintext → **atomically marks session as `used: true` and deletes it** → private key is gone
7. **Browser** on modal close → `DELETE /api/key-session/:id` with `keepalive: true` (survives tab close); fallback to `navigator.sendBeacon`

### Threat mitigations

| Attack | Mitigation |
|---|---|
| Read `input.value` in devtools | Input `.value` is always `""` — paste was intercepted before DOM write |
| Network sniffing | Only RSA-OAEP ciphertext transmitted — useless without server's ephemeral private key |
| Ciphertext replay | Session is single-use: `used` flag set atomically on first claim; second attempt → 410 Gone |
| Session ID guessing | 128-bit cryptographic random UUID |
| Orphaned sessions (tab close, crash) | TTL sweep cleans up within TTL window |
| Multiple modal opens ("oops") | Client DELETEs previous session before requesting a new one; server TTL cleans up any that slip through |
| XSS intercepts keystrokes | No keyboard input accepted — paste-only field |
| Static private key theft | No static key exists — each session generates a fresh ephemeral pair |

---

## API Endpoints

### `GET /api/status`

Polled every 30 seconds. Drives all dashboard state.

**Response shape:**
```json
{
  "keys_loaded": 3,
  "subumbra_keys_healthy": true,
  "subumbra_keys_error": null,
  "worker_reachable": true,
  "worker_auth": "ok",
  "worker_error": null,
  "stats_available": true,
  "audit_available": true,
  "audit_error": null,
  "keys": [
    {
      "key_id": "prod-anthropic-1",
      "provider": "anthropic",
      "request_count": 142,
      "last_access": "2026-04-22T18:30:00Z",
      "created_at": "2026-04-01T09:00:00Z"
    }
  ],
  "recent_log": [
    {
      "timestamp": "2026-04-22T18:30:00Z",
      "adapter_id": "claude-adapter",
      "endpoint": "/v1/messages",
      "key_id": "prod-anthropic-1",
      "remote": "192.168.1.10",
      "verdict": "allow",
      "reason_code": "ok"
    }
  ]
}
```

**Notes:**
- **`worker_auth`** (`ok` \| `stale` \| `unreachable`) is the authoritative Worker auth signal from the proxy. **`worker_reachable`** is the derived boolean: proxy `/health` succeeded **and** `worker_auth == "ok"` (so `stale` can still show `worker_reachable: false` while the Worker is up).
- `provider` must be one of: `anthropic`, `openai`, `groq`, `deepseek` (controls badge colour)
- `verdict` must be one of: `allow`, `deny` (controls log row colour)
- `stats_available: false` → dashboard shows a warning banner; `request_count` and `last_access` may be stale
- `audit_available: false` → dashboard hides the log table and shows an info banner; include `audit_error` string

---

### `GET /api/key-session`

Called when Add Key or Rotate Key modal opens. Must respond quickly — modal shows a spinner until this returns.

**Response shape:**
```json
{
  "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "publicKeyJwk": {
    "kty": "RSA",
    "alg": "RSA-OAEP-256",
    "use": "enc",
    "n": "...",
    "e": "AQAB"
  },
  "expiresAt": "2026-04-22T18:35:00Z"
}
```

**Server-side requirements:**
- Generate RSA-2048 keypair (or pop from pre-warmed pool — see Session Store section)
- `sessionId`: `crypto.randomUUID()` or equivalent 128-bit random
- `expiresAt`: recommend 5 minutes from now (generous to handle slow paste / distraction)
- Store `{ sessionId → { privateKey, expiresAt, used: false } }` in the in-memory session Map
- Export public key as JWK — must include `n`, `e`, `kty: "RSA"`, `alg: "RSA-OAEP-256"`, `use: "enc"`
- The browser imports this with `{ name: "RSA-OAEP", hash: "SHA-256" }` — make sure your keygen matches

**Error responses:**
- `503` if keypair pool is exhausted and generation fails

---

### `DELETE /api/key-session/:id`

Called on modal close, page unload (`keepalive: true`), and before each new session request (replaces previous orphan).

**Behaviour:**
- Look up `sessionId` in session Map
- If found: delete it (private key gone)
- If not found: return `200` silently (idempotent — may have already been consumed or TTL-swept)

**Response:** `200 OK` (no body needed)

---

### `POST /api/add-key`

Submit a new encrypted API key for storage.

**Request body:**
```json
{
  "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "provider": "anthropic",
  "keyId": "prod-anthropic-1",
  "ciphertext": "<base64-encoded RSA-OAEP ciphertext>"
}
```

**Server-side flow:**
1. Look up `sessionId` — if missing or `used: true` → `410 Gone`
2. Check `expiresAt` — if expired → `401 Unauthorized`
3. **Atomically** set `used: true` (prevents replay)
4. RSA-OAEP decrypt `ciphertext` using stored `privateKey` → plaintext API key bytes
5. Pass plaintext to existing subumbra-keys DEK encryption pipeline
6. Zero the plaintext bytes in memory
7. Delete the session entry (private key gone)
8. Return `201 Created`

**Error responses:**
- `400` — missing fields or invalid base64
- `401` — session expired
- `409` — `keyId` already exists (if duplicates not allowed)
- `410` — session already used or not found

**Notes:**
- `keyId` is a user-supplied label (e.g. `prod-anthropic-1`) — validate it is non-empty and safe for your storage key format
- `provider` will be one of: `anthropic`, `openai`, `groq`, `deepseek`

---

### `POST /api/rotate-key`

Replace an existing key's value in place. Identical request shape to `/api/add-key`.

**Request body:**
```json
{
  "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "keyId": "prod-anthropic-1",
  "provider": "anthropic",
  "ciphertext": "<base64-encoded RSA-OAEP ciphertext>"
}
```

**Server-side flow:**
Same as `/api/add-key` steps 1–7, except in step 5:
- Look up existing key record by `keyId` → `404` if not found
- Re-encrypt new plaintext with existing DEK
- Replace the stored ciphertext in place
- Preserve `created_at`, update `last_rotated_at` if tracked

**Error responses:**
- Same as `/api/add-key`, plus:
- `404` — `keyId` not found (can't rotate a key that doesn't exist)

---

## Session Store Implementation

### Recommended: in-memory Map with TTL sweep

```typescript
interface SessionEntry {
  privateKey: CryptoKey;     // or KeyObject in Node, or equivalent
  expiresAt:  number;        // Date.now() ms
  used:       boolean;
}

const sessions = new Map<string, SessionEntry>();

// TTL sweep — run every 30s
setInterval(() => {
  const now = Date.now();
  for (const [id, entry] of sessions) {
    if (entry.used || entry.expiresAt < now) {
      sessions.delete(id);
    }
  }
}, 30_000);
```

**No database, no Redis, no disk.** If the server restarts, all sessions are gone — clients get a `401`/`410` and the modal prompts them to reopen. This is correct behaviour.

### Optional: keypair pool (for burst traffic)

RSA-2048 keygen takes ~2–5ms. For small teams this is fine on-demand. For higher concurrency, pre-warm a pool:

```typescript
const POOL_SIZE = 20;
const pool: CryptoKeyPair[] = [];

async function replenishPool() {
  while (pool.length < POOL_SIZE) {
    const pair = await crypto.subtle.generateKey(
      { name: "RSA-OAEP", modulusLength: 2048, publicExponent: new Uint8Array([1,0,1]), hash: "SHA-256" },
      true,   // exportable so we can send the public key as JWK
      ["encrypt", "decrypt"]
    );
    pool.push(pair);
  }
}

// Run on startup and after each pop
replenishPool();

function popKeypair(): CryptoKeyPair {
  const pair = pool.pop();
  replenishPool(); // refill async in background
  return pair ?? generateKeyOnDemand();
}
```

---

## RSA-OAEP Decryption (Node.js / Web Crypto)

The browser encrypts with `{ name: "RSA-OAEP", hash: "SHA-256" }`. Your server must decrypt with the same parameters.

**Node.js (WebCrypto API — Node 18+):**
```typescript
const { webcrypto } = require("crypto");

async function decryptCiphertext(
  privateKey: CryptoKey,
  ciphertextBase64: string
): Promise<Uint8Array> {
  const ciphertext = Buffer.from(ciphertextBase64, "base64");
  const plaintext  = await webcrypto.subtle.decrypt(
    { name: "RSA-OAEP" },
    privateKey,
    ciphertext
  );
  return new Uint8Array(plaintext);
}

// Zero after use:
// plaintextBytes.fill(0);
```

**Important:** Zero the `Uint8Array` immediately after passing the value to your DEK pipeline. Don't let it sit in memory or get logged.

---

## Existing subumbra-keys Integration

The new endpoints slot into your existing bootstrap/encryption pipeline at step 5 of the server-side flow. The decrypted plaintext API key should be treated identically to a key received via terminal `stdin` during bootstrap:

```
decrypted plaintext
  └→ existing DEK encrypt (AES-256-GCM or equivalent)
  └→ store ciphertext in your key store
  └→ zero plaintext
  └→ done
```

No changes to the DEK or key store format — the browser path is just an alternative input channel for the same pipeline.

---

## Frontend Files

All files are in the project root. Serve them as static assets:

| File | Description |
|---|---|
| `Subumbra Dashboard.html` | Main dashboard page — serve at `/` or `/dashboard` |
| `template.css` | CSS design tokens (`:root` variables) |
| `main.css` | All component styles |
| `dashboard.js` | Dashboard runtime — polling, crypto, modal lifecycles |

No build step. No npm. No bundler. Serve the four files directly.

---

## Design Tokens (key values)

```
Background page:    #061027
Background surface: #1e293b
Background raised:  #263044
Background sunken:  #0f172a
Border:             #334155

Text primary:       #f1f5f9
Text muted:         #94a3b8
Text faint:         #64748b

Accent blue:        #3b82f6
Accent green:       #22c55e
Accent red:         #ef4444
Accent yellow:      #f59e0b

Provider — Anthropic:  bg #451a03  fg #fb923c
Provider — OpenAI:     bg #052e16  fg #4ade80
Provider — Groq:       bg #1e1b4b  fg #a78bfa
Provider — DeepSeek:   bg #082f49  fg #38bdf8

Font sans:  'Segoe UI', system-ui, sans-serif
Font mono:  ui-monospace, 'Cascadia Code', 'Fira Code', monospace
```

---

## Prompt for Claude Code

Paste this prompt into Claude Code (Antigravity) to implement the backend:

---

> I have a complete production frontend for **Subumbra**, a zero-knowledge API key proxy dashboard. The frontend is vanilla HTML/CSS/JS and is already built — I need you to implement the backend API endpoints it calls.
>
> All context is in `design_handoff_subumbra/README.md` in this project. Read it fully before writing any code — it contains the full security model, all five endpoint specs with exact request/response shapes, the session store implementation pattern, the RSA-OAEP decryption code, and how the new endpoints slot into the existing subumbra-keys DEK pipeline.
>
> The frontend files (`Subumbra Dashboard.html`, `template.css`, `main.css`, `dashboard.js`) are in the project root — serve them as static assets. Do not modify them.
>
> Implement in whatever language/runtime the existing subumbra-keys service uses. If it is a Docker service, add the new routes to the existing server rather than creating a new one. The session store should be in-memory only — no database, no Redis, no disk writes for session data.
>
> Start by reading `design_handoff_subumbra/README.md`, then examine the existing subumbra-keys server code to understand the DEK pipeline, then implement the five endpoints in order: `GET /api/status` (if not already present), `GET /api/key-session`, `DELETE /api/key-session/:id`, `POST /api/add-key`, `POST /api/rotate-key`.


## From Claude Design conversation, please cleanup as needed:
Great call — Server-Sent Events (SSE) is the right tool. The server pushes a status event only when something actually changes. The browser's EventSource auto-reconnects if the connection drops. No polling, no timer, no wasted cycles.

Minimal changes: dashboard.js loses the countdown timer, gains an EventSource. The refresh bar becomes a live connection indicator. Manual ↺ now stays for sanity.

Now update the HTML topbar — swap countdown for live indicator:

Also remove the now-unused REFRESH_INTERVAL_S constant:

Done. Here's what changed and what the backend needs:

Frontend: polling timer is gone entirely. On load it does one GET /api/status snapshot, then opens a persistent EventSource to /api/events. The topbar now shows ● live / ● reconnecting… instead of a countdown.

One new backend endpoint needed:

GET /api/events          → text/event-stream

# Push this whenever state changes:
event: status
data: {"keys_loaded":3,"keys":[...],"recent_log":[...],...}

# Same JSON shape as /api/status — reuse that handler
Trigger a push on: key added, key rotated, health state change, new audit log entry. The EventSource auto-reconnects on drop — no client-side retry logic needed. Add this to the handoff README for Claude Code and it'll have everything.