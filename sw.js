// Service Worker — Agent Gmail IA PWA
const CACHE = 'agent-gmail-v1';
const ASSETS = ['/', '/manifest.json', '/icons/icon-192.png', '/icons/icon-512.png'];

// Installation — mise en cache des assets
self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE).then(function(cache) {
      return cache.addAll(ASSETS);
    })
  );
  self.skipWaiting();
});

// Activation — nettoyage des anciens caches
self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return k !== CACHE; })
            .map(function(k) { return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

// Fetch — stratégie Network First pour l'API, Cache First pour les assets
self.addEventListener('fetch', function(e) {
  var url = new URL(e.request.url);

  // API calls : toujours réseau (pas de cache)
  if (url.pathname.startsWith('/api/') || url.pathname === '/health') {
    e.respondWith(fetch(e.request));
    return;
  }

  // Assets : réseau d'abord, cache en fallback
  e.respondWith(
    fetch(e.request)
      .then(function(res) {
        var clone = res.clone();
        caches.open(CACHE).then(function(cache) { cache.put(e.request, clone); });
        return res;
      })
      .catch(function() {
        return caches.match(e.request);
      })
  );
});
