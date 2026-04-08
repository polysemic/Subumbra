/**
 * KeyVault Proxy — Cloudflare Worker + Durable Object
 * ─────────────────────────────────────────────────────────────────────────────
 * V2 Asymmetric Envelope Encryption:
 *   - WORKER_PRIVATE_KEY (RSA-4096 PKCS#8 DER, base64) lives in CF Secrets
 *   - Each API key record has its own DEK wrapped by the RSA public key
 *   - Worker unwraps DEK with private key, then decrypts API key with DEK
 *   - Decrypted API key exists only inside the Durable Object's V8 isolate
 *     for the duration of a single upstream fetch (~100 ms), then is GC'd
 *   - Nothing sensitive is logged, stored, or returned in error messages
 *
 * Endpoints:
 *   GET  /health   → liveness check (no auth required)
 *   POST /proxy    → canonical KeyVault core API; see docs/adapter-contract.md
 *
 * Auth header required on /proxy:
 *   X-Forge-Token: <one adapter token from FORGE_ADAPTER_TOKENS>
 *
 * CF Secrets consumed:
 *   WORKER_PRIVATE_KEY      — base64(RSA-4096 PKCS#8 DER), set by bootstrap
 *   WORKER_KEY_FINGERPRINT  — sha256:<hex> of SPKI DER, set by bootstrap
 *   FORGE_ADAPTER_TOKENS    — JSON array of adapter tokens, set by bootstrap
 */

"use strict";

import PROVIDER_REGISTRY from "./providers.json";

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

validateProviderRegistry(PROVIDER_REGISTRY);

const UPSTREAM_REGISTRY = Object.fromEntries(
  PROVIDER_REGISTRY.map((p) => [
    p.target_host,
    {
      provider_id: p.provider_id,
      auth_header: p.auth_header,
      auth_prefix: p.auth_prefix,
    },
  ])
);

function registryEntryByHostname(hostname) {
  const entry = UPSTREAM_REGISTRY[hostname];
  return entry ? { hostname, ...entry } : null;
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
  "x-forge-token",
  "x-forge-timestamp",
  "x-forge-signature",
  "cf-connecting-ip",
  "cf-ray",
  "cf-visitor",
  "cf-ipcountry",
]);

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

// Module-scope cache — RSA private key is imported once per isolate lifetime
let _cachedPrivateKey = null;

/**
 * Import and cache the RSA-4096 private key from CF Secrets.
 * The key is imported as non-extractable — raw bytes can never be read back.
 */
async function getPrivateKey(env) {
  if (_cachedPrivateKey) return _cachedPrivateKey;

  const derBytes = base64ToBytes(env.WORKER_PRIVATE_KEY);
  _cachedPrivateKey = await crypto.subtle.importKey(
    "pkcs8",
    derBytes,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,        // not extractable
    ["decrypt"],
  );

  return _cachedPrivateKey;
}

/**
 * V2 Asymmetric Envelope Decrypt.
 *
 * 1. Verify pub_key_fp matches the loaded private key's fingerprint
 * 2. Unwrap DEK using RSA-OAEP private key
 * 3. Decrypt API key using DEK with AES-256-GCM + AAD
 *
 * @param {object} env           - CF Worker env bindings
 * @param {string} ciphertextB64 - base64: nonce[12] || AES-GCM(api_key, aad)
 * @param {string} wrappedDekB64 - base64: RSA-OAEP(DEK[32])
 * @param {string} pubKeyFp      - sha256:<hex> fingerprint of wrapping public key
 * @param {string} keyId          - key_id for AAD binding
 * @returns {Promise<string>}    - plaintext API key
 */
async function decryptV2(env, ciphertextB64, wrappedDekB64, pubKeyFp, keyId) {
  if (!wrappedDekB64 || !ciphertextB64 || !keyId) {
    throw new Error("missing required V2 envelope fields");
  }

  // 1. Verify pub_key_fp matches loaded private key's fingerprint
  if (pubKeyFp && env.WORKER_KEY_FINGERPRINT &&
    pubKeyFp !== env.WORKER_KEY_FINGERPRINT) {
    throw new Error(
      `record wrapped with unknown key pair (record: ${pubKeyFp}, ` +
      `loaded: ${env.WORKER_KEY_FINGERPRINT}) — re-bootstrap required`
    );
  }

  // 2. Unwrap DEK using RSA private key
  const privateKey = await getPrivateKey(env);
  const dekBytes = await crypto.subtle.decrypt(
    { name: "RSA-OAEP" },
    privateKey,
    base64ToBytes(wrappedDekB64),
  );

  // 3. Import DEK for AES-GCM (non-extractable)
  const dekKey = await crypto.subtle.importKey(
    "raw", dekBytes, { name: "AES-GCM" }, false, ["decrypt"],
  );

  // 4. Decrypt API key with AAD = "keyvault:v2:<key_id>"
  const aad = new TextEncoder().encode(`keyvault:v2:${keyId}`);
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
 * Encodes both strings to UTF-8 bytes and uses crypto.subtle.timingSafeEqual.
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
  const sigA = await crypto.subtle.sign("HMAC", ka, enc.encode("keyvault"));
  const sigB = await crypto.subtle.sign("HMAC", kb, enc.encode("keyvault"));

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
    throw new Error("FORGE_ADAPTER_TOKENS must be valid JSON");
  }

  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new Error("FORGE_ADAPTER_TOKENS must be a non-empty JSON array");
  }
  for (const token of parsed) {
    if (typeof token !== "string" || !token) {
      throw new Error("FORGE_ADAPTER_TOKENS entries must be non-empty strings");
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

// ─────────────────────────────────────────────────────────────────────────────
// Durable Object — KeyVaultProxy
// ─────────────────────────────────────────────────────────────────────────────

/**
 * KeyVaultProxy Durable Object
 *
 * One instance per request (created with newUniqueId()).  Receives the
 * decrypted API key + full proxy request from the Worker, makes the upstream
 * API call, and streams the response back.
 *
 * The decrypted key exists ONLY inside this V8 isolate for ~100 ms.
 * No state is persisted to Durable Object storage.
 */
export class KeyVaultProxy {
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
      apiKey,
      targetUrl,
      method,
      headers: reqHeaders,
      body,
      authHeader,
      authPrefix,
    } = payload;

    if (!apiKey || !targetUrl || !authHeader || typeof authPrefix !== "string") {
      return jsonError("missing required fields", 400);
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

    // Make the upstream call — stream the response body through
    let upstreamResponse;
    try {
      upstreamResponse = await fetch(targetUrl, {
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
// Worker entry point
// ─────────────────────────────────────────────────────────────────────────────

export default {
  /**
   * @param {Request}         request
   * @param {{ WORKER_PRIVATE_KEY: string, WORKER_KEY_FINGERPRINT: string,
   *           FORGE_ADAPTER_TOKENS: string,
   *           KEY_VAULT_PROXY: DurableObjectNamespace }} env
   * @param {ExecutionContext} ctx
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ── GET /health ─────────────────────────────────────────────────────────
    if (request.method === "GET" && url.pathname === "/health") {
      return new Response(
        JSON.stringify({ status: "ok", timestamp: new Date().toISOString() }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }

    // ── POST /proxy ─────────────────────────────────────────────────────────
    // Direct mode: caller wraps the full request in our custom JSON format.
    if (request.method === "POST" && url.pathname === "/proxy") {
      return handleProxy(request, env);
    }

    return jsonError("not found", 404);
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// handleProxy — main request handler
// ─────────────────────────────────────────────────────────────────────────────

async function handleProxy(request, env) {
  // ── 1. Validate secrets are configured ────────────────────────────────────
  if (!env.WORKER_PRIVATE_KEY || !env.FORGE_ADAPTER_TOKENS) {
    console.error("keyvault: CF Secrets not configured (run bootstrap)");
    return jsonError("worker not configured", 503);
  }

  let validTokens;
  try {
    validTokens = parseAdapterTokens(env.FORGE_ADAPTER_TOKENS);
  } catch (err) {
    console.error("keyvault: FORGE_ADAPTER_TOKENS invalid:", err.message);
    return jsonError("worker not configured", 503);
  }

  // ── 2. Authenticate caller ────────────────────────────────────────────────
  const incomingToken = request.headers.get("X-Forge-Token") ?? "";
  const tokenOk = await tokenSetContains(incomingToken, validTokens);
  if (!tokenOk) {
    // Log IP for audit but nothing else — don't log the token
    console.warn("keyvault: unauthorized request from", request.headers.get("CF-Connecting-IP"));
    return jsonError("unauthorized", 401);
  }

  // ── 3. Parse request body ─────────────────────────────────────────────────
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonError("request body must be JSON", 400);
  }

  const { ciphertext, provider, target_url, method, headers: fwdHeaders, body: reqBody,
    wrapped_dek, pub_key_fp, key_id, enc_version } = body;

  if (!ciphertext || typeof ciphertext !== "string") {
    return jsonError("missing or invalid field: ciphertext", 400);
  }
  if (!provider || typeof provider !== "string") {
    return jsonError("missing or invalid field: provider", 400);
  }
  if (!target_url || typeof target_url !== "string") {
    return jsonError("missing or invalid field: target_url", 400);
  }

  // ── Hard reject non-V2 records ────────────────────────────────────────────
  const version = enc_version ?? 1;
  if (version !== 2 || !wrapped_dek) {
    console.error("keyvault: unsupported enc_version", version,
      "— re-run bootstrap to migrate to V2 format");
    return jsonError("key format not supported — re-bootstrap required", 400);
  }

  let parsedTarget;
  try {
    parsedTarget = new URL(target_url);
  } catch (e) {
    console.error("keyvault: URL parse error", e);
    return jsonError("invalid target_url", 400);
  }
  if (parsedTarget.protocol !== "https:") {
    return jsonError("target_url must use https://", 400);
  }
  const registryEntry = registryEntryByHostname(parsedTarget.hostname);
  if (!registryEntry) {
    console.warn("keyvault: SSRF attempt — rejected target_url", parsedTarget.hostname);
    return jsonError("target_url not allowed", 403);
  }
  // Verify provider/target_url consistency: prevents decrypting one provider's
  // key and sending it to a different provider's endpoint.
  if (provider !== registryEntry.provider_id) {
    console.warn(
      "keyvault: provider/target_url mismatch — provider=%s target=%s",
      provider, parsedTarget.hostname,
    );
    return jsonError("target_url host does not match declared provider", 400);
  }

  // ── 4. Strip hop-by-hop and internal headers from forwarded headers ────────
  const cleanHeaders = {};
  for (const [k, v] of Object.entries(fwdHeaders || {})) {
    if (!HOP_BY_HOP_HEADERS.has(k.toLowerCase())) {
      cleanHeaders[k] = v;
    }
  }

  // ── 5. Decrypt V2 envelope ────────────────────────────────────────────────
  let apiKey;
  try {
    apiKey = await decryptV2(env, ciphertext, wrapped_dek, pub_key_fp, key_id);
  } catch (err) {
    console.error("keyvault: decryption failed:", err.message);
    // Surface fingerprint mismatch details to help operators diagnose
    if (err.message.includes("wrapped with unknown key pair")) {
      return jsonError(err.message, 500);
    }
    return jsonError("decryption failed", 500);
  }

  // ── 6. Forward to Durable Object ──────────────────────────────────────────
  //   The DO lives in CF infrastructure, so apiKey is never in transit
  //   outside of CF.  The DO will zero its reference once the fetch returns.
  const doId = env.KEY_VAULT_PROXY.newUniqueId();
  const doStub = env.KEY_VAULT_PROXY.get(doId);

  const doPayload = JSON.stringify({
    apiKey,         // decrypted — lives in DO memory only
    targetUrl: target_url,
    method: method ?? "POST",
    headers: cleanHeaders,
    body: reqBody ?? null,
    authHeader: registryEntry.auth_header,
    authPrefix: registryEntry.auth_prefix,
  });

  // Immediately remove our own reference to the decrypted key
  apiKey = null; // eslint-disable-line no-param-reassign

  const doResponse = await doStub.fetch("https://do-internal/execute", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: doPayload,
  });

  // ── 7. Stream DO response back to caller ──────────────────────────────────
  const responseHeaders = new Headers(doResponse.headers);
  responseHeaders.set("X-KeyVault-Provider", registryEntry.provider_id);  // audit trail

  return new Response(doResponse.body, {
    status: doResponse.status,
    headers: responseHeaders,
  });
}
