const CACHE_NAME = 'kabootar-static-v34';
const STATIC_ASSETS = [
  '/static/style.css?v=20260331j',
  '/static/settings.css?v=20260331e',
  '/static/index.js?v=20260331h',
  '/static/settings.js?v=20260331e',
  '/static/i18n/en.json',
  '/static/i18n/fa.json',
  '/static/kabootar.svg',
  '/static/t_logo.svg',
  '/static/vendor/lucide/search.svg',
  '/static/vendor/lucide/refresh-cw.svg',
  '/static/vendor/lucide/settings-2.svg',
  '/static/vendor/lucide/menu.svg',
  '/static/vendor/lucide/arrow-left.svg',
  '/static/vendor/lucide/bug.svg',
  '/static/vendor/lucide/languages.svg',
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
