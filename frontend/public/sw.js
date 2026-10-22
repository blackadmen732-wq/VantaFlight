// VantaFlight service worker: keeps the app shell available offline.
//
// The backend already runs on this machine, so "offline" means no internet,
// not no backend. API and WebSocket traffic always goes to the live backend
// (never served from cache: stale telemetry would be worse than none). The
// built UI files are cached so the window opens even if the backend is
// still starting, and so a reload never needs the network.
const CACHE = "vantaflight-shell-v1";
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/vantaflight-icon.png", "/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws/")) return; // always live

  // Network first (new builds win), cache as the fallback.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() =>
        caches.match(event.request).then((hit) => hit || caches.match("/index.html"))
      )
  );
});
