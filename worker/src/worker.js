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
const AUTH_RATE_LIMITS = {
  "auth-ping": 20,
  "manage-key": 20,
  "setup-keygen": 5,
  "ssh-keygen": 3,
  "ssh-import": 3,
  "ssh-sign": 60,
  "internal-rotate": 5,
  "internal-vault-status": 5,
  "internal-vault-reset": 5,
};
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

function jsonError(message, status) {
  return jsonResponse({ error: message }, status);
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

function base64UrlToBytes(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
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
        "INSERT INTO ssh_keys (key_id, private_key_pkcs8, public_key_raw, public_key_ssh, algorithm, created_at) VALUES (?, ?, ?, ?, ?, ?)",
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
        "INSERT INTO ssh_keys (key_id, private_key_pkcs8, public_key_raw, public_key_ssh, algorithm, created_at) VALUES (?, ?, ?, ?, ?, ?)",
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

    const row = this._loadSshKeyRow(payload.key_id);
    if (!row) {
      return jsonError("key not found", 404);
    }

    try {
      const challengeBytes = Uint8Array.from(atob(payload.challenge), (c) => c.charCodeAt(0));
      const privateKey = await this._importEd25519PrivateKey(row.private_key_pkcs8);
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

  const authResult = await authorizeRequest(request, env);
  if (!authResult.ok) {
    return authResult.response;
  }
  const auth = authResult.auth;

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

  const vaultInstance =
    typeof keyEntry.vault_instance === "string" && keyEntry.vault_instance
      ? keyEntry.vault_instance
      : VAULT_INSTANCE_NAME;

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
