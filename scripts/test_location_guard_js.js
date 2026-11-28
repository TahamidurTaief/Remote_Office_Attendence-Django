const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadLocationGuard(watchImpl) {
  let clearedWatches = 0;
  const windowObj = {
    isSecureContext: true,
    location: { hostname: "localhost", pathname: "/staff/check-in/" },
    addEventListener: () => {},
    dispatchEvent: () => {},
    CustomEvent: function (name, opts) { return { name, detail: opts && opts.detail }; },
  };
  const context = {
    window: windowObj,
    document: {
      body: { getAttribute: () => "staff" },
      getElementById: () => null,
      addEventListener: () => {},
    },
    navigator: {
      geolocation: {
        getCurrentPosition: () => {},
        watchPosition: watchImpl,
        clearWatch: () => { clearedWatches += 1; },
      },
      permissions: undefined,
    },
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    sessionStorage: { getItem: () => null, setItem: () => {} },
    setTimeout,
    clearTimeout,
    console: { log: () => {}, warn: () => {}, error: () => {} },
  };
  vm.createContext(context);
  const code = fs.readFileSync(path.join(__dirname, "..", "static", "js", "location_guard.js"), "utf8");
  vm.runInContext(code, context);
  return { guard: context.window.LocationGuard, getClearedWatches: () => clearedWatches };
}

(async function runTests() {
  // 1. Finishes early when <= 50m target fix arrives, uses position.timestamp, allows (0,0), and clears watchPosition
  {
    const sampleTs = Date.UTC(2026, 10, 25, 8, 30, 0);
    const { guard, getClearedWatches } = loadLocationGuard((onSuccess) => {
      queueMicrotask(() => {
        onSuccess({ coords: { latitude: 0, longitude: 0, accuracy: 35 }, timestamp: sampleTs });
      });
      return 1;
    });
    const res = await guard.acquireFreshFix({ maxAccuracyMeters: 100, targetAccuracyMeters: 50, maxDurationMs: 200 });
    assert.strictEqual(res.ok, true);
    assert.strictEqual(res.coords.latitude, 0);
    assert.strictEqual(res.coords.longitude, 0);
    assert.strictEqual(res.coords.accuracy, 35);
    assert.strictEqual(res.coords.capturedAt, new Date(sampleTs).toISOString());
    assert.strictEqual(getClearedWatches(), 1);
  }

  // 2. Rejects >100m indoor/approximate fix with explicit poor_accuracy result (never injects defaults)
  {
    const { guard, getClearedWatches } = loadLocationGuard((onSuccess) => {
      queueMicrotask(() => {
        onSuccess({ coords: { latitude: 23.81, longitude: 90.41, accuracy: 0 } }); // fabricated 0 ignored
        onSuccess({ coords: { latitude: 23.81, longitude: 90.41, accuracy: 180 } });
      });
      return 2;
    });
    const res = await guard.acquireFreshFix({ maxAccuracyMeters: 100, targetAccuracyMeters: 50, maxDurationMs: 50 });
    assert.strictEqual(res.ok, false);
    assert.strictEqual(res.reason, "poor_accuracy");
    assert.strictEqual(res.bestFix.accuracy, 180);
    assert.strictEqual(getClearedWatches(), 1);
  }

  // 3. Failed GPS acquisition / denied permission returns explicit error without fabricating defaults
  {
    const { guard, getClearedWatches } = loadLocationGuard((_onSuccess, onError) => {
      queueMicrotask(() => {
        onError({ code: 1 });
      });
      return 3;
    });
    const res = await guard.acquireFreshFix({ maxAccuracyMeters: 100, targetAccuracyMeters: 50, maxDurationMs: 200 });
    assert.strictEqual(res.ok, false);
    assert.strictEqual(res.reason, "denied");
    assert.strictEqual(res.bestFix, null);
    assert.strictEqual(getClearedWatches(), 1);
  }

  console.log("LocationGuard fresh-fix JS tests passed: 3/3 groups ok.");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
