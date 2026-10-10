/**
 * FieldTrack Service Worker (sw.js)
 * Caches ONLY static assets (HTML shell / CSS / JS / fonts / icons / images).
 * Business data and dynamic API endpoints are NEVER cached here.
 */

const CACHE_NAME = 'fieldtrack-static-v6';

const STATIC_ASSETS = [
  '/',
  '/static/css/dist/styles.css',
  '/static/vendor/htmx.min.js',
  '/static/vendor/alpine-collapse.min.js',
  '/static/vendor/alpine.min.js',
  '/static/vendor/lucide.min.js',
  '/static/vendor/chart.min.js',
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
  // 0. Only handle HTTP/HTTPS GET requests from same origin
  if (!event.request || !event.request.url || event.request.method !== 'GET') {
    return;
  }

  const reqUrl = event.request.url;
  if (!reqUrl.startsWith('http://') && !reqUrl.startsWith('https://')) {
    // Ignore non-http(s) schemes like chrome-extension://, moz-extension://, blob:, data:
    return;
  }

  let url;
  try {
    url = new URL(reqUrl);
  } catch (e) {
    return;
  }

  // Strictly ignore cross-origin and extension requests
  if (url.origin !== self.location.origin) {
    return;
  }

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
    url.pathname.startsWith('/backups/')
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
              if (
                networkResponse &&
                networkResponse.status === 200 &&
                (event.request.url.startsWith('http://') || event.request.url.startsWith('https://'))
              ) {
                caches.open(CACHE_NAME).then((cache) => {
                  cache.put(event.request, networkResponse).catch(() => {});
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
            networkResponse.type === 'basic' &&
            (event.request.url.startsWith('http://') || event.request.url.startsWith('https://'))
          ) {
            const responseToCache = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseToCache).catch(() => {});
            });
          }
          return networkResponse;
        });
      })
    );
  }
});

function isSafeSameOriginPath(url) {
  if (typeof url !== 'string') return false;
  const trimmed = url.trim();
  if (!trimmed.startsWith('/') || trimmed.startsWith('//') || trimmed.includes('\\')) {
    return false;
  }
  try {
    const baseOrigin = self.location ? self.location.origin : 'http://localhost';
    const parsed = new URL(trimmed, baseOrigin);
    return parsed.origin === baseOrigin && parsed.pathname.startsWith('/');
  } catch (e) {
    return false;
  }
}

self.addEventListener('push', (event) => {
  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      try {
        data = { title: event.data.text() };
      } catch (err) {
        data = {};
      }
    }
  }

  const title = data.title || 'FieldTrack Alert';
  const targetUrl = isSafeSameOriginPath(data.url || data.redirect_url) ? (data.url || data.redirect_url) : '/notifications/';
  const notifId = data.id || data.notification_id || 'push';

  const options = {
    body: data.body || data.message || '',
    icon: data.icon || '/static/icons/icon.png',
    badge: '/static/icons/icon.png',
    tag: data.tag || `ft-notif-${notifId}`,
    data: {
      id: notifId,
      notification_id: notifId,
      url: targetUrl
    }
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const rawUrl = event.notification.data && event.notification.data.url;
  const targetPath = isSafeSameOriginPath(rawUrl) ? rawUrl : '/notifications/';
  const targetUrl = new URL(targetPath, self.location.origin).href;

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url && 'focus' in client) {
          try {
            const clientOrigin = new URL(client.url).origin;
            if (clientOrigin === self.location.origin) {
              if ('navigate' in client && client.url !== targetUrl) {
                return client.navigate(targetUrl).then((navigated) => {
                  return (navigated || client).focus();
                });
              }
              return client.focus();
            }
          } catch (e) {
            // Keep inspecting remaining clients
          }
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
