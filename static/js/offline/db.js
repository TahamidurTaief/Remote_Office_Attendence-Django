/**
 * FieldTrack IndexedDB Layer (db.js)
 * Manages local persistent storage for offline queuing, offline business data placeholders,
 * and offline-aware session caching.
 */

(function (window) {
  'use strict';

  const DB_NAME = 'fieldtrack_db';
  const DB_VERSION = 1;

  let dbInstance = null;

  function openDB() {
    if (dbInstance) return Promise.resolve(dbInstance);

    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);

      request.onupgradeneeded = (event) => {
        const db = event.target.result;

        // 1. Primary Sync Queue Store
        if (!db.objectStoreNames.contains('sync_queue')) {
          const syncStore = db.createObjectStore('sync_queue', { keyPath: 'uuid' });
          syncStore.createIndex('status', 'status', { unique: false });
          syncStore.createIndex('priority', 'priority', { unique: false });
          syncStore.createIndex('created_time', 'created_time', { unique: false });
        }

        // 2. Business Data Placeholder Stores (Structure only for future steps)
        const placeholderStores = [
          'attendance',
          'gps',
          'leave',
          'expense',
          'reports',
          'photos',
          'auth_session'
        ];

        placeholderStores.forEach((storeName) => {
          if (!db.objectStoreNames.contains(storeName)) {
            db.createObjectStore(storeName, { keyPath: 'id' });
          }
        });
      };

      request.onsuccess = (event) => {
        dbInstance = event.target.result;
        resolve(dbInstance);
      };

      request.onerror = (event) => {
        console.error('IndexedDB open error:', event.target.error);
        reject(event.target.error);
      };
    });
  }

  function generateUUID() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function getItemEventTimeMs(item) {
    const rawTime =
      (item.payload && item.payload.client_event_time) ||
      item.client_event_time ||
      item.created_time;
    const parsed = rawTime ? Date.parse(rawTime) : NaN;
    if (!Number.isNaN(parsed)) return parsed;
    const createdParsed = item.created_time ? Date.parse(item.created_time) : NaN;
    return Number.isNaN(createdParsed) ? 0 : createdParsed;
  }

  function getItemTieRank(item) {
    const action = item.action || item.action_type;
    if (action === 'check_in') return 0;
    if (action === 'field_visit') return 1;
    if (action === 'check_out') return 2;
    return typeof item.priority === 'number' ? item.priority + 3 : 8;
  }

  function sortSyncItemsByDependencyAndTime(items) {
    const n = items.length;
    const uuidToIdx = new Map();
    items.forEach((item, idx) => {
      if (item && item.uuid) {
        uuidToIdx.set(String(item.uuid), idx);
      }
    });

    const children = Array.from({ length: n }, () => []);
    const inDegree = new Array(n).fill(0);
    const meta = items.map((item, idx) => ({
      idx,
      timeMs: getItemEventTimeMs(item),
      tieRank: getItemTieRank(item),
      createdMs: item.created_time ? (Date.parse(item.created_time) || 0) : 0
    }));

    items.forEach((item, idx) => {
      const dep =
        item.depends_on_sync_uuid ||
        (item.payload && (item.payload.depends_on_sync_uuid || item.payload.check_in_sync_uuid || item.payload.parent_sync_uuid));
      if (!dep) return;
      const depStr = String(dep);
      if (item.uuid && depStr === String(item.uuid)) {
        inDegree[idx] += 1; // Self-cycle
        return;
      }
      if (uuidToIdx.has(depStr)) {
        const parentIdx = uuidToIdx.get(depStr);
        children[parentIdx].push(idx);
        inDegree[idx] += 1;
      }
    });

    const compareIdx = (aIdx, bIdx) => {
      const a = meta[aIdx];
      const b = meta[bIdx];
      if (a.timeMs !== b.timeMs) return a.timeMs - b.timeMs;
      if (a.tieRank !== b.tieRank) return a.tieRank - b.tieRank;
      if (a.createdMs !== b.createdMs) return a.createdMs - b.createdMs;
      return a.idx - b.idx;
    };

    const ready = [];
    for (let i = 0; i < n; i++) {
      if (inDegree[i] === 0) ready.push(i);
    }
    ready.sort(compareIdx);

    const ordered = [];
    const visited = new Set();
    while (ready.length > 0) {
      const idx = ready.shift();
      visited.add(idx);
      ordered.push(items[idx]);
      let added = false;
      for (const childIdx of children[idx]) {
        inDegree[childIdx] -= 1;
        if (inDegree[childIdx] === 0) {
          ready.push(childIdx);
          added = true;
        }
      }
      if (added) ready.sort(compareIdx);
    }

    const cycleItems = [];
    for (let i = 0; i < n; i++) {
      if (!visited.has(i)) cycleItems.push(items[i]);
    }

    return { ordered, cycleItems };
  }

  const FieldTrackDB = {
    open: openDB,
    sortSyncItemsByDependencyAndTime: sortSyncItemsByDependencyAndTime,

    /**
     * Add a record to sync_queue
     */
    enqueueSyncItem: async function (moduleName, action, payload, priority = 5, forcedUuid = null) {
      const db = await openDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('sync_queue', 'readwrite');
        const store = tx.objectStore('sync_queue');

        const safePayload = Object.assign({}, payload || {});
        delete safePayload.csrfmiddlewaretoken;
        delete safePayload.csrf_token;
        delete safePayload.csrftoken;

        const record = {
          uuid: forcedUuid || safePayload.sync_uuid || generateUUID(),
          module: moduleName,
          action: action,
          payload: safePayload,
          depends_on_sync_uuid: safePayload.depends_on_sync_uuid || null,
          priority: priority,
          status: 'pending',
          retry_count: 0,
          created_time: new Date().toISOString(),
          synced_time: null
        };

        const req = store.add(record);
        req.onsuccess = () => resolve(record);
        req.onerror = (e) => reject(e.target.error);
      });
    },

    /**
     * Get pending sync queue items sorted by priority ascending, created_time ascending
     */
    getPendingSyncItems: async function () {
      const db = await openDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('sync_queue', 'readonly');
        const store = tx.objectStore('sync_queue');
        const req = store.getAll();

        req.onsuccess = () => {
          const all = req.result || [];
          const pending = all.filter(
            (item) => item.status === 'pending' || item.status === 'failed' || item.status === 'blocked'
          );
          const { ordered, cycleItems } = sortSyncItemsByDependencyAndTime(pending);
          resolve(ordered.concat(cycleItems));
        };

        req.onerror = (e) => reject(e.target.error);
      });
    },

    /**
     * Update item status in sync_queue
     */
    updateSyncItem: async function (uuid, updates) {
      const db = await openDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('sync_queue', 'readwrite');
        const store = tx.objectStore('sync_queue');
        const getReq = store.get(uuid);

        getReq.onsuccess = () => {
          const item = getReq.result;
          if (!item) {
            return reject(new Error('Sync item not found: ' + uuid));
          }
          Object.assign(item, updates);
          const putReq = store.put(item);
          putReq.onsuccess = () => resolve(item);
          putReq.onerror = (e) => reject(e.target.error);
        };

        getReq.onerror = (e) => reject(e.target.error);
      });
    },

    /**
     * Get a single item from sync_queue by uuid
     */
    getSyncItem: async function (uuid) {
      const db = await openDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('sync_queue', 'readonly');
        const store = tx.objectStore('sync_queue');
        const req = store.get(uuid);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = (e) => reject(e.target.error);
      });
    },

    /**
     * Get blocked items from sync_queue
     */
    getBlockedSyncItems: async function () {
      const pending = await this.getPendingSyncItems();
      return pending.filter((item) => item.status === 'blocked' || item.blocked === true);
    },

    /**
     * Suppress local missing-checkout reminders when a checkout is already queued locally.
     * Local-only indicator ("Sync pending"); does not claim another device or server knows unuploaded state.
     */
    getReminderSuppressionState: async function (sessionId = null, checkInSyncUuid = null) {
      const pending = await this.getPendingSyncItems();
      const hasQueuedCheckout = pending.some((item) => {
        if (item.action !== 'check_out' || item.status === 'synced') return false;
        const p = item.payload || {};
        if (sessionId !== null && sessionId !== undefined && String(p.session_id || p.attendance_id || '') === String(sessionId)) {
          return true;
        }
        const dep = item.depends_on_sync_uuid || p.depends_on_sync_uuid || p.check_in_sync_uuid || p.parent_sync_uuid;
        if (checkInSyncUuid && dep && String(dep) === String(checkInSyncUuid)) {
          return true;
        }
        return !sessionId && !checkInSyncUuid;
      });
      return {
        syncPending: hasQueuedCheckout,
        suppressLocalReminder: hasQueuedCheckout,
        label: hasQueuedCheckout ? 'Sync pending' : null
      };
    },

    /**
     * Get the latest unresolved offline check_in item that has no dependent check_out queued yet
     */
    getActiveOfflineCheckIn: async function () {
      const pending = await this.getPendingSyncItems();
      const dependentCheckInUuids = new Set();
      pending.forEach((item) => {
        if (item.action === 'check_out') {
          const dep = item.depends_on_sync_uuid || (item.payload && item.payload.depends_on_sync_uuid);
          if (dep) dependentCheckInUuids.add(dep);
        }
      });
      const openCheckIns = pending.filter(
        (item) =>
          item.action === 'check_in' &&
          item.status === 'pending' &&
          !item.permanent &&
          !dependentCheckInUuids.has(item.uuid)
      );
      return openCheckIns.length ? openCheckIns[openCheckIns.length - 1] : null;
    },

    /**
     * Remove completed synced records from sync_queue
     */
    removeCompletedSyncItems: async function () {
      const db = await openDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('sync_queue', 'readwrite');
        const store = tx.objectStore('sync_queue');
        const req = store.getAll();

        req.onsuccess = () => {
          const all = req.result || [];
          all.forEach((item) => {
            if (item.status === 'synced') {
              store.delete(item.uuid);
            }
          });
          resolve();
        };

        req.onerror = (e) => reject(e.target.error);
      });
    },

    /**
     * Offline-aware session caching (in IndexedDB auth_session store, not localStorage)
     */
    saveOfflineSession: async function (sessionData) {
      const db = await openDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('auth_session', 'readwrite');
        const store = tx.objectStore('auth_session');
        const record = {
          id: 'current_session',
          user_id: sessionData.user_id,
          email: sessionData.email,
          role: sessionData.role,
          session_key: sessionData.session_key,
          device_id: sessionData.device_id,
          last_validated: new Date().toISOString()
        };
        const req = store.put(record);
        req.onsuccess = () => resolve(record);
        req.onerror = (e) => reject(e.target.error);
      });
    },

    getOfflineSession: async function () {
      const db = await openDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('auth_session', 'readonly');
        const store = tx.objectStore('auth_session');
        const req = store.get('current_session');
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = (e) => reject(e.target.error);
      });
    },

    clearOfflineSession: async function () {
      const db = await openDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('auth_session', 'readwrite');
        const store = tx.objectStore('auth_session');
        const req = store.delete('current_session');
        req.onsuccess = () => resolve();
        req.onerror = (e) => reject(e.target.error);
      });
    }
  };

  window.FieldTrackDB = FieldTrackDB;

  window.queueOfflineAction = async function (data) {
    const action = data.action;
    const priority = action === 'check_in' ? 1 : (action === 'check_out' ? 2 : (action === 'field_visit' ? 1 : 5));
    const payload = { ...data };
    const uuid = data.sync_uuid || generateUUID();
    delete payload.sync_uuid;
    delete payload.csrfmiddlewaretoken;
    delete payload.csrf_token;
    delete payload.csrftoken;
    if (action === 'check_out' && !payload.session_id && !payload.depends_on_sync_uuid) {
      const activeOfflineCi = await FieldTrackDB.getActiveOfflineCheckIn();
      if (activeOfflineCi) {
        payload.depends_on_sync_uuid = activeOfflineCi.uuid;
      }
    }
    return FieldTrackDB.enqueueSyncItem('attendance', action, payload, priority, uuid);
  };

  // Auto-init database on load
  document.addEventListener('DOMContentLoaded', () => {
    FieldTrackDB.open().catch((err) =>
      console.warn('FieldTrackDB auto-init failed:', err)
    );
  });
})(window);
