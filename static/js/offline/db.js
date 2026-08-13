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

  const FieldTrackDB = {
    open: openDB,

    /**
     * Add a record to sync_queue
     */
    enqueueSyncItem: async function (moduleName, action, payload, priority = 5, forcedUuid = null) {
      const db = await openDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('sync_queue', 'readwrite');
        const store = tx.objectStore('sync_queue');

        const record = {
          uuid: forcedUuid || payload.sync_uuid || generateUUID(),
          module: moduleName,
          action: action,
          payload: payload || {},
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
            (item) => item.status === 'pending' || item.status === 'failed'
          );
          // Sort by priority asc (1 is highest), then created_time asc
          pending.sort((a, b) => {
            if (a.priority !== b.priority) return a.priority - b.priority;
            return new Date(a.created_time) - new Date(b.created_time);
          });
          resolve(pending);
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
    return FieldTrackDB.enqueueSyncItem('attendance', action, payload, priority, uuid);
  };

  // Auto-init database on load
  document.addEventListener('DOMContentLoaded', () => {
    FieldTrackDB.open().catch((err) =>
      console.warn('FieldTrackDB auto-init failed:', err)
    );
  });
})(window);
