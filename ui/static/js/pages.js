/* ─────────────────────────────────────────────────────────────────
   pages.js — page-level interactivity glue
   Wires chips, tabs, action buttons, copy, modals, SSE.
   ───────────────────────────────────────────────────────────────── */

"use strict";

document.addEventListener("DOMContentLoaded", () => {
  initSidebar();
  initJanus();
  initChips();
  initTabs();
  initSelectRows();
  initCopy();
  initSwitches();
  initActions();
  initLiveData();
});

/* ── Janus indicator ─────────────────────────────────────────────
   • Subscribed device  → green dot, no badge.
   • Unsubscribed device → amber "Subscribe" badge with a ✕ dismiss.
     Dismissed state saved to localStorage; clicking Subscribe calls
     window.ensureSubumbraPushSubscription() from push.js.
   No-ops silently if Push API is unavailable or VAPID key missing.
   ───────────────────────────────────────────────────────────── */
function initJanus() {
  const el = document.getElementById("janusIndicator");
  if (!el || !("serviceWorker" in navigator)) return;

  const DISMISS_KEY = "janus-subscribe-dismissed";

  function addSubscribeBadge() {
    if (localStorage.getItem(DISMISS_KEY) === "1") return;
    if (el.querySelector(".janus-subscribe")) return;

    const badge = document.createElement("span");
    badge.className = "janus-subscribe";

    const btn = document.createElement("button");
    btn.className = "janus-subscribe__btn";
    btn.textContent = "Subscribe";
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!window.ensureSubumbraPushSubscription) return;
      btn.textContent = "…";
      btn.disabled = true;
      try {
        await window.ensureSubumbraPushSubscription();
        badge.remove();
        el.classList.add("is-subscribed");
      } catch {
        btn.textContent = "Subscribe";
        btn.disabled = false;
      }
    });

    const dismiss = document.createElement("button");
    dismiss.className = "janus-subscribe__dismiss";
    dismiss.textContent = "✕";
    dismiss.setAttribute("aria-label", "Dismiss");
    dismiss.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      localStorage.setItem(DISMISS_KEY, "1");
      badge.remove();
    });

    badge.appendChild(btn);
    badge.appendChild(dismiss);
    el.appendChild(badge);
  }

  navigator.serviceWorker.getRegistration("/").then((reg) => {
    if (!reg) { addSubscribeBadge(); return; }
    return reg.pushManager.getSubscription().then((sub) => {
      if (sub) el.classList.add("is-subscribed");
      else addSubscribeBadge();
    });
  }).catch(() => {});
}

/* ── Sidebar toggle ──────────────────────────────────────────────
   Desktop: .shell.is-side-collapsed → icons-only 52px rail.
   Mobile:  .side.is-open + backdrop overlay.
   Preference persisted in localStorage as "sideCollapsed".
   ───────────────────────────────────────────────────────────── */
function initSidebar() {
  const shell    = document.querySelector(".shell");
  const sidebar  = document.getElementById("sidebar");
  const colBtn   = document.getElementById("sideCollapseBtn");
  const menuBtn  = document.getElementById("sideMenuBtn");
  const backdrop = document.getElementById("sideBackdrop");
  if (!shell || !sidebar) return;

  const isMobile = () => window.innerWidth <= 880;

  function openMobile() {
    sidebar.classList.add("is-open");
    if (backdrop) backdrop.classList.add("is-visible");
    if (menuBtn) { menuBtn.setAttribute("aria-expanded", "true"); menuBtn.textContent = "✕"; }
    if (colBtn)  colBtn.textContent = "✕";
  }
  function closeMobile() {
    sidebar.classList.remove("is-open");
    if (backdrop) backdrop.classList.remove("is-visible");
    if (menuBtn) { menuBtn.setAttribute("aria-expanded", "false"); menuBtn.textContent = "☰"; }
    if (colBtn)  colBtn.textContent = "‹";
  }

  function setDesktopCollapsed(collapsed) {
    shell.classList.toggle("is-side-collapsed", collapsed);
    localStorage.setItem("sideCollapsed", collapsed ? "1" : "0");
  }

  // Restore desktop preference
  if (!isMobile() && localStorage.getItem("sideCollapsed") === "1") {
    shell.classList.add("is-side-collapsed");
  }

  // Collapse/close button — desktop collapses rail, mobile closes overlay
  if (colBtn) {
    colBtn.addEventListener("click", () => {
      if (isMobile()) { closeMobile(); return; }
      setDesktopCollapsed(!shell.classList.contains("is-side-collapsed"));
    });
  }

  // Mobile hamburger
  if (menuBtn) {
    menuBtn.addEventListener("click", () => {
      if (sidebar.classList.contains("is-open")) closeMobile(); else openMobile();
    });
  }

  // Backdrop tap closes mobile sidebar
  if (backdrop) {
    backdrop.addEventListener("click", closeMobile);
  }

  // Close mobile sidebar on nav link click
  sidebar.querySelectorAll(".side__item").forEach((link) => {
    link.addEventListener("click", () => { if (isMobile()) closeMobile(); });
  });

  // Re-evaluate on resize
  window.addEventListener("resize", () => {
    if (!isMobile()) closeMobile();
  });
}

/* ── Select rows (key/SSH/policy/adapter navigation) ────────────
   Rows and cards that carry a select-target attribute navigate to
   ?select=<id> on click so the server renders the drawer pre-opened.
   Attribute map:
     [data-key-id]      → /vault?select=  (API and SSH tables)
     [data-policy]      → /policies?select=
     [data-adapter-id]  → /adapters?select=
   ───────────────────────────────────────────────────────────── */
function initSelectRows() {
  const map = [
    { sel: "[data-key-id]",     param: (el) => el.dataset.keyId },
    { sel: "[data-policy]",     param: (el) => el.dataset.policy },
    { sel: "[data-adapter-id]", param: (el) => el.dataset.adapterId },
  ];
  map.forEach(({ sel, param }) => {
    document.querySelectorAll(sel).forEach((el) => {
      if (el.dataset.jsBound === "1") return;
      el.dataset.jsBound = "1";
      el.style.cursor = "pointer";
      el.addEventListener("click", async (e) => {
        if (e.target.closest("a,button,input,select,textarea")) return;
        const id = param(el);
        if (!id) return;
        if (sel === "[data-key-id]" && (location.pathname === "/vault" || location.pathname === "/vault/ssh")) {
          e.preventDefault();
          await updateVaultDrawer(id, el);
          return;
        }
        const url = new URL(location.href);
        url.searchParams.set("select", id);
        location.href = url.toString();
      });
    });
  });
}

async function updateVaultDrawer(id, row) {
  const container = document.querySelector(".split-drawer");
  const drawer = container?.querySelector(".drawer");
  if (!container || !drawer) {
    const url = new URL(location.href);
    url.searchParams.set("select", id);
    location.href = url.toString();
    return;
  }

  const url = new URL(location.href);
  url.searchParams.set("select", id);
  url.searchParams.set("partial", "drawer");

  document.body.style.cursor = "progress";
  row?.classList.add("is-loading");
  try {
    const resp = await fetch(url.toString(), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const html = await resp.text();
    drawer.outerHTML = html;
    document.querySelectorAll("[data-key-id].is-selected").forEach((el) => el.classList.remove("is-selected"));
    if (row) row.classList.add("is-selected");

    const nextUrl = new URL(location.href);
    nextUrl.searchParams.set("select", id);
    nextUrl.searchParams.delete("partial");
    history.replaceState({}, "", nextUrl.toString());

    initTabs();
  } catch (err) {
    console.error("vault drawer update failed", err);
    const nextUrl = new URL(location.href);
    nextUrl.searchParams.set("select", id);
    location.href = nextUrl.toString();
  } finally {
    row?.classList.remove("is-loading");
    document.body.style.cursor = "";
  }
}

/* ── Chip groups ─────────────────────────────────────────────────
   - .chips[data-radio="key"] → single-select (click sets is-active on
     one chip, removes from siblings).
   - Otherwise → toggle is-active on click (multi-select filter).
   ───────────────────────────────────────────────────────────── */
function initChips() {
  document.querySelectorAll(".chips").forEach((grp) => {
    const radio = grp.dataset.radio;
    grp.addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip || !grp.contains(chip)) return;
      if (radio) {
        grp.querySelectorAll(".chip").forEach(c => c.classList.remove("is-active"));
        chip.classList.add("is-active");
      } else {
        chip.classList.toggle("is-active");
      }
      grp.dispatchEvent(new CustomEvent("chip:change", {
        bubbles: true,
        detail: { value: chip.dataset.value, active: chip.classList.contains("is-active") },
      }));
    });
  });
}

/* ── Tabs (drawer + page) ────────────────────────────────────────
   .drawer__tabs and .tabs both contain .drawer__tab / .tabs__tab.
   Click switches is-active across the group and swaps .drawer__pane
   siblings by index inside the nearest .drawer ancestor.
   ───────────────────────────────────────────────────────────── */
function initTabs() {
  ["[role='tablist'],.drawer__tabs,.tabs"].forEach(sel => {
    document.querySelectorAll(sel).forEach((bar) => {
      if (bar.dataset.jsBound === "1") return;
      bar.dataset.jsBound = "1";
      bar.addEventListener("click", (e) => {
        const tab = e.target.closest(".drawer__tab,.tabs__tab");
        if (!tab || !bar.contains(tab) || tab.tagName === "A") return;

        // Activate tab button
        const tabs = [...bar.querySelectorAll(".drawer__tab,.tabs__tab")];
        tabs.forEach(t => t.classList.remove("is-active"));
        tab.classList.add("is-active");

        // Swap drawer panes if present
        const drawer = bar.closest(".drawer");
        if (drawer) {
          const panes = [...drawer.querySelectorAll(".drawer__pane")];
          const idx = tabs.indexOf(tab);
          panes.forEach((p, i) => p.classList.toggle("is-hidden", i !== idx));
        }
      });
    });
  });
}

/* ── Copy buttons (data-copy="…") ────────────────────────────── */
function initCopy() {
  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    const text = btn.dataset.copy || "";
    navigator.clipboard?.writeText(text).then(
      () => SubToast.show("Copied to clipboard"),
      () => SubToast.show("Copy failed — select & ⌘C manually", { sev: "warn" })
    );
  });
}

/* ── Switches (visual toggle for the prototype) ─────────────── */
function initSwitches() {
  document.body.addEventListener("click", (e) => {
    const sw = e.target.closest(".switch");
    if (!sw) return;
    sw.classList.toggle("is-on");
  });
}

/* ── Action buttons ──────────────────────────────────────────────
   Each [data-action] dispatches to the right handler. Write actions
   are intercepted and routed through the API client; until the
   management API ships, they 501 and we surface a useful toast.
   ───────────────────────────────────────────────────────────── */
function initActions() {
  document.body.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;

    switch (action) {
      case "add-key":         openAddKey();        return;
      case "lock-all":        openLockAll();       return;
      case "import-env":      SubToast.show("Guided .env import lands in Q4 — see Upcoming", { sev: "info" }); return;
      case "import-ssh":      SubToast.show("Passphrase-protected import is a follow-up — use --add-ssh-key for now", { sev: "info" }); return;
      case "generate-ssh":    SubToast.show("Generate via the CLI: ./bootstrap.sh --add-ssh-key <id>", { sev: "info" }); return;
      case "add-adapter":     SubToast.show("Add adapter via CLI: ./bootstrap.sh --add-adapter <name>", { sev: "info" }); return;
      case "rotate-adapter":  return _stub(btn, "rotate-adapter");
      case "revoke-adapter":  return _stub(btn, "revoke-adapter");
      case "pause-key":       return _stubKey(btn, "pause",  Api.pauseKey);
      case "rotate-key":      return _stub(btn, "rotate-key");
      case "revoke-key":      return _confirmRevoke(btn);
      case "close-session":   return _stubSess(btn, "close");
      case "nuke-cloudflare": return _stub(btn, "nuke-cloudflare");
    }
  });

  // Open-session form
  const form = document.getElementById("open-session-form");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const resp = await Api.openSession({ name: form.elements.name?.value });
      if (resp.ok) { SubToast.show("Session opened"); setTimeout(() => location.reload(), 600); }
      else _showApiToast(resp, "open session");
    });
  }
}

async function _stubKey(btn, label, apiFn) {
  const key = btn.dataset.key || "";
  const resp = await apiFn.call(Api, key);
  _showApiToast(resp, `${label} ${key}`);
}
async function _stubSess(btn, label) {
  const id = btn.dataset.id || "";
  const resp = await Api.closeSession({ id });
  _showApiToast(resp, `${label} session ${id}`);
}
async function _stub(btn, label) {
  // No safe direct call — show the canonical hint.
  SubToast.show(`Action “${label}” lands with the management API (R45+). Use the bootstrap CLI for now.`, { sev: "warn" });
}
async function _confirmRevoke(btn) {
  const key = btn.dataset.key || "this key";
  if (!confirm(`Revoke ${key}? This is irreversible without a CLI restore.`)) return;
  const resp = await Api.revokeKey(key);
  _showApiToast(resp, `revoke ${key}`);
}
function _showApiToast(resp, label) {
  if (resp.ok) { SubToast.show(`${label}: done`); return; }
  if (resp.status === 501) {
    const hint = resp.body?.cli_hint
      ? `Use CLI: ${resp.body.cli_hint}`
      : (resp.body?.fallback || "Use the bootstrap CLI on the host.");
    SubToast.show(`${label}: management API not yet implemented. ${hint}`, { sev: "warn", ttl: 6500 });
  } else {
    SubToast.show(`${label}: HTTP ${resp.status} — ${(resp.body?.error || "error")}`, { sev: "err" });
  }
}

/* ── Add Key modal (built lazily on first open) ──────────────── */
async function openAddKey() {
  let modal = document.getElementById("add-key-modal");
  if (!modal) modal = mountAddKeyModal();
  modal.open();

  const sessionResp = await Api.newKeySession();
  if (!sessionResp.ok) {
    SubToast.show("Could not mint a secure-paste session (cryptography lib missing on UI image?)", { sev: "err" });
    modal.close();
    return;
  }
  const { sessionId, publicKeyJwk, expiresAt } = sessionResp.body;
  modal.querySelector("[data-bind='session-id']").textContent = sessionId.slice(0, 8) + "…" + sessionId.slice(-4);
  modal.querySelector("[data-bind='expires']").textContent   = new Date(expiresAt).toLocaleTimeString();

  const paste = modal.querySelector("sub-secure-paste");
  await paste.bind(sessionId, publicKeyJwk);

  // Wire the submit button — POSTs the ciphertext.
  const submit = modal.querySelector("[data-bind='submit']");
  submit.replaceWith(submit.cloneNode(true));
  const submitBtn = modal.querySelector("[data-bind='submit']");
  submitBtn.disabled = true;
  paste.addEventListener("subumbra:captured", () => { submitBtn.disabled = false; }, { once: true });

  const onSubmit = async () => {
    if (!paste.value) return;
    const keyId     = modal.querySelector("[data-bind='key-id']").value;
    const provider  = modal.querySelector("[data-bind='provider']").value;
    const resp = await Api.addKey({ sessionId, keyId, provider, ciphertext: paste.value });
    if (resp.ok) {
      SubToast.show(`Key ${keyId} added`);
      modal.close();
      setTimeout(() => location.reload(), 600);
    } else {
      _showApiToast(resp, `add ${keyId}`);
    }
  };
  submitBtn.addEventListener("click", onSubmit, { once: true });

  // Drop the server session when the modal closes (releases the private key).
  modal.addEventListener("close", () => { Api.dropKeySession(sessionId); paste.reset(); }, { once: true });
}

function mountAddKeyModal() {
  const root = document.getElementById("modal-root");
  root.insertAdjacentHTML("beforeend", `
    <sub-modal id="add-key-modal" hidden>
      <div class="modal-overlay">
        <div class="modal">
          <div class="modal__head">
            <div class="key-icon" style="width:34px;height:34px;font-size:14px">◆</div>
            <div>
              <div class="modal__title">Add API key</div>
              <div class="modal__sub">Plaintext never crosses the wire. Encrypted under an ephemeral RSA-OAEP keypair.</div>
            </div>
            <div class="modal__close" data-action="close" aria-label="close">×</div>
          </div>

          <div style="padding:14px 22px 4px;border-bottom:1px solid var(--border)">
            <div class="steps">
              <span class="step is-done"><span class="step__num">✓</span>Identity</span>
              <span style="color:var(--text-dim)">→</span>
              <span class="step is-active"><span class="step__num">2</span>Paste key</span>
              <span style="color:var(--text-dim)">→</span>
              <span class="step"><span class="step__num">3</span>Confirm</span>
            </div>
          </div>

          <div class="modal__body">

            <div class="session-info">
              <div><div class="k">Session ID</div><div class="v" data-bind="session-id">—</div></div>
              <div><div class="k">Expires</div>   <div class="v" style="color:var(--ok)" data-bind="expires">—</div></div>
              <div><div class="k">Keypair</div>   <div class="v">RSA-OAEP-256 · ephemeral</div></div>
            </div>

            <div class="form-row">
              <label class="form-label">Key ID</label>
              <input class="input mono" placeholder="e.g. anthropic_staging" data-bind="key-id">
            </div>

            <div class="form-row">
              <label class="form-label">Provider</label>
              <select class="select" data-bind="provider">
                <option value="anthropic">anthropic</option>
                <option value="openai">openai</option>
                <option value="groq">groq</option>
                <option value="deepseek">deepseek</option>
                <option value="generic">generic</option>
              </select>
            </div>

            <div class="form-row">
              <label class="form-label">Provider key value</label>
              <sub-secure-paste aria-label="Provider key value"></sub-secure-paste>
              <div class="form-help">Paste your real provider key — typing is blocked. It encrypts in your browser before the <span class="mono">input.value</span> can hold it.</div>
            </div>

            <div class="alert alert--info">
              <span aria-hidden="true">⚿</span>
              <div>
                <div style="color:var(--text);font-weight:500;margin-bottom:2px">What happens when you submit</div>
                The ciphertext goes to <span class="mono">subumbra-keys</span>. The session's private key decrypts it once, in memory, then the session is destroyed. <span class="mono">subumbra-keys</span> wraps the plaintext under the live DEK and hands the envelope to your Cloudflare vault. Plaintext lifetime: under 100ms.
              </div>
            </div>
          </div>

          <div class="modal__foot">
            <button class="btn btn--ghost" data-action="close">Cancel</button>
            <span style="margin-left:auto"></span>
            <button class="btn btn--primary" data-bind="submit">Add key →</button>
          </div>
        </div>
      </div>
    </sub-modal>
  `);
  return document.getElementById("add-key-modal");
}

/* ── Lock All modal ──────────────────────────────────────────── */
async function openLockAll() {
  const resp = await Api.status();
  const sess = resp.body?.active_sessions || [];
  let modal = document.getElementById("lock-all-modal");
  if (!modal) modal = mountLockAllModal();
  fillLockAllModal(modal, sess);
  modal.open();

  const confirm = modal.querySelector("[data-bind='confirm']");
  confirm.replaceWith(confirm.cloneNode(true));
  const confirmBtn = modal.querySelector("[data-bind='confirm']");
  const input   = modal.querySelector("[data-bind='confirm-input']");
  const update  = () => confirmBtn.disabled = (input.value.trim() !== "LOCK");
  input.addEventListener("input", update);
  update();

  const onConfirm = async () => {
    const r = await Api.lockAll();
    if (r.ok) { SubToast.show("All sessions closed"); modal.close(); setTimeout(() => location.reload(), 600); }
    else _showApiToast(r, "lock all");
  };
  confirmBtn.addEventListener("click", onConfirm, { once: true });
}

function mountLockAllModal() {
  const root = document.getElementById("modal-root");
  root.insertAdjacentHTML("beforeend", `
    <sub-modal id="lock-all-modal" hidden>
      <div class="modal-overlay">
        <div class="modal modal--wide">
          <div class="modal__head modal__head--danger">
            <div class="danger-icon">⚠</div>
            <div>
              <div class="modal__title">Lock all sessions</div>
              <div class="modal__sub">Immediately closes every active session window. Proxy and SSH routes fail-closed within seconds.</div>
            </div>
            <div class="modal__close" data-action="close" aria-label="close">×</div>
          </div>

          <div class="modal__body">
            <div class="impact" data-bind="impact"></div>

            <div class="grid-3" data-bind="impact-stats"></div>

            <div class="alert alert--err">
              <span aria-hidden="true">ⓘ</span>
              <div>
                <div style="color:var(--text);font-weight:500;margin-bottom:2px">In-flight requests will fail.</div>
                Provider calls mid-stream return their in-progress bytes; new requests get a <span class="mono">system_locked</span> deny. SSH channels already open keep running; new sign requests fail.
              </div>
            </div>

            <div class="form-row">
              <label class="form-label" style="color:var(--err)">Type <span class="mono" style="background:var(--sunken);padding:1px 5px;border-radius:2px">LOCK</span> to confirm</label>
              <input class="confirm-input" data-bind="confirm-input" placeholder="LOCK">
            </div>
          </div>

          <div class="modal__foot">
            <button class="btn btn--ghost" data-action="close">Cancel</button>
            <span style="margin-left:auto"></span>
            <button class="btn btn--danger-solid" data-bind="confirm" disabled>Lock everything now</button>
          </div>
        </div>
      </div>
    </sub-modal>
  `);
  return document.getElementById("lock-all-modal");
}

function fillLockAllModal(modal, sess) {
  const impact = modal.querySelector("[data-bind='impact']");
  const stats  = modal.querySelector("[data-bind='impact-stats']");
  const adapters = [...new Set(sess.flatMap(s => s.adapters || []))];
  const keys     = [...new Set(sess.flatMap(s => s.allowed_keys || s.keys || []))];
  const queries  = sess.reduce((a, s) => a + (s.queries_used || 0), 0);

  impact.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:baseline">
      <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.1em;color:var(--text-muted);font-weight:600">Sessions to be closed</div>
      <div class="mono" style="font-size:11px;color:var(--text-dim)">${sess.length} active</div>
    </div>
    ${sess.map(s => `
      <div class="impact__sess">
        <span class="pill pill--active">open</span>
        <div>
          <div class="name">${_esc(s.name || s.id || "session")}</div>
          <div class="mono" style="font-size:10px;color:var(--text-dim)">${_esc((s.adapters || []).join(", "))} → ${(s.allowed_keys || s.keys || []).length} key${(s.allowed_keys || s.keys || []).length > 1 ? "s" : ""}</div>
        </div>
        <div class="ttl">${_esc(s.ttl_label || "—")} left</div>
      </div>
    `).join("") || `<div style="color:var(--text-dim);font-size:12px">No active sessions — clicking Lock will harden the lockdown state and is a no-op.</div>`}
  `;

  stats.innerHTML = `
    <div style="padding:10px 12px;background:var(--sunken);border:1px solid var(--border);border-radius:var(--r)">
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:var(--text-dim);font-weight:600">Adapters affected</div>
      <div style="font-size:20px;font-weight:500;margin-top:4px">${adapters.length}</div>
      <div class="mono" style="font-size:10px;color:var(--text-muted);margin-top:2px">${_esc(adapters.join(", "))}</div>
    </div>
    <div style="padding:10px 12px;background:var(--sunken);border:1px solid var(--border);border-radius:var(--r)">
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:var(--text-dim);font-weight:600">Keys gated</div>
      <div style="font-size:20px;font-weight:500;margin-top:4px">${keys.length}</div>
      <div class="mono" style="font-size:10px;color:var(--text-muted);margin-top:2px">${_esc(keys.slice(0,3).join(", "))}${keys.length>3?"…":""}</div>
    </div>
    <div style="padding:10px 12px;background:var(--sunken);border:1px solid var(--border);border-radius:var(--r)">
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:var(--text-dim);font-weight:600">Served this window</div>
      <div style="font-size:20px;font-weight:500;margin-top:4px">${queries}</div>
      <div class="mono" style="font-size:10px;color:var(--text-muted);margin-top:2px">queries</div>
    </div>
  `;
}

/* ── Live data: SSE + periodic refresh ───────────────────────── */
function initLiveData() {
  if (document.body.dataset.page === "audit" && window.initEventStream) window.initEventStream();
  // Refresh the audit page on SSE tick (a future round can be more granular)
  if (document.body.dataset.page === "audit") {
    window.addEventListener("subumbra:status", async () => {
      const r = await Api.status();
      const tb = document.getElementById("audit-rows");
      if (!r.ok || !tb) return;
      tb.innerHTML = (r.body.recent_log || []).slice(0, 50).map(e => `
        <tr>
          <td class="ts">${_esc(e.date || "")} · ${_esc(e.ts || "")}</td>
          <td class="mono">${_esc(e.adapter_id || e.adapter || "")}</td>
          <td><span class="tag" style="font-size:10px">${_esc(e.method || "POST")}</span></td>
          <td class="mono" style="color:var(--text-muted);font-size:11px">${_esc(e.endpoint || "")}</td>
          <td class="mono">${_esc(e.key_id || e.keyId || "")}</td>
          <td><span class="badge badge--${_esc(e.provider || "generic")}">${_esc(e.provider || "")}</span></td>
          <td class="mono" style="color:var(--text-dim);font-size:11px">${_esc(e.remote || "")}</td>
          <td class="${e.verdict === "allow" ? "verdict-allow" : "verdict-deny"}">${_esc(e.verdict || "")}</td>
          <td class="mono" style="font-size:11px">${_esc(e.reason_code || e.reason || "")}</td>
        </tr>
      `).join("");
    });
  }
}
