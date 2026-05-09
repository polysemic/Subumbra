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
    intent: policy.intent && typeof policy.intent === "object" ? policy.intent : null,
    deny_patterns: Array.isArray(policy.response?.deny_patterns) ? policy.response.deny_patterns : [],
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

function jsonError(message, status) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "content-type": "application/json" },
  });
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

function bytesToHex(bytes) {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
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
      authScheme,
      authHeaderName,
      authQueryParam,
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
    cleanUrl.searchParams.delete("key");
    cleanUrl.searchParams.delete("api_key");
    cleanUrl.searchParams.delete("apikey");

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
        JSON.stringify({ status: "ok" }),
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

async function handleInternalRotate(request, env) {
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
    policyHash: policy_hash ?? registryEntry.policy_hash,
    targetUrl: target_url,
    method: method ?? "POST",
    headers: cleanHeaders,
    body: reqBody ?? null,
    authScheme: registryEntry.auth_scheme,
    authHeaderName: registryEntry.auth_header_name ?? null,
    authQueryParam: registryEntry.auth_query_param ?? null,
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
  const contentType = doResponse.headers.get("content-type") ?? "";
  const shouldScan =
    denyPatterns.length > 0 &&
    (contentType.startsWith("application/json") || contentType.startsWith("text/plain"));

  if (shouldScan) {
    let responseBody;
    try {
      responseBody = await doResponse.text();
    } catch (e) {
      console.error("subumbra: response_read_error key_id=%s", key_id);
      return jsonError("response_read_error", 403);
    }
    for (let i = 0; i < denyPatterns.length; i++) {
      if (new RegExp(denyPatterns[i]).test(responseBody)) {
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
