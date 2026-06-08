"use strict";

(function () {
  function base64UrlToUint8Array(value) {
    const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
    const raw = atob(padded);
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function ensureSubumbraPushSubscription() {
    const publicKey = document.body.dataset.gateVapidPublicKey || "";
    if (!publicKey) {
      throw new Error("push not configured");
    }
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      throw new Error("push unsupported");
    }
    const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    const existing = await registration.pushManager.getSubscription();
    const subscription = existing || await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: base64UrlToUint8Array(publicKey),
    });
    const response = await fetch("/api/janus/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(subscription.toJSON()),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return subscription;
  }

  window.ensureSubumbraPushSubscription = ensureSubumbraPushSubscription;
})();
