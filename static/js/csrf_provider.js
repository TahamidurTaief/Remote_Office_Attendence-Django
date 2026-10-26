/**
 * CSRF Provider (csrf_provider.js)
 * FieldTrack Secure Shared Masked-Token Provider & Resilient Fetch Client
 *
 * Designed to preserve HttpOnly cookies while providing:
 * 1. Synchronized in-memory & DOM masked-token management backed by Django get_token()
 * 2. Same-origin authenticated non-cacheable token refresh (/attendance/csrf-token/)
 * 3. Consistent credentials & X-CSRFToken dispatch with FormData.set('csrfmiddlewaretoken', ...)
 * 4. Controlled single-retry on explicit CSRF rejection (preserving photo file & idempotency key)
 * 5. Expired-session / HTML login redirect detection (no JSON parse crashes or infinite loops)
 * 6. Automatic HTMX request configuration hook (htmx:configRequest)
 */

(function (window) {
  'use strict';

  let inMemoryToken = null;
  let isRefreshing = false;
  let refreshPromise = null;

  const CSRFProvider = {
    /**
     * Retrieves the current masked CSRF token.
     * Checks in-memory cache, meta tag, or DOM hidden input.
     * Does NOT read document.cookie (HttpOnly preserved).
     */
    getToken: function () {
      if (inMemoryToken) {
        return inMemoryToken;
      }

      // Check meta tag
      const meta = document.querySelector('meta[name="csrf-token"]');
      if (meta && meta.content && meta.content !== 'NOTPROVIDED' && !meta.content.includes('<input')) {
        inMemoryToken = meta.content.trim();
        return inMemoryToken;
      }

      // Check form input
      const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
      if (input && input.value) {
        inMemoryToken = input.value.trim();
        return inMemoryToken;
      }

      return '';
    },

    /**
     * Updates the in-memory token, meta tag, and all form inputs on the page.
     */
    setToken: function (newToken) {
      if (!newToken || typeof newToken !== 'string') return;
      const cleanToken = newToken.trim();
      inMemoryToken = cleanToken;

      // Update meta tag
      let meta = document.querySelector('meta[name="csrf-token"]');
      if (!meta) {
        meta = document.createElement('meta');
        meta.setAttribute('name', 'csrf-token');
        document.head.appendChild(meta);
      }
      meta.content = cleanToken;

      // Synchronize all existing form hidden inputs in the DOM
      const inputs = document.querySelectorAll('input[name="csrfmiddlewaretoken"]');
      inputs.forEach((input) => {
        input.value = cleanToken;
      });
    },

    /**
     * Refreshes masked CSRF token from same-origin authenticated endpoint.
     * Uses non-cacheable GET request. Deduplicates concurrent refresh calls.
     */
    refreshToken: async function () {
      if (isRefreshing && refreshPromise) {
        return refreshPromise;
      }

      isRefreshing = true;
      refreshPromise = (async () => {
        try {
          const response = await fetch('/attendance/csrf-token/', {
            method: 'GET',
            headers: {
              'Accept': 'application/json',
              'X-Requested-With': 'XMLHttpRequest'
            },
            credentials: 'same-origin',
            cache: 'no-store'
          });

          // Edge case: Expired session returns HTML login redirect instead of JSON
          if (response.redirected && response.url && response.url.includes('/login')) {
            console.warn('[CSRFProvider] Session expired (redirected to login).');
            return { success: false, reason: 'session_expired', redirectUrl: response.url };
          }

          const contentType = response.headers.get('content-type') || '';
          if (!contentType.includes('application/json')) {
            const bodyText = await response.text();
            if (bodyText.includes('<!DOCTYPE') || bodyText.includes('<html') || bodyText.includes('login')) {
              console.warn('[CSRFProvider] Session expired (received HTML login page).');
              return { success: false, reason: 'session_expired' };
            }
            return { success: false, reason: 'unexpected_content_type', raw: bodyText };
          }

          if (response.status === 401 || response.status === 403) {
            return { success: false, reason: 'unauthenticated', status: response.status };
          }

          if (response.ok) {
            const data = await response.json();
            if (data.csrf_token) {
              this.setToken(data.csrf_token);
              return { success: true, token: data.csrf_token };
            }
          }

          return { success: false, reason: 'refresh_failed_status_' + response.status };
        } catch (err) {
          console.error('[CSRFProvider] Network failure refreshing CSRF token:', err);
          return { success: false, error: err };
        } finally {
          isRefreshing = false;
          refreshPromise = null;
        }
      })();

      return refreshPromise;
    },

    /**
     * Synchronizes token into FormData replacing any stale csrfmiddlewaretoken.
     * Django views may prefer POST form token over X-CSRFToken header.
     */
    prepareFormData: function (formData, token) {
      const activeToken = token || this.getToken();
      if (formData instanceof FormData && activeToken) {
        formData.set('csrfmiddlewaretoken', activeToken);
      }
      return formData;
    },

    /**
     * Determines if a response is an explicit CSRF rejection.
     */
    isCsrfRejection: function (status, bodyData, rawText) {
      if (status !== 403) return false;
      if (bodyData && bodyData.code === 'CSRF_FAILURE') return true;
      if (bodyData && typeof bodyData.error === 'string' && bodyData.error.toLowerCase().includes('csrf')) return true;
      if (typeof rawText === 'string' && rawText.includes('CSRF')) return true;
      return false;
    },

    /**
     * Resilient authenticated fetch with consistent X-CSRFToken and credentials.
     * Retries ONCE only upon an explicit CSRF rejection, preserving photo and payload.
     * Does NOT blindly retry general network failures or 5xx/400 errors.
     */
    fetchWithCsrf: async function (url, options = {}, isRetry = false) {
      const opts = Object.assign({}, options);
      opts.credentials = opts.credentials || 'same-origin';
      opts.headers = Object.assign({}, opts.headers || {});

      let token = this.getToken();
      if (token) {
        opts.headers['X-CSRFToken'] = token;
      }

      // If body is FormData, replace stale token with fresh token
      if (opts.body instanceof FormData) {
        this.prepareFormData(opts.body, token);
      }

      let response;
      try {
        response = await fetch(url, opts);
      } catch (err) {
        // Blind network errors are NEVER retried
        throw err;
      }

      // Check for explicit CSRF rejection (403 + CSRF code/reason)
      if (response.status === 403 && !isRetry) {
        let isCsrf = false;
        let responseJson = null;
        let responseText = null;

        const cloned = response.clone();
        try {
          const ct = cloned.headers.get('content-type') || '';
          if (ct.includes('application/json')) {
            responseJson = await cloned.json();
            isCsrf = this.isCsrfRejection(403, responseJson, null);
          } else {
            responseText = await cloned.text();
            isCsrf = this.isCsrfRejection(403, null, responseText);
          }
        } catch (parseErr) {
          isCsrf = false;
        }

        if (isCsrf) {
          console.warn('[CSRFProvider] Explicit CSRF failure detected. Attempting single token refresh & retry...');
          const refreshResult = await this.refreshToken();
          if (refreshResult && refreshResult.success && refreshResult.token) {
            const newToken = refreshResult.token;
            opts.headers['X-CSRFToken'] = newToken;
            if (opts.body instanceof FormData) {
              opts.body.set('csrfmiddlewaretoken', newToken);
            }
            // Retry once only
            return this.fetchWithCsrf(url, opts, true);
          } else {
            console.warn('[CSRFProvider] Token refresh failed:', refreshResult);
          }
        }
      }

      return response;
    }
  };

  // ── Global HTMX integration ──────────────────────────────────────────
  document.addEventListener('htmx:configRequest', function (evt) {
    const token = CSRFProvider.getToken();
    if (token) {
      evt.detail.headers['X-CSRFToken'] = token;
      if (evt.detail.parameters) {
        evt.detail.parameters['csrfmiddlewaretoken'] = token;
      }
    }
    evt.detail.withCredentials = true;
  });

  // Handle HTMX 403 CSRF responses
  document.addEventListener('htmx:responseError', async function (evt) {
    if (evt.detail && evt.detail.xhr && evt.detail.xhr.status === 403) {
      try {
        const text = evt.detail.xhr.responseText || '';
        if (text.includes('CSRF_FAILURE') || text.includes('CSRF verification failed')) {
          console.warn('[CSRFProvider] HTMX encountered CSRF rejection. Refreshing token...');
          await CSRFProvider.refreshToken();
        }
      } catch (e) {}
    }
  });

  window.CSRFProvider = CSRFProvider;

  // Auto-seed token from meta tag on load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => CSRFProvider.getToken());
  } else {
    CSRFProvider.getToken();
  }
})(window);
