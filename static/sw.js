/* Minimal, deliberately conservative service worker.
 *
 * - Static assets (/static/...): cache-first, so the app shell loads instantly.
 * - Page navigations: network-first; only if the network is unreachable do we
 *   show a small offline page. We never serve a *cached* real page, so there is
 *   no risk of stale CSRF tokens or stale session-dependent content.
 * - Everything else (POST, JSON endpoints): passed straight through, never cached.
 *
 * Bump CACHE_VERSION on any change to force clients to refresh the shell.
 */
const CACHE_VERSION = "econ-tests-v2";
const PRECACHE = [
  "/static/css/style.css",
  "/static/js/student.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/offline",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // never touch POST/PUT/etc.

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Static assets: cache-first.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((cached) =>
        cached ||
        fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy));
          return res;
        })
      )
    );
    return;
  }

  // Page navigations: network-first, offline page as last resort.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("/offline"))
    );
    return;
  }
  // Anything else: default network behaviour.
});

// ---- Web Push ----
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "Econ Tests", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "Econ Tests";
  const options = {
    body: data.body || "",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    data: { url: data.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if (w.url.indexOf(target) !== -1 && "focus" in w) return w.focus();
      }
      return clients.openWindow(target);
    })
  );
});
