(function () {
  const currentScript = document.currentScript;

  function initNotificationPoller() {
    const configEl = currentScript || document.querySelector('script[data-feed-url]') || document.querySelector('script[src*="notification_poller.js"]');
    if (!configEl) return;

    const feedUrl = configEl.getAttribute('data-feed-url');
    const userId = configEl.getAttribute('data-user-id');
    if (!feedUrl || !userId) return;

    const storageKey = `ft_notif_cursor_${userId}`;
    let inFlight = false;
    let pollInterval = null;

    function isSafeSameOriginPath(url) {
      return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//') && !url.includes('\\');
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
