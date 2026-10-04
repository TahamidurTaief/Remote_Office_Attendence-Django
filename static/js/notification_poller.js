(function () {
  const currentScript = document.currentScript;

  function initNotificationPoller() {
    const configEl = currentScript || document.querySelector('script[data-feed-url]') || document.querySelector('script[src*="notification_poller.js"]');
    if (!configEl) return;

    const feedUrl = configEl.getAttribute('data-feed-url');
    const pushConfigUrl = configEl.getAttribute('data-push-config-url') || '/notifications/push/config/';
    const pushSubscriptionUrl = configEl.getAttribute('data-push-subscription-url') || '/notifications/push/subscription/';
    const userId = configEl.getAttribute('data-user-id');
    if (!feedUrl || !userId) return;

    const storageKey = `ft_notif_cursor_${userId}`;
    const permPromptedKey = `ft_notif_perm_prompted_${userId}`;
    let inFlight = false;
    let pollInterval = null;
    let pushSyncInProgress = false;

    function urlBase64ToUint8Array(base64String) {
      try {
        const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
        const base64 = (base64String + padding)
          .replace(/-/g, '+')
          .replace(/_/g, '/');
        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);
        for (let i = 0; i < rawData.length; ++i) {
          outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
      } catch (e) {
        return null;
      }
    }

    async function syncWebPushSubscription() {
      if (pushSyncInProgress) return;
      if (typeof window === 'undefined' || !('Notification' in window) || Notification.permission !== 'granted') {
        return;
      }
      if (!('serviceWorker' in navigator) || typeof window.PushManager === 'undefined') {
        return;
      }
      if (!pushConfigUrl || !pushSubscriptionUrl) {
        return;
      }

      pushSyncInProgress = true;
      try {
        const configRes = await fetch(pushConfigUrl, {
          method: 'GET',
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          },
          cache: 'no-store'
        });

        if (!configRes.ok) return;
        const config = await configRes.json();
        if (!config || !config.public_key || typeof config.public_key !== 'string' || !config.public_key.trim()) {
          return;
        }

        const registration = await navigator.serviceWorker.ready;
        if (!registration || !registration.pushManager) return;

        let subscription = await registration.pushManager.getSubscription();
        if (!subscription) {
          const applicationServerKey = urlBase64ToUint8Array(config.public_key.trim());
          if (!applicationServerKey) return;
          subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: applicationServerKey
          });
        }

        if (!subscription) return;

        const subJson = typeof subscription.toJSON === 'function' ? subscription.toJSON() : {};
        const endpoint = subJson.endpoint || subscription.endpoint;
        if (!endpoint) return;

        let p256dh = subJson.keys && subJson.keys.p256dh;
        let auth = subJson.keys && subJson.keys.auth;

        if (!p256dh && typeof subscription.getKey === 'function') {
          const rawKey = subscription.getKey('p256dh');
          if (rawKey) {
            p256dh = btoa(String.fromCharCode.apply(null, new Uint8Array(rawKey)));
          }
        }
        if (!auth && typeof subscription.getKey === 'function') {
          const rawAuth = subscription.getKey('auth');
          if (rawAuth) {
            auth = btoa(String.fromCharCode.apply(null, new Uint8Array(rawAuth)));
          }
        }

        if (!p256dh || !auth) return;

        const csrfToken = config.csrf_token || '';

        await fetch(pushSubscriptionUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-CSRFToken': csrfToken,
            'X-Requested-With': 'XMLHttpRequest'
          },
          cache: 'no-store',
          body: JSON.stringify({
            endpoint: endpoint,
            keys: {
              p256dh: p256dh,
              auth: auth
            }
          })
        });
      } catch (err) {
        // Silently recover if browser push fails, keeping foreground notifications working
      } finally {
        pushSyncInProgress = false;
      }
    }

    function isSafeSameOriginPath(url) {
      if (typeof url !== 'string') return false;
      const trimmed = url.trim();
      if (!trimmed.startsWith('/') || trimmed.startsWith('//') || trimmed.includes('\\')) {
        return false;
      }
      try {
        const baseOrigin = typeof window !== 'undefined' && window.location ? window.location.origin : 'http://localhost';
        const parsed = new URL(trimmed, baseOrigin);
        return parsed.origin === baseOrigin && parsed.pathname.startsWith('/');
      } catch (e) {
        return false;
      }
    }

    function requestDeviceNotificationPermission() {
      if (typeof window === 'undefined' || !('Notification' in window)) return;

      if (Notification.permission === 'granted') {
        syncWebPushSubscription();
        return;
      }

      if (Notification.permission !== 'default') return;
      if (localStorage.getItem(permPromptedKey)) return;

      localStorage.setItem(permPromptedKey, '1');
      try {
        const onPermResult = (permission) => {
          if (permission === 'granted') {
            syncWebPushSubscription();
          }
        };
        const res = Notification.requestPermission(onPermResult);
        if (res && typeof res.then === 'function') {
          res.then(onPermResult).catch(() => {});
        }
      } catch (e) {
        // Silently recover if browser throws
      }
    }

    function showDeviceNotification(item) {
      if (typeof window === 'undefined' || !('Notification' in window) || Notification.permission !== 'granted') {
        return;
      }
      if (!('serviceWorker' in navigator) || typeof navigator.serviceWorker.getRegistration !== 'function') {
        return;
      }

      navigator.serviceWorker.getRegistration().then((registration) => {
        if (!registration || typeof registration.showNotification !== 'function') return;

        const safeRedirect = isSafeSameOriginPath(item.redirect_url) ? item.redirect_url : '/notifications/';
        const title = item.title || 'FieldTrack Alert';
        const options = {
          body: item.message || item.title || '',
          icon: '/static/icons/icon.png',
          tag: `ft-notif-${item.id}`,
          data: {
            id: item.id,
            notification_id: item.id,
            url: safeRedirect
          }
        };

        registration.showNotification(title, options).catch(() => {});
      }).catch(() => {});
    }

    async function poll() {
      if (inFlight) return;
      if (document.hidden || !navigator.onLine) return;

      inFlight = true;
      const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      let timeoutId = null;

      if (controller) {
        timeoutId = setTimeout(() => {
          controller.abort();
        }, 10000);
      }

      try {
        const storedCursor = localStorage.getItem(storageKey);
        let url = feedUrl;
        if (storedCursor !== null && storedCursor !== '' && !isNaN(storedCursor)) {
          const sep = url.includes('?') ? '&' : '?';
          url = `${url}${sep}after=${encodeURIComponent(storedCursor)}`;
        }

        const fetchOptions = {
          method: 'GET',
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          },
          cache: 'no-store'
        };
        if (controller) {
          fetchOptions.signal = controller.signal;
        }

        const res = await fetch(url, fetchOptions);

        if (res.status === 400) {
          // If server rejects a corrupted cursor, reset cursor for clean bootstrap
          localStorage.removeItem(storageKey);
        } else if (res.ok) {
          const data = await res.json();
          if (data && typeof data.next_cursor !== 'undefined' && data.next_cursor !== null) {
            localStorage.setItem(storageKey, String(data.next_cursor));
          }

          if (Array.isArray(data.notifications)) {
            data.notifications.forEach((item) => {
              const text = item.message || item.title || 'New notification';
              const redirect = isSafeSameOriginPath(item.redirect_url) ? item.redirect_url : null;
              if (typeof window.showToast === 'function') {
                window.showToast(text, 'info', redirect);
              }

              const isScheduleOrTask = item.type === 'schedule_event' ||
                (typeof item.type === 'string' && (item.type.startsWith('task') || item.type === 'task'));

              if (isScheduleOrTask) {
                showDeviceNotification(item);
              }
            });
          }
        }
      } catch (err) {
        // Silently recover from network errors without infinite loading
      } finally {
        if (timeoutId) {
          clearTimeout(timeoutId);
        }
        inFlight = false;
      }
    }

    function startInterval() {
      if (pollInterval) clearInterval(pollInterval);
      pollInterval = setInterval(poll, 25000);
    }

    function resumeImmediately() {
      if (!document.hidden && navigator.onLine) {
        poll();
      }
    }

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        resumeImmediately();
      }
    });

    window.addEventListener('online', () => {
      resumeImmediately();
    });

    document.addEventListener('click', (event) => {
      const trigger = event.target && event.target.closest && event.target.closest('[data-notification-permission-trigger]');
      if (trigger) {
        requestDeviceNotificationPermission();
      }
    });

    // Initial bootstrap / poll and schedule interval
    poll();
    startInterval();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initNotificationPoller);
  } else {
    initNotificationPoller();
  }
})();
