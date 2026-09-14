/* Vela hub service worker.
   - Precaches the app shell so the hub opens offline.
   - Navigations: network-first, falling back to the cached shell.
   - /api/*: network only; credentials and app data are never cached.
   - Other same-origin GETs: stale-while-revalidate.
   Requests under /apps/ are left alone — installed web apps ship their own
   service workers with their own caches. */

const SHELL_CACHE = 'vela-shell-v2';
const RUNTIME_CACHE = 'vela-runtime-v2';

const SHELL_ASSETS = ['/', '/manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  const keep = new Set([SHELL_CACHE, RUNTIME_CACHE]);
  // Only clean up stale versions of our own caches — per-app caches
  // (vela-{appId}-{version}) belong to installed apps and are left alone.
  const own = (k) =>
    k.startsWith('vela-shell-') || k.startsWith('vela-runtime-') || k.startsWith('vela-api-');
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => own(k) && !keep.has(k)).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;

  // Never intercept non-GET (install/launch/stop must always hit the backend).
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Installed web apps manage their own caching via /apps/{id}/sw.js.
  if (url.pathname.startsWith('/apps/')) return;

  // Authenticated API traffic must never enter a shared browser cache.
  if (url.pathname.startsWith('/api/')) return;

  // SPA navigations: network-first, fall back to cached shell.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put('/', copy));
          return response;
        })
        .catch(() => caches.match('/')),
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  event.respondWith(
    caches.match(request).then((cached) => {
      const fetched = fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(RUNTIME_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || fetched;
    }),
  );
});
