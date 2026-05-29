/* ─────────────────────────────────────────────────────────────────
   api.js — thin fetch wrapper + SSE client
   ───────────────────────────────────────────────────────────────── */

"use strict";

let _subumbraEventSource = null;
let _subumbraEventStreamWired = false;

const Api = {

  async _fetch(path, opts = {}) {
    const resp = await fetch(path, {
      credentials: "same-origin",
      headers: {
        "Accept":       "application/json",
        "Content-Type": "application/json",
        ...(opts.headers || {}),
      },
      ...opts,
    });
    let body = null;
    try { body = await resp.json(); } catch {}
    return { ok: resp.ok, status: resp.status, body };
  },

  status()              { return this._fetch("/api/status"); },
  console()             { return this._fetch("/api/console"); },

  // Add-key secure flow
  newKeySession()       { return this._fetch("/api/key-session"); },
  dropKeySession(sid)   { return this._fetch(`/api/key-session/${encodeURIComponent(sid)}`, { method: "DELETE" }); },
  addKey(payload)       { return this._fetch("/api/add-key",    { method: "POST", body: JSON.stringify(payload) }); },
  rotateKey(payload)    { return this._fetch("/api/rotate-key", { method: "POST", body: JSON.stringify(payload) }); },

  // Lifecycle
  pauseKey(kid)         { return this._fetch(`/api/keys/${encodeURIComponent(kid)}/pause`,  { method: "POST" }); },
  resumeKey(kid)        { return this._fetch(`/api/keys/${encodeURIComponent(kid)}/resume`, { method: "POST" }); },
  revokeKey(kid)        { return this._fetch(`/api/keys/${encodeURIComponent(kid)}`,        { method: "DELETE" }); },

  // Sessions
  openSession(payload)  { return this._fetch("/api/sessions/open",  { method: "POST", body: JSON.stringify(payload) }); },
  closeSession(payload) { return this._fetch("/api/sessions/close", { method: "POST", body: JSON.stringify(payload) }); },
  lockAll()             { return this._fetch("/api/lock-all",       { method: "POST" }); },
};

/* SSE — reconnects automatically. Fires `subumbra:status` events on
   the window whenever the heartbeat ticks; the page can choose
   whether to re-fetch /api/status on the tick. */
function initEventStream() {
  if (!("EventSource" in window)) return;
  if (_subumbraEventSource) _subumbraEventSource.close();

  function connect() {
    _subumbraEventSource = new EventSource("/api/events");
    _subumbraEventSource.addEventListener("open",  () => window.dispatchEvent(new CustomEvent("subumbra:live")));
    _subumbraEventSource.addEventListener("error", () => window.dispatchEvent(new CustomEvent("subumbra:reconnecting")));
    _subumbraEventSource.addEventListener("message", () => window.dispatchEvent(new CustomEvent("subumbra:status")));
  }

  connect();

  if (!_subumbraEventStreamWired) {
    const disconnect = () => {
      if (_subumbraEventSource) {
        _subumbraEventSource.close();
        _subumbraEventSource = null;
      }
    };
    window.addEventListener("pagehide", disconnect);
    window.addEventListener("beforeunload", disconnect);
    _subumbraEventStreamWired = true;
  }
}

window.Api = Api;
window.initEventStream = initEventStream;
