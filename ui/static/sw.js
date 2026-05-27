"use strict";

self.addEventListener("push", (event) => {
  let payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch {
      payload = {};
    }
  }
  const title = payload.title || "Subumbra approval required";
  const body = payload.body || "A gated request is waiting for operator approval.";
  const data = {
    approve_url: payload.approve_url || "/",
    deny_url: payload.deny_url || "/",
  };
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      tag: payload.request_id || "subumbra-gate",
      requireInteraction: true,
      data,
      actions: [
        { action: "approve", title: "Approve" },
        { action: "deny", title: "Deny" },
      ],
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  let target = "/";
  if (event.action === "approve" && data.approve_url) {
    target = data.approve_url;
  } else if (event.action === "deny" && data.deny_url) {
    target = data.deny_url;
  } else if (data.approve_url) {
    target = data.approve_url;
  }
  event.waitUntil(self.clients.openWindow(target));
});
