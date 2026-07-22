const CACHE_NAME = 'fieldtrack-v3';
const OFFLINE_URL = '/staff/home/';

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll([
        '/static/js/location_tracker.js',
        '/static/js/location_guard.js',
      ]);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter(key => key !== CACHE_NAME)
            .map(key => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Network first for API calls and any stylesheet
  if (event.request.url.includes('/attendance/') ||
      event.request.url.includes('/api/') ||
      event.request.url.includes('.css') ||
      event.request.url.includes('styles.css')) {
    return;
  }
  
  // Cache first for static files
  if (event.request.url.includes('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        return cached || fetch(event.request).then(
          (response) => {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, clone);
            });
            return response;
          }
        );
      })
    );
  }
});
