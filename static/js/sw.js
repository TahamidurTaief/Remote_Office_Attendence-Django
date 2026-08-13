/**
 * FieldTrack Service Worker (sw.js)
 * Caches ONLY static assets (HTML shell / CSS / JS / fonts / icons / images).
 * Business data and dynamic API endpoints are NEVER cached here.
 */

const CACHE_NAME = 'fieldtrack-static-v4';

const STATIC_ASSETS = [
  '/',
  '/static/css/dist/styles.css',
  '/static/js/location_tracker.js',
  '/static/js/location_guard.js',
  '/static/js/offline/db.js',
  '/static/js/offline/sync_engine.js',
  '/static/icons/icon-72.png',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/main_logo.jpg',
  '/manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS).catch((err) => {
        console.warn('[SW] Cache addAll warning:', err);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // 1. NEVER cache business data, API calls, or sensitive documents/media
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/attendance/') ||
    url.pathname.startsWith('/leave/') ||
    url.pathname.startsWith('/expense/') ||
    url.pathname.startsWith('/staff/') ||
    url.pathname.startsWith('/admin-panel/') ||
    url.pathname.startsWith('/projects/') ||
    url.pathname.startsWith('/schedule/') ||
    url.pathname.startsWith('/notifications/') ||
    url.pathname.startsWith('/employees/') ||
    url.pathname.startsWith('/branches/') ||
    url.pathname.startsWith('/reports/') ||
    url.pathname.startsWith('/media/') ||     // Employee docs, NID, salary PDFs — never cache
    url.pathname.startsWith('/backups/') ||
    event.request.method !== 'GET'
  ) {
    // Network-only for all business logic & sensitive data
    return;
  }

  // 2. Cache-first strategy ONLY for static assets (.js, .css, images, fonts, icons, static files)
  if (
    url.pathname.startsWith('/static/') ||
    url.pathname.endsWith('.png') ||
    url.pathname.endsWith('.jpg') ||
    url.pathname.endsWith('.svg') ||
    url.pathname.endsWith('.ico') ||
    url.pathname === '/manifest.json'
  ) {
    event.respondWith(
      caches.match(event.request).then((cachedResponse) => {
        if (cachedResponse) {
          // Return cached asset and update cache in background
          fetch(event.request)
            .then((networkResponse) => {
              if (networkResponse && networkResponse.status === 200) {
                caches.open(CACHE_NAME).then((cache) => {
                  cache.put(event.request, networkResponse);
                });
              }
            })
            .catch(() => {});
          return cachedResponse;
        }

        return fetch(event.request).then((networkResponse) => {
          if (
            networkResponse &&
            networkResponse.status === 200 &&
            networkResponse.type === 'basic'
          ) {
            const responseToCache = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseToCache);
            });
          }
          return networkResponse;
        });
      })
    );
  }
});
