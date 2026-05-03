/* ═══════════════════════════════════════════════════════════════
   dashboard.js — Subumbra Dashboard runtime
   Vanilla JS, no dependencies. Requires SubtleCrypto (modern browsers).
═══════════════════════════════════════════════════════════════ */

"use strict";

/* ── Constants ───────────────────────────────────────────────── */

const SESSION_WARN_SECS = 60;
const PROVIDER_CLASS = {
  anthropic: "provider-anthropic",
  openai: "provider-openai",
  groq: "provider-groq",
  deepseek: "provider-deepseek",
};

/* ── Dashboard state ─────────────────────────────────────────── */

let _status = null;
let _es = null;   // EventSource instance

/* ── DOM helper ──────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);

/* ── Sanitise / format helpers ───────────────────────────────── */

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtTimestamp(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch { return iso; }
}

function fmtRelative(iso) {
  if (!iso) return null;
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function verdictClass(v) {
  if (v === "allow") return "status-ok";
  if (v === "deny") return "status-deny";
  return "status-unknown";
}

function providerClass(p) {
  return PROVIDER_CLASS[p] ?? "provider-unknown";
}

function setAlert(el, visible) {
  el.classList.toggle("visible", visible);
}

/* ═══════════════════════════════════════════════════════════════
   DASHBOARD — render functions
═══════════════════════════════════════════════════════════════ */

function renderHealth(data) {
  const sDot = $("subumbra-health-dot");
  const sText = $("subumbra-health-text");
  const wDot = $("worker-health-dot");
  const wText = $("worker-health-text");

  sDot.className = "health-dot " + (data.subumbra_keys_healthy ? "ok" : "err");
  sText.textContent = data.subumbra_keys_healthy
    ? "subumbra-keys healthy"
    : (data.subumbra_keys_error ?? "subumbra-keys error");
  sText.classList.toggle("err", !data.subumbra_keys_healthy);

  wDot.className = "health-dot " + (data.worker_reachable ? "ok" : "err");
  wText.textContent = data.worker_reachable
    ? "worker reachable"
    : (data.worker_error ?? "worker unreachable");
  wText.classList.toggle("err", !data.worker_reachable);
}

function renderErrorBanner(data) {
  const show = !data.subumbra_keys_healthy && !!data.subumbra_keys_error;
  if (show) $("error-text").textContent = data.subumbra_keys_error;
  setAlert($("error-banner"), show);
}

function renderSummary(data) {
  const totalReqs = data.keys.reduce((s, k) => s + (k.request_count || 0), 0);
  const activeKeys = data.keys.filter(k => k.request_count > 0).length;
  const lastReq = data.keys.map(k => k.last_access).filter(Boolean).sort().at(-1);

  $("stat-keys").textContent = data.keys_loaded;
  $("stat-total-reqs").textContent = totalReqs;
  $("stat-active-keys").textContent = activeKeys;

  const node = $("stat-last-req");
  if (lastReq) {
    node.innerHTML =
      `<div>${esc(fmtRelative(lastReq))}</div>` +
      `<div class="stat-sub">${esc(fmtTimestamp(lastReq))}</div>`;
    node.classList.remove("small");
  } else {
    node.textContent = "No requests yet";
    node.classList.add("small");
  }
}

function renderKeys(keys) {
  const grid = $("keys-grid");
  if (!keys.length) {
    grid.innerHTML =
      `<div class="empty-state">No keys loaded. Run bootstrap first:` +
      `<div class="empty-code">docker compose --profile bootstrap run --rm -it bootstrap</div></div>`;
    return;
  }

  grid.innerHTML = keys.map(k => {
    const pClass = providerClass(k.provider);
    const relTime = k.last_access ? fmtRelative(k.last_access) : null;
    const created = k.created_at ? fmtTimestamp(k.created_at) : "—";

    return `
<article class="key-card">
  <span class="provider-badge ${esc(pClass)}">${esc(k.provider)}</span>
  <div class="key-card-body">
    <div class="key-card-info">
      <div class="key-id">${esc(k.key_id)}</div>
      <div class="key-meta">Created: ${esc(created)}</div>
      <div class="key-meta${relTime ? "" : " never"}">${relTime ? `Last used: ${esc(relTime)}` : "Never used"}</div>
    </div>
    <div class="key-stats">
      <div class="key-req-count">${(k.request_count || 0).toLocaleString()}</div>
      <div class="key-req-label">requests</div>
    </div>
  </div>
  <div class="key-card-footer">
    <button class="btn btn-subtle" type="button"
      onclick="openRotateModal('${esc(k.key_id)}', '${esc(k.provider)}')">↺ Rotate</button>
  </div>
</article>`;
  }).join("");
}

function renderLog(log, auditAvailable) {
  const tbody = $("log-body");
  $("log-count").textContent = log.length ? `(${log.length} entries)` : "";

  if (!auditAvailable) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-state">Audit trail unavailable. See warning above.</td></tr>`;
    return;
  }
  if (!log.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-state">No requests recorded yet.</td></tr>`;
    return;
  }

  const provMap = {};
  if (_status) for (const k of _status.keys) provMap[k.key_id] = k.provider;

  tbody.innerHTML = log.map(entry => {
    const prov = provMap[entry.key_id] ?? "—";
    const pClass = providerClass(prov);
    const vClass = verdictClass(entry.verdict);
    return `
<tr>
  <td class="td-ts">${esc(fmtTimestamp(entry.timestamp))}</td>
  <td class="td-mono">${esc(entry.adapter_id ?? "—")}</td>
  <td>${esc(entry.endpoint ?? "—")}</td>
  <td class="td-mono">${esc(entry.key_id ?? "—")}</td>
  <td><span class="provider-badge ${esc(pClass)}">${esc(prov)}</span></td>
  <td class="td-muted">${esc(entry.remote ?? "—")}</td>
  <td class="${vClass}">${esc(entry.verdict ?? "—")}</td>
  <td>${esc(entry.reason_code ?? "—")}</td>
</tr>`;
  }).join("");
}

function renderWarnings(data) {
  setAlert($("stats-warning"), data.stats_available === false);
  const auditUnavail = data.audit_available === false;
  if (auditUnavail) {
    $("audit-warning-text").textContent =
      data.audit_error ?? "Recent structured audit entries are temporarily unavailable.";
  }
  setAlert($("audit-warning"), auditUnavail);
}

/* ── Fetch & live updates via SSE ────────────────────────────── */

function applyStatus(data) {
  _status = data;
  renderHealth(data);
  renderErrorBanner(data);
  renderSummary(data);
  renderKeys(data.keys);
  renderLog(data.recent_log, data.audit_available !== false);
  renderWarnings(data);
}

/* One-shot fetch — used on init and manual refresh button */
async function loadStatus() {
  try {
    const resp = await fetch("/api/status");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    applyStatus(await resp.json());
  } catch (err) {
    setLiveIndicator("error", "fetch error: " + err.message);
  }
}

/* SSE connection — server pushes { event: "status", data: <json> }
   whenever state changes (key added/rotated, health flip, new log entry).
   EventSource auto-reconnects on drop.
   Backend endpoint: GET /api/events (text/event-stream) */
function initEventSource() {
  if (_es) { _es.close(); _es = null; }

  _es = new EventSource("/api/events");

  _es.addEventListener("status", (e) => {
    try {
      applyStatus(JSON.parse(e.data));
      setLiveIndicator("live");
    } catch { /* malformed push — ignore, wait for next */ }
  });

  _es.addEventListener("open", () => setLiveIndicator("live"));

  _es.addEventListener("error", () => {
    /* EventSource will auto-retry; just update the indicator */
    setLiveIndicator("reconnecting");
  });
}

/* Update the live connection badge in the topbar */
function setLiveIndicator(state, msg) {
  const dot = $("live-dot");
  const text = $("live-text");
  if (!dot || !text) return;
  const map = {
    live: { cls: "ok", label: "live" },
    reconnecting: { cls: "err", label: "reconnecting…" },
    error: { cls: "err", label: msg ?? "error" },
  };
  const s = map[state] ?? map.error;
  dot.className = `health-dot ${s.cls}`;
  text.textContent = s.label;
}

/* ═══════════════════════════════════════════════════════════════
   SHARED CRYPTO HELPERS
   Used by both Add Key and Rotate Key modals.
═══════════════════════════════════════════════════════════════ */

/**
 * Import a JWK public key as a non-extractable CryptoKey for RSA-OAEP.
 * extractable: false — the key can be used but never read back out of JS.
 */
async function importPublicKey(jwk) {
  return crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["encrypt"]
  );
}

/**
 * Encrypt a plaintext string with a CryptoKey.
 * Zeros the intermediate Uint8Array in a finally block before GC sees it.
 */
async function encryptPlaintext(plaintext, cryptoKey) {
  const encoded = new TextEncoder().encode(plaintext);
  try {
    return await crypto.subtle.encrypt({ name: "RSA-OAEP" }, cryptoKey, encoded);
  } finally {
    encoded.fill(0);
  }
}

/** Encode an ArrayBuffer to base64 for JSON transport. */
function bufferToBase64(buf) {
  const bytes = new Uint8Array(buf);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

/**
 * Request an ephemeral session keypair from the backend.
 * Expected response: { sessionId: string, publicKeyJwk: JsonWebKey, expiresAt: string }
 */
async function fetchSession() {
  const resp = await fetch("/api/key-session", { method: "GET" });
  if (!resp.ok) throw new Error(`Session request failed: HTTP ${resp.status}`);
  return resp.json();
}

/**
 * Delete a session on the server — fire-and-forget.
 * Uses keepalive so it survives tab close / page unload.
 */
function deleteSession(sessionId) {
  if (!sessionId) return;
  try {
    fetch(`/api/key-session/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
      keepalive: true,
    });
  } catch {
    navigator.sendBeacon(`/api/key-session/${encodeURIComponent(sessionId)}`);
  }
}

/* ── Shared: session TTL countdown factory ───────────────────── */

/**
 * Returns a { start, stop } pair that drives a TTL countdown
 * for whichever modal IDs are passed in.
 */
function makeSessionTtl(sessionRef, ttlElId, dotElId, onExpire) {
  let timer = null;

  function start() {
    stop();
    timer = setInterval(() => {
      if (!sessionRef.current) return stop();
      const secsLeft = Math.floor((sessionRef.current.expiresAt - Date.now()) / 1000);
      const ttlEl = $(ttlElId);
      const dotEl = $(dotElId);
      if (secsLeft <= 0) {
        stop();
        if (ttlEl) { ttlEl.textContent = "expired"; ttlEl.classList.add("warn"); }
        if (dotEl) dotEl.className = "session-dot expired";
        onExpire();
        return;
      }
      if (ttlEl) { ttlEl.textContent = `${secsLeft}s`; ttlEl.classList.toggle("warn", secsLeft <= SESSION_WARN_SECS); }
      if (dotEl) dotEl.className = "session-dot active";
    }, 1000);
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  return { start, stop };
}

/* ── Shared: paste + keyboard interception factory ───────────── */

/**
 * Attach security detectors to a secure input.
 * Returns a detach function. Call before attachSecureInput().
 */
function attachSecurityDetectors(input, warnEl, proceedBtn) {
  const detectors = [];

  function showWarning(msg) {
    if (!warnEl) return;
    warnEl.querySelector('.secure-warning-msg').textContent = msg;
    warnEl.classList.add('visible');
    if (proceedBtn) proceedBtn.disabled = true;
  }

  // 1. DOM mutation — extension injecting attributes
  const mo = new MutationObserver(() => {
    showWarning('A browser extension modified this field. Proceed only if you trust all installed extensions.');
  });
  mo.observe(input, { attributes: true, childList: true, characterData: true });
  detectors.push(() => mo.disconnect());

  // 2. Value-read trap — anything reading .value
  const nativeDesc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  Object.defineProperty(input, 'value', {
    get() {
      const v = nativeDesc.get.call(this);
      if (v !== '') showWarning('Something read the key field value — a script or extension may have captured it.');
      return v;
    },
    set(v) { nativeDesc.set.call(this, v); },
    configurable: true,
  });
  detectors.push(() => {
    try { delete input.value; } catch (_) { }
  });

  // 3. isTrusted check — handled inside paste handler (see attachSecureInput)

  return () => detectors.forEach(fn => fn());
}

/**
 * Returns a detach() function for cleanup.
 */
function attachSecureInput(input, sessionRef, pendingRef, onCaptured, onError) {
  const keydownHandler = (e) => {
    const isPaste = (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v";
    const isNav = ["Tab", "Escape", "Enter"].includes(e.key);
    if (!isPaste && !isNav) e.preventDefault();
  };

  const pasteHandler = async (e) => {
    e.preventDefault();
    if (!e.isTrusted) {
      onError("Synthetic paste detected — event was not triggered by a real user action. Paste intercepted for security.");
      return;
    }
    if (!plain.trim()) return;

    if (!sessionRef.current || Date.now() >= sessionRef.current.expiresAt) {
      onError("Session expired — please close and reopen the modal.");
      return;
    }

    onCaptured("loading");
    try {
      pendingRef.current = await encryptPlaintext(plain, sessionRef.current.publicKey);
      onCaptured("captured");
    } catch (err) {
      pendingRef.current = null;
      onError("Encryption failed: " + err.message);
    }
  };

  input.addEventListener("keydown", keydownHandler);
  input.addEventListener("paste", pasteHandler);

  return function detach() {
    input.removeEventListener("keydown", keydownHandler);
    input.removeEventListener("paste", pasteHandler);
  };
}

/* ═══════════════════════════════════════════════════════════════
   ADD KEY MODAL
═══════════════════════════════════════════════════════════════ */

const _akm = {
  session: { current: null },   // { sessionId, publicKey, expiresAt }
  pending: { current: null },   // ArrayBuffer ciphertext
  detach: null,                // cleanup fn from attachSecureInput
  ttl: null,                // { start, stop }
};

_akm.ttl = makeSessionTtl(
  _akm.session,
  "akm-session-ttl",
  "akm-session-dot",
  () => {
    setAkmError("Session expired — please close and reopen to try again.");
    akmDisableSubmit();
  }
);

function akmSetState(id) {
  ["akm-loading", "akm-form", "akm-success"].forEach(s => {
    const el = $(s);
    if (el) el.classList.toggle("active", s === id);
  });
}

function akmSetSecureState(state) {
  const wrap = $("akm-secure-wrap");
  const input = $("akm-key-input");
  if (!wrap || !input) return;
  wrap.dataset.state = state;
  input.value = "";
  input.placeholder = state === "captured" ? "● ● ● ● ● ● ●  encrypted" : "Paste your API key here…";
}

function setAkmError(msg) {
  const el = $("akm-error");
  if (!el) return;
  el.textContent = msg;
  setAlert(el, !!msg);
}

function akmEnableSubmit() { const b = $("akm-submit"); if (b) b.disabled = false; }
function akmDisableSubmit() { const b = $("akm-submit"); if (b) b.disabled = true; }

function akmReset() {
  if (_akm.detach) { _akm.detach(); _akm.detach = null; }
  _akm.pending.current = null;
  akmDisableSubmit();
  setAkmError("");
  akmSetSecureState("idle");
  const ttl = $("akm-session-ttl");
  if (ttl) { ttl.textContent = ""; ttl.classList.remove("warn"); }
  const dot = $("akm-session-dot");
  if (dot) dot.className = "session-dot loading";
  $("akm-key-name") && ($("akm-key-name").value = "");
  $("akm-provider") && ($("akm-provider").value = "anthropic");
}

async function openAddKeyModal() {
  if (_akm.session.current) {
    deleteSession(_akm.session.current.sessionId);
    _akm.session.current = null;
  }
  akmReset();
  akmSetState("akm-loading");
  $("add-key-modal").classList.add("open");

  try {
    const data = await fetchSession();
    const publicKey = await importPublicKey(data.publicKeyJwk);
    _akm.session.current = {
      sessionId: data.sessionId,
      publicKey,
      expiresAt: new Date(data.expiresAt).getTime(),
    };

    akmSetState("akm-form");
    _akm.ttl.start();

    const input = $("akm-key-input");
    if (input) {
      const _dDet = attachSecurityDetectors(input, $("akm-sec-warning"), $("akm-submit"));
      const _dPaste = attachSecureInput(
        input,
        _akm.session,
        _akm.pending,
        (state) => {
          akmSetSecureState(state);
          if (state === "captured") akmEnableSubmit();
          else akmDisableSubmit();
        },
        (msg) => { setAkmError(msg); akmSetSecureState("error"); akmDisableSubmit(); }
      );
      _akm.detach = () => { _dDet(); _dPaste(); };
    }

    const dot = $("akm-session-dot");
    const info = $("akm-session-info");
    if (dot) dot.className = "session-dot active";
    if (info) info.textContent = "Session active · keypair generated server-side";

  } catch (err) {
    akmSetState("akm-form");
    setAkmError("Could not establish secure session: " + err.message);
    const dot = $("akm-session-dot");
    if (dot) dot.className = "session-dot expired";
  }
}

function closeAddKeyModal() {
  if (_akm.session.current) {
    deleteSession(_akm.session.current.sessionId);
    _akm.session.current = null;
  }
  _akm.ttl.stop();
  if (_akm.detach) { _akm.detach(); _akm.detach = null; }
  _akm.pending.current = null;
  $("add-key-modal").classList.remove("open");
  setTimeout(akmReset, 250);
}

async function submitAddKey() {
  if (!_akm.pending.current) { setAkmError("No key captured yet — paste your API key first."); return; }
  if (!_akm.session.current) { setAkmError("Session expired — please close and reopen."); return; }

  const provider = $("akm-provider")?.value?.trim();
  const keyId = $("akm-key-name")?.value?.trim();
  if (!keyId) { setAkmError("Please enter a Key ID / label before submitting."); return; }

  setAkmError("");
  akmDisableSubmit();
  _akm.ttl.stop();

  try {
    const body = JSON.stringify({
      sessionId: _akm.session.current.sessionId,
      provider,
      keyId,
      ciphertext: bufferToBase64(_akm.pending.current),
    });
    _akm.pending.current = null;

    const resp = await fetch("/api/add-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    if (!resp.ok) throw new Error(await resp.text().catch(() => `HTTP ${resp.status}`));

    _akm.session.current = null;
    akmSetState("akm-success");
    loadStatus();
  } catch (err) {
    setAkmError("Submission failed: " + err.message);
    akmEnableSubmit();
    _akm.ttl.start();
  }
}

/* ═══════════════════════════════════════════════════════════════
   ROTATE KEY MODAL
   Same ephemeral session keypair + paste interception flow.
   Key ID is known at open time (passed from the key card button).
   POSTs to /api/rotate-key instead of /api/add-key.
═══════════════════════════════════════════════════════════════ */

const _rkm = {
  session: { current: null },
  pending: { current: null },
  detach: null,
  ttl: null,
  keyId: null,
  provider: null,
};

_rkm.ttl = makeSessionTtl(
  _rkm.session,
  "rkm-session-ttl",
  "rkm-session-dot",
  () => {
    setRkmError("Session expired — please close and reopen to try again.");
    rkmDisableSubmit();
  }
);

function rkmSetState(id) {
  ["rkm-loading", "rkm-form", "rkm-success"].forEach(s => {
    const el = $(s);
    if (el) el.classList.toggle("active", s === id);
  });
}

function rkmSetSecureState(state) {
  const wrap = $("rkm-secure-wrap");
  const input = $("rkm-key-input");
  if (!wrap || !input) return;
  wrap.dataset.state = state;
  input.value = "";
  input.placeholder = state === "captured" ? "● ● ● ● ● ● ●  encrypted" : "Paste your new API key here…";
}

function setRkmError(msg) {
  const el = $("rkm-error");
  if (!el) return;
  el.textContent = msg;
  setAlert(el, !!msg);
}

function rkmEnableSubmit() { const b = $("rkm-submit"); if (b) b.disabled = false; }
function rkmDisableSubmit() { const b = $("rkm-submit"); if (b) b.disabled = true; }

function rkmReset() {
  if (_rkm.detach) { _rkm.detach(); _rkm.detach = null; }
  _rkm.pending.current = null;
  rkmDisableSubmit();
  setRkmError("");
  rkmSetSecureState("idle");
  const ttl = $("rkm-session-ttl");
  if (ttl) { ttl.textContent = ""; ttl.classList.remove("warn"); }
  const dot = $("rkm-session-dot");
  if (dot) dot.className = "session-dot loading";
}

async function openRotateModal(keyId, provider) {
  // Destroy any previous orphaned session
  if (_rkm.session.current) {
    deleteSession(_rkm.session.current.sessionId);
    _rkm.session.current = null;
  }

  _rkm.keyId = keyId;
  _rkm.provider = provider;

  // Populate static fields
  const keyIdEl = $("rkm-key-id");
  const provBadge = $("rkm-provider-badge");
  const cmdEl = $("rkm-cmd");

  if (keyIdEl) keyIdEl.textContent = keyId;
  if (provBadge) {
    provBadge.textContent = provider;
    provBadge.className = `provider-badge ${providerClass(provider)}`;
  }
  if (cmdEl) cmdEl.textContent = `docker compose --profile bootstrap run --rm -it bootstrap --rotate`;

  rkmReset();
  rkmSetState("rkm-loading");
  $("rotate-modal").classList.add("open");

  try {
    const data = await fetchSession();
    const publicKey = await importPublicKey(data.publicKeyJwk);
    _rkm.session.current = {
      sessionId: data.sessionId,
      publicKey,
      expiresAt: new Date(data.expiresAt).getTime(),
    };

    rkmSetState("rkm-form");
    _rkm.ttl.start();

    const input = $("rkm-key-input");
    if (input) {
      const _rDet = attachSecurityDetectors(input, $("rkm-sec-warning"), $("rkm-submit"));
      const _rPaste = attachSecureInput(
        input,
        _rkm.session,
        _rkm.pending,
        (state) => {
          rkmSetSecureState(state);
          if (state === "captured") rkmEnableSubmit();
          else rkmDisableSubmit();
        },
        (msg) => { setRkmError(msg); rkmSetSecureState("error"); rkmDisableSubmit(); }
      );
      _rkm.detach = () => { _rDet(); _rPaste(); };
    }

    const dot = $("rkm-session-dot");
    const info = $("rkm-session-info");
    if (dot) dot.className = "session-dot active";
    if (info) info.textContent = "Session active · keypair generated server-side";

  } catch (err) {
    rkmSetState("rkm-form");
    setRkmError("Could not establish secure session: " + err.message);
    const dot = $("rkm-session-dot");
    if (dot) dot.className = "session-dot expired";
  }
}

function closeRotateModal() {
  if (_rkm.session.current) {
    deleteSession(_rkm.session.current.sessionId);
    _rkm.session.current = null;
  }
  _rkm.ttl.stop();
  if (_rkm.detach) { _rkm.detach(); _rkm.detach = null; }
  _rkm.pending.current = null;
  $("rotate-modal").classList.remove("open");
  setTimeout(rkmReset, 250);
}

async function submitRotateKey() {
  if (!_rkm.pending.current) { setRkmError("No key captured yet — paste the new API key first."); return; }
  if (!_rkm.session.current) { setRkmError("Session expired — please close and reopen."); return; }

  setRkmError("");
  rkmDisableSubmit();
  _rkm.ttl.stop();

  try {
    const body = JSON.stringify({
      sessionId: _rkm.session.current.sessionId,
      keyId: _rkm.keyId,
      provider: _rkm.provider,
      ciphertext: bufferToBase64(_rkm.pending.current),
    });
    _rkm.pending.current = null;

    const resp = await fetch("/api/rotate-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    if (!resp.ok) throw new Error(await resp.text().catch(() => `HTTP ${resp.status}`));

    _rkm.session.current = null;
    rkmSetState("rkm-success");
    loadStatus();
  } catch (err) {
    setRkmError("Submission failed: " + err.message);
    rkmEnableSubmit();
    _rkm.ttl.start();
  }
}

/* ── Clean up sessions on page unload ────────────────────────── */

window.addEventListener("pagehide", () => {
  if (_akm.session.current) deleteSession(_akm.session.current.sessionId);
  if (_rkm.session.current) deleteSession(_rkm.session.current.sessionId);
});

/* ── Init ────────────────────────────────────────────────────── */

document.addEventListener("DOMContentLoaded", () => {
  loadStatus();      // immediate snapshot on load
  initEventSource(); // then switch to live push
});
