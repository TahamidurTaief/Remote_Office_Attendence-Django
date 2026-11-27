const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

// Persistent in-memory IndexedDB mock that survives simulated app reloads
function createPersistentIndexedDB() {
  const stores = {
    sync_queue: new Map(),
    auth_session: new Map(),
  };

  function makeRequest(executor) {
    const req = { onsuccess: null, onerror: null, result: undefined, error: null };
    queueMicrotask(() => {
      try {
        req.result = executor();
        if (req.onsuccess) req.onsuccess({ target: req });
      } catch (err) {
        req.error = err;
        if (req.onerror) req.onerror({ target: req });
      }
    });
    return req;
  }

  return {
    stores,
    indexedDB: {
      open: () => {
        const req = { onupgradeneeded: null, onsuccess: null, onerror: null, result: null };
        const db = {
          objectStoreNames: { contains: (n) => Boolean(stores[n]) },
          transaction: (storeName) => ({
            objectStore: () => {
              const map = stores[storeName];
              return {
                add: (rec) =>
                  makeRequest(() => {
                    if (map.has(rec.uuid)) throw new Error("Duplicate key");
                    map.set(rec.uuid, JSON.parse(JSON.stringify(rec)));
                    return rec.uuid;
                  }),
                put: (rec) =>
                  makeRequest(() => {
                    const key = storeName === "sync_queue" ? rec.uuid : rec.id;
                    map.set(key, JSON.parse(JSON.stringify(rec)));
                    return key;
                  }),
                get: (key) =>
                  makeRequest(() => {
                    const v = map.get(key);
                    return v ? JSON.parse(JSON.stringify(v)) : undefined;
                  }),
                getAll: () =>
                  makeRequest(() =>
                    Array.from(map.values()).map((v) => JSON.parse(JSON.stringify(v)))
                  ),
                delete: (key) =>
                  makeRequest(() => {
                    map.delete(key);
                  }),
              };
            },
          }),
        };
        queueMicrotask(() => {
          req.result = db;
          if (req.onsuccess) req.onsuccess({ target: req });
        });
        return req;
      },
    },
  };
}

function loadOfflineEnv(idbMock, fetchHandler, fixedNowMs) {
  const domListeners = {};
  const windowObj = {
    addEventListener: () => {},
    location: { href: "" },
    crypto: { randomUUID: () => "uuid-" + Math.random().toString(36).slice(2, 10) },
  };
  const context = {
    window: windowObj,
    document: {
      querySelector: () => null,
      addEventListener: (evt, fn) => {
        domListeners[evt] = domListeners[evt] || [];
        domListeners[evt].push(fn);
      },
    },
    navigator: { onLine: true },
    indexedDB: idbMock.indexedDB,
    fetch: fetchHandler,
    localStorage: { getItem: () => null, setItem: () => {} },
    Date: class extends Date {
      constructor(...args) {
        super(...(args.length ? args : [fixedNowMs]));
      }
      static now() {
        return fixedNowMs;
      }
    },
    console: { log: () => {}, warn: () => {}, error: () => {} },
    setInterval: () => 0,
    setTimeout: () => 0,
  };
  windowObj.Date = context.Date;

  vm.createContext(context);
  const dbCode = fs.readFileSync(path.join(__dirname, "..", "static", "js", "offline", "db.js"), "utf8");
  const syncCode = fs.readFileSync(path.join(__dirname, "..", "static", "js", "offline", "sync_engine.js"), "utf8");
  vm.runInContext(dbCode, context);
  vm.runInContext(syncCode, context);
  return context;
}

(async function runTests() {
  const fixedNow = Date.parse("2026-11-28T12:00:00Z");

  // 1. Offline check-in -> checkout dependency + reload persistence + stable fields
  {
    const idb = createPersistentIndexedDB();
    let capturedRequests = [];
    let env = loadOfflineEnv(
      idb,
      async (url, opts) => {
        const body = JSON.parse(opts.body);
        capturedRequests.push({ url, body });
        return {
          ok: true,
          status: 200,
          json: async () => ({
            success: true,
            results: body.items.map((it) => ({
              uuid: it.uuid,
              status: "success",
              data: it.action === "check_in" ? { session_id: 501 } : { session_id: 501, total_hours: 8.0 },
            })),
          }),
        };
      },
      fixedNow
    );

    const ci = await env.window.queueOfflineAction({
      action: "check_in",
      sync_uuid: "ci-offline-1",
      latitude: 23.8103,
      longitude: 90.4125,
      client_event_time: "2026-11-28T03:00:00Z",
      photo: "data:image/webp;base64,AAA",
    });
    assert.strictEqual(ci.uuid, "ci-offline-1");

    // Simulate page reload before checkout is queued
    env = loadOfflineEnv(idb, env.fetch, fixedNow + 1000);
    const activeOfflineCi = await env.window.FieldTrackDB.getActiveOfflineCheckIn();
    assert.strictEqual(activeOfflineCi.uuid, "ci-offline-1");

    const co = await env.window.queueOfflineAction({
      action: "check_out",
      sync_uuid: "co-offline-1",
      latitude: 23.8104,
      longitude: 90.4126,
      client_event_time: "2026-11-28T11:00:00Z",
      photo: "data:image/webp;base64,BBB",
    });
    assert.strictEqual(co.depends_on_sync_uuid, "ci-offline-1");
    assert.strictEqual(co.payload.depends_on_sync_uuid, "ci-offline-1");

    await env.window.SyncEngine.processQueue();
    assert.strictEqual(capturedRequests.length, 1, "Single bulk-sync request expected");
    assert.strictEqual(capturedRequests[0].body.items.length, 2);
    assert.strictEqual(capturedRequests[0].body.items[0].uuid, "ci-offline-1");
    assert.strictEqual(capturedRequests[0].body.items[1].uuid, "co-offline-1");
    assert.strictEqual((await env.window.FieldTrackDB.getPendingSyncItems()).length, 0);
  }

  // 2. Lost-response retry preserves UUIDs, timestamps, location, photo
  {
    const idb = createPersistentIndexedDB();
    let attempt = 0;
    let sentBatches = [];
    const makeFetch = () => async (url, opts) => {
      attempt += 1;
      const body = JSON.parse(opts.body);
      sentBatches.push(body.items);
      if (attempt === 1) {
        throw new Error("Connection dropped after server commit");
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          success: true,
          results: body.items.map((it) => ({
            uuid: it.uuid,
            status: "success",
            data: { session_id: 777 },
          })),
        }),
      };
    };

    let env = loadOfflineEnv(idb, makeFetch(), fixedNow);
    await env.window.queueOfflineAction({
      action: "check_in",
      sync_uuid: "ci-lost-1",
      latitude: 23.81,
      longitude: 90.41,
      client_event_time: "2026-11-28T03:00:00Z",
      photo: "data:image/webp;base64,CI",
    });
    await env.window.queueOfflineAction({
      action: "check_out",
      sync_uuid: "co-lost-1",
      latitude: 23.82,
      longitude: 90.42,
      client_event_time: "2026-11-28T11:00:00Z",
      photo: "data:image/webp;base64,CO",
    });

    await env.window.SyncEngine.processQueue();
    // Simulate app reopen after lost response
    env = loadOfflineEnv(idb, makeFetch(), fixedNow + 5000);
    await env.window.SyncEngine.processQueue();

    assert.strictEqual(sentBatches.length, 2);
    assert.deepStrictEqual(sentBatches[0][0].payload, sentBatches[1][0].payload);
    assert.deepStrictEqual(sentBatches[0][1].payload, sentBatches[1][1].payload);
    assert.strictEqual((await env.window.FieldTrackDB.getPendingSyncItems()).length, 0);
  }

  // 3. Permanent dependency failure leaves checkout visibly blocked, not deleted or retargeted
  {
    const idb = createPersistentIndexedDB();
    let requestCount = 0;
    const env = loadOfflineEnv(
      idb,
      async (url, opts) => {
        requestCount += 1;
        const body = JSON.parse(opts.body);
        return {
          ok: true,
          status: 200,
          json: async () => ({
            success: true,
            results: body.items.map((it) =>
              it.uuid === "ci-bad"
                ? { uuid: it.uuid, status: "failed", permanent: true, error: "Already checked in" }
                : { uuid: it.uuid, status: "failed", blocked: true, permanent: true, error: "Blocked: dependent check-in failed" }
            ),
          }),
        };
      },
      fixedNow
    );

    await env.window.queueOfflineAction({
      action: "check_in",
      sync_uuid: "ci-bad",
      latitude: 23.81,
      longitude: 90.41,
      client_event_time: "2026-11-28T03:00:00Z",
    });
    await env.window.queueOfflineAction({
      action: "check_out",
      sync_uuid: "co-blocked",
      latitude: 23.81,
      longitude: 90.41,
      client_event_time: "2026-11-28T11:00:00Z",
    });

    await env.window.SyncEngine.processQueue();
    // Second queue pass must not re-send blocked checkout or failed check-in
    await env.window.SyncEngine.processQueue();
    assert.strictEqual(requestCount, 1);

    const blockedItem = await env.window.FieldTrackDB.getSyncItem("co-blocked");
    assert.ok(blockedItem, "Blocked checkout must not be deleted");
    assert.strictEqual(blockedItem.status, "blocked");
    assert.strictEqual(blockedItem.permanent, true);
    assert.strictEqual(blockedItem.payload.session_id, undefined);
    assert.strictEqual((await env.window.FieldTrackDB.getBlockedSyncItems()).length, 1);
  }

  console.log("Offline session-targeting & dependency JS tests passed: 3/3 groups ok.");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
