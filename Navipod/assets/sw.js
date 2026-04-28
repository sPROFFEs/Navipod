/**
 * sw.js — Navipod Service Worker
 *
 * Minimal and bulletproof. Goals:
 *  1. Install without any possibility of failure (no pre-caching).
 *  2. Cache /assets/ files cache-first (safe: they use ?v= cache-busting).
 *  3. Leave ALL other requests (navigation, API, streams) to the browser.
 *
 * Having a registered + active SW with a fetch handler is what Chrome
 * needs to show "Install App" instead of "Add shortcut".
 */

const CACHE = 'navipod-shell-v1';

// ── Install ────────────────────────────────────────────────────────────────
// No pre-caching: avoids any async failure that would leave the SW in the
// "redundant" state and break PWA installability.

self.addEventListener('install', () => {
  self.skipWaiting(); // Activate immediately, don't wait for old SW to die
});

// ── Activate ───────────────────────────────────────────────────────────────

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ── Fetch ──────────────────────────────────────────────────────────────────
// Only intercept same-origin GET requests for /assets/.
// Everything else (navigation, API, audio streams) → default browser fetch.

self.addEventListener('fetch', event => {
  const { request } = event;

  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Cross-origin → browser handles it
  if (url.origin !== self.location.origin) return;

  // Only cache static assets — safe because they carry ?v=<hash> busting
  if (!url.pathname.startsWith('/assets/')) return;

  event.respondWith(
    caches.match(request).then(cached => {
      // Serve from cache; in parallel, refresh in background
      if (cached) {
        fetch(request).then(res => {
          if (res.ok) caches.open(CACHE).then(c => c.put(request, res));
        }).catch(() => {});
        return cached;
      }
      // Not cached yet: fetch, cache, and return
      return fetch(request).then(res => {
        if (res.ok) caches.open(CACHE).then(c => c.put(request, res.clone()));
        return res;
      });
    })
  );
});
