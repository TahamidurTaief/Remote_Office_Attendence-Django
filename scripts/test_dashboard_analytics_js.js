const assert = require("assert");
const path = require("path");

// Minimal DOM & Chart.js mock for lifecycle unit testing
const instances = new Map();
let createCount = 0;
let updateCount = 0;
let destroyCount = 0;

class MockChart {
  constructor(ctx, cfg) {
    this.ctx = ctx;
    this.data = cfg.data;
    this.options = cfg.options || {};
    createCount += 1;
    instances.set(ctx.canvas, this);
  }
  update(mode) {
    updateCount += 1;
    this.lastUpdateMode = mode;
  }
  destroy() {
    destroyCount += 1;
    instances.delete(this.ctx.canvas);
  }
  static getChart(canvas) {
    return instances.get(canvas) || null;
  }
}

const payloadObj = {
  trend: { labels: ["20 Nov"], present: [5], late: [1], absent: [0] },
  groupBar: { labels: ["Engineering"], rates: [100], present: [5], eligible: [5] },
  workMode: { labels: ["Office Only", "Field Only", "Mixed (Office + Field)"], values: [4, 1, 0] },
};

function makeCanvas(id) {
  const c = { id, offsetParent: {} };
  c.getContext = () => ({ canvas: c });
  return c;
}

const elements = {
  "attendance-analytics-chart-data": { textContent: JSON.stringify(payloadObj) },
  analyticsTrendChart: makeCanvas("analyticsTrendChart"),
  analyticsGroupBarChart: makeCanvas("analyticsGroupBarChart"),
  analyticsWorkModeChart: makeCanvas("analyticsWorkModeChart"),
};

const listeners = {};
global.document = {
  readyState: "complete",
  querySelector: (sel) => (sel === "#attendance-analytics-chart-data" ? elements["attendance-analytics-chart-data"] : null),
  getElementById: (id) => elements[id] || null,
  addEventListener: (evt, fn) => {
    listeners[evt] = listeners[evt] || [];
    listeners[evt].push(fn);
  },
};

let reducedMotion = false;
global.window = {
  matchMedia: () => ({ matches: reducedMotion }),
  Chart: MockChart,
};
global.Chart = MockChart;

const analytics = require(path.resolve(__dirname, "../static/js/dashboard_attendance_analytics.js"));

// 1. Initial render creates 3 charts once (Note: module auto-rendered on load because readyState === 'complete')
assert.strictEqual(createCount, 3, "Expected 3 charts created on initial load");
assert.strictEqual(updateCount, 0, "Expected 0 updates on initial load");

// 2. Re-rendering updates in place without creating duplicates and disables animation
analytics.renderAnalyticsCharts(global.document);
assert.strictEqual(createCount, 3, "Should not create duplicate chart instances");
assert.strictEqual(updateCount, 3, "Should update 3 charts in place");
assert.strictEqual(MockChart.getChart(elements.analyticsTrendChart).options.animation.duration, 0);

// 3. HTMX beforeSwap destroys charts cleanly
(listeners["htmx:beforeSwap"] || []).forEach((fn) =>
  fn({ detail: { target: { id: "attendance-analytics-section" } } })
);
assert.strictEqual(destroyCount, 3, "Expected 3 charts destroyed before HTMX swap");
assert.strictEqual(MockChart.getChart(elements.analyticsTrendChart), null);

// 4. Reduced motion disables initial animation
reducedMotion = true;
analytics.renderAnalyticsCharts(global.document);
assert.strictEqual(createCount, 6);
assert.strictEqual(MockChart.getChart(elements.analyticsTrendChart).options.animation.duration, 0);

// 5. bindGlobalLifecycle is idempotent (no duplicate listeners)
const beforeCount = (listeners["htmx:beforeSwap"] || []).length;
analytics.bindGlobalLifecycle();
assert.strictEqual((listeners["htmx:beforeSwap"] || []).length, beforeCount);

console.log("JS chart lifecycle tests passed: 5/5 assertions ok.");
