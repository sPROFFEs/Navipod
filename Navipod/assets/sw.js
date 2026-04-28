/**
 * sw.js — Navipod Service Worker
 *
 * Strategy:
 *  - App shell (CSS, JS, icons): cache-first, update in background.
 *  - API calls, audio streams, proxy routes: network-only (never cached).
 *  - Navigation (HTML pages): network-first with cache fallback.
 *
 * The service worker is intentionally minimal. Its primary purpose is to
 * satisfy Chrome's PWA installability criteria (requires a fetch handler).
 * Offline playback is not a goal for a streaming app.
 */

const CACHE = 'navipod-shell-v1';

// Static assets to pre-cache on install
const PRECACHE = [
  '/assets/icon.png',
  '/assets/android-chrome-192x192.png',
  '/assets/android-chrome-512x512.png',
  '/assets/apple-touch-icon.png',
  '/assets/site.webmanifest',
];

// Requests matching these patterns bypass the cache entirely
const NETWORK_ONLY = [
  /^\/api\//,
  /^\/user\//,
  /^\/admin\//,
  /\/proxy\//,
  /\/rest\//,
  /\.mp3($|\?)/,
  /\.flac($|\?)/,
  /\.m3u($|\?)/,
];

// ── Install ────────────────────────────────────────────────────────────────

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
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

self.addEventListener('fetch', event => {
  const { request } = event;

  // Only intercept GET requests
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Cross-origin requests (fonts, CDN) → network only
  if (url.origin !== self.location.origin) return;

  // API / stream / proxy routes → network only, no caching
  if (NETWORK_ONLY.some(pattern => pattern.test(url.pathname))) return;

  // Static assets (CSS, JS, images) → cache-first, refresh in background
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.open(CACHE).then(cache =>
        cache.match(request).then(cached => {
          const networkFetch = fetch(request).then(response => {
            if (response.ok) cache.put(request, response.clone());
            return response;
          });
          return cached || networkFetch;
        })
      )
    );
    return;
  }

  // Navigation (HTML) → network-first, fall back to cache
  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE).then(c => c.put(request, clone));
        }
        return response;
      })
      .catch(() => caches.match(request))
  );
});
