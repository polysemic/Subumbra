/* ═══════════════════════════════════════════════════════════════
   dashboard.js — Subumbra Dashboard runtime
   Vanilla JS, no dependencies. Requires SubtleCrypto (modern browsers).
═══════════════════════════════════════════════════════════════ */

"use strict";

/* ── Constants ───────────────────────────────────────────────── */

const SESSION_WARN_SECS = 60;
const STATUS_POLL_MS = 30000;
const PROVIDER_CLASS = {
  anthropic: "provider-anthropic",
  openai:    "provider-openai",
  groq:      "provider-groq",
  deepseek:  "provider-deepseek",
};

/* ── Dashboard state ─────────────────────────────────────────── */

let _status = null;
let _es     = null;   // EventSource instance

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
  if (diff < 60)    return `${diff}s ago`;
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function verdictClass(v) {
  if (v === "allow") return "status-ok";
  if (v === "deny")  return "status-deny";
  return "status-unknown";
}

function providerClass(p) {
  return PROVIDER_CLASS[p] ?? "provider-unknown";
}

function setAlert(el, visible) {
  el.classList.toggle("visible", visible);
}

function fmtBooleanLabel(value) {
  return value ? "Yes" : "No";
}

function renderTagList(id, values, emptyLabel = "—") {
  const el = $(id);
  if (!el) return;
  const items = Array.isArray(values) ? values.filter((value) => typeof value === "string" && value) : [];
  if (!items.length) {
    el.innerHTML = `<span class="kdm-tag">${esc(emptyLabel)}</span>`;
    return;
  }
  el.innerHTML = items
    .map((value) => `<span class="kdm-tag">${esc(value)}</span>`)
    .join("");
}

/* ═══════════════════════════════════════════════════════════════
   DASHBOARD — render functions
═══════════════════════════════════════════════════════════════ */

function renderHealth(data) {
  const sDot  = $("subumbra-health-dot");
  const sText = $("subumbra-health-text");
  const wDot  = $("worker-health-dot");
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
  const totalReqs  = data.keys.reduce((s, k) => s + (k.request_count || 0), 0);
  const activeKeys = data.keys.filter(k => k.request_count > 0).length;
  const lastReq    = data.keys.map(k => k.last_access).filter(Boolean).sort().at(-1);

  $("stat-keys").textContent       = data.keys_loaded;
  $("stat-total-reqs").textContent  = totalReqs;
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
    grid.className = "keys-grid";
    grid.innerHTML =
      `<div class="empty-state">No keys loaded. Run bootstrap first:` +
      `<div class="empty-code">docker compose --profile bootstrap run --rm -it bootstrap</div></div>`;
    return;
  }
  if (_keysView === "list") renderKeysList(keys);
  else renderKeysGrid(keys);
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
    const prov   = provMap[entry.key_id] ?? "—";
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

/* SSE connection — heartbeat-only in this round.
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
  const dot  = $("live-dot");
  const text = $("live-text");
  if (!dot || !text) return;
  const map = {
    live:         { cls: "ok",  label: "live" },
    reconnecting: { cls: "err", label: "reconnecting…" },
    error:        { cls: "err", label: msg ?? "error" },
  };
  const s = map[state] ?? map.error;
  dot.className  = `health-dot ${s.cls}`;
  text.textContent = s.label;
}

/* ── Init ────────────────────────────────────────────────────── */

/* ═══════════════════════════════════════════════════════════════
   VIEW TOGGLE + LIST RENDER
═══════════════════════════════════════════════════════════════ */

let _keysView = "card"; // "card" | "list"

function setKeysView(mode) {
  _keysView = mode;
  $("btn-view-card").classList.toggle("active", mode === "card");
  $("btn-view-list").classList.toggle("active", mode === "list");
  if (_status) renderKeys(_status.keys);
}

function renderKeysGrid(keys) {
  const grid = $("keys-grid");
  grid.className = "keys-grid";
  grid.innerHTML = keys.map(k => {
    const pClass  = providerClass(k.provider);
    const relTime = k.last_access ? fmtRelative(k.last_access) : null;
    const created = k.created_at  ? fmtTimestamp(k.created_at) : "—";
    return `
<article class="key-card" onclick="openKeyDetail('${esc(k.key_id)}','${esc(k.provider)}')">
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
</article>`;
  }).join("");
}

function renderKeysList(keys) {
  const grid = $("keys-grid");
  grid.className = "keys-list";
  grid.innerHTML = keys.map(k => {
    const pClass  = providerClass(k.provider);
    const relTime = k.last_access ? fmtRelative(k.last_access) : null;
    return `
<div class="key-row" onclick="openKeyDetail('${esc(k.key_id)}','${esc(k.provider)}')">
  <span class="provider-badge ${esc(pClass)}">${esc(k.provider)}</span>
  <div>
    <div class="key-row-id">${esc(k.key_id)}</div>
    <div class="key-row-meta">${relTime ? `Last used: ${esc(relTime)}` : "Never used"}</div>
  </div>
  <div class="key-row-counts">
    <div class="key-row-req-num">${(k.request_count || 0).toLocaleString()}</div>
    <div class="key-row-req-label">reqs</div>
  </div>
</div>`;
  }).join("");
}

/* ═══════════════════════════════════════════════════════════════
   KEY DETAIL MODAL
═══════════════════════════════════════════════════════════════ */

let _kdmCurrentKey = null;
let _kdmCurrentProvider = null;

function openKeyDetail(keyId, provider) {
  _kdmCurrentKey = keyId;
  _kdmCurrentProvider = provider;

  // Find key data from last status
  const k = (_status?.keys || []).find(k => k.key_id === keyId) || {};

  // Provider badge
  const badge = $("kdm-provider-badge");
  badge.textContent = provider;
  badge.className = `provider-badge ${providerClass(provider)}`;

  // Title
  $("kdm-title").textContent = keyId;

  // Overview fields
  $("kdm-key-id").textContent      = keyId;
  $("kdm-provider").textContent    = provider;
  $("kdm-label").textContent       = k.label || keyId;
  $("kdm-created").textContent     = k.created_at   ? fmtTimestamp(k.created_at)  : "—";
  $("kdm-last-used").textContent   = k.last_access  ? fmtTimestamp(k.last_access) : "Never";
  $("kdm-req-count").textContent   = (k.request_count || 0).toLocaleString() + " requests";
  $("kdm-paused").textContent      = fmtBooleanLabel(Boolean(k.paused));
  $("kdm-revoked").textContent     = fmtBooleanLabel(Boolean(k.revoked));
  $("kdm-target-host").textContent = k.target_host  || "—";
  $("kdm-base-path").textContent   = k.base_path    || "/";

  // Policy tab
  $("kdm-auth-scheme").textContent  = k.auth_scheme  || "header";
  $("kdm-auth-header").textContent  = k.auth_header  || "—";
  $("kdm-auth-prefix").textContent  = k.auth_prefix  || "—";
  $("kdm-protocol").textContent     = k.protocol     || "http_rest";
  $("kdm-policy-id").textContent    = k.policy_id    || "—";
  $("kdm-policy-hash").textContent  = k.policy_hash  || "—";
  $("kdm-capability-class").textContent = k.capability_class || "—";

  renderTagList("kdm-allow-adapters", k.allow_adapters);
  renderTagList("kdm-allow-methods", k.allow_methods);
  renderTagList("kdm-allow-paths", k.allow_path_prefixes);

  // Schema preview — forward-compatible template
  const schema = {
    key_id:     keyId,
    policy_id:  k.policy_id  || `${provider}-prod`,
    protocol:   k.protocol   || "http_rest",
    target: {
      host:      k.target_host || "—",
      base_path: k.base_path   || "/",
    },
    auth: {
      scheme:      k.auth_scheme  || "header",
      header_name: k.auth_header  || "Authorization",
      prefix:      k.auth_prefix  || "Bearer ",
    },
    allow: {
      adapters:      ["subumbra-proxy"],
      methods:       ["GET", "POST"],
      path_prefixes: [],
      content_types: ["application/json"],
      max_body_bytes: 1048576,
    },
    meta: {
      label: k.label || keyId,
      notes: "",
    },
    bind: {
      mode:         "strict",
      extra_fields: [],
    },
  };
  $("kdm-schema-json").textContent = JSON.stringify(schema, null, 2);

  // Reset to overview tab
  kdmShowTab("overview");
  $("key-detail-modal").classList.add("open");
}

function closeKeyDetail() {
  $("key-detail-modal").classList.remove("open");
  _kdmCurrentKey = null;
  _kdmCurrentProvider = null;
}

function kdmShowTab(name) {
  ["overview","policy","allow","bind","schema"].forEach(t => {
    $(`tab-${t}`).classList.toggle("active", t === name);
    $(`kdm-panel-${t}`).classList.toggle("active", t === name);
  });
}

function kdmSelectBind(btn, mode) {
  btn.closest(".kdm-bind-modes").querySelectorAll(".kdm-bind-mode")
    .forEach(b => b.classList.remove("selected"));
  btn.classList.add("selected");
}

document.addEventListener("DOMContentLoaded", () => {
  loadStatus();      // immediate snapshot on load
  initEventSource(); // then switch to live push
  window.setInterval(loadStatus, STATUS_POLL_MS);
});
