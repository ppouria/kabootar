const CACHE_NAME = 'kabootar-static-v20';
const STATIC_ASSETS = [
  '/static/style.css?v=20260330a',
  '/static/settings.css?v=20260327a',
  '/static/index.js?v=20260328c',
  '/static/settings.js?v=20260328b',
  '/static/i18n/en.json',
  '/static/i18n/fa.json',
  '/static/kabootar.svg',
  '/static/t_logo.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  if (event.request.method !== 'GET') return;

  // cache static frontend assets for fast repeat opens
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        const networkFetch = fetch(event.request)
          .then((res) => {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
            return res;
          })
          .catch(() => cached);

        return cached || networkFetch;
      })
    );
    return;
  }
});
