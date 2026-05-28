/* ─────────────────────────────────────────────────────────────────
   components.js — Subumbra Console
   Native Custom Elements (light DOM, no shadow root) so global CSS
   reaches inside. No framework, no build step.
   ───────────────────────────────────────────────────────────────── */

"use strict";

const _esc = (s) => String(s ?? "")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

/* ── <sub-stat-card> ────────────────────────────────────────────────
   Usage:
     <sub-stat-card label="Requests · 24h" value="8,142" unit="%" sub="…"
                    spark="20,35,28,…"></sub-stat-card>
   Re-renders on attribute change so JS-driven updates feel native.
   ───────────────────────────────────────────────────────────────── */
class SubStatCard extends HTMLElement {
  static observedAttributes = ["label", "value", "unit", "sub", "spark"];
  connectedCallback()        { this.classList.add("stat"); this.render(); }
  attributeChangedCallback() { if (this.isConnected) this.render(); }

  render() {
    const label = this.getAttribute("label") ?? "";
    const value = this.getAttribute("value") ?? "—";
    const unit  = this.getAttribute("unit");
    const sub   = this.getAttribute("sub");
    const spark = (this.getAttribute("spark") ?? "")
      .split(",").map(n => Number(n) || 0).filter(Boolean);

    this.innerHTML = `
      <div class="stat__label">${_esc(label)}</div>
      <div class="stat__value">${_esc(value)}${unit ? `<span class="unit">${_esc(unit)}</span>` : ""}</div>
      ${sub ? `<div class="stat__sub">${_esc(sub)}</div>` : ""}
      ${spark.length ? `
        <div class="stat__spark">
          ${spark.map(h => `<span style="height:${Math.min(100, h)}%"></span>`).join("")}
        </div>` : ""}
    `;
  }
}
customElements.define("sub-stat-card", SubStatCard);

/* ── <sub-meter> ────────────────────────────────────────────────────
   Renders a row of `cells` blocks, mostly green, with a couple of
   warn/miss spots per service (deterministic per data-mode value so
   the picture is stable across refreshes).
   ───────────────────────────────────────────────────────────────── */
class SubMeter extends HTMLElement {
  connectedCallback() {
    const cells = Number(this.getAttribute("cells") ?? 48);
    const mode  = this.getAttribute("data-mode") ?? "";
    this.classList.add("obs-meter");

    let out = [];
    if (mode === "subumbra-proxy") {
      for (let i = 0; i < cells; i++) {
        const cls = i === 14 ? "warn" : i === 15 ? "miss" : i === 16 ? "warn" : "";
        out.push(`<span class="${cls}"></span>`);
      }
    } else if (mode === "subumbra-agent") {
      for (let i = 0; i < cells; i++) {
        out.push(`<span class="${i < 3 ? "idle" : ""}"></span>`);
      }
    } else {
      for (let i = 0; i < cells; i++) out.push("<span></span>");
    }
    this.innerHTML = out.join("");
  }
}
customElements.define("sub-meter", SubMeter);

/* ── <sub-modal> ────────────────────────────────────────────────────
   Wraps a modal panel. Markup:
     <sub-modal id="lock-all" hidden>
       <div class="modal-overlay">
         <div class="modal modal--wide">…</div>
       </div>
     </sub-modal>
   Behavior:
     - .open()/.close() toggle the `hidden` attribute
     - Clicking outside the .modal (i.e. on the overlay) closes
     - Escape closes
     - Elements with data-action="close" close
   ───────────────────────────────────────────────────────────────── */
class SubModal extends HTMLElement {
  connectedCallback() {
    if (!this.hasAttribute("hidden")) this._wire();
    this.addEventListener("click", (e) => {
      const closer = e.target.closest("[data-action='close']");
      if (closer) this.close();
      // Backdrop click — overlay is the modal-overlay, clicking outside .modal closes
      if (e.target.classList?.contains("modal-overlay")) this.close();
    });
  }

  open()  {
    this.hidden = false;
    this._wire();
    document.body.style.overflow = "hidden";
    // Focus the first focusable, if any
    queueMicrotask(() => this.querySelector("input,button,[tabindex]")?.focus());
  }
  close() {
    this.hidden = true;
    document.removeEventListener("keydown", this._onKey);
    document.body.style.overflow = "";
    this.dispatchEvent(new CustomEvent("close", { bubbles: true }));
  }
  _wire() {
    this._onKey ??= (e) => { if (e.key === "Escape") this.close(); };
    document.addEventListener("keydown", this._onKey);
  }
}
customElements.define("sub-modal", SubModal);

/* ── <sub-secure-paste> ─────────────────────────────────────────────
   Captures a paste event, encrypts the pasted value with the provided
   RSA-OAEP public key (loaded as non-extractable), zeros the
   intermediate buffer, fires `subumbra:captured` with { ciphertext }.
   The input's .value is NEVER set to the plaintext.

   Usage:
     <sub-secure-paste name="api_key"></sub-secure-paste>
     // then: el.bind(sessionId, publicKeyJwk)
   ───────────────────────────────────────────────────────────────── */
class SubSecurePaste extends HTMLElement {
  connectedCallback() {
    this.classList.add("secure-paste");
    this.dataset.state = "empty";
    this.innerHTML = `
      <input class="secure-paste__input" type="text" autocomplete="off"
             spellcheck="false" inputmode="none"
             placeholder="paste your provider key here — never typed, never stored"
             aria-label="${_esc(this.getAttribute("aria-label") ?? "Secure paste")}">
      <span class="secure-paste__lock" aria-hidden="true">⚿</span>
    `;
    this._input = this.querySelector("input");

    // Block typing — paste only.
    this._input.addEventListener("keydown", (e) => {
      const allow = ["Tab", "Escape", "ArrowLeft", "ArrowRight", "Home", "End", "Meta", "Control"];
      if (!allow.includes(e.key)) e.preventDefault();
    });

    this._input.addEventListener("paste", async (e) => {
      e.preventDefault();
      if (!this._publicKey) {
        this.dataset.state = "error";
        this._input.value = "(no active session — refresh and try again)";
        return;
      }
      const text = e.clipboardData?.getData("text") ?? "";
      if (!text) return;

      const enc = new TextEncoder();
      const plain = enc.encode(text);
      let cipher;
      try {
        cipher = await crypto.subtle.encrypt({ name: "RSA-OAEP" }, this._publicKey, plain);
      } catch (err) {
        this.dataset.state = "error";
        this._input.value = `(encrypt failed: ${err.message})`;
        return;
      } finally {
        // Zero the plaintext buffer.
        for (let i = 0; i < plain.length; i++) plain[i] = 0;
      }

      const ciphertextB64 = btoa(String.fromCharCode(...new Uint8Array(cipher)));
      this._ciphertext = ciphertextB64;
      this.dataset.state = "captured";
      this._input.value = `•••••••• captured (${text.length} bytes)`;

      this.dispatchEvent(new CustomEvent("subumbra:captured", {
        bubbles: true,
        detail: { sessionId: this._sessionId, ciphertext: ciphertextB64 },
      }));
    });
  }

  /** Bind an ephemeral session and its public key (JWK). */
  async bind(sessionId, publicKeyJwk) {
    this._sessionId = sessionId;
    this._publicKey = await crypto.subtle.importKey(
      "jwk",
      publicKeyJwk,
      { name: "RSA-OAEP", hash: "SHA-256" },
      false,                // ← non-extractable
      ["encrypt"]
    );
    this.dataset.state = "ready";
  }

  reset() {
    this._ciphertext = null;
    this._publicKey  = null;
    this._sessionId  = null;
    this._input.value = "";
    this.dataset.state = "empty";
  }

  /** Returns the captured ciphertext (base64) or null. */
  get value() { return this._ciphertext ?? null; }
}
customElements.define("sub-secure-paste", SubSecurePaste);

/* ── <sub-toast> ────────────────────────────────────────────────────
   Lightweight inline notification. Usage:
     SubToast.show("Pause not yet wired — use the CLI", { sev: "warn" });
   ───────────────────────────────────────────────────────────────── */
class SubToast extends HTMLElement {
  connectedCallback() {
    this.classList.add("alert", "alert--" + (this.dataset.sev || "info"));
    this.style.position = "fixed";
    this.style.right = "24px";
    this.style.bottom = "24px";
    this.style.maxWidth = "420px";
    this.style.zIndex = "300";
    this.style.boxShadow = "0 12px 32px rgba(0,0,0,0.4)";
  }
  static show(message, { sev = "info", ttl = 4500 } = {}) {
    const t = document.createElement("sub-toast");
    t.dataset.sev = sev;
    t.innerHTML = `<span aria-hidden="true">${sev === "warn" ? "⚠" : sev === "err" ? "✕" : "ⓘ"}</span><div>${_esc(message)}</div>`;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), ttl);
  }
}
customElements.define("sub-toast", SubToast);
window.SubToast = SubToast;
