(function () {
  "use strict";

  var btn = document.querySelector("[data-push-button]");
  if (!btn) return;

  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  var vapidKey = btn.getAttribute("data-vapid-key") || "";

  var supported =
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window;

  if (!supported || !vapidKey) {
    btn.textContent = "Notifications not supported on this browser";
    btn.disabled = true;
    return;
  }

  function urlBase64ToUint8Array(base64String) {
    var padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    var base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    var raw = atob(base64);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; ++i) out[i] = raw.charCodeAt(i);
    return out;
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(body),
    });
  }

  function setState(state) {
    // state: "on" | "off" | "blocked" | "working"
    btn.dataset.state = state;
    if (state === "on") {
      btn.textContent = "🔔 Notifications on — tap to turn off";
      btn.disabled = false;
    } else if (state === "blocked") {
      btn.textContent = "Notifications blocked in browser settings";
      btn.disabled = true;
    } else if (state === "working") {
      btn.textContent = "Working…";
      btn.disabled = true;
    } else {
      btn.textContent = "🔔 Enable notifications";
      btn.disabled = false;
    }
  }

  function refresh() {
    if (Notification.permission === "denied") return setState("blocked");
    navigator.serviceWorker.ready
      .then(function (reg) {
        return reg.pushManager.getSubscription();
      })
      .then(function (sub) {
        setState(sub ? "on" : "off");
      })
      .catch(function () {
        setState("off");
      });
  }

  function enable() {
    setState("working");
    Notification.requestPermission().then(function (perm) {
      if (perm !== "granted") {
        refresh();
        return;
      }
      navigator.serviceWorker.ready
        .then(function (reg) {
          return reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(vapidKey),
          });
        })
        .then(function (sub) {
          return post("/push/subscribe", sub.toJSON());
        })
        .then(function () {
          setState("on");
        })
        .catch(function (err) {
          console.warn("Push enable failed:", err);
          setState("off");
        });
    });
  }

  function disable() {
    setState("working");
    navigator.serviceWorker.ready
      .then(function (reg) {
        return reg.pushManager.getSubscription();
      })
      .then(function (sub) {
        if (!sub) return;
        var endpoint = sub.endpoint;
        return sub.unsubscribe().then(function () {
          return post("/push/unsubscribe", { endpoint: endpoint });
        });
      })
      .then(function () {
        setState("off");
      })
      .catch(function () {
        refresh();
      });
  }

  btn.addEventListener("click", function () {
    if (btn.dataset.state === "on") disable();
    else enable();
  });

  refresh();
})();
