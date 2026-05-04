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
 *   SUBUMBRA_SETUP_TOKEN    — transient setup token, set by bootstrap
 */

"use strict";

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

function validateProviderRegistry(registry) {
  const REQUIRED = ["provider_id", "target_host", "auth_header", "auth_prefix"];
  const seenIds = new Set();
  const seenHosts = new Set();

  if (!Array.isArray(registry)) {
    throw new Error("providers.json: top-level value must be an array");
  }

  for (const entry of registry) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error(`providers.json: each entry must be an object: ${JSON.stringify(entry)}`);
    }

    for (const field of REQUIRED) {
      if (typeof entry[field] !== "string") {
        throw new Error(
          `providers.json: entry missing or non-string field '${field}': ${JSON.stringify(entry)}`
        );
      }
    }

    if (seenIds.has(entry.provider_id)) {
      throw new Error(`providers.json: duplicate provider_id '${entry.provider_id}'`);
    }
    if (seenHosts.has(entry.target_host)) {
      throw new Error(`providers.json: duplicate target_host '${entry.target_host}'`);
    }

    seenIds.add(entry.provider_id);
    seenHosts.add(entry.target_host);
  }
}

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

  let template = null;
  const templateName = keyEntry.template_name;
  if (typeof templateName === "string" && templateName) {
    const templateRaw = await env.PROVIDER_REGISTRY_KV.get(`template:${templateName}`, {
      cacheTtl: 30,
    });
    if (!templateRaw) {
      const err = new Error(`template:${templateName} missing`);
      err.code = "registry_missing";
      throw err;
    }
    template = parseStructuredRegistryJson(templateRaw, `template:${templateName}`);
  }

  if (keyEntry.target_host !== policyTarget.host) {
    const err = new Error(`key:${keyId} target_host does not match policy target.host`);
    err.code = "registry_invalid_schema";
    throw err;
  }

  return {
    key_id: keyEntry.key_id,
    policy_id: policyId,
    policy_hash: keyEntry.policy_hash,
    target_host: keyEntry.target_host,
    provider_id: keyEntry.provider,
    auth_header: template?.auth_header ?? keyEntry.auth_header,
    auth_prefix: template?.auth_prefix ?? keyEntry.auth_prefix,
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
]);

const VAULT_INSTANCE_NAME = "vault";
const VAULT_SETUP_PATH = "/setup-keygen";
const VAULT_EXECUTE_PATH = "/execute";
const VAULT_ROTATE_PATH = "/rotate";
const VAULT_SCHEMA = `
  CREATE TABLE IF NOT EXISTS custody (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    private_key_pkcs8 BLOB NOT NULL,
    public_key_spki BLOB NOT NULL,
    pub_key_fp TEXT NOT NULL,
    created_at TEXT NOT NULL
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

async function importLegacyPrivateKey(env) {
  const derBytes = base64ToBytes(env.WORKER_PRIVATE_KEY);
  return crypto.subtle.importKey(
    "pkcs8",
    derBytes,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["decrypt"],
  );
}

/**
 * V2 Asymmetric Envelope Decrypt.
 *
 * 1. Verify pub_key_fp matches the loaded private key's fingerprint
 * 2. Unwrap DEK using RSA-OAEP private key
 * 3. Decrypt API key using DEK with AES-256-GCM + AAD
 *
 * @param {CryptoKey} privateKey - non-extractable RSA private key
 * @param {string} expectedPubKeyFp - stored fingerprint for the loaded key
 * @param {string} ciphertextB64 - base64: nonce[12] || AES-GCM(api_key, aad)
 * @param {string} wrappedDekB64 - base64: RSA-OAEP(DEK[32])
 * @param {string} pubKeyFp      - sha256:<hex> fingerprint of wrapping public key
 * @param {string} keyId         - key_id for AAD binding
 * @returns {Promise<string>}    - plaintext API key
 */
async function decryptV2(privateKey, expectedPubKeyFp, ciphertextB64, wrappedDekB64, pubKeyFp, keyId) {
  if (!wrappedDekB64 || !ciphertextB64 || !keyId) {
    throw new Error("missing required V2 envelope fields");
  }

  // 1. Verify pub_key_fp matches loaded private key's fingerprint
  if (pubKeyFp !== expectedPubKeyFp) {
    throw new Error(
      `record wrapped with unknown key pair (record: ${pubKeyFp}, ` +
      `loaded: ${expectedPubKeyFp}) — re-bootstrap required`
    );
  }

  // 2. Unwrap DEK using RSA private key
  const dekBytes = await crypto.subtle.decrypt(
    { name: "RSA-OAEP" },
    privateKey,
    base64ToBytes(wrappedDekB64),
  );

  // 3. Import DEK for AES-GCM (non-extractable)
  const dekKey = await crypto.subtle.importKey(
    "raw", dekBytes, { name: "AES-GCM" }, false, ["decrypt"],
  );

  // 4. Decrypt API key with AAD = "subumbra:v2:<key_id>"
  const aad = new TextEncoder().encode(`subumbra:v2:${keyId}`);
  const ctBlob = base64ToBytes(ciphertextB64);
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: ctBlob.slice(0, 12), additionalData: aad },
    dekKey,
    ctBlob.slice(12),
  );

  return new TextDecoder().decode(plaintext);
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
  for (const token of parsed) {
    if (typeof token !== "string" || !token) {
      throw new Error("SUBUMBRA_ADAPTER_TOKENS entries must be non-empty strings");
    }
  }
  return parsed;
}

async function tokenSetContains(incomingToken, validTokens) {
  let matched = false;
  for (const validToken of validTokens) {
    if (await timingSafeEqual(incomingToken, validToken)) {
      matched = true;
    }
  }
  return matched;
}

// ─────────────────────────────────────────────────────────────────────────────
// Response helpers
// ─────────────────────────────────────────────────────────────────────────────

function jsonError(message, status) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function getVaultStub(env) {
  const vaultId = env.SUBUMBRA_VAULT.idFromName(VAULT_INSTANCE_NAME);
  return env.SUBUMBRA_VAULT.get(vaultId);
}

function parseBearerToken(request) {
  const auth = request.headers.get("authorization") ?? "";
  const match = auth.match(/^Bearer\s+(.+)$/i);
  return match ? match[1] : "";
}

function bytesToHex(bytes) {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

// ─────────────────────────────────────────────────────────────────────────────
// Durable Object — SubumbraProxy
// ─────────────────────────────────────────────────────────────────────────────

/**
 * SubumbraProxy Durable Object
 *
 * Legacy request-scoped proxy DO retained for compatibility during the vault
 * migration round. Active traffic now routes through SubumbraVault instead.
 *
 * The DO holds the forwarded decrypted key only for the duration of the
 * upstream fetch (~100 ms).
 * No state is persisted to Durable Object storage.
 */
export class SubumbraProxy {
  constructor(state, env) {
    // state.storage is available but we intentionally never use it —
    // the DO is purely ephemeral for this use case.
    this.state = state;
    this.env = env;
  }

  async fetch(request) {
    if (request.method !== "POST") {
      return jsonError("method not allowed", 405);
    }

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
      targetUrl,
      method,
      headers: reqHeaders,
      body,
      authHeader,
      authPrefix,
    } = payload;

    if (
      !ciphertext ||
      !wrappedDek ||
      !pubKeyFp ||
      !keyId ||
      !targetUrl ||
      !authHeader ||
      typeof authPrefix !== "string"
    ) {
      return jsonError("missing required fields", 400);
    }

    let apiKey;
    try {
      const privateKey = await importLegacyPrivateKey(this.env);
      apiKey = await decryptV2(
        privateKey,
        this.env.WORKER_KEY_FINGERPRINT,
        ciphertext,
        wrappedDek,
        pubKeyFp,
        keyId,
      );
    } catch (err) {
      console.error("subumbra: decryption failed:", err.message);
      return jsonError("decryption failed", 500);
    }

    // Build upstream request headers:
    //   1. Start with caller-supplied headers (already stripped of hop-by-hop)
    //   2. Override / inject the auth header from the resolved registry policy
    const upstreamHeaders = new Headers();
    for (const [k, v] of Object.entries(reqHeaders || {})) {
      upstreamHeaders.set(k, v);
    }

    upstreamHeaders.set(authHeader, `${authPrefix}${apiKey}`);

    // If the upstream auth scheme is not Authorization, remove any stale caller-supplied
    // Authorization header so it does not leak alongside x-api-key style auth.
    if (authHeader.toLowerCase() !== "authorization") {
      upstreamHeaders.delete("authorization");
    }

    // Strip API-key query parameters from the target URL — auth is always
    // injected via headers above. Some providers (e.g. Gemini native API)
    // have LiteLLM embed a ?key=<value> param in the URL; that value is a
    // pre-substituted Subumbra token, not a valid API key, so it must be
    // removed before the upstream call or it overrides the injected header.
    const cleanUrl = new URL(targetUrl);
    cleanUrl.searchParams.delete("key");
    cleanUrl.searchParams.delete("api_key");
    cleanUrl.searchParams.delete("apikey");

    // Make the upstream call — stream the response body through
    let upstreamResponse;
    try {
      upstreamResponse = await fetch(cleanUrl.toString(), {
        method: method ?? "POST",
        headers: upstreamHeaders,
        body: body != null ? JSON.stringify(body) : undefined,
      });
    } catch (err) {
      // Network error reaching provider — do not expose err.message
      return jsonError("upstream connection failed", 502);
    }

    // apiKey reference goes out of scope here; V8 GC will collect it.
    // There is no explicit zeroing API for JS strings, but the key is
    // no longer reachable after this point.

    // Forward the upstream response with its original status + headers.
    // Preserve streaming — do not buffer.
    const responseHeaders = new Headers();
    for (const [k, v] of upstreamResponse.headers.entries()) {
      // Strip hop-by-hop headers from upstream response
      if (!HOP_BY_HOP_HEADERS.has(k.toLowerCase())) {
        responseHeaders.set(k, v);
      }
    }

    return new Response(upstreamResponse.body, {
      status: upstreamResponse.status,
      headers: responseHeaders,
    });
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

    if (url.pathname === VAULT_EXECUTE_PATH) {
      return this._handleExecute(request);
    }

    if (url.pathname === VAULT_ROTATE_PATH) {
      return this._handleRotate(request);
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

  async _importPrivateKey(pkcs8Bytes) {
    return crypto.subtle.importKey(
      "pkcs8",
      pkcs8Bytes,
      { name: "RSA-OAEP", hash: "SHA-256" },
      false,
      ["decrypt"],
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

      return new Response(JSON.stringify({
        public_key_pem: this._encodePublicKeyPem(publicKeySpki),
        pub_key_fp: pubKeyFp,
        created_at: createdAt,
      }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    } catch {
      console.error("subumbra: vault setup keygen internal error");
      return jsonError("setup failed", 500);
    }
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
      authHeader,
      authPrefix,
    } = payload;

    if (
      !ciphertext ||
      !wrappedDek ||
      !pubKeyFp ||
      !keyId ||
      !encVersion ||
      !targetUrl ||
      !authHeader ||
      typeof authPrefix !== "string"
    ) {
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
        apiKey = await decryptV2(
          privateKey,
          custodyRow.pub_key_fp,
          ciphertext,
          wrappedDek,
          pubKeyFp,
          keyId,
        );
      } else {
        return jsonError("unsupported enc_version", 400);
      }
    } catch (err) {
      if (encVersion === 3) {
        console.error("subumbra: decryption failed (policy_hash binding mismatch):", err.message);
      } else {
        console.error("subumbra: decryption failed:", err.message);
      }
      return jsonError("decryption failed", 500);
    }

    const upstreamHeaders = new Headers();
    for (const [k, v] of Object.entries(reqHeaders || {})) {
      upstreamHeaders.set(k, v);
    }

    upstreamHeaders.set(authHeader, `${authPrefix}${apiKey}`);

    if (authHeader.toLowerCase() !== "authorization") {
      upstreamHeaders.delete("authorization");
    }

    const cleanUrl = new URL(targetUrl);
    cleanUrl.searchParams.delete("key");
    cleanUrl.searchParams.delete("api_key");
    cleanUrl.searchParams.delete("apikey");

    let upstreamResponse;
    try {
      upstreamResponse = await fetch(cleanUrl.toString(), {
        method: method ?? "POST",
        headers: upstreamHeaders,
        body: body != null ? JSON.stringify(body) : undefined,
      });
    } catch {
      return jsonError("upstream connection failed", 502);
    }

    const responseHeaders = new Headers();
    for (const [k, v] of upstreamResponse.headers.entries()) {
      if (!HOP_BY_HOP_HEADERS.has(k.toLowerCase())) {
        responseHeaders.set(k, v);
      }
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
        apiKey = await decryptV2(
          privateKey,
          custodyRow.pub_key_fp,
          ciphertext,
          wrappedDek,
          pubKeyFp,
          keyId,
        );
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

    return new Response(JSON.stringify({
      ciphertext: ciphertextOut,
      enc_version: 3,
    }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Worker entry point
// ─────────────────────────────────────────────────────────────────────────────

export default {
  /**
   * @param {Request}         request
   * @param {{ SUBUMBRA_ADAPTER_TOKENS: string,
   *           SUBUMBRA_VAULT: DurableObjectNamespace,
   *           PROVIDER_REGISTRY_KV: KVNamespace,
   *           SUBUMBRA_SETUP_TOKEN?: string }} env
   * @param {ExecutionContext} ctx
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ── GET /health ─────────────────────────────────────────────────────────
    if (request.method === "GET" && url.pathname === "/health") {
      return new Response(
        JSON.stringify({ status: "ok", timestamp: new Date().toISOString(), vault_configured: !!env.SUBUMBRA_VAULT }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }

    if (request.method === "GET" && url.pathname === "/auth-ping") {
      return handleAuthPing(request, env);
    }

    if (request.method === "POST" && url.pathname === "/setup/keygen") {
      return handleSetupKeygen(request, env);
    }

    if (request.method === "POST" && url.pathname === "/internal/rotate") {
      return handleInternalRotate(request, env);
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
  try {
    if (!env.SUBUMBRA_VAULT) {
      throw new Error("vault binding missing");
    }
    const vault = getVaultStub(env);
    return await vault.fetch(`https://do-internal${VAULT_SETUP_PATH}`, {
      method: "POST",
      headers: request.headers,
    });
  } catch {
    console.error("subumbra: setup keygen vault unavailable");
    return jsonError("vault unavailable", 503);
  }
}

async function handleInternalRotate(request, env) {
  try {
    if (!env.SUBUMBRA_VAULT) {
      throw new Error("vault binding missing");
    }
    const vault = getVaultStub(env);
    return await vault.fetch(`https://do-internal${VAULT_ROTATE_PATH}`, {
      method: "POST",
      headers: request.headers,
      body: await request.text(),
    });
  } catch {
    console.error("subumbra: internal rotate vault unavailable");
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
  const tokenOk = await tokenSetContains(incomingToken, validTokens);
  if (!tokenOk) {
    console.warn("subumbra: unauthorized request from", request.headers.get("CF-Connecting-IP"));
    return { ok: false, response: jsonError("unauthorized", 401) };
  }

  return { ok: true };
}

async function handleAuthPing(request, env) {
  const auth = await authorizeRequest(request, env);
  if (!auth.ok) {
    return auth.response;
  }

  return new Response(
    JSON.stringify({ status: "ok", timestamp: new Date().toISOString() }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
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

  // ── 2. Parse request body ─────────────────────────────────────────────────
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonError("request body must be JSON", 400);
  }

  const { ciphertext, provider, target_url, method, headers: fwdHeaders, body: reqBody,
    wrapped_dek, pub_key_fp, key_id, enc_version, policy_id, policy_hash } = body;

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

  const version = enc_version ?? 1;
  if (!wrapped_dek || typeof wrapped_dek !== "string") {
    return jsonError("missing or invalid field: wrapped_dek", 400);
  }
  if (version === 3 && (!policy_hash || typeof policy_hash !== "string")) {
    return jsonError("missing or invalid field: policy_hash", 400);
  }
  if (version !== 2 && version !== 3) {
    console.error("subumbra: unsupported enc_version", version);
    return jsonError("key format not supported — re-bootstrap required", 400);
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
  if (version === 3) {
    console.info("subumbra: enc_version=3 key_id=%s", key_id);
  } else {
    console.warn("subumbra: v2 grace path key_id=%s", key_id);
  }

  // ── 4. Strip hop-by-hop and internal headers from forwarded headers ────────
  const cleanHeaders = {};
  for (const [k, v] of Object.entries(fwdHeaders || {})) {
    if (!HOP_BY_HOP_HEADERS.has(k.toLowerCase())) {
      cleanHeaders[k] = v;
    }
  }

  // ── 5. Forward encrypted envelope to Durable Object ───────────────────────
  const doStub = getVaultStub(env);

  const doPayload = JSON.stringify({
    ciphertext,
    wrappedDek: wrapped_dek,
    pubKeyFp: pub_key_fp,
    keyId: key_id,
    encVersion: version,
    policyId: policy_id ?? registryEntry.policy_id,
    policyHash: policy_hash ?? registryEntry.policy_hash,
    targetUrl: target_url,
    method: method ?? "POST",
    headers: cleanHeaders,
    body: reqBody ?? null,
    authHeader: registryEntry.auth_header,
    authPrefix: registryEntry.auth_prefix,
  });

  const doResponse = await doStub.fetch(`https://do-internal${VAULT_EXECUTE_PATH}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: doPayload,
  });

  // ── 7. Stream DO response back to caller ──────────────────────────────────
  const responseHeaders = new Headers(doResponse.headers);
  responseHeaders.set("X-Subumbra-Provider", registryEntry.provider_id);  // audit trail

  return new Response(doResponse.body, {
    status: doResponse.status,
    headers: responseHeaders,
  });
}
