/**
 * FieldTrack Sync Engine (sync_engine.js)
 * Handles network connectivity detection, priority queue processing, exponential backoff retries,
 * bulk sync dispatcher, and reconnect session re-validation.
 */

(function (window) {
  'use strict';

  // 1. Priority Order Constants
  const PRIORITIES = {
    'check-in': 1,
    'check-out': 2,
    'gps': 3,
    'leave': 4,
    'expense': 5,
    'project-update': 6,
    'daily-report': 7,
    'photo-upload': 8
  };

  // Exponential backoff retry intervals in minutes: 1 -> 2 -> 5 -> 10 -> 20 -> 40
  const BACKOFF_MINUTES = [1, 2, 5, 10, 20, 40];
  const MAX_RETRIES = 6;

  let isSyncing = false;
  let syncTimer = null;

  // Per-module upload handler registry (TODO stubs for later steps)
  const moduleHandlers = {
    attendance: async (item) => {
      console.log('[SyncEngine] Attendance synchronized successfully with server:', item.uuid);
      return { success: true };
    },
    gps: async (item) => {
      /* TODO Step 3: GPS upload handler */
      return { success: true };
    },
    leave: async (item) => {
      /* TODO Step 4: Leave upload handler */
      return { success: true };
    },
    expense: async (item) => {
      /* TODO Step 4: Expense upload handler */
      return { success: true };
    },
    projects: async (item) => {
      /* TODO Step 5: Projects upload handler */
      return { success: true };
    },
    reports: async (item) => {
      /* TODO Step 5: Reports upload handler */
      return { success: true };
    },
    photos: async (item) => {
      /* TODO Step 5: Photos upload handler */
      return { success: true };
    }
  };

  /**
   * Calculate next retry delay in milliseconds based on retry count
   */
  function getRetryDelayMs(retryCount) {
    const idx = Math.min(retryCount, BACKOFF_MINUTES.length - 1);
    return BACKOFF_MINUTES[idx] * 60 * 1000;
  }

  /**
   * Helper to fetch CSRF token from shared CSRFProvider (HttpOnly preserved)
   */
  function getCsrfToken() {
    if (window.CSRFProvider && typeof window.CSRFProvider.getToken === 'function') {
      return window.CSRFProvider.getToken();
    }
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content && !meta.content.includes('<input')) return meta.content.trim();
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value.trim() : '';
  }

  const SyncEngine = {
    PRIORITIES: PRIORITIES,

    init: function () {
      window.addEventListener('online', () => {
        console.log('[SyncEngine] Network restored. Triggering session re-validation & queue sync...');
        this.onNetworkReconnect();
      });

      window.addEventListener('offline', () => {
        console.warn('[SyncEngine] Operating offline. Local changes will queue in IndexedDB.');
      });

      // Heartbeat sync attempt every 2 minutes if online
      setInterval(() => {
        if (navigator.onLine && !isSyncing) {
          this.processQueue();
        }
      }, 120000);

      // Initial check if online
      if (navigator.onLine) {
        setTimeout(() => this.processQueue(), 2000);
      }
    },

    /**
     * Triggered automatically when device reconnects.
     * Obtains a fresh masked token from backend before synchronizing.
     */
    onNetworkReconnect: async function () {
      if (window.CSRFProvider && typeof window.CSRFProvider.refreshToken === 'function') {
        try {
          await window.CSRFProvider.refreshToken();
        } catch (e) {
          console.warn('[SyncEngine] Network reconnect CSRF token refresh warning:', e);
        }
      }
      const sessionValid = await this.revalidateSession();
      if (sessionValid) {
        await this.processQueue();
      }
    },

    /**
     * Re-validates cached session token with server on reconnect.
     * Force logout ONLY after server explicitly confirms session invalidation (401 response).
     * Never forces logout while offline.
     */
    revalidateSession: async function () {
      if (!navigator.onLine) {
        // App stays usable offline without forced logout
        return true;
      }

      try {
        const cachedSession = await window.FieldTrackDB.getOfflineSession();
        const fetchFn = (window.CSRFProvider && typeof window.CSRFProvider.fetchWithCsrf === 'function')
          ? window.CSRFProvider.fetchWithCsrf.bind(window.CSRFProvider)
          : fetch;

        const response = await fetchFn('/api/session/validate/', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
          },
          body: JSON.stringify({
            session_key: cachedSession ? cachedSession.session_key : null
          })
        });

        if (response.status === 401) {
          const data = await response.json().catch(() => ({}));
          console.warn('[SyncEngine] Server confirmed session invalidation:', data);
          await window.FieldTrackDB.clearOfflineSession();

          // Trigger UI device notice and redirect to login
          const reason = data.reason || 'logged_in_elsewhere';
          window.location.href = `/login/?device_notice=${encodeURIComponent(reason)}`;
          return false;
        } else if (response.ok) {
          const data = await response.json();
          if (data.session && cachedSession) {
            cachedSession.last_validated = new Date().toISOString();
            await window.FieldTrackDB.saveOfflineSession(cachedSession);
          }
          return true;
        }
      } catch (err) {
        console.warn('[SyncEngine] Session re-validation network error (staying offline-active):', err);
        return true; // Keep app accessible offline if network request fails transiently
      }

      return true;
    },

    /**
     * Reads pending items from IndexedDB sync_queue, orders by priority asc, and dispatches to bulk sync endpoint stub
     */
    processQueue: async function () {
      if (isSyncing || !navigator.onLine) return;
      isSyncing = true;

      try {
        const pendingItems = await window.FieldTrackDB.getPendingSyncItems();
        if (!pendingItems || pendingItems.length === 0) {
          isSyncing = false;
          return;
        }

        // Filter items whose exponential backoff timer has elapsed, excluding permanently failed/blocked items
        const now = Date.now();
        const itemsByUuid = new Map(pendingItems.map((item) => [item.uuid, item]));
        const isItemRunnable = (item) => {
          if (item.permanent || item.status === 'failed' || item.status === 'blocked' || (item.retry_count || 0) >= MAX_RETRIES) {
            return false;
          }
          if (!item.last_attempt_time) return true;
          const nextAttemptTime = new Date(item.last_attempt_time).getTime() + getRetryDelayMs(item.retry_count);
          return now >= nextAttemptTime;
        };

        const runnableSet = new Set(pendingItems.filter(isItemRunnable).map((i) => i.uuid));
        const { ordered: orderedRunnable, cycleItems: cycleRunnable } =
          typeof window.FieldTrackDB.sortSyncItemsByDependencyAndTime === 'function'
            ? window.FieldTrackDB.sortSyncItemsByDependencyAndTime(pendingItems.filter((i) => runnableSet.has(i.uuid)))
            : { ordered: pendingItems.filter((i) => runnableSet.has(i.uuid)), cycleItems: [] };

        for (const cycItem of cycleRunnable) {
          await window.FieldTrackDB.updateSyncItem(cycItem.uuid, {
            status: 'blocked',
            blocked: true,
            permanent: true,
            last_attempt_time: new Date().toISOString(),
            last_error: 'Blocked: dependency cycle detected.'
          });
        }

        const readyItems = [];
        const blockedInPass = new Set();

        for (const item of orderedRunnable) {
          const depUuid =
            item.depends_on_sync_uuid ||
            (item.payload && (item.payload.depends_on_sync_uuid || item.payload.check_in_sync_uuid || item.payload.parent_sync_uuid));
          const hasExplicitSession = Boolean(item.payload && (item.payload.session_id || item.payload.attendance_id));

          if (depUuid && !hasExplicitSession) {
            if (blockedInPass.has(depUuid)) {
              blockedInPass.add(item.uuid);
              continue;
            }
            const parent = itemsByUuid.get(depUuid);
            if (parent) {
              if (parent.permanent || parent.status === 'failed' || parent.status === 'blocked' || (parent.retry_count || 0) >= MAX_RETRIES) {
                blockedInPass.add(item.uuid);
                await window.FieldTrackDB.updateSyncItem(item.uuid, {
                  status: 'blocked',
                  blocked: true,
                  permanent: true,
                  last_attempt_time: new Date().toISOString(),
                  last_error: 'Blocked: dependent event failed permanently.'
                });
                continue;
              }
              if (!runnableSet.has(parent.uuid)) {
                // Parent event is waiting for backoff; hold dependent item until parent succeeds
                blockedInPass.add(item.uuid);
                continue;
              }
            }
          }

          readyItems.push(item);
        }

        if (readyItems.length === 0) {
          isSyncing = false;
          return;
        }

        console.log(`[SyncEngine] Processing ${readyItems.length} queued items...`);

        // Bulk sync upload call to POST /attendance/bulk-sync/
        const fetchFn = (window.CSRFProvider && typeof window.CSRFProvider.fetchWithCsrf === 'function')
          ? window.CSRFProvider.fetchWithCsrf.bind(window.CSRFProvider)
          : fetch;

        const response = await fetchFn('/attendance/bulk-sync/', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
          },
          body: JSON.stringify({ items: readyItems })
        });

        if (response.ok) {
          const result = await response.json();
          const itemResults = result.results || [];
          const resolvedSessions = new Map();
          const syncedUuids = new Set();

          for (const item of readyItems) {
            const res = itemResults.find((r) => r.uuid === item.uuid);
            if (res && res.status === 'success') {
              syncedUuids.add(item.uuid);
              if (item.action === 'check_in' && res.data && res.data.session_id) {
                resolvedSessions.set(item.uuid, res.data.session_id);
              }
              await window.FieldTrackDB.updateSyncItem(item.uuid, {
                status: 'synced',
                synced_time: new Date().toISOString()
              });

              // Execute local module handler
              const handler = moduleHandlers[item.module];
              if (handler) await handler(item);
            } else {
              const blocked = Boolean(res && res.blocked);
              const permanent = Boolean(res && res.permanent);
              const isMissingPhoto = res && res.error && res.error.toLowerCase().includes('photo is required');
              if (blocked && !permanent) {
                // Parent check-in had a transient failure; keep dependent checkout pending without burning retries
                await window.FieldTrackDB.updateSyncItem(item.uuid, {
                  status: 'pending',
                  last_attempt_time: new Date().toISOString(),
                  last_error: res ? res.error : 'Waiting for dependent check-in'
                });
              } else if (blocked && permanent) {
                await window.FieldTrackDB.updateSyncItem(item.uuid, {
                  status: 'blocked',
                  blocked: true,
                  permanent: true,
                  last_attempt_time: new Date().toISOString(),
                  last_error: res ? res.error : 'Blocked by failed check-in'
                });
              } else {
                const newRetryCount = permanent ? MAX_RETRIES : (isMissingPhoto ? (item.retry_count || 0) : (item.retry_count || 0) + 1);
                await window.FieldTrackDB.updateSyncItem(item.uuid, {
                  status: isMissingPhoto ? 'pending' : (permanent || newRetryCount >= MAX_RETRIES ? 'failed' : 'pending'),
                  permanent: permanent,
                  retry_count: newRetryCount,
                  last_attempt_time: new Date().toISOString(),
                  last_error: res ? res.error : 'Sync failed'
                });
              }
            }
          }

          // Persist resolved server session_id onto any remaining dependent checkout items without overwriting explicit targets
          if (resolvedSessions.size > 0) {
            for (const otherItem of pendingItems) {
              if (otherItem.action === 'check_out' && !syncedUuids.has(otherItem.uuid) && otherItem.status !== 'synced') {
                const existingPayload = otherItem.payload || {};
                const hasExplicitTarget =
                  (existingPayload.session_id !== undefined && existingPayload.session_id !== null && existingPayload.session_id !== '') ||
                  (existingPayload.attendance_id !== undefined && existingPayload.attendance_id !== null && existingPayload.attendance_id !== '');
                if (hasExplicitTarget) continue;

                const depUuid =
                  otherItem.depends_on_sync_uuid ||
                  existingPayload.depends_on_sync_uuid ||
                  existingPayload.check_in_sync_uuid ||
                  existingPayload.parent_sync_uuid;
                if (depUuid && resolvedSessions.has(depUuid)) {
                  const updatedPayload = Object.assign({}, existingPayload, {
                    session_id: resolvedSessions.get(depUuid)
                  });
                  await window.FieldTrackDB.updateSyncItem(otherItem.uuid, {
                    payload: updatedPayload
                  }).catch(() => {});
                }
              }
            }
          }

          // Clean up completed items
          await window.FieldTrackDB.removeCompletedSyncItems();
        } else {
          console.warn('[SyncEngine] Bulk sync endpoint returned status:', response.status);
        }
      } catch (err) {
        console.error('[SyncEngine] Queue sync error:', err);
      } finally {
        isSyncing = false;
      }
    },

    registerModuleHandler: function (moduleName, handlerFn) {
      moduleHandlers[moduleName] = handlerFn;
    },

    cacheRbacSnapshot: function (permissionsMap) {
      try {
        localStorage.setItem('ft_rbac_snapshot', JSON.stringify({
          permissions: permissionsMap || {},
          timestamp: new Date().toISOString()
        }));
      } catch (e) {
        console.warn('[SyncEngine] Failed to save RBAC snapshot:', e);
      }
    },

    hasOfflinePermission: function (codename) {
      try {
        const snapshot = JSON.parse(localStorage.getItem('ft_rbac_snapshot') || '{}');
        if (!snapshot.permissions) return false;
        const perm = snapshot.permissions[codename];
        return perm && (perm.granted === true || perm === true);
      } catch (e) {
        return false;
      }
    }
  };

  window.SyncEngine = SyncEngine;

  document.addEventListener('DOMContentLoaded', () => {
    SyncEngine.init();
  });
})(window);
