/**
 * Subumbra Proxy — Cloudflare Worker + Durable Object
 * ─────────────────────────────────────────────────────────────────────────────
 * V2/V3 Asymmetric Envelope Encryption:
 *   - Each API key record has its own DEK wrapped by the RSA public key
 *   - A SQLite-backed vault Durable Object generates and stores the key pair
 *   - The vault decrypts the envelope and proxies the upstream request
 *   - Offline rotation still uses the public_key.pem artifact from bootstrap
 *   - Nothing sensitive is logged, stored outside the vault, or returned
 *
 * Endpoints:
 *   GET  /health   → liveness check (no auth required)
 *   POST /proxy    → canonical Subumbra core API; see docs/adapter-contract.md
 *
 * Auth header required on /proxy:
 *   X-Subumbra-Token: <one adapter token from SUBUMBRA_ADAPTER_TOKENS>
 *
 * CF Secrets consumed:
 *   SUBUMBRA_ADAPTER_TOKENS — JSON array of adapter tokens, set by bootstrap
 *   SUBUMBRA_MANAGEMENT_TOKEN — management bearer token, set by bootstrap
 *   SUBUMBRA_SETUP_TOKEN    — transient setup token, set by bootstrap
 */

"use strict";

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

function parseStructuredRegistryJson(raw, keyName) {
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    const err = new Error(`${keyName} invalid JSON`);
    err.code = "registry_invalid_json";
    throw err;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    const err = new Error(`${keyName} invalid schema`);
    err.code = "registry_invalid_schema";
    throw err;
  }
  return parsed;
}

function optionalStringArray(value) {
  if (!Array.isArray(value)) {
    return null;
  }
  const parsed = [];
  for (const entry of value) {
    if (typeof entry !== "string" || !entry) {
      return null;
    }
    parsed.push(entry);
  }
  return parsed;
}

function optionalGatePolicy(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  if (!Array.isArray(value.require_approval) || value.require_approval.length === 0) {
    return null;
  }
  const rules = [];
  for (const entry of value.require_approval) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      return null;
    }
    if (!Number.isInteger(entry.timeout_seconds) || entry.timeout_seconds <= 0) {
      return null;
    }
    const when = entry.when;
    if (!when || typeof when !== "object" || Array.isArray(when)) {
      return null;
    }
    const normalizedWhen = {};
    if (when.any_request === true) {
      normalizedWhen.any_request = true;
    }
    if (typeof when.adapter === "string" && when.adapter) {
      normalizedWhen.adapter = when.adapter;
    }
    if (typeof when.method === "string" && when.method) {
      normalizedWhen.method = when.method;
    }
    if (typeof when.path_prefix === "string" && when.path_prefix) {
      normalizedWhen.path_prefix = when.path_prefix;
    }
    if (Object.keys(normalizedWhen).length === 0) {
      return null;
    }
    rules.push({
      when: normalizedWhen,
      timeout_seconds: entry.timeout_seconds,
    });
  }
  return {
    require_approval: rules,
  };
}

async function getRegistryEntry(env, keyId) {
  const registryVersion = await env.PROVIDER_REGISTRY_KV.get("registry_version", {
    cacheTtl: 30,
  });
  if (!registryVersion) {
    const err = new Error("registry_version missing");
    err.code = "registry_missing";
    throw err;
  }
  if (registryVersion !== "1") {
    const err = new Error(`unsupported registry_version '${registryVersion}'`);
    err.code = "registry_invalid_schema";
    throw err;
  }

  const keyRaw = await env.PROVIDER_REGISTRY_KV.get(`key:${keyId}`, {
    cacheTtl: 30,
  });
  if (!keyRaw) {
    const err = new Error(`key:${keyId} missing`);
    err.code = "registry_missing";
    throw err;
  }
  const keyEntry = parseStructuredRegistryJson(keyRaw, `key:${keyId}`);
  const policyId = keyEntry.policy_id;
  if (typeof policyId !== "string" || !policyId) {
    const err = new Error(`key:${keyId} missing policy_id`);
    err.code = "registry_invalid_schema";
    throw err;
  }

  const policyRaw = await env.PROVIDER_REGISTRY_KV.get(`policy:${policyId}`, {
    cacheTtl: 30,
  });
  if (!policyRaw) {
    const err = new Error(`policy:${policyId} missing`);
    err.code = "registry_missing";
    throw err;
  }
  const policy = parseStructuredRegistryJson(policyRaw, `policy:${policyId}`);
  const policyTarget = policy.target;
  if (!policyTarget || typeof policyTarget !== "object" || typeof policyTarget.host !== "string") {
    const err = new Error(`policy:${policyId} missing target.host`);
    err.code = "registry_invalid_schema";
    throw err;
  }

  if (keyEntry.target_host !== policyTarget.host) {
    const err = new Error(`key:${keyId} target_host does not match policy target.host`);
    err.code = "registry_invalid_schema";
    throw err;
  }

  const allow = policy.allow ?? {};
  const policyAuth = policy.auth ?? {};
  return {
    key_id: keyEntry.key_id,
    policy_id: policyId,
    policy_hash: keyEntry.policy_hash,
    target_host: keyEntry.target_host,
    provider_id: keyEntry.provider,
    paused: keyEntry.paused === true,
    auth_scheme: typeof policyAuth.scheme === "string" ? policyAuth.scheme : "bearer",
    auth_header_name:
      typeof policyAuth.header_name === "string" ? policyAuth.header_name : null,
    auth_query_param:
      typeof policyAuth.query_param === "string" ? policyAuth.query_param : null,
    allow_adapters: Array.isArray(allow.adapters) ? allow.adapters : [],
    allow_methods: Array.isArray(allow.methods) ? allow.methods : [],
    allow_path_prefixes: Array.isArray(allow.path_prefixes) ? allow.path_prefixes : [],
    allow_content_types: Array.isArray(allow.content_types) ? allow.content_types : [],
    allow_max_body_bytes: typeof allow.max_body_bytes === "number" ? allow.max_body_bytes : null,
    allow_request_headers: Array.isArray(allow.request_headers) ? allow.request_headers : [],
    intent: policy.intent && typeof policy.intent === "object" ? policy.intent : null,
    deny_patterns: Array.isArray(policy.response?.deny_patterns) ? policy.response.deny_patterns : [],
    response_allow_headers: Array.isArray(policy.response?.allow_headers)
      ? policy.response.allow_headers
      : [],
    gate: optionalGatePolicy(policy.gate),
    velocity: policy.velocity && typeof policy.velocity === "object"
      ? {
        adapter_rpm: typeof policy.velocity.adapter_rpm === "number" ? policy.velocity.adapter_rpm : null,
        key_rpm: typeof policy.velocity.key_rpm === "number" ? policy.velocity.key_rpm : null,
        breaker_failures: typeof policy.velocity.breaker_failures === "number" ? policy.velocity.breaker_failures : null,
        breaker_cooldown_seconds: typeof policy.velocity.breaker_cooldown_seconds === "number" ? policy.velocity.breaker_cooldown_seconds : null,
      }
      : null,
  };
}

// Headers that must not be forwarded to the upstream provider
// (they reference our internal infrastructure, not the provider)
const HOP_BY_HOP_HEADERS = new Set([
  "host",
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "x-subumbra-token",
  "x-subumbra-timestamp",
  "x-subumbra-signature",
  "cf-connecting-ip",
  "cf-ray",
  "cf-visitor",
  "cf-ipcountry",
  "authorization",
  "x-api-key",
  "x-api-key-id",
]);

const VAULT_INSTANCE_NAME = "vault";
const GATE_INSTANCE_NAME = "gate";
const VAULT_SETUP_PATH = "/setup-keygen";
const VAULT_SSH_KEYGEN_PATH = "/ssh-keygen";
const VAULT_SSH_IMPORT_PATH = "/ssh-import";
const VAULT_SSH_SIGN_PATH = "/ssh-sign";
const VAULT_STATUS_PATH = "/status";
const VAULT_EXECUTE_PATH = "/execute";
const VAULT_ROTATE_PATH = "/rotate";
const VAULT_RESET_PATH = "/reset";
const VAULT_MANAGEMENT_AUDIT_PATH = "/management-audit";
const VAULT_RATE_CHECK_PATH = "/rate-check";
const GATE_SUBMIT_PATH = "/submit";
const GATE_CONSUME_PATH = "/consume";
const AUTH_RATE_LIMITS = {
  "auth-ping": 20,
  "manage-key": 20,
  "proxy": 120,
  "setup-keygen": 5,
  "ssh-keygen": 3,
  "ssh-import": 3,
  "ssh-sign": 60,
  "internal-rotate": 5,
  "internal-vault-status": 5,
  "internal-vault-reset": 5,
};
const GATE_SCHEMA = `
  CREATE TABLE IF NOT EXISTS pending_requests (
    request_id TEXT PRIMARY KEY,
    flow TEXT NOT NULL,
    key_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    target_summary TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL,
    approval_token_hash TEXT NOT NULL,
    terminal_at TEXT,
    consumed_at TEXT
  );
  CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint_hash TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    p256dh TEXT NOT NULL,
    auth_secret TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
  );
`;
const VAULT_SCHEMA = `
  CREATE TABLE IF NOT EXISTS custody (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    private_key_pkcs8 BLOB NOT NULL,
    public_key_spki BLOB NOT NULL,
    pub_key_fp TEXT NOT NULL,
    created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS ssh_keys (
    key_id TEXT PRIMARY KEY,
    private_key_pkcs8 BLOB NOT NULL,
    public_key_raw BLOB NOT NULL,
    public_key_ssh TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS ssh_session_quota (
    session_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    max_sign_ops INTEGER NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (session_id, adapter_id, key_id)
  );
  CREATE TABLE IF NOT EXISTS velocity_counters (
    scope TEXT NOT NULL,
    adapter_id TEXT,
    key_id TEXT,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (scope, adapter_id, key_id, window_start)
  );
  CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    key_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL,
    opened_at INTEGER NOT NULL,
    half_open_probe_active INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS management_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    operation TEXT NOT NULL,
    key_id TEXT,
    actor_token_prefix TEXT NOT NULL,
    result TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS auth_attempts (
    endpoint TEXT NOT NULL,
    ip_key TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (endpoint, ip_key, window_start)
  );
`;

// ─────────────────────────────────────────────────────────────────────────────
// Crypto helpers  (Web Crypto API — available in CF Workers)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Decode a base64 string to a Uint8Array without using Buffer (not available
 * in CF Workers).
 */
function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

async function decryptV3(privateKey, expectedPubKeyFp, ciphertextB64, wrappedDekB64, pubKeyFp, keyId, policyHash) {
  if (!policyHash) {
    throw new Error("missing required V3 policy_hash");
  }
  if (pubKeyFp !== expectedPubKeyFp) {
    throw new Error(
      `record wrapped with unknown key pair (record: ${pubKeyFp}, ` +
      `loaded: ${expectedPubKeyFp}) — re-bootstrap required`
    );
  }

  const dekBytes = await crypto.subtle.decrypt(
    { name: "RSA-OAEP" },
    privateKey,
    base64ToBytes(wrappedDekB64),
  );

  const dekKey = await crypto.subtle.importKey(
    "raw", dekBytes, { name: "AES-GCM" }, false, ["decrypt"],
  );

  const aad = new TextEncoder().encode(`subumbra:v3:${keyId}:${policyHash}`);
  const ctBlob = base64ToBytes(ciphertextB64);
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: ctBlob.slice(0, 12), additionalData: aad },
    dekKey,
    ctBlob.slice(12),
  );

  return new TextDecoder().decode(plaintext);
}

// ─────────────────────────────────────────────────────────────────────────────
// Auth helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Constant-time string comparison to prevent timing attacks.
 * Signs both candidate strings with HMAC-SHA256 and XOR-compares the resulting
 * fixed-length digests so equality checks do not short-circuit on prefix leaks.
 */
async function timingSafeEqual(a, b) {
  if (!a || !b) return false;
  const enc = new TextEncoder();
  const ka = await crypto.subtle.importKey(
    "raw", enc.encode(a), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const kb = await crypto.subtle.importKey(
    "raw", enc.encode(b), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sigA = await crypto.subtle.sign("HMAC", ka, enc.encode("subumbra"));
  const sigB = await crypto.subtle.sign("HMAC", kb, enc.encode("subumbra"));

  // Both signatures are the same length (HMAC-SHA256 = 32 bytes), so
  // comparing them leaks only whether the keys were equal, not the keys.
  const bytesA = new Uint8Array(sigA);
  const bytesB = new Uint8Array(sigB);
  let diff = 0;
  for (let i = 0; i < bytesA.length; i++) {
    diff |= bytesA[i] ^ bytesB[i];
  }
  return diff === 0;
}

function parseAdapterTokens(raw) {
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("SUBUMBRA_ADAPTER_TOKENS must be valid JSON");
  }

  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new Error("SUBUMBRA_ADAPTER_TOKENS must be a non-empty JSON array");
  }
  for (const entry of parsed) {
    if (
      !entry ||
      typeof entry !== "object" ||
      typeof entry.id !== "string" ||
      !entry.id ||
      typeof entry.token !== "string" ||
      !entry.token
    ) {
      throw new Error(
        "SUBUMBRA_ADAPTER_TOKENS entries must be {id, token} objects with non-empty string fields"
      );
    }
  }
  return parsed;
}

async function resolveAdapterToken(incomingToken, validTokens) {
  let matchedId = null;
  for (const entry of validTokens) {
    if (await timingSafeEqual(incomingToken, entry.token)) {
      matchedId = entry.id;
    }
  }
  return matchedId;
}

// ─────────────────────────────────────────────────────────────────────────────
// Response helpers
// ─────────────────────────────────────────────────────────────────────────────

const DEFAULT_JSON_HEADERS = {
  "content-type": "application/json",
  "cache-control": "no-store",
  "pragma": "no-cache",
  "x-content-type-options": "nosniff",
  "cross-origin-resource-policy": "same-origin",
  "strict-transport-security": "max-age=31536000; includeSubDomains",
};

function jsonResponse(payload, status = 200, extraHeaders = {}) {
  return new Response(payload == null ? null : JSON.stringify(payload), {
    status,
    headers: {
      ...DEFAULT_JSON_HEADERS,
      ...extraHeaders,
    },
  });
}

function jsonError(message, status, extraHeaders = {}) {
  return jsonResponse({ error: message }, status, extraHeaders);
}

function buildSshQuotaHeaders(sessionId, signCount, maxSignOps, limitReached = false) {
  const headers = {
    "X-Subumbra-Session-Id": sessionId,
    "X-Subumbra-Sign-Count": String(signCount),
    "X-Subumbra-Sign-Limit": String(maxSignOps),
  };
  if (limitReached) {
    headers["X-Subumbra-Sign-Limit-Reached"] = "1";
  }
  return headers;
}

function parseSshSessionScopeMetadata(rawValue, expectedAdapterId, expectedKeyId) {
  let parsed;
  try {
    parsed = JSON.parse(rawValue);
  } catch {
    throw new Error("invalid ssh session scope JSON");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("invalid ssh session scope shape");
  }
  if (typeof parsed.session_id !== "string" || !parsed.session_id) {
    throw new Error("missing ssh session_id");
  }
  if (typeof parsed.adapter_id !== "string" || parsed.adapter_id !== expectedAdapterId) {
    throw new Error("invalid ssh adapter_id");
  }
  if (typeof parsed.key_id !== "string" || parsed.key_id !== expectedKeyId) {
    throw new Error("invalid ssh key_id");
  }
  if (typeof parsed.expires_at !== "string" || !parsed.expires_at) {
    throw new Error("missing ssh expires_at");
  }
  const expiresAtMs = Date.parse(parsed.expires_at);
  if (!Number.isFinite(expiresAtMs)) {
    throw new Error("invalid ssh expires_at");
  }
  if (expiresAtMs <= Date.now()) {
    throw new Error("expired ssh expires_at");
  }
  if (!Object.prototype.hasOwnProperty.call(parsed, "max_sign_ops")) {
    throw new Error("missing ssh max_sign_ops");
  }
  const rawMaxSignOps = parsed.max_sign_ops;
  if (rawMaxSignOps === null) {
    return {
      sessionId: parsed.session_id,
      adapterId: parsed.adapter_id,
      keyId: parsed.key_id,
      expiresAt: parsed.expires_at,
      maxSignOps: null,
    };
  }
  if (!Number.isInteger(rawMaxSignOps) || rawMaxSignOps <= 0) {
    throw new Error("invalid ssh max_sign_ops");
  }
  return {
    sessionId: parsed.session_id,
    adapterId: parsed.adapter_id,
    keyId: parsed.key_id,
    expiresAt: parsed.expires_at,
    maxSignOps: rawMaxSignOps,
  };
}

function getVaultStub(env, vaultInstance = VAULT_INSTANCE_NAME) {
  const vaultId = env.SUBUMBRA_VAULT.idFromName(vaultInstance);
  return env.SUBUMBRA_VAULT.get(vaultId);
}

function parseBearerToken(request) {
  const auth = request.headers.get("authorization") ?? "";
  const match = auth.match(/^Bearer\s+(.+)$/i);
  return match ? match[1] : "";
}

function tokenPrefix(token) {
  return token.slice(0, 8);
}

function bytesToHex(bytes) {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function bytesToBase64(bytes) {
  return btoa(String.fromCharCode(...bytes));
}

function bytesToBase64Url(bytes) {
  return bytesToBase64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlToBytes(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

function utf8Bytes(value) {
  return new TextEncoder().encode(value);
}

function concatBytes(...parts) {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

function uint32ToBytes(value) {
  const bytes = new Uint8Array(4);
  new DataView(bytes.buffer).setUint32(0, value);
  return bytes;
}

async function hmacSha256(keyBytes, dataBytes) {
  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, dataBytes));
}

async function deriveWebPushIkm(ecdhSecret, authSecret, uaPublic, asPublic) {
  const prkKey = await hmacSha256(authSecret, ecdhSecret);
  const keyInfo = concatBytes(utf8Bytes("WebPush: info"), Uint8Array.of(0x00), uaPublic, asPublic, Uint8Array.of(0x01));
  return hmacSha256(prkKey, keyInfo);
}

async function deriveWebPushContentKeys(ikm, salt) {
  const prk = await hmacSha256(salt, ikm);
  const cek = await hmacSha256(prk, concatBytes(utf8Bytes("Content-Encoding: aes128gcm"), Uint8Array.of(0x00, 0x01)));
  const nonce = await hmacSha256(prk, concatBytes(utf8Bytes("Content-Encoding: nonce"), Uint8Array.of(0x00, 0x01)));
  return {
    cek: cek.slice(0, 16),
    nonce: nonce.slice(0, 12),
  };
}

async function encryptWebPushPayload(p256dh, authSecret, payload) {
  const uaPublic = base64UrlToBytes(p256dh);
  const authBytes = base64UrlToBytes(authSecret);
  const serverKeys = await crypto.subtle.generateKey(
    { name: "ECDH", namedCurve: "P-256" },
    true,
    ["deriveBits"],
  );
  const serverPublic = new Uint8Array(await crypto.subtle.exportKey("raw", serverKeys.publicKey));
  const uaPublicKey = await crypto.subtle.importKey(
    "raw",
    uaPublic,
    { name: "ECDH", namedCurve: "P-256" },
    false,
    [],
  );
  const ecdhSecret = new Uint8Array(
    await crypto.subtle.deriveBits(
      { name: "ECDH", public: uaPublicKey },
      serverKeys.privateKey,
      256,
    ),
  );
  const ikm = await deriveWebPushIkm(ecdhSecret, authBytes, uaPublic, serverPublic);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const { cek, nonce } = await deriveWebPushContentKeys(ikm, salt);
  const cekKey = await crypto.subtle.importKey("raw", cek, { name: "AES-GCM" }, false, ["encrypt"]);
  const plaintext = concatBytes(utf8Bytes(JSON.stringify(payload)), Uint8Array.of(0x02));
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, cekKey, plaintext),
  );
  const recordSize = Math.max(4096, plaintext.length + 17);
  return {
    body: concatBytes(salt, uint32ToBytes(recordSize), Uint8Array.of(serverPublic.length), serverPublic, ciphertext),
    headers: {
      "Content-Encoding": "aes128gcm",
      "Content-Type": "application/octet-stream",
    },
  };
}

function canonicalizeJson(value) {
  if (Array.isArray(value)) {
    return value.map((entry) => canonicalizeJson(entry));
  }
  if (value && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      out[key] = canonicalizeJson(value[key]);
    }
    return out;
  }
  return value;
}

function normalizeHeaderMap(headers) {
  const normalized = {};
  for (const [key, value] of Object.entries(headers || {})) {
    if (typeof value !== "string") {
      continue;
    }
    normalized[key.toLowerCase()] = value;
  }
  return canonicalizeJson(normalized);
}

async function sha256HexBytes(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return bytesToHex(new Uint8Array(digest));
}

async function sha256HexText(value) {
  return sha256HexBytes(new TextEncoder().encode(value));
}

function htmlResponse(body, status = 200) {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "pragma": "no-cache",
      "x-content-type-options": "nosniff",
    },
  });
}

function getGateStub(env) {
  const gateId = env.SUBUMBRA_GATE.idFromName(GATE_INSTANCE_NAME);
  return env.SUBUMBRA_GATE.get(gateId);
}

function matchGateRule(gatePolicy, context) {
  if (!gatePolicy || !Array.isArray(gatePolicy.require_approval)) {
    return null;
  }
  for (const rule of gatePolicy.require_approval) {
    const when = rule.when || {};
    if (when.adapter && when.adapter !== context.adapterId) {
      continue;
    }
    if (context.flow === "proxy") {
      if (when.method && when.method !== context.method) {
        continue;
      }
      if (when.path_prefix && !context.path.startsWith(when.path_prefix)) {
        continue;
      }
    } else if (when.method || when.path_prefix) {
      continue;
    }
    if (when.any_request !== true && !when.adapter && !when.method && !when.path_prefix) {
      continue;
    }
    return rule;
  }
  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Durable Object — SubumbraGate
// ─────────────────────────────────────────────────────────────────────────────

export class SubumbraGate {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this._constructorError = null;
    this.state.blockConcurrencyWhile(async () => {
      try {
        this.state.storage.sql.exec(GATE_SCHEMA);
      } catch (err) {
        this._constructorError = err;
        console.error("subumbra: gate DO constructor failed — instance is degraded");
      }
    });
  }

  async fetch(request) {
    if (this._constructorError) {
      console.error("subumbra: gate DO degraded — request rejected");
      return jsonError("gate unavailable", 503);
    }

    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === GATE_SUBMIT_PATH) {
      return this._handleSubmit(request);
    }
    if (request.method === "POST" && url.pathname === GATE_CONSUME_PATH) {
      return this._handleConsume(request);
    }
    if (request.method === "POST" && url.pathname === "/gate/subscribe") {
      return this._handleSubscribe(request);
    }
    if (request.method === "GET" && url.pathname === "/gate/pending") {
      return this._handlePending();
    }
    if (request.method === "GET" && url.pathname.startsWith("/gate/status/")) {
      return this._handleStatus(url.pathname.slice("/gate/status/".length));
    }
    if (request.method === "GET" && url.pathname.startsWith("/gate/approve/")) {
      return this._handleDecisionPage("approved", url.pathname.slice("/gate/approve/".length), url.searchParams);
    }
    if (request.method === "POST" && url.pathname.startsWith("/gate/approve/")) {
      return this._handleDecisionAction("approved", url.pathname.slice("/gate/approve/".length), request);
    }
    if (request.method === "GET" && url.pathname.startsWith("/gate/deny/")) {
      return this._handleDecisionPage("denied", url.pathname.slice("/gate/deny/".length), url.searchParams);
    }
    if (request.method === "POST" && url.pathname.startsWith("/gate/deny/")) {
      return this._handleDecisionAction("denied", url.pathname.slice("/gate/deny/".length), request);
    }
    return jsonError("not found", 404);
  }

  async alarm() {
    const nowIso = new Date().toISOString();
    const expiredRows = this.state.storage.sql.exec(
      "SELECT request_id FROM pending_requests WHERE status='pending' AND expires_at <= ?",
      nowIso,
    ).toArray();
    if (expiredRows.length > 0) {
      this.state.storage.sql.exec(
        "UPDATE pending_requests SET status='expired', terminal_at=? WHERE status='pending' AND expires_at <= ?",
        nowIso,
        nowIso,
      );
      for (const row of expiredRows) {
        console.warn("gate_expire request_id=%s", row.request_id);
      }
    }
    await this._scheduleNextAlarm();
  }

  _loadPendingRow(requestId) {
    const rows = this.state.storage.sql.exec(
      `SELECT request_id, flow, key_id, adapter_id, target_summary, request_digest,
              created_at, expires_at, status, approval_token_hash, terminal_at, consumed_at
         FROM pending_requests
        WHERE request_id = ?`,
      requestId,
    ).toArray();
    return rows.length > 0 ? rows[0] : null;
  }

  async _tokenMac(requestId, nonce, expiryUnix) {
    const rawKey = this.env.SUBUMBRA_GATE_HMAC_KEY ?? "";
    if (!rawKey) {
      throw new Error("gate hmac secret missing");
    }
    const cryptoKey = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(rawKey),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const payload = new TextEncoder().encode(`${requestId}\n${nonce}\n${expiryUnix}`);
    const mac = await crypto.subtle.sign("HMAC", cryptoKey, payload);
    return bytesToBase64Url(new Uint8Array(mac));
  }

  async _buildCapabilityToken(requestId, expiresAtIso) {
    const nonce = crypto.randomUUID();
    const expiryUnix = Math.floor(Date.parse(expiresAtIso) / 1000);
    const mac = await this._tokenMac(requestId, nonce, expiryUnix);
    return `${nonce}.${expiryUnix}.${mac}`;
  }

  async _hashApprovalToken(token) {
    return sha256HexText(token);
  }

  async _validateToken(requestId, token) {
    if (!token || typeof token !== "string") {
      console.warn("gate_token_invalid request_id=%s reason=missing", requestId);
      return { ok: false, response: htmlResponse("<h1>Forbidden</h1><p>Missing token.</p>", 403) };
    }
    const parts = token.split(".");
    if (parts.length !== 3 || !parts[0] || !parts[1] || !parts[2]) {
      console.warn("gate_token_invalid request_id=%s reason=malformed", requestId);
      return { ok: false, response: htmlResponse("<h1>Forbidden</h1><p>Malformed token.</p>", 403) };
    }
    const [nonce, expiryRaw, mac] = parts;
    const expiryUnix = Number.parseInt(expiryRaw, 10);
    if (!Number.isFinite(expiryUnix) || expiryUnix <= 0) {
      console.warn("gate_token_invalid request_id=%s reason=malformed", requestId);
      return { ok: false, response: htmlResponse("<h1>Forbidden</h1><p>Malformed token.</p>", 403) };
    }
    if (Date.now() >= expiryUnix * 1000) {
      console.warn("gate_token_invalid request_id=%s reason=expired", requestId);
      return { ok: false, response: htmlResponse("<h1>Forbidden</h1><p>Token expired.</p>", 403) };
    }
    const expectedMac = await this._tokenMac(requestId, nonce, expiryUnix);
    const macOk = await timingSafeEqual(mac, expectedMac);
    if (!macOk) {
      console.warn("gate_token_invalid request_id=%s reason=hmac", requestId);
      return { ok: false, response: htmlResponse("<h1>Forbidden</h1><p>Invalid token.</p>", 403) };
    }
    const row = this._loadPendingRow(requestId);
    if (!row) {
      console.warn("gate_token_invalid request_id=%s reason=not_found", requestId);
      return { ok: false, response: htmlResponse("<h1>Forbidden</h1><p>Request not found.</p>", 403) };
    }
    const tokenHash = await this._hashApprovalToken(token);
    const tokenHashOk = await timingSafeEqual(tokenHash, row.approval_token_hash);
    if (!tokenHashOk) {
      console.warn("gate_token_invalid request_id=%s reason=hash", requestId);
      return { ok: false, response: htmlResponse("<h1>Forbidden</h1><p>Invalid token.</p>", 403) };
    }
    if (row.status === "consumed" || row.status === "approved" || row.status === "denied") {
      return { ok: false, response: htmlResponse("<h1>Conflict</h1><p>Request already resolved.</p>", 409) };
    }
    if (row.status === "expired" || Date.parse(row.expires_at) <= Date.now()) {
      if (row.status === "pending") {
        const nowIso = new Date().toISOString();
        this.state.storage.sql.exec(
          "UPDATE pending_requests SET status='expired', terminal_at=? WHERE request_id=? AND status='pending'",
          nowIso,
          requestId,
        );
      }
      return { ok: false, response: htmlResponse("<h1>Forbidden</h1><p>Request expired.</p>", 403) };
    }
    return { ok: true, row };
  }

  _renderDecisionPage(action, requestId, token) {
    const verb = action === "approved" ? "Approve" : "Deny";
    return htmlResponse(
      `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${verb} Gate Request</title></head><body><main><h1>${verb} request</h1><p>Request ID: <code>${requestId}</code></p><form method="post"><input type="hidden" name="token" value="${token.replace(/"/g, "&quot;")}"><button type="submit">${verb}</button></form></main></body></html>`,
      200,
    );
  }

  async _scheduleNextAlarm() {
    const rows = this.state.storage.sql.exec(
      "SELECT MIN(expires_at) AS next_expires_at FROM pending_requests WHERE status='pending'",
    ).toArray();
    const nextIso = rows.length > 0 ? rows[0].next_expires_at : null;
    if (!nextIso) {
      await this.state.storage.deleteAlarm();
      return;
    }
    const nextMs = Date.parse(nextIso);
    if (Number.isFinite(nextMs)) {
      await this.state.storage.setAlarm(nextMs);
    }
  }

  async _importVapidPrivateKey() {
    const rawJwk = this.env.SUBUMBRA_GATE_VAPID_PRIVATE_JWK ?? "";
    if (!rawJwk) {
      throw new Error("gate vapid secret missing");
    }
    let jwk;
    try {
      jwk = JSON.parse(rawJwk);
    } catch {
      throw new Error("gate vapid secret invalid");
    }
    return crypto.subtle.importKey(
      "jwk",
      jwk,
      { name: "ECDSA", namedCurve: "P-256" },
      false,
      ["sign"],
    );
  }

  _vapidPublicKeyFromSecret() {
    const rawJwk = this.env.SUBUMBRA_GATE_VAPID_PRIVATE_JWK ?? "";
    if (!rawJwk) {
      throw new Error("gate vapid secret missing");
    }
    let jwk;
    try {
      jwk = JSON.parse(rawJwk);
    } catch {
      throw new Error("gate vapid secret invalid");
    }
    if (typeof jwk.x !== "string" || typeof jwk.y !== "string") {
      throw new Error("gate vapid secret invalid");
    }
    const x = base64UrlToBytes(jwk.x);
    const y = base64UrlToBytes(jwk.y);
    const publicKey = new Uint8Array(65);
    publicKey[0] = 0x04;
    publicKey.set(x, 1);
    publicKey.set(y, 33);
    return bytesToBase64Url(publicKey);
  }

  async _buildVapidHeaders(endpoint) {
    const audience = new URL(endpoint).origin;
    const privateKey = await this._importVapidPrivateKey();
    const publicKey = this._vapidPublicKeyFromSecret();
    const nowSeconds = Math.floor(Date.now() / 1000);
    const header = bytesToBase64Url(new TextEncoder().encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
    const claims = bytesToBase64Url(
      new TextEncoder().encode(
        JSON.stringify({
          aud: audience,
          exp: nowSeconds + 12 * 60 * 60,
          sub: "mailto:subumbra@localhost.invalid",
        }),
      ),
    );
    const signingInput = `${header}.${claims}`;
    const signature = await crypto.subtle.sign(
      { name: "ECDSA", hash: "SHA-256" },
      privateKey,
      new TextEncoder().encode(signingInput),
    );
    const jwt = `${signingInput}.${bytesToBase64Url(new Uint8Array(signature))}`;
    return {
      Authorization: `vapid t=${jwt}, k=${publicKey}`,
      "Crypto-Key": `p256ecdsa=${publicKey}`,
      TTL: "60",
      Urgency: "high",
    };
  }

  async _deliverPushNotifications(message) {
    const rows = this.state.storage.sql.exec(
      "SELECT endpoint_hash, endpoint, p256dh, auth_secret FROM push_subscriptions ORDER BY last_seen_at DESC",
    ).toArray();
    let successCount = 0;
    for (const row of rows) {
      try {
        const vapidHeaders = await this._buildVapidHeaders(row.endpoint);
        const encrypted = await encryptWebPushPayload(row.p256dh, row.auth_secret, {
          title: "Subumbra approval required",
          body: "A gated request is waiting for operator approval.",
          request_id: message.request_id,
          approve_url: message.approve_url,
          deny_url: message.deny_url,
        });
        const response = await fetch(row.endpoint, {
          method: "POST",
          headers: {
            Topic: message.request_id,
            ...vapidHeaders,
            ...encrypted.headers,
          },
          body: encrypted.body,
        });
        if (response.ok) {
          successCount += 1;
          continue;
        }
        if (response.status === 404 || response.status === 410) {
          this.state.storage.sql.exec(
            "DELETE FROM push_subscriptions WHERE endpoint_hash = ?",
            row.endpoint_hash,
          );
        }
        console.warn(
          "gate_notification_dispatch_failure request_id=%s status=%s",
          message.request_id,
          response.status,
        );
      } catch {
        console.warn("gate_notification_dispatch_failure request_id=%s status=error", message.request_id);
        // Best-effort only. Approval still fails closed on timeout.
      }
    }
    if (successCount > 0) {
      console.info("gate_notification_dispatch_success request_id=%s subscription_count=%s", message.request_id, successCount);
    }
  }

  async _handleSubmit(request) {
    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonError("invalid JSON body", 400);
    }
    const {
      flow,
      key_id: keyId,
      adapter_id: adapterId,
      target_summary: targetSummary,
      request_digest: requestDigest,
      timeout_seconds: timeoutSeconds,
      origin,
    } = payload ?? {};
    if (
      (flow !== "proxy" && flow !== "ssh_sign")
      || typeof keyId !== "string" || !keyId
      || typeof adapterId !== "string" || !adapterId
      || typeof targetSummary !== "string" || !targetSummary
      || typeof requestDigest !== "string" || !requestDigest
      || !Number.isInteger(timeoutSeconds) || timeoutSeconds <= 0
      || typeof origin !== "string" || !origin
    ) {
      return jsonError("missing required fields", 400);
    }
    const requestId = crypto.randomUUID();
    const createdAt = new Date().toISOString();
    const expiresAt = new Date(Date.now() + timeoutSeconds * 1000).toISOString();
    const capabilityToken = await this._buildCapabilityToken(requestId, expiresAt);
    const approvalTokenHash = await this._hashApprovalToken(capabilityToken);
    this.state.storage.sql.exec(
      `INSERT INTO pending_requests
       (request_id, flow, key_id, adapter_id, target_summary, request_digest, created_at, expires_at, status, approval_token_hash, terminal_at, consumed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL)`,
      requestId,
      flow,
      keyId,
      adapterId,
      targetSummary,
      requestDigest,
      createdAt,
      expiresAt,
      approvalTokenHash,
    );
    await this._scheduleNextAlarm();

    const approveUrl = new URL(`/gate/approve/${requestId}`, origin);
    approveUrl.searchParams.set("token", capabilityToken);
    const denyUrl = new URL(`/gate/deny/${requestId}`, origin);
    denyUrl.searchParams.set("token", capabilityToken);

    console.info(
      "gate_submit request_id=%s flow=%s key_id=%s adapter=%s expires_at=%s",
      requestId,
      flow,
      keyId,
      adapterId,
      expiresAt,
    );
    await this._deliverPushNotifications({
      request_id: requestId,
      approve_url: approveUrl.toString(),
      deny_url: denyUrl.toString(),
    });
    return jsonResponse({
      request_id: requestId,
      poll_url: `/gate/status/${requestId}`,
      status: "pending",
    }, 202);
  }

  async _handleConsume(request) {
    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonError("invalid JSON body", 400);
    }
    const {
      request_id: requestId,
      request_digest: requestDigest,
      flow,
      key_id: keyId,
      adapter_id: adapterId,
    } = payload ?? {};
    if (
      typeof requestId !== "string" || !requestId
      || typeof requestDigest !== "string" || !requestDigest
      || (flow !== "proxy" && flow !== "ssh_sign")
      || typeof keyId !== "string" || !keyId
      || typeof adapterId !== "string" || !adapterId
    ) {
      return jsonError("missing required fields", 400);
    }
    const row = this._loadPendingRow(requestId);
    if (!row) {
      return jsonError("gate_not_found", 404);
    }
    if (row.adapter_id !== adapterId || row.key_id !== keyId || row.flow !== flow) {
      return jsonError("gate_digest_mismatch", 403);
    }
    if (row.status === "pending") {
      return jsonError("gate_pending", 409);
    }
    if (row.status === "denied" || row.status === "expired") {
      return jsonError(row.status === "denied" ? "gate_denied" : "gate_timeout", 403);
    }
    if (row.status === "consumed" || row.consumed_at) {
      return jsonError("gate_replayed", 409);
    }
    const digestOk = await timingSafeEqual(requestDigest, row.request_digest);
    if (!digestOk) {
      return jsonError("gate_digest_mismatch", 403);
    }
    const nowIso = new Date().toISOString();
    this.state.storage.sql.exec(
      "UPDATE pending_requests SET status='consumed', consumed_at=? WHERE request_id=? AND status='approved'",
      nowIso,
      requestId,
    );
    console.info("gate_consume request_id=%s", requestId);
    console.info("gate_vault_dispatch request_id=%s flow=%s", requestId, row.flow);
    return new Response(null, { status: 204 });
  }

  async _handleSubscribe(request) {
    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonError("invalid JSON body", 400);
    }
    const endpoint = payload?.endpoint;
    const p256dh = payload?.keys?.p256dh;
    const authSecret = payload?.keys?.auth;
    if (
      typeof endpoint !== "string" || !endpoint
      || typeof p256dh !== "string" || !p256dh
      || typeof authSecret !== "string" || !authSecret
    ) {
      return jsonError("missing required fields", 400);
    }
    const endpointHash = await sha256HexText(endpoint);
    const nowIso = new Date().toISOString();
    this.state.storage.sql.exec(
      `INSERT INTO push_subscriptions (endpoint_hash, endpoint, p256dh, auth_secret, created_at, last_seen_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(endpoint_hash) DO UPDATE SET
         endpoint=excluded.endpoint,
         p256dh=excluded.p256dh,
         auth_secret=excluded.auth_secret,
         last_seen_at=excluded.last_seen_at`,
      endpointHash,
      endpoint,
      p256dh,
      authSecret,
      nowIso,
      nowIso,
    );
    return jsonResponse({ status: "ok" }, 200);
  }

  async _handlePending() {
    const pending = this.state.storage.sql.exec(
      `SELECT request_id, flow, key_id, adapter_id, target_summary, created_at, expires_at, status
         FROM pending_requests
        WHERE status='pending'
        ORDER BY created_at DESC`,
    ).toArray();
    const subscriptionCountRows = this.state.storage.sql.exec(
      "SELECT COUNT(*) AS count FROM push_subscriptions",
    ).toArray();
    const sinceIso = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    const countRows = this.state.storage.sql.exec(
      `SELECT status, COUNT(*) AS count
         FROM pending_requests
        WHERE terminal_at IS NOT NULL AND terminal_at >= ?
          AND status IN ('approved', 'denied', 'expired')
        GROUP BY status`,
      sinceIso,
    ).toArray();
    const recentCounts = { approved: 0, denied: 0, timeout: 0 };
    for (const row of countRows) {
      if (row.status === "approved") {
        recentCounts.approved = row.count;
      } else if (row.status === "denied") {
        recentCounts.denied = row.count;
      } else if (row.status === "expired") {
        recentCounts.timeout = row.count;
      }
    }
    return jsonResponse({
      subscription_count: subscriptionCountRows.length > 0 ? subscriptionCountRows[0].count : 0,
      pending_count: pending.length,
      recent_counts_24h: recentCounts,
      pending,
    }, 200);
  }

  async _handleStatus(requestId) {
    const row = this._loadPendingRow(requestId);
    if (!row) {
      return jsonError("gate_not_found", 404);
    }
    let error = null;
    if (row.status === "denied") {
      error = "gate_denied";
    } else if (row.status === "expired") {
      error = "gate_timeout";
    } else if (row.status === "consumed") {
      error = "gate_replayed";
    }
    return jsonResponse({
      request_id: row.request_id,
      status: row.status,
      created_at: row.created_at,
      expires_at: row.expires_at,
      ...(error ? { error } : {}),
    }, 200);
  }

  async _handleDecisionPage(action, requestId, searchParams) {
    const token = searchParams.get("token") ?? "";
    const validated = await this._validateToken(requestId, token);
    if (!validated.ok) {
      return validated.response;
    }
    return this._renderDecisionPage(action, requestId, token);
  }

  async _handleDecisionAction(action, requestId, request) {
    const url = new URL(request.url);
    let token = url.searchParams.get("token") ?? "";
    if (!token) {
      const formData = await request.formData();
      token = String(formData.get("token") ?? "");
    }
    const validated = await this._validateToken(requestId, token);
    if (!validated.ok) {
      return validated.response;
    }
    const nowIso = new Date().toISOString();
    this.state.storage.sql.exec(
      "UPDATE pending_requests SET status=?, terminal_at=? WHERE request_id=? AND status='pending'",
      action,
      nowIso,
      requestId,
    );
    console.info("%s request_id=%s", action === "approved" ? "gate_approve" : "gate_deny", requestId);
    return htmlResponse(
      `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Gate decision recorded</title></head><body><main><h1>${action === "approved" ? "Approved" : "Denied"}</h1><p>Request <code>${requestId}</code> is now ${action}.</p></main></body></html>`,
      200,
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Durable Object — SubumbraVault
// ─────────────────────────────────────────────────────────────────────────────

export class SubumbraVault {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this._cachedPrivateKey = null;
    this._constructorError = null;
    this.state.blockConcurrencyWhile(async () => {
      try {
        this.state.storage.sql.exec(VAULT_SCHEMA);
        await this._primeCachedPrivateKey();
      } catch (err) {
        this._constructorError = err;
        console.error("subumbra: vault DO constructor failed — instance is degraded");
      }
    });
  }

  async fetch(request) {
    if (this._constructorError) {
      console.error("subumbra: vault DO degraded — request rejected");
      return jsonError("vault unavailable", 503);
    }

    const url = new URL(request.url);

    if (request.method !== "POST") {
      return jsonError("method not allowed", 405);
    }

    if (url.pathname === VAULT_SETUP_PATH) {
      return this._handleSetupKeygen(request);
    }

    if (url.pathname === VAULT_SSH_KEYGEN_PATH) {
      return this._handleSshKeygen(request);
    }

    if (url.pathname === VAULT_SSH_IMPORT_PATH) {
      return this._handleSshImport(request);
    }

    if (url.pathname === VAULT_SSH_SIGN_PATH) {
      return this._handleSshSign(request);
    }

    if (url.pathname === VAULT_STATUS_PATH) {
      return this._handleStatus(request);
    }

    if (url.pathname === VAULT_EXECUTE_PATH) {
      return this._handleExecute(request);
    }

    if (url.pathname === VAULT_ROTATE_PATH) {
      return this._handleRotate(request);
    }

    if (url.pathname === VAULT_RESET_PATH) {
      return this._handleReset(request);
    }

    if (url.pathname === VAULT_MANAGEMENT_AUDIT_PATH) {
      return this._handleManagementAudit(request);
    }

    if (url.pathname === VAULT_RATE_CHECK_PATH) {
      return this._handleRateCheck(request);
    }

    return jsonError("not found", 404);
  }

  _loadCustodyRow() {
    const rows = this.state.storage.sql.exec(
      "SELECT private_key_pkcs8, public_key_spki, pub_key_fp, created_at FROM custody WHERE id = 1"
    ).toArray();
    if (rows.length === 0) {
      return null;
    }
    const row = rows[0];
    return {
      private_key_pkcs8: row.private_key_pkcs8,
      public_key_spki: row.public_key_spki,
      pub_key_fp: row.pub_key_fp,
      created_at: row.created_at,
    };
  }

  _loadSshKeyRow(keyId) {
    const rows = this.state.storage.sql.exec(
      "SELECT key_id, private_key_pkcs8, public_key_raw, public_key_ssh, algorithm, created_at FROM ssh_keys WHERE key_id = ?",
      keyId,
    ).toArray();
    if (rows.length === 0) {
      return null;
    }
    const row = rows[0];
    return {
      key_id: row.key_id,
      private_key_pkcs8: row.private_key_pkcs8,
      public_key_raw: row.public_key_raw,
      public_key_ssh: row.public_key_ssh,
      algorithm: row.algorithm,
      created_at: row.created_at,
    };
  }

  _loadSshSessionQuotaRow(sessionId, adapterId, keyId) {
    const rows = this.state.storage.sql.exec(
      "SELECT session_id, adapter_id, key_id, max_sign_ops, sign_count, last_updated FROM ssh_session_quota WHERE session_id = ? AND adapter_id = ? AND key_id = ?",
      sessionId,
      adapterId,
      keyId,
    ).toArray();
    if (rows.length === 0) {
      return null;
    }
    const row = rows[0];
    return {
      session_id: row.session_id,
      adapter_id: row.adapter_id,
      key_id: row.key_id,
      max_sign_ops: row.max_sign_ops,
      sign_count: row.sign_count,
      last_updated: row.last_updated,
    };
  }

  async _importPrivateKey(pkcs8Bytes) {
    return crypto.subtle.importKey(
      "pkcs8",
      pkcs8Bytes,
      { name: "RSA-OAEP", hash: "SHA-256" },
      false,
      ["decrypt"],
    );
  }

  async _importEd25519PrivateKey(pkcs8Bytes, extractable = false) {
    return crypto.subtle.importKey(
      "pkcs8",
      pkcs8Bytes,
      { name: "Ed25519" },
      extractable,
      ["sign"],
    );
  }

  async _primeCachedPrivateKey() {
    if (this._cachedPrivateKey) {
      return this._cachedPrivateKey;
    }
    const row = this._loadCustodyRow();
    if (!row) {
      return null;
    }
    this._cachedPrivateKey = await this._importPrivateKey(row.private_key_pkcs8);
    return this._cachedPrivateKey;
  }

  async _computeFingerprint(spkiBytes) {
    const digest = await crypto.subtle.digest("SHA-256", spkiBytes);
    return `sha256:${bytesToHex(new Uint8Array(digest))}`;
  }

  _encodePublicKeyPem(spkiBytes) {
    const base64 = btoa(String.fromCharCode(...new Uint8Array(spkiBytes)));
    const body = base64.match(/.{1,64}/g)?.join("\n") ?? base64;
    return `-----BEGIN PUBLIC KEY-----\n${body}\n-----END PUBLIC KEY-----\n`;
  }

  _encodeSshEd25519PublicKey(rawPublicBytes, keyId) {
    const keyType = new TextEncoder().encode("ssh-ed25519");
    const payload = new Uint8Array(4 + keyType.length + 4 + rawPublicBytes.length);
    const view = new DataView(payload.buffer);
    view.setUint32(0, keyType.length, false);
    payload.set(keyType, 4);
    view.setUint32(4 + keyType.length, rawPublicBytes.length, false);
    payload.set(rawPublicBytes, 4 + keyType.length + 4);
    return `ssh-ed25519 ${bytesToBase64(payload)} subumbra:${keyId}`;
  }

  async _handleSetupKeygen(request) {
    const bearerToken = parseBearerToken(request);
    if (!bearerToken) {
      console.warn("subumbra: setup keygen rejected (missing bearer token)");
      return jsonError("unauthorized", 401);
    }

    const expectedToken = this.env.SUBUMBRA_SETUP_TOKEN ?? "";
    const tokenOk = await timingSafeEqual(bearerToken, expectedToken);
    if (!tokenOk) {
      console.warn("subumbra: setup keygen rejected (invalid bearer token)");
      return jsonError("forbidden", 403);
    }

    if (this._loadCustodyRow()) {
      console.info("subumbra: setup keygen rejected (vault already initialized)");
      return jsonError("already initialized", 409);
    }

    try {
      const keyPair = await crypto.subtle.generateKey(
        {
          name: "RSA-OAEP",
          modulusLength: 4096,
          publicExponent: new Uint8Array([1, 0, 1]),
          hash: "SHA-256",
        },
        true,
        ["encrypt", "decrypt"],
      );

      const privateKeyPkcs8 = new Uint8Array(await crypto.subtle.exportKey("pkcs8", keyPair.privateKey));
      const publicKeySpki = new Uint8Array(await crypto.subtle.exportKey("spki", keyPair.publicKey));
      const pubKeyFp = await this._computeFingerprint(publicKeySpki);
      const createdAt = new Date().toISOString();

      this.state.storage.sql.exec(
        "INSERT INTO custody (id, private_key_pkcs8, public_key_spki, pub_key_fp, created_at) VALUES (?, ?, ?, ?, ?)",
        1,
        privateKeyPkcs8,
        publicKeySpki,
        pubKeyFp,
        createdAt,
      );

      this._cachedPrivateKey = await this._importPrivateKey(privateKeyPkcs8);

      return jsonResponse({
        public_key_pem: this._encodePublicKeyPem(publicKeySpki),
        pub_key_fp: pubKeyFp,
        created_at: createdAt,
      }, 200);
    } catch {
      console.error("subumbra: vault setup keygen internal error");
      return jsonError("setup failed", 500);
    }
  }

  async _handleSshKeygen(request) {
    const bearerToken = parseBearerToken(request);
    if (!bearerToken) {
      console.warn("subumbra: ssh keygen rejected (missing bearer token)");
      return jsonError("unauthorized", 401);
    }

    const expectedToken = this.env.SUBUMBRA_SETUP_TOKEN ?? "";
    const tokenOk = await timingSafeEqual(bearerToken, expectedToken);
    if (!tokenOk) {
      console.warn("subumbra: ssh keygen rejected (invalid bearer token)");
      return jsonError("forbidden", 403);
    }

    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonError("invalid JSON body", 400);
    }
    if (
      !payload ||
      typeof payload.key_id !== "string" ||
      !payload.key_id ||
      typeof payload.vault_instance !== "string" ||
      !payload.vault_instance
    ) {
      return jsonError("missing required fields", 400);
    }

    try {
      const keyPair = await crypto.subtle.generateKey(
        { name: "Ed25519" },
        true,
        ["sign", "verify"],
      );
      const privateKeyPkcs8 = new Uint8Array(await crypto.subtle.exportKey("pkcs8", keyPair.privateKey));
      const publicKeyRaw = new Uint8Array(await crypto.subtle.exportKey("raw", keyPair.publicKey));
      const publicKey = this._encodeSshEd25519PublicKey(publicKeyRaw, payload.key_id);
      const createdAt = new Date().toISOString();

      this.state.storage.sql.exec(
        "INSERT OR REPLACE INTO ssh_keys (key_id, private_key_pkcs8, public_key_raw, public_key_ssh, algorithm, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        payload.key_id,
        privateKeyPkcs8,
        publicKeyRaw,
        publicKey,
        "ed25519",
        createdAt,
      );

      return jsonResponse({
        key_id: payload.key_id,
        type: "ssh_key",
        key_source: "generated",
        algorithm: "ed25519",
        public_key: publicKey,
        created_at: createdAt,
      }, 200);
    } catch {
      console.error("subumbra: vault ssh keygen internal error");
      return jsonError("setup failed", 500);
    }
  }

  async _handleSshImport(request) {
    const bearerToken = parseBearerToken(request);
    if (!bearerToken) {
      console.warn("subumbra: ssh import rejected (missing bearer token)");
      return jsonError("unauthorized", 401);
    }

    const expectedToken = this.env.SUBUMBRA_SETUP_TOKEN ?? "";
    const tokenOk = await timingSafeEqual(bearerToken, expectedToken);
    if (!tokenOk) {
      console.warn("subumbra: ssh import rejected (invalid bearer token)");
      return jsonError("forbidden", 403);
    }

    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonError("invalid JSON body", 400);
    }
    if (
      !payload ||
      typeof payload.key_id !== "string" ||
      !payload.key_id ||
      typeof payload.vault_instance !== "string" ||
      !payload.vault_instance ||
      typeof payload.encrypted_private_key !== "string" ||
      !payload.encrypted_private_key
    ) {
      return jsonError("missing required fields", 400);
    }

    const custodyKey = await this._primeCachedPrivateKey();
    if (!custodyKey) {
      return jsonError("vault unavailable", 503);
    }

    try {
      const ciphertextBytes = Uint8Array.from(atob(payload.encrypted_private_key), (c) => c.charCodeAt(0));
      const pkcs8Bytes = new Uint8Array(
        await crypto.subtle.decrypt({ name: "RSA-OAEP" }, custodyKey, ciphertextBytes),
      );
      const privateKey = await this._importEd25519PrivateKey(pkcs8Bytes, true);
      const jwk = await crypto.subtle.exportKey("jwk", privateKey);
      if (typeof jwk.x !== "string") {
        throw new Error("missing public component");
      }
      const publicKeyRaw = base64UrlToBytes(jwk.x);
      const publicKeySsh = this._encodeSshEd25519PublicKey(publicKeyRaw, payload.key_id);
      const createdAt = new Date().toISOString();

      this.state.storage.sql.exec(
        "INSERT OR REPLACE INTO ssh_keys (key_id, private_key_pkcs8, public_key_raw, public_key_ssh, algorithm, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        payload.key_id,
        new Uint8Array(pkcs8Bytes),
        publicKeyRaw,
        publicKeySsh,
        "ed25519",
        createdAt,
      );

      return jsonResponse({
        key_id: payload.key_id,
        type: "ssh_key",
        key_source: "provided",
        algorithm: "ed25519",
        public_key: publicKeySsh,
        created_at: createdAt,
      }, 200);
    } catch {
      console.error("subumbra: vault ssh import internal error");
      return jsonError("setup failed", 500);
    }
  }

  async _handleSshSign(request) {
    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonError("invalid JSON body", 400);
    }
    if (!payload || typeof payload.key_id !== "string" || !payload.key_id || typeof payload.challenge !== "string" || !payload.challenge) {
      return jsonError("missing required fields", 400);
    }
    const hasQuotaFields =
      Object.prototype.hasOwnProperty.call(payload, "session_id") ||
      Object.prototype.hasOwnProperty.call(payload, "adapter_id") ||
      Object.prototype.hasOwnProperty.call(payload, "max_sign_ops");
    let quotaContext = null;
    if (hasQuotaFields) {
      if (
        typeof payload.session_id !== "string" ||
        !payload.session_id ||
        typeof payload.adapter_id !== "string" ||
        !payload.adapter_id ||
        !Number.isInteger(payload.max_sign_ops) ||
        payload.max_sign_ops <= 0
      ) {
        console.error("subumbra: vault ssh quota metadata invalid key_id=%s", payload.key_id);
        return jsonError("session_quota_unavailable", 503);
      }
      quotaContext = {
        sessionId: payload.session_id,
        adapterId: payload.adapter_id,
        keyId: payload.key_id,
        maxSignOps: payload.max_sign_ops,
      };
    }

    const row = this._loadSshKeyRow(payload.key_id);
    if (!row) {
      return jsonError("key not found", 404);
    }

    try {
      const challengeBytes = Uint8Array.from(atob(payload.challenge), (c) => c.charCodeAt(0));
      const privateKey = await this._importEd25519PrivateKey(row.private_key_pkcs8);
      let signCount = null;
      let limitReached = false;

      if (quotaContext) {
        const nowIso = new Date().toISOString();
        try {
          // Durable Objects already serialize fetch handlers, so this
          // read-check-write sequence is single-request atomic here.
          const existing = this._loadSshSessionQuotaRow(
            quotaContext.sessionId,
            quotaContext.adapterId,
            quotaContext.keyId,
          );
          const currentCount = existing ? Number(existing.sign_count) : 0;
          if (!Number.isInteger(currentCount) || currentCount < 0) {
            throw new Error("invalid ssh quota row");
          }
          if (currentCount >= quotaContext.maxSignOps) {
            console.warn(
              "subumbra: ssh sign denied key_id=%s adapter=%s session_id=%s reason=session_sign_limit_reached count=%s limit=%s",
              payload.key_id,
              quotaContext.adapterId,
              quotaContext.sessionId,
              currentCount,
              quotaContext.maxSignOps,
            );
            return jsonError(
              "session_sign_limit_reached",
              403,
              buildSshQuotaHeaders(
                quotaContext.sessionId,
                currentCount,
                quotaContext.maxSignOps,
                true,
              ),
            );
          }

          const signature = new Uint8Array(
            await crypto.subtle.sign({ name: "Ed25519" }, privateKey, challengeBytes),
          );
          signCount = currentCount + 1;
          limitReached = signCount >= quotaContext.maxSignOps;
          this.state.storage.sql.exec(
            `INSERT INTO ssh_session_quota (session_id, adapter_id, key_id, max_sign_ops, sign_count, last_updated)
             VALUES (?, ?, ?, ?, ?, ?)
             ON CONFLICT(session_id, adapter_id, key_id) DO UPDATE SET
               max_sign_ops = excluded.max_sign_ops,
               sign_count = excluded.sign_count,
               last_updated = excluded.last_updated`,
            quotaContext.sessionId,
            quotaContext.adapterId,
            quotaContext.keyId,
            quotaContext.maxSignOps,
            signCount,
            nowIso,
          );
          return jsonResponse(
            {
              key_id: payload.key_id,
              signature: bytesToBase64(signature),
            },
            200,
            buildSshQuotaHeaders(
              quotaContext.sessionId,
              signCount,
              quotaContext.maxSignOps,
              limitReached,
            ),
          );
        } catch (err) {
          if (err instanceof Response) {
            return err;
          }
          console.error(
            "subumbra: vault ssh quota unavailable key_id=%s session_id=%s adapter=%s",
            payload.key_id,
            quotaContext.sessionId,
            quotaContext.adapterId,
          );
          return jsonError("session_quota_unavailable", 503);
        }
      }

      const signature = new Uint8Array(
        await crypto.subtle.sign({ name: "Ed25519" }, privateKey, challengeBytes),
      );
      return jsonResponse({
        key_id: payload.key_id,
        signature: bytesToBase64(signature),
      }, 200);
    } catch {
      console.error("subumbra: vault ssh sign internal error");
      return jsonError("signing_failed", 500);
    }
  }

  async _handleStatus(request) {
    const bearerToken = parseBearerToken(request);
    if (!bearerToken) {
      console.warn("subumbra: internal status rejected (missing bearer token)");
      return jsonError("unauthorized", 401);
    }

    const expectedToken = this.env.SUBUMBRA_SETUP_TOKEN ?? "";
    const tokenOk = await timingSafeEqual(bearerToken, expectedToken);
    if (!tokenOk) {
      console.warn("subumbra: internal status rejected (invalid bearer token)");
      return jsonError("forbidden", 403);
    }

    const vaultInstance = request.headers.get("X-Subumbra-Vault-Instance") ?? VAULT_INSTANCE_NAME;
    const initialized = this._loadCustodyRow() !== null;
    return jsonResponse({
      status: "ok",
      vault_instance: vaultInstance,
      initialized,
    }, 200);
  }

  async _handleExecute(request) {
    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonError("invalid JSON body", 400);
    }

    const {
      ciphertext,
      wrappedDek,
      pubKeyFp,
      keyId,
      encVersion,
      policyHash,
      targetUrl,
      method,
      headers: reqHeaders,
      body,
      authScheme,
      authHeaderName,
      authQueryParam,
      responseAllowHeaders: responseAllowHeadersRaw,
      adapterId,
      velocity,
    } = payload;

    if (
      !ciphertext ||
      !wrappedDek ||
      !pubKeyFp ||
      !keyId ||
      !encVersion ||
      !targetUrl ||
      !authScheme
    ) {
      return jsonError("missing required fields", 400);
    }

    // ── R49: velocity and circuit breaker pre-checks ──────────────────────
    const nowSeconds = Math.floor(Date.now() / 1000);
    const windowStart = Math.floor(nowSeconds / 60) * 60;
    if (velocity && typeof velocity === "object") {
      const effectiveAdapterId = adapterId ?? "";

      if (typeof velocity.key_rpm === "number") {
        const keyCountRows = this.state.storage.sql.exec(
          "SELECT count FROM velocity_counters WHERE scope='key' AND adapter_id='' AND key_id=? AND window_start=?",
          keyId, windowStart
        ).toArray();
        const keyCount = keyCountRows.length > 0 ? keyCountRows[0].count : 0;
        if (keyCount >= velocity.key_rpm) {
          console.warn(
            "subumbra: policy deny reason=rate_limit_exceeded_key adapter=%s key_id=%s",
            effectiveAdapterId, keyId
          );
          return jsonError("rate_limit_exceeded_key", 429);
        }
      }

      if (typeof velocity.adapter_rpm === "number") {
        const adapterCountRows = this.state.storage.sql.exec(
          "SELECT count FROM velocity_counters WHERE scope='adapter' AND adapter_id=? AND key_id='' AND window_start=?",
          effectiveAdapterId, windowStart
        ).toArray();
        const adapterCount = adapterCountRows.length > 0 ? adapterCountRows[0].count : 0;
        if (adapterCount >= velocity.adapter_rpm) {
          console.warn(
            "subumbra: policy deny reason=rate_limit_exceeded_adapter adapter=%s key_id=%s",
            effectiveAdapterId, keyId
          );
          return jsonError("rate_limit_exceeded_adapter", 429);
        }
      }

      if (typeof velocity.breaker_failures === "number" && typeof velocity.breaker_cooldown_seconds === "number") {
        const brkPreRows = this.state.storage.sql.exec(
          "SELECT state, consecutive_failures, opened_at, half_open_probe_active FROM circuit_breaker_state WHERE key_id=?",
          keyId
        ).toArray();
        if (brkPreRows.length > 0) {
          const brk = brkPreRows[0];
          if (brk.state === "open") {
            if (nowSeconds - brk.opened_at < velocity.breaker_cooldown_seconds) {
              console.warn(
                "subumbra: policy deny reason=circuit_breaker_open adapter=%s key_id=%s",
                effectiveAdapterId, keyId
              );
              return jsonError("circuit_breaker_open", 429);
            }
            this.state.storage.sql.exec(
              "UPDATE circuit_breaker_state SET state='half_open', half_open_probe_active=1 WHERE key_id=?",
              keyId
            );
            console.info("subumbra: circuit_breaker half_open probe admitted key_id=%s", keyId);
          } else if (brk.state === "half_open" && brk.half_open_probe_active === 1) {
            console.warn(
              "subumbra: policy deny reason=circuit_breaker_open adapter=%s key_id=%s",
              effectiveAdapterId, keyId
            );
            return jsonError("circuit_breaker_open", 429);
          }
        }
      }

      // Increment counters only for requests admitted for upstream execution
      if (typeof velocity.key_rpm === "number") {
        this.state.storage.sql.exec(
          `INSERT INTO velocity_counters (scope, adapter_id, key_id, window_start, count)
           VALUES ('key', '', ?, ?, 1)
           ON CONFLICT (scope, adapter_id, key_id, window_start) DO UPDATE SET count = count + 1`,
          keyId, windowStart
        );
      }
      if (typeof velocity.adapter_rpm === "number") {
        this.state.storage.sql.exec(
          `INSERT INTO velocity_counters (scope, adapter_id, key_id, window_start, count)
           VALUES ('adapter', ?, '', ?, 1)
           ON CONFLICT (scope, adapter_id, key_id, window_start) DO UPDATE SET count = count + 1`,
          effectiveAdapterId, windowStart
        );
      }
    }

    const custodyRow = this._loadCustodyRow();
    if (!custodyRow) {
      console.error("subumbra: vault not initialized");
      return jsonError("worker not configured", 503);
    }

    let apiKey;
    try {
      const privateKey = await this._primeCachedPrivateKey();
      if (encVersion === 3) {
        apiKey = await decryptV3(
          privateKey,
          custodyRow.pub_key_fp,
          ciphertext,
          wrappedDek,
          pubKeyFp,
          keyId,
          policyHash,
        );
      } else {
        return jsonError("unsupported enc_version", 400);
      }
    } catch (err) {
      console.error("subumbra: decryption failed (policy_hash binding mismatch):", err.message);
      return jsonError("decryption failed", 500);
    }

    const upstreamHeaders = new Headers();
    for (const [k, v] of Object.entries(reqHeaders || {})) {
      upstreamHeaders.set(k, v);
    }

    const cleanUrl = new URL(targetUrl);
    cleanUrl.username = "";
    cleanUrl.password = "";
    cleanUrl.hash = "";
    for (const p of ["key", "api_key", "apikey", "api-key", "apiKey",
      "access_token", "token", "auth_token", "Authorization", "secret"]) {
      cleanUrl.searchParams.delete(p);
    }

    if (authScheme === "bearer") {
      upstreamHeaders.set("authorization", `Bearer ${apiKey}`);
    } else if (authScheme === "basic") {
      upstreamHeaders.set("authorization", `Basic ${btoa(apiKey + ":")}`);
    } else if (authScheme === "header" && authHeaderName) {
      upstreamHeaders.delete(authHeaderName);
      upstreamHeaders.set(authHeaderName, apiKey);
      upstreamHeaders.delete("authorization");
    } else if (authScheme === "query" && authQueryParam) {
      cleanUrl.searchParams.delete(authQueryParam);
      cleanUrl.searchParams.set(authQueryParam, apiKey);
      upstreamHeaders.delete("authorization");
    } else {
      return jsonError("unsupported auth scheme", 400);
    }

    let upstreamResponse = null;
    let fetchFailed = false;
    try {
      upstreamResponse = await fetch(cleanUrl.toString(), {
        method: method ?? "POST",
        headers: upstreamHeaders,
        body: body != null ? JSON.stringify(body) : undefined,
        redirect: "manual",
      });
    } catch {
      fetchFailed = true;
    }

    // ── R49: circuit breaker outcome recording ────────────────────────────
    if (velocity && typeof velocity === "object" &&
      typeof velocity.breaker_failures === "number" &&
      typeof velocity.breaker_cooldown_seconds === "number") {
      const effectiveAdapterId = adapterId ?? "";
      const isTrackedFailure =
        fetchFailed ||
        (upstreamResponse !== null && (upstreamResponse.status === 401 || upstreamResponse.status >= 500));
      const brkRows = this.state.storage.sql.exec(
        "SELECT state, consecutive_failures FROM circuit_breaker_state WHERE key_id=?",
        keyId
      ).toArray();
      const brkState = brkRows.length > 0 ? brkRows[0].state : "closed";
      const brkFailures = brkRows.length > 0 ? brkRows[0].consecutive_failures : 0;

      if (brkState === "closed") {
        if (isTrackedFailure) {
          const newFailures = brkFailures + 1;
          if (newFailures >= velocity.breaker_failures) {
            this.state.storage.sql.exec(
              `INSERT INTO circuit_breaker_state (key_id, state, consecutive_failures, opened_at, half_open_probe_active)
               VALUES (?, 'open', ?, ?, 0)
               ON CONFLICT (key_id) DO UPDATE SET state='open', consecutive_failures=?, opened_at=?, half_open_probe_active=0`,
              keyId, newFailures, nowSeconds, newFailures, nowSeconds
            );
            console.warn(
              "subumbra: circuit_breaker opened key_id=%s consecutive_failures=%d",
              keyId, newFailures
            );
          } else {
            this.state.storage.sql.exec(
              `INSERT INTO circuit_breaker_state (key_id, state, consecutive_failures, opened_at, half_open_probe_active)
               VALUES (?, 'closed', ?, 0, 0)
               ON CONFLICT (key_id) DO UPDATE SET consecutive_failures=?`,
              keyId, newFailures, newFailures
            );
          }
        } else if (upstreamResponse !== null &&
          upstreamResponse.status >= 200 && upstreamResponse.status < 300 &&
          brkFailures > 0) {
          this.state.storage.sql.exec(
            "UPDATE circuit_breaker_state SET consecutive_failures=0 WHERE key_id=?",
            keyId
          );
        }
        // non-failure non-2xx while closed: no change
      } else if (brkState === "half_open") {
        if (isTrackedFailure) {
          const newFailures = Math.max(velocity.breaker_failures, brkFailures + 1);
          this.state.storage.sql.exec(
            "UPDATE circuit_breaker_state SET state='open', consecutive_failures=?, opened_at=?, half_open_probe_active=0 WHERE key_id=?",
            newFailures, nowSeconds, keyId
          );
          console.warn(
            "subumbra: circuit_breaker opened key_id=%s consecutive_failures=%d",
            keyId, newFailures
          );
        } else {
          this.state.storage.sql.exec(
            "UPDATE circuit_breaker_state SET state='closed', consecutive_failures=0, half_open_probe_active=0 WHERE key_id=?",
            keyId
          );
          console.info(
            "subumbra: circuit_breaker closed adapter=%s key_id=%s",
            effectiveAdapterId, keyId
          );
        }
      }
    }

    if (fetchFailed) {
      return jsonError("upstream connection failed", 502);
    }

    const responseHeaders = new Headers();
    const responseAllowHeaders = new Set(
      (Array.isArray(responseAllowHeadersRaw) ? responseAllowHeadersRaw : [])
        .map((headerName) => headerName.toLowerCase()),
    );
    for (const [k, v] of upstreamResponse.headers.entries()) {
      const lower = k.toLowerCase();
      if (HOP_BY_HOP_HEADERS.has(lower)) {
        continue;
      }
      if (
        responseAllowHeaders.size > 0 &&
        !responseAllowHeaders.has(lower)
      ) {
        continue;
      }
      responseHeaders.set(k, v);
    }

    return new Response(upstreamResponse.body, {
      status: upstreamResponse.status,
      headers: responseHeaders,
    });
  }

  async _handleRotate(request) {
    const bearerToken = parseBearerToken(request);
    if (!bearerToken) {
      console.warn("subumbra: internal rotate rejected (missing bearer token)");
      return jsonError("unauthorized", 401);
    }

    const expectedToken = this.env.SUBUMBRA_SETUP_TOKEN ?? "";
    const tokenOk = await timingSafeEqual(bearerToken, expectedToken);
    if (!tokenOk) {
      console.warn("subumbra: internal rotate rejected (invalid bearer token)");
      return jsonError("forbidden", 403);
    }

    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonError("invalid JSON body", 400);
    }

    const {
      key_id: keyId,
      enc_version: encVersion,
      ciphertext,
      wrapped_dek: wrappedDek,
      pub_key_fp: pubKeyFp,
      policy_hash: policyHash,
      new_policy_hash: newPolicyHash,
    } = payload;

    if (!keyId || !ciphertext || !wrappedDek || !pubKeyFp || !newPolicyHash) {
      return jsonError("missing required fields", 400);
    }

    const custodyRow = this._loadCustodyRow();
    if (!custodyRow) {
      console.error("subumbra: vault not initialized");
      return jsonError("worker not configured", 503);
    }

    let apiKey;
    try {
      const privateKey = await this._primeCachedPrivateKey();
      if (encVersion === 3) {
        apiKey = await decryptV3(
          privateKey,
          custodyRow.pub_key_fp,
          ciphertext,
          wrappedDek,
          pubKeyFp,
          keyId,
          policyHash,
        );
      } else if (encVersion === 2) {
        return jsonError("enc_version 2 not supported — run --rotate-policy to upgrade", 410);
      } else {
        return jsonError("unsupported enc_version", 400);
      }
    } catch (err) {
      console.error("subumbra: rotate decryption failed:", err.message);
      return jsonError("decryption failed", 500);
    }

    const dekBytes = new Uint8Array(
      await crypto.subtle.decrypt(
        { name: "RSA-OAEP" },
        await this._primeCachedPrivateKey(),
        base64ToBytes(wrappedDek),
      )
    );
    const dekKey = await crypto.subtle.importKey(
      "raw", dekBytes, { name: "AES-GCM" }, false, ["encrypt"],
    );
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const aad = new TextEncoder().encode(`subumbra:v3:${keyId}:${newPolicyHash}`);
    const plaintextBytes = new TextEncoder().encode(apiKey);
    const encrypted = new Uint8Array(
      await crypto.subtle.encrypt(
        { name: "AES-GCM", iv: nonce, additionalData: aad },
        dekKey,
        plaintextBytes,
      )
    );
    const combined = new Uint8Array(nonce.length + encrypted.length);
    combined.set(nonce, 0);
    combined.set(encrypted, nonce.length);
    const ciphertextOut = btoa(String.fromCharCode(...combined));
    console.info("subumbra: internal rotate complete key_id=%s", keyId);

    return jsonResponse({
      ciphertext: ciphertextOut,
      enc_version: 3,
    }, 200);
  }

  async _handleReset(request) {
    const bearerToken = parseBearerToken(request);
    if (!bearerToken) {
      console.warn("subumbra: internal reset rejected (missing bearer token)");
      return jsonError("unauthorized", 401);
    }

    const expectedToken = this.env.SUBUMBRA_SETUP_TOKEN ?? "";
    const tokenOk = await timingSafeEqual(bearerToken, expectedToken);
    if (!tokenOk) {
      console.warn("subumbra: internal reset rejected (invalid bearer token)");
      return jsonError("forbidden", 403);
    }

    const vaultInstance = request.headers.get("X-Subumbra-Vault-Instance") ?? VAULT_INSTANCE_NAME;
    try {
      await this.state.storage.deleteAll();
      this._cachedPrivateKey = null;
      console.info("subumbra: internal reset complete vault_instance=%s", vaultInstance);
      return jsonResponse({
        status: "ok",
        vault_instance: vaultInstance,
      }, 200);
    } catch {
      console.error("subumbra: internal reset failed vault_instance=%s", vaultInstance);
      return jsonError("reset failed", 500);
    }
  }

  async _handleManagementAudit(request) {
    const bearerToken = parseBearerToken(request);
    if (!bearerToken) {
      console.warn("subumbra: internal management audit rejected (missing bearer token)");
      return jsonError("unauthorized", 401);
    }

    const expectedToken = this.env.SUBUMBRA_MANAGEMENT_TOKEN ?? "";
    const tokenOk = await timingSafeEqual(bearerToken, expectedToken);
    if (!tokenOk) {
      console.warn("subumbra: internal management audit rejected (invalid bearer token)");
      return jsonError("forbidden", 403);
    }

    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonError("invalid JSON body", 400);
    }

    if (payload && payload.action === "list") {
      const rows = this.state.storage.sql.exec(
        "SELECT id, ts, operation, key_id, actor_token_prefix, result FROM management_audit ORDER BY id ASC"
      ).toArray();
      return jsonResponse({ rows }, 200);
    }

    const {
      operation,
      key_id: keyId = null,
      actor_token_prefix: actorTokenPrefix,
      result,
    } = payload ?? {};
    if (
      typeof operation !== "string" ||
      !operation ||
      (keyId !== null && (typeof keyId !== "string" || !keyId)) ||
      typeof actorTokenPrefix !== "string" ||
      !actorTokenPrefix ||
      typeof result !== "string" ||
      !result
    ) {
      return jsonError("missing required fields", 400);
    }

    this.state.storage.sql.exec(
      "INSERT INTO management_audit (ts, operation, key_id, actor_token_prefix, result) VALUES (?, ?, ?, ?, ?)",
      new Date().toISOString(),
      operation,
      keyId,
      actorTokenPrefix,
      result,
    );

    return jsonResponse({ status: "ok" }, 200);
  }

  async _handleRateCheck(request) {
    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonError("invalid JSON body", 400);
    }

    const {
      endpoint,
      ip_key: ipKey,
      limit,
    } = payload ?? {};
    if (
      typeof endpoint !== "string" ||
      !endpoint ||
      typeof ipKey !== "string" ||
      !ipKey ||
      !Number.isInteger(limit) ||
      limit <= 0
    ) {
      return jsonError("missing required fields", 400);
    }

    const nowSeconds = Math.floor(Date.now() / 1000);
    const windowStart = Math.floor(nowSeconds / 60) * 60;
    this.state.storage.sql.exec(
      `INSERT INTO auth_attempts (endpoint, ip_key, window_start, count)
       VALUES (?, ?, ?, 1)
       ON CONFLICT (endpoint, ip_key, window_start) DO UPDATE SET count = count + 1`,
      endpoint, ipKey, windowStart
    );

    const rows = this.state.storage.sql.exec(
      "SELECT count FROM auth_attempts WHERE endpoint=? AND ip_key=? AND window_start=?",
      endpoint, ipKey, windowStart
    ).toArray();
    const count = rows.length > 0 ? rows[0].count : 0;
    if (count > limit) {
      return jsonResponse({ error: "rate_limit_exceeded_auth" }, 429);
    }

    return new Response(null, { status: 204 });
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Worker entry point
// ─────────────────────────────────────────────────────────────────────────────

export default {
  /**
   * @param {Request}         request
   * @param {{ SUBUMBRA_ADAPTER_TOKENS: string,
   *           SUBUMBRA_MANAGEMENT_TOKEN?: string,
   *           SUBUMBRA_GATE_HMAC_KEY?: string,
   *           SUBUMBRA_GATE_VAPID_PRIVATE_JWK?: string,
   *           SUBUMBRA_GATE: DurableObjectNamespace,
   *           SUBUMBRA_VAULT: DurableObjectNamespace,
   *           PROVIDER_REGISTRY_KV: KVNamespace,
   *           SUBUMBRA_SETUP_TOKEN?: string }} env
   * @param {ExecutionContext} ctx
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ── GET /health ─────────────────────────────────────────────────────────
    if ((request.method === "GET" || request.method === "HEAD") && url.pathname === "/health") {
      return jsonResponse(request.method === "HEAD" ? null : { status: "ok" }, 200);
    }

    if (request.method === "GET" && url.pathname === "/auth-ping") {
      return handleAuthPing(request, env);
    }

    if (request.method === "POST" && url.pathname === "/gate/subscribe") {
      return handleGateSubscribe(request, env);
    }

    if (request.method === "GET" && url.pathname === "/gate/pending") {
      return handleGatePending(request, env);
    }

    if (request.method === "GET" && url.pathname.startsWith("/gate/status/")) {
      return handleGateStatus(request, env);
    }

    if (
      (request.method === "GET" || request.method === "POST")
      && (url.pathname.startsWith("/gate/approve/") || url.pathname.startsWith("/gate/deny/"))
    ) {
      return handleGateDecision(request, env);
    }

    if (request.method === "POST" && url.pathname === "/setup/keygen") {
      return handleSetupKeygen(request, env);
    }

    if (request.method === "POST" && url.pathname === "/setup/ssh-keygen") {
      return handleSetupSshKeygen(request, env);
    }

    if (request.method === "POST" && url.pathname === "/setup/ssh-import") {
      return handleSetupSshImport(request, env);
    }

    if (request.method === "POST" && url.pathname === "/internal/rotate") {
      return handleInternalRotate(request, env);
    }

    if (request.method === "POST" && url.pathname === "/internal/vault-status") {
      return handleInternalVaultStatus(request, env);
    }

    if (request.method === "POST" && url.pathname === "/internal/vault-reset") {
      return handleInternalVaultReset(request, env);
    }

    if (request.method === "POST" && url.pathname === "/manage/key/pause") {
      return handleManagePauseToggle(request, env, true);
    }

    if (request.method === "POST" && url.pathname === "/manage/key/unpause") {
      return handleManagePauseToggle(request, env, false);
    }

    if (request.method === "POST" && url.pathname === "/ssh/sign") {
      return handleSshSign(request, env);
    }

    // ── POST /proxy ─────────────────────────────────────────────────────────
    // Direct mode: caller wraps the full request in our custom JSON format.
    if (request.method === "POST" && url.pathname === "/proxy") {
      return handleProxy(request, env);
    }

    return jsonError("not found", 404);
  },
};

async function forwardGateRequest(request, env) {
  if (!env.SUBUMBRA_GATE) {
    console.error("subumbra: gate binding missing");
    return jsonError("gate unavailable", 503);
  }
  const gate = getGateStub(env);
  let body = undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.text();
  }
  return gate.fetch(`https://do-internal${new URL(request.url).pathname}${new URL(request.url).search}`, {
    method: request.method,
    headers: request.headers,
    body,
  });
}

async function handleGateSubscribe(request, env) {
  return forwardGateRequest(request, env);
}

async function handleGatePending(request, env) {
  return forwardGateRequest(request, env);
}

async function handleGateDecision(request, env) {
  return forwardGateRequest(request, env);
}

async function handleGateStatus(request, env) {
  const auth = await authorizeRequest(request, env);
  if (!auth.ok) {
    return auth.response;
  }
  return forwardGateRequest(request, env);
}

async function handleSetupKeygen(request, env) {
  const rateLimitResponse = await checkAuthRateLimit(request, env, "setup-keygen");
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const setupAuth = await authorizeSetupRequest(request, env);
  if (!setupAuth.ok) {
    return setupAuth.response;
  }

  let vaultInstance = VAULT_INSTANCE_NAME;
  const bodyText = await request.text();
  if (bodyText.trim() !== "") {
    let payload;
    try {
      payload = JSON.parse(bodyText);
    } catch {
      return jsonError("invalid JSON body", 400);
    }
    if (!payload || typeof payload.vault_instance !== "string" || !payload.vault_instance) {
      return jsonError("missing or invalid field: vault_instance", 400);
    }
    vaultInstance = payload.vault_instance;
  }

  try {
    if (!env.SUBUMBRA_VAULT) {
      throw new Error("vault binding missing");
    }
    const vault = getVaultStub(env, vaultInstance);
    return await vault.fetch(`https://do-internal${VAULT_SETUP_PATH}`, {
      method: "POST",
      headers: request.headers,
    });
  } catch {
    console.error("subumbra: setup keygen vault unavailable");
    return jsonError("vault unavailable", 503);
  }
}

async function handleSetupSshKeygen(request, env) {
  const rateLimitResponse = await checkAuthRateLimit(request, env, "ssh-keygen");
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const setupAuth = await authorizeSetupRequest(request, env);
  if (!setupAuth.ok) {
    return setupAuth.response;
  }

  let payloadText;
  try {
    payloadText = await request.text();
  } catch {
    return jsonError("invalid JSON body", 400);
  }

  let payload;
  try {
    payload = JSON.parse(payloadText);
  } catch {
    return jsonError("invalid JSON body", 400);
  }
  if (
    !payload ||
    typeof payload.key_id !== "string" ||
    !payload.key_id ||
    typeof payload.vault_instance !== "string" ||
    !payload.vault_instance
  ) {
    return jsonError("missing required fields", 400);
  }

  try {
    if (!env.SUBUMBRA_VAULT) {
      throw new Error("vault binding missing");
    }
    const vault = getVaultStub(env, payload.vault_instance);
    return await vault.fetch(`https://do-internal${VAULT_SSH_KEYGEN_PATH}`, {
      method: "POST",
      headers: request.headers,
      body: payloadText,
    });
  } catch {
    console.error("subumbra: setup ssh keygen vault unavailable");
    return jsonError("vault unavailable", 503);
  }
}

async function handleSetupSshImport(request, env) {
  const rateLimitResponse = await checkAuthRateLimit(request, env, "ssh-import");
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const setupAuth = await authorizeSetupRequest(request, env);
  if (!setupAuth.ok) {
    return setupAuth.response;
  }

  let payloadText;
  try {
    payloadText = await request.text();
  } catch {
    return jsonError("invalid JSON body", 400);
  }

  let payload;
  try {
    payload = JSON.parse(payloadText);
  } catch {
    return jsonError("invalid JSON body", 400);
  }
  if (
    !payload ||
    typeof payload.key_id !== "string" ||
    !payload.key_id ||
    typeof payload.vault_instance !== "string" ||
    !payload.vault_instance ||
    typeof payload.encrypted_private_key !== "string" ||
    !payload.encrypted_private_key
  ) {
    return jsonError("missing required fields", 400);
  }

  try {
    if (!env.SUBUMBRA_VAULT) {
      throw new Error("vault binding missing");
    }
    const vault = getVaultStub(env, payload.vault_instance);
    return await vault.fetch(`https://do-internal${VAULT_SSH_IMPORT_PATH}`, {
      method: "POST",
      headers: request.headers,
      body: payloadText,
    });
  } catch {
    console.error("subumbra: setup ssh import vault unavailable");
    return jsonError("vault unavailable", 503);
  }
}

async function handleInternalRotate(request, env) {
  const rateLimitResponse = await checkAuthRateLimit(request, env, "internal-rotate");
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const setupAuth = await authorizeSetupRequest(request, env);
  if (!setupAuth.ok) {
    return setupAuth.response;
  }

  let payloadText;
  try {
    payloadText = await request.text();
  } catch {
    return jsonError("invalid JSON body", 400);
  }

  let vaultInstance = VAULT_INSTANCE_NAME;
  let payload;
  try {
    payload = JSON.parse(payloadText);
  } catch {
    return jsonError("invalid JSON body", 400);
  }
  if (payload.vault_instance !== undefined) {
    if (typeof payload.vault_instance !== "string" || !payload.vault_instance) {
      return jsonError("missing or invalid field: vault_instance", 400);
    }
    vaultInstance = payload.vault_instance;
  }

  try {
    if (!env.SUBUMBRA_VAULT) {
      throw new Error("vault binding missing");
    }
    const vault = getVaultStub(env, vaultInstance);
    return await vault.fetch(`https://do-internal${VAULT_ROTATE_PATH}`, {
      method: "POST",
      headers: request.headers,
      body: payloadText,
    });
  } catch {
    console.error("subumbra: internal rotate vault unavailable");
    return jsonError("vault unavailable", 503);
  }
}

async function parseVaultInstancePayload(request) {
  let payloadText;
  try {
    payloadText = await request.text();
  } catch {
    return { error: jsonError("invalid JSON body", 400) };
  }

  let vaultInstance = VAULT_INSTANCE_NAME;
  if (payloadText.trim() !== "") {
    let payload;
    try {
      payload = JSON.parse(payloadText);
    } catch {
      return { error: jsonError("invalid JSON body", 400) };
    }
    if (!payload || typeof payload.vault_instance !== "string" || !payload.vault_instance) {
      return { error: jsonError("missing or invalid field: vault_instance", 400) };
    }
    vaultInstance = payload.vault_instance;
  }

  return { payloadText, vaultInstance };
}

async function handleInternalVaultStatus(request, env) {
  const rateLimitResponse = await checkAuthRateLimit(request, env, "internal-vault-status");
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const setupAuth = await authorizeSetupRequest(request, env);
  if (!setupAuth.ok) {
    return setupAuth.response;
  }

  const parsed = await parseVaultInstancePayload(request);
  if (parsed.error) {
    return parsed.error;
  }

  try {
    if (!env.SUBUMBRA_VAULT) {
      throw new Error("vault binding missing");
    }
    const vault = getVaultStub(env, parsed.vaultInstance);
    return await vault.fetch(`https://do-internal${VAULT_STATUS_PATH}`, {
      method: "POST",
      headers: {
        Authorization: request.headers.get("Authorization") ?? "",
        "X-Subumbra-Vault-Instance": parsed.vaultInstance,
      },
    });
  } catch {
    console.error("subumbra: internal vault status unavailable");
    return jsonError("vault unavailable", 503);
  }
}

async function handleInternalVaultReset(request, env) {
  const rateLimitResponse = await checkAuthRateLimit(request, env, "internal-vault-reset");
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const setupAuth = await authorizeSetupRequest(request, env);
  if (!setupAuth.ok) {
    return setupAuth.response;
  }

  const parsed = await parseVaultInstancePayload(request);
  if (parsed.error) {
    return parsed.error;
  }

  try {
    if (!env.SUBUMBRA_VAULT) {
      throw new Error("vault binding missing");
    }
    const vault = getVaultStub(env, parsed.vaultInstance);
    return await vault.fetch(`https://do-internal${VAULT_RESET_PATH}`, {
      method: "POST",
      headers: {
        Authorization: request.headers.get("Authorization") ?? "",
        "X-Subumbra-Vault-Instance": parsed.vaultInstance,
      },
    });
  } catch {
    console.error("subumbra: internal vault reset unavailable");
    return jsonError("vault unavailable", 503);
  }
}

async function handleManagePauseToggle(request, env, paused) {
  const rateLimitResponse = await checkAuthRateLimit(request, env, "manage-key");
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const managementAuth = await authorizeManagementRequest(request, env);
  if (!managementAuth.ok) {
    return managementAuth.response;
  }

  if (!env.PROVIDER_REGISTRY_KV) {
    console.error("subumbra: worker bindings not configured (run bootstrap)");
    return jsonError("worker not configured", 503);
  }

  let payload;
  try {
    payload = await request.json();
  } catch {
    return jsonError("invalid JSON body", 400);
  }
  if (!payload || typeof payload.key_id !== "string" || !payload.key_id) {
    return jsonError("missing required fields", 400);
  }

  const keyName = `key:${payload.key_id}`;
  const currentRaw = await env.PROVIDER_REGISTRY_KV.get(keyName);
  if (!currentRaw) {
    return jsonError("key not found", 404);
  }

  let keyEntry;
  try {
    keyEntry = parseStructuredRegistryJson(currentRaw, keyName);
  } catch (err) {
    console.error("subumbra: management mutation failed — invalid key entry %s", keyName);
    return jsonError("worker not configured", 503);
  }

  keyEntry.paused = paused;
  await env.PROVIDER_REGISTRY_KV.put(keyName, JSON.stringify(keyEntry));
  try {
    await writeManagementAudit(env, {
      operation: paused ? "pause_key" : "unpause_key",
      key_id: payload.key_id,
      actor_token_prefix: managementAuth.actorTokenPrefix,
      result: "success",
    });
  } catch {
    await env.PROVIDER_REGISTRY_KV.put(keyName, currentRaw);
    console.error("subumbra: management audit write failed key_id=%s", payload.key_id);
    return jsonError("vault unavailable", 503);
  }

  return jsonResponse({ status: "ok", key_id: payload.key_id, paused }, 200);
}

async function handleSshSign(request, env) {
  const rateLimitResponse = await checkAuthRateLimit(request, env, "ssh-sign");
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const auth = await authorizeRequest(request, env);
  if (!auth.ok) {
    return auth.response;
  }

  if (!env.PROVIDER_REGISTRY_KV) {
    console.error("subumbra: worker bindings not configured (run bootstrap)");
    return jsonError("worker not configured", 503);
  }

  const sessionActive = await env.PROVIDER_REGISTRY_KV.get(`active_adapter:${auth.adapterId}`);
  if (!sessionActive) {
    console.warn("subumbra: system_locked adapter=%s", auth.adapterId);
    return jsonError("system_locked", 403);
  }

  let payload;
  try {
    payload = await request.json();
  } catch {
    return jsonError("invalid JSON body", 400);
  }
  if (!payload || typeof payload.key_id !== "string" || !payload.key_id || typeof payload.challenge !== "string" || !payload.challenge) {
    return jsonError("missing required fields", 400);
  }
  if (
    Object.prototype.hasOwnProperty.call(payload, "verified_host_fingerprint") &&
    (typeof payload.verified_host_fingerprint !== "string" || !payload.verified_host_fingerprint)
  ) {
    return jsonError("missing required fields", 400);
  }
  const gateApprovedId =
    typeof payload.gate_approved_id === "string" && payload.gate_approved_id
      ? payload.gate_approved_id
      : null;

  const keyRaw = await env.PROVIDER_REGISTRY_KV.get(`key:${payload.key_id}`, {
    cacheTtl: 30,
  });
  if (!keyRaw) {
    return jsonError("key not found", 404);
  }

  let keyEntry;
  try {
    keyEntry = parseStructuredRegistryJson(keyRaw, `key:${payload.key_id}`);
  } catch {
    console.error("subumbra: invalid ssh key registry entry key_id=%s", payload.key_id);
    return jsonError("worker not configured", 503);
  }
  if (keyEntry.type !== "ssh_key") {
    return jsonError("key not found", 404);
  }
  const adapters = optionalStringArray(keyEntry.adapters);
  if (!adapters || !adapters.includes(auth.adapterId)) {
    return jsonError("adapter not permitted", 403);
  }
  const policyAllow = keyEntry && keyEntry.policy && typeof keyEntry.policy === "object" ? keyEntry.policy.allow : undefined;
  const rawAllowedHosts = policyAllow && typeof policyAllow === "object" ? policyAllow.hosts : undefined;
  const allowedHosts = optionalStringArray(rawAllowedHosts);
  if (rawAllowedHosts !== undefined && allowedHosts === null) {
    console.error("subumbra: invalid ssh allow.hosts key_id=%s", payload.key_id);
    return jsonError("worker not configured", 503);
  }
  const verifiedHostFingerprint =
    typeof payload.verified_host_fingerprint === "string" && payload.verified_host_fingerprint
      ? payload.verified_host_fingerprint
      : null;
  if (allowedHosts && allowedHosts.length > 0) {
    if (!verifiedHostFingerprint) {
      console.warn("subumbra: ssh sign denied key_id=%s reason=host_required", payload.key_id);
      return jsonError("host_required", 403);
    }
    if (!allowedHosts.includes(verifiedHostFingerprint)) {
      console.warn(
        "subumbra: ssh sign denied key_id=%s reason=host_not_allowed host_fp=%s",
        payload.key_id,
        verifiedHostFingerprint,
      );
      return jsonError("host_not_allowed", 403);
    }
  }

  let sessionQuota = null;
  const sessionScopeKey = `ssh_session_scope:${auth.adapterId}:${payload.key_id}`;
  let sessionScopeRaw;
  try {
    sessionScopeRaw = await env.PROVIDER_REGISTRY_KV.get(sessionScopeKey);
  } catch {
    console.error("subumbra: ssh session scope unavailable key=%s", sessionScopeKey);
    return jsonError("session_quota_unavailable", 503);
  }
  if (typeof sessionScopeRaw === "string" && sessionScopeRaw) {
    try {
      sessionQuota = parseSshSessionScopeMetadata(
        sessionScopeRaw,
        auth.adapterId,
        payload.key_id,
      );
    } catch (err) {
      console.error(
        "subumbra: invalid ssh session scope key=%s error=%s",
        sessionScopeKey,
        err instanceof Error ? err.message : "unknown",
      );
      return jsonError("session_quota_unavailable", 503);
    }
  }

  const vaultInstance =
    typeof keyEntry.vault_instance === "string" && keyEntry.vault_instance
      ? keyEntry.vault_instance
      : VAULT_INSTANCE_NAME;

  const requestDigest = await sha256HexText(JSON.stringify(canonicalizeJson({
    flow: "ssh_sign",
    key_id: payload.key_id,
    adapter_id: auth.adapterId,
    challenge: payload.challenge,
    verified_host_fingerprint: verifiedHostFingerprint,
    policy_hash: keyEntry.policy_hash,
    vault_instance: vaultInstance,
  })));
  const gateRule = matchGateRule(keyEntry.policy?.gate ?? optionalGatePolicy(keyEntry.policy?.gate), {
    flow: "ssh_sign",
    adapterId: auth.adapterId,
    method: "POST",
    path: "/ssh/sign",
  });
  if (gateRule) {
    if (!gateApprovedId) {
      try {
        return await submitGateApproval(env, {
          flow: "ssh_sign",
          key_id: payload.key_id,
          adapter_id: auth.adapterId,
          target_summary: verifiedHostFingerprint ? `ssh:${verifiedHostFingerprint}` : "ssh:unverified-host",
          request_digest: requestDigest,
          timeout_seconds: gateRule.timeout_seconds,
          origin: new URL(request.url).origin,
        });
      } catch {
        console.error("subumbra: gate submit unavailable");
        return jsonError("gate_unavailable", 503);
      }
    }
    try {
      const gateConsumeResponse = await consumeGateApproval(env, {
        request_id: gateApprovedId,
        request_digest: requestDigest,
        flow: "ssh_sign",
        key_id: payload.key_id,
        adapter_id: auth.adapterId,
      });
      if (gateConsumeResponse.status !== 204) {
        return gateConsumeResponse;
      }
    } catch {
      console.error("subumbra: gate consume unavailable");
      return jsonError("gate_unavailable", 503);
    }
  }

  try {
    if (!env.SUBUMBRA_VAULT) {
      throw new Error("vault binding missing");
    }
    const vault = getVaultStub(env, vaultInstance);
    return await vault.fetch(`https://do-internal${VAULT_SSH_SIGN_PATH}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        key_id: payload.key_id,
        challenge: payload.challenge,
        ...(sessionQuota && sessionQuota.maxSignOps !== null
          ? {
            session_id: sessionQuota.sessionId,
            adapter_id: sessionQuota.adapterId,
            max_sign_ops: sessionQuota.maxSignOps,
          }
          : {}),
      }),
    });
  } catch {
    console.error("subumbra: ssh sign vault unavailable");
    return jsonError("vault unavailable", 503);
  }
}

async function authorizeRequest(request, env) {
  if (!env.SUBUMBRA_ADAPTER_TOKENS) {
    console.error("subumbra: worker bindings not configured (run bootstrap)");
    return { ok: false, response: jsonError("worker not configured", 503) };
  }

  let validTokens;
  try {
    validTokens = parseAdapterTokens(env.SUBUMBRA_ADAPTER_TOKENS);
  } catch (err) {
    console.error("subumbra: SUBUMBRA_ADAPTER_TOKENS invalid:", err.message);
    return { ok: false, response: jsonError("worker not configured", 503) };
  }

  const incomingToken = request.headers.get("X-Subumbra-Token") ?? "";
  const adapterId = await resolveAdapterToken(incomingToken, validTokens);
  if (adapterId === null) {
    console.warn("subumbra: unauthorized request");
    return { ok: false, response: jsonError("unauthorized", 401) };
  }

  return { ok: true, adapterId };
}

async function authorizeManagementRequest(request, env) {
  const expectedToken = env.SUBUMBRA_MANAGEMENT_TOKEN ?? "";
  if (!expectedToken) {
    console.error("subumbra: management auth unavailable (run bootstrap)");
    return { ok: false, response: jsonError("worker not configured", 503) };
  }

  const bearerToken = parseBearerToken(request);
  if (!bearerToken) {
    console.warn("subumbra: unauthorized management request");
    return { ok: false, response: jsonError("unauthorized", 401) };
  }

  const tokenOk = await timingSafeEqual(bearerToken, expectedToken);
  if (!tokenOk) {
    console.warn("subumbra: forbidden management request");
    return { ok: false, response: jsonError("forbidden", 403) };
  }

  return {
    ok: true,
    actorTokenPrefix: tokenPrefix(bearerToken),
  };
}

async function authorizeSetupRequest(request, env) {
  const expectedToken = env.SUBUMBRA_SETUP_TOKEN ?? "";
  if (!expectedToken) {
    return { ok: false, response: jsonError("unauthorized", 401) };
  }

  const bearerToken = parseBearerToken(request);
  if (!bearerToken) {
    return { ok: false, response: jsonError("unauthorized", 401) };
  }

  const tokenOk = await timingSafeEqual(bearerToken, expectedToken);
  if (!tokenOk) {
    return { ok: false, response: jsonError("forbidden", 403) };
  }

  return { ok: true };
}

async function checkAuthRateLimit(request, env, endpoint) {
  if (!env.SUBUMBRA_VAULT) {
    console.error("subumbra: worker bindings not configured (run bootstrap)");
    return jsonError("worker not configured", 503);
  }

  const limit = AUTH_RATE_LIMITS[endpoint];
  if (!Number.isInteger(limit) || limit <= 0) {
    console.error("subumbra: auth rate limit misconfigured endpoint=%s", endpoint);
    return jsonError("worker not configured", 503);
  }

  const ipKey = request.cf?.connectingIp ?? "unknown";

  try {
    const vault = getVaultStub(env);
    const response = await vault.fetch(`https://do-internal${VAULT_RATE_CHECK_PATH}`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({
        endpoint,
        ip_key: ipKey,
        limit,
      }),
    });

    if (response.status === 429) {
      console.warn("subumbra: auth rate limit exceeded endpoint=%s ip=%s", endpoint, ipKey);
      return jsonResponse({ error: "rate_limit_exceeded_auth" }, 429);
    }
    if (response.status === 204) {
      return null;
    }

    console.error("subumbra: auth rate limit check failed endpoint=%s status=%s", endpoint, response.status);
    return jsonError("vault unavailable", 503);
  } catch {
    console.error("subumbra: auth rate limit check unavailable");
    return jsonError("vault unavailable", 503);
  }
}

async function writeManagementAudit(env, payload, vaultInstance = VAULT_INSTANCE_NAME) {
  if (!env.SUBUMBRA_VAULT) {
    throw new Error("vault binding missing");
  }
  const bearerToken = env.SUBUMBRA_MANAGEMENT_TOKEN ?? "";
  if (!bearerToken) {
    throw new Error("management token missing");
  }

  const vault = getVaultStub(env, vaultInstance);
  const response = await vault.fetch(`https://do-internal${VAULT_MANAGEMENT_AUDIT_PATH}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${bearerToken}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`management audit write failed: HTTP ${response.status}`);
  }
}

async function submitGateApproval(env, payload) {
  if (!env.SUBUMBRA_GATE) {
    throw new Error("gate binding missing");
  }
  const gate = getGateStub(env);
  return gate.fetch(`https://do-internal${GATE_SUBMIT_PATH}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

async function consumeGateApproval(env, payload) {
  if (!env.SUBUMBRA_GATE) {
    throw new Error("gate binding missing");
  }
  const gate = getGateStub(env);
  return gate.fetch(`https://do-internal${GATE_CONSUME_PATH}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

async function handleAuthPing(request, env) {
  const rateLimitResponse = await checkAuthRateLimit(request, env, "auth-ping");
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  // CF Access is the auth gate for this endpoint; no Subumbra token required.
  // Reaching here means CF Access passed the request through.
  return jsonResponse({ status: "ok", timestamp: new Date().toISOString() }, 200);
}

// ─────────────────────────────────────────────────────────────────────────────
// handleProxy — main request handler
// ─────────────────────────────────────────────────────────────────────────────

async function handleProxy(request, env) {
  const rateLimitResponse = await checkAuthRateLimit(request, env, "proxy");
  if (rateLimitResponse) {
    return rateLimitResponse;
  }
  const auth = await authorizeRequest(request, env);
  if (!auth.ok) {
    return auth.response;
  }

  if (!env.PROVIDER_REGISTRY_KV || !env.SUBUMBRA_VAULT) {
    console.error("subumbra: worker bindings not configured (run bootstrap)");
    return jsonError("worker not configured", 503);
  }

  const sessionActive = await env.PROVIDER_REGISTRY_KV.get(
    `active_adapter:${auth.adapterId}`
  );
  if (!sessionActive) {
    console.warn("subumbra: system_locked adapter=%s", auth.adapterId);
    return jsonError("system_locked", 403);
  }

  // ── 2. Parse request body ─────────────────────────────────────────────────
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonError("request body must be JSON", 400);
  }

  const { ciphertext, provider, target_url, method, headers: fwdHeaders, body: reqBody,
    wrapped_dek, pub_key_fp, key_id, enc_version, policy_id, policy_hash } = body;
  const vaultInstance = body.vault_instance ?? VAULT_INSTANCE_NAME;
  const gateApprovedId =
    typeof body.gate_approved_id === "string" && body.gate_approved_id
      ? body.gate_approved_id
      : null;

  if (!ciphertext || typeof ciphertext !== "string") {
    return jsonError("missing or invalid field: ciphertext", 400);
  }
  if (!provider || typeof provider !== "string") {
    return jsonError("missing or invalid field: provider", 400);
  }
  if (!target_url || typeof target_url !== "string") {
    return jsonError("missing or invalid field: target_url", 400);
  }
  if (!pub_key_fp || typeof pub_key_fp !== "string") {
    return jsonError("missing or invalid field: pub_key_fp", 400);
  }
  if (!key_id || typeof key_id !== "string") {
    return jsonError("missing or invalid field: key_id", 400);
  }
  if (!vaultInstance || typeof vaultInstance !== "string") {
    return jsonError("missing or invalid field: vault_instance", 400);
  }

  const version = enc_version ?? 1;
  if (!wrapped_dek || typeof wrapped_dek !== "string") {
    return jsonError("missing or invalid field: wrapped_dek", 400);
  }
  if (version === 3 && (!policy_hash || typeof policy_hash !== "string")) {
    return jsonError("missing or invalid field: policy_hash", 400);
  }
  if (version === 2) {
    console.warn("subumbra: v2 deprecated key_id=%s", key_id);
    return jsonError("enc_version 2 not supported — run --rotate-policy to upgrade", 410);
  }
  if (version !== 3) {
    console.error("subumbra: unsupported enc_version", version);
    return jsonError("key format not supported — re-bootstrap required", 400);
  }

  // ── 2.5. Intent observer / request-side guardrails ──────────────────────
  const intentField =
    body.intent && typeof body.intent === "object" && !Array.isArray(body.intent)
      ? body.intent
      : null;
  const intentSource = intentField && typeof intentField.source === "string"
    ? intentField.source
    : null;
  const intentTrust =
    intentField && intentField.trust && typeof intentField.trust === "object" &&
      !Array.isArray(intentField.trust)
      ? intentField.trust
      : null;
  if (intentField) {
    console.info("subumbra: intent key_id=%s source=%s", key_id, intentSource);
  }

  let parsedTarget;
  try {
    parsedTarget = new URL(target_url);
  } catch (e) {
    console.error("subumbra: URL parse error", e);
    return jsonError("invalid target_url", 400);
  }
  if (parsedTarget.protocol !== "https:") {
    return jsonError("target_url must use https://", 400);
  }
  let registryEntry;
  try {
    registryEntry = await getRegistryEntry(env, key_id);
  } catch (err) {
    if (err.code === "registry_missing") {
      console.error("subumbra: structured registry entry missing:", err.message);
    } else if (err.code === "registry_invalid_json") {
      console.error("subumbra: structured registry invalid JSON");
    } else {
      console.error("subumbra: structured registry validation failed:", err.message);
    }
    return jsonError("worker not configured", 503);
  }
  if (registryEntry.paused) {
    console.warn("subumbra: policy deny reason=key_paused adapter=%s key_id=%s", auth.adapterId, key_id);
    return jsonError("key_paused", 403);
  }
  if (provider !== registryEntry.provider_id) {
    console.warn(
      "subumbra: provider/target_url mismatch — provider=%s target=%s",
      provider, parsedTarget.hostname,
    );
    return jsonError("target_url host does not match declared provider", 400);
  }
  if (parsedTarget.hostname !== registryEntry.target_host) {
    console.warn("subumbra: SSRF attempt — rejected target_url %s for key_id=%s", parsedTarget.hostname, key_id);
    return jsonError("target_url not allowed", 403);
  }
  if (parsedTarget.port !== "" && parsedTarget.port !== "443") {
    console.warn(
      "subumbra: SSRF port attempt — port=%s key_id=%s",
      parsedTarget.port,
      key_id,
    );
    return jsonError("target_url port not allowed", 403);
  }
  console.info("subumbra: enc_version=3 key_id=%s", key_id);

  const policyIntent =
    registryEntry.intent && typeof registryEntry.intent === "object" && !Array.isArray(registryEntry.intent)
      ? registryEntry.intent
      : null;
  const policyTrust =
    policyIntent && policyIntent.trust && typeof policyIntent.trust === "object" &&
      !Array.isArray(policyIntent.trust)
      ? policyIntent.trust
      : null;
  const allowedInitiators = policyTrust
    ? optionalStringArray(policyTrust.allowed_initiators)
    : null;
  const allowedContentSources = policyTrust
    ? optionalStringArray(policyTrust.allowed_content_sources)
    : null;

  if (
    ((allowedInitiators && allowedInitiators.length > 0) ||
      (allowedContentSources && allowedContentSources.length > 0)) &&
    intentField === null
  ) {
    console.warn(
      "subumbra: policy deny reason=intent_required adapter=%s key_id=%s",
      auth.adapterId,
      key_id,
    );
    return jsonError("intent_required", 403);
  }

  const requestInitiators = intentTrust
    ? optionalStringArray(intentTrust.allowed_initiators)
    : null;
  if (
    allowedInitiators &&
    allowedInitiators.length > 0 &&
    requestInitiators &&
    !requestInitiators.every((value) => allowedInitiators.includes(value))
  ) {
    console.warn(
      "subumbra: policy deny reason=intent_disallowed_initiator adapter=%s key_id=%s source=%s",
      auth.adapterId,
      key_id,
      intentSource,
    );
    return jsonError("intent_disallowed_initiator", 403);
  }

  const requestContentSources = intentTrust
    ? optionalStringArray(intentTrust.allowed_content_sources)
    : null;
  if (
    allowedContentSources &&
    allowedContentSources.length > 0 &&
    requestContentSources &&
    !requestContentSources.every((value) => allowedContentSources.includes(value))
  ) {
    console.warn(
      "subumbra: policy deny reason=intent_disallowed_content_source adapter=%s key_id=%s source=%s",
      auth.adapterId,
      key_id,
      intentSource,
    );
    return jsonError("intent_disallowed_content_source", 403);
  }

  const policyMatch =
    policyIntent && typeof policyIntent.policy_match === "string"
      ? policyIntent.policy_match
      : null;
  if (policyMatch && intentSource) {
    if (!new RegExp(policyMatch).test(intentSource)) {
      console.warn(
        "subumbra: policy deny reason=intent_disallowed_source adapter=%s key_id=%s source=%s",
        auth.adapterId,
        key_id,
        intentSource,
      );
      return jsonError("intent_disallowed_source", 403);
    }
  }

  // ── 3.5. Allow-block enforcement ─────────────────────────────────────────
  if (!registryEntry.allow_adapters.includes(auth.adapterId)) {
    console.warn(
      "subumbra: policy deny adapter=%s key_id=%s",
      auth.adapterId,
      key_id,
    );
    return jsonError("adapter not permitted", 403);
  }

  const reqMethod = method ?? "";
  if (!registryEntry.allow_methods.includes(reqMethod)) {
    console.warn("subumbra: policy deny method=%s key_id=%s", reqMethod, key_id);
    return jsonError("method not allowed", 405);
  }

  const targetPath = parsedTarget.pathname;
  const pathAllowed = registryEntry.allow_path_prefixes.some((prefix) =>
    targetPath.startsWith(prefix),
  );
  if (!pathAllowed) {
    console.warn("subumbra: policy deny path key_id=%s", key_id);
    return jsonError("path not permitted", 403);
  }

  if (registryEntry.allow_content_types.length > 0) {
    // Enforce content-type whenever fwdHeaders declares one, or when a body
    // is present. Do NOT skip this check based on reqBody being null — a
    // forwarded Content-Type header with an empty body still violates policy.
    let rawCT = "";
    for (const [k, v] of Object.entries(fwdHeaders || {})) {
      if (k.toLowerCase() === "content-type") {
        rawCT = v.toLowerCase().split(";")[0].trim();
        break;
      }
    }
    // Only enforce when a content-type is actually declared in the forwarded
    // headers. If fwdHeaders has no content-type key at all, skip (no CT to
    // check against). This preserves GET / HEAD without Content-Type.
    if (rawCT !== "") {
      const ctAllowed = registryEntry.allow_content_types.some(
        (ct) => rawCT === ct.toLowerCase(),
      );
      if (!ctAllowed) {
        console.warn("subumbra: policy deny content_type key_id=%s", key_id);
        return jsonError("content-type not permitted", 415);
      }
    }
  }

  if (registryEntry.allow_max_body_bytes !== null && reqBody !== null) {
    const bodySize = new TextEncoder().encode(JSON.stringify(reqBody)).length;
    if (bodySize > registryEntry.allow_max_body_bytes) {
      console.warn(
        "subumbra: policy deny body_size=%d max=%d key_id=%s",
        bodySize,
        registryEntry.allow_max_body_bytes,
        key_id,
      );
      return jsonError("request body too large", 413);
    }
  }

  // ── 4. Strip hop-by-hop and internal headers from forwarded headers ────────
  const cleanHeaders = {};
  for (const [k, v] of Object.entries(fwdHeaders || {})) {
    if (!HOP_BY_HOP_HEADERS.has(k.toLowerCase())) {
      cleanHeaders[k] = v;
    }
  }
  if (registryEntry.auth_scheme === "header" && registryEntry.auth_header_name) {
    const stripLower = registryEntry.auth_header_name.toLowerCase();
    for (const k of Object.keys(cleanHeaders)) {
      if (k.toLowerCase() === stripLower) {
        delete cleanHeaders[k];
      }
    }
  }
  if (registryEntry.allow_request_headers.length > 0) {
    const requestAllowHeaders = new Set(
      registryEntry.allow_request_headers.map((headerName) => headerName.toLowerCase()),
    );
    for (const k of Object.keys(cleanHeaders)) {
      if (!requestAllowHeaders.has(k.toLowerCase())) {
        delete cleanHeaders[k];
      }
    }
  }

  const requestDigest = await sha256HexText(JSON.stringify(canonicalizeJson({
    flow: "proxy",
    key_id,
    adapter_id: auth.adapterId,
    target_url,
    method: method ?? "POST",
    headers: normalizeHeaderMap(cleanHeaders),
    body: canonicalizeJson(reqBody ?? null),
    policy_hash: registryEntry.policy_hash,
    vault_instance: vaultInstance,
  })));
  const gateRule = matchGateRule(registryEntry.gate, {
    flow: "proxy",
    adapterId: auth.adapterId,
    method: method ?? "POST",
    path: targetPath,
  });
  if (gateRule) {
    if (!gateApprovedId) {
      try {
        return await submitGateApproval(env, {
          flow: "proxy",
          key_id,
          adapter_id: auth.adapterId,
          target_summary: `${parsedTarget.hostname}${targetPath}`,
          request_digest: requestDigest,
          timeout_seconds: gateRule.timeout_seconds,
          origin: new URL(request.url).origin,
        });
      } catch {
        console.error("subumbra: gate submit unavailable");
        return jsonError("gate_unavailable", 503);
      }
    }
    try {
      const gateConsumeResponse = await consumeGateApproval(env, {
        request_id: gateApprovedId,
        request_digest: requestDigest,
        flow: "proxy",
        key_id,
        adapter_id: auth.adapterId,
      });
      if (gateConsumeResponse.status !== 204) {
        return gateConsumeResponse;
      }
    } catch {
      console.error("subumbra: gate consume unavailable");
      return jsonError("gate_unavailable", 503);
    }
  }

  // ── 5. Forward encrypted envelope to Durable Object ───────────────────────
  const doStub = getVaultStub(env, vaultInstance);

  const doPayload = JSON.stringify({
    ciphertext,
    wrappedDek: wrapped_dek,
    pubKeyFp: pub_key_fp,
    keyId: key_id,
    vaultInstance,
    encVersion: version,
    policyId: policy_id ?? registryEntry.policy_id,
    policyHash: registryEntry.policy_hash,
    targetUrl: target_url,
    method: method ?? "POST",
    headers: cleanHeaders,
    body: reqBody ?? null,
    authScheme: registryEntry.auth_scheme,
    authHeaderName: registryEntry.auth_header_name ?? null,
    authQueryParam: registryEntry.auth_query_param ?? null,
    responseAllowHeaders: registryEntry.response_allow_headers,
    adapterId: auth.adapterId,
    velocity: registryEntry.velocity,
  });

  const doResponse = await doStub.fetch(`https://do-internal${VAULT_EXECUTE_PATH}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: doPayload,
  });

  // ── 7. Scan or stream DO response back to caller ──────────────────────────
  const responseHeaders = new Headers(doResponse.headers);
  responseHeaders.set("X-Subumbra-Provider", registryEntry.provider_id);  // audit trail
  responseHeaders.set("cache-control", "no-store");

  const denyPatterns = registryEntry.deny_patterns;
  const contentType = (doResponse.headers.get("content-type") ?? "").toLowerCase();
  const shouldScan =
    denyPatterns.length > 0 &&
    !contentType.startsWith("text/event-stream");

  if (shouldScan) {
    let responseBody;
    try {
      responseBody = await doResponse.text();
    } catch (e) {
      console.error("subumbra: response_read_error key_id=%s", key_id);
      return jsonError("response_read_error", 403);
    }
    const scanBody = responseBody.toLowerCase();
    for (let i = 0; i < denyPatterns.length; i++) {
      if (scanBody.includes(denyPatterns[i].toLowerCase())) {
        console.warn(
          "subumbra: policy deny reason=response_deny_pattern_match adapter=%s key_id=%s pattern_index=%d",
          auth.adapterId,
          key_id,
          i,
        );
        return jsonError("response_deny_pattern_match", 403);
      }
    }
    return new Response(responseBody, {
      status: doResponse.status,
      headers: responseHeaders,
    });
  }

  return new Response(doResponse.body, {
    status: doResponse.status,
    headers: responseHeaders,
  });
}
