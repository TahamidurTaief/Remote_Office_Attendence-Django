(function (global) {
  "use strict";

  var CHART_IDS = {
    trend: "analyticsTrendChart",
    groupBar: "analyticsGroupBarChart",
    workMode: "analyticsWorkModeChart",
    leaveTypes: "analyticsLeaveTypeChart",
    taskStatus: "analyticsWorkStatusChart",
    financeBreakdown: "analyticsFinanceChart",
  };

  function prefersReducedMotion() {
    if (typeof global.matchMedia !== "function") return false;
    return global.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function getAnimationConfig(isUpdate) {
    if (isUpdate || prefersReducedMotion()) {
      return { duration: 0 };
    }
    return { duration: 300 };
  }

  function readChartPayload(root) {
    var container = root || document;
    var el = container.querySelector("#attendance-analytics-chart-data");
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || "{}");
    } catch (e) {
      return null;
    }
  }

  function destroyChartById(canvasId) {
    if (typeof global.Chart === "undefined") return;
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    var existing = global.Chart.getChart(canvas);
    if (existing) {
      existing.destroy();
    }
  }

  function destroyAllAnalyticsCharts() {
    destroyChartById(CHART_IDS.trend);
    destroyChartById(CHART_IDS.groupBar);
    destroyChartById(CHART_IDS.workMode);
    destroyChartById(CHART_IDS.leaveTypes);
    destroyChartById(CHART_IDS.taskStatus);
    destroyChartById(CHART_IDS.financeBreakdown);
  }

  function readPayloadById(elId, root) {
    var container = root || document;
    var el = container.querySelector("#" + elId);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || "{}");
    } catch (e) {
      return null;
    }
  }

  function renderPanelCharts(root) {
    if (typeof global.Chart === "undefined") return false;
    var plPayload = readPayloadById("people-leave-analytics-chart-data", root);
    if (plPayload && plPayload.leaveTypes) {
      upsertChart(CHART_IDS.leaveTypes, function () {
        return {
          type: "doughnut",
          data: {
            labels: plPayload.leaveTypes.labels || [],
            datasets: [{ data: plPayload.leaveTypes.counts || [], backgroundColor: ["#0284c7", "#10b981", "#f59e0b", "#8b5cf6"], borderWidth: 2, borderColor: "transparent" }],
          },
          options: { responsive: true, maintainAspectRatio: false, cutout: "60%", plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 10 }, color: "#64748b" } } } },
        };
      });
    }
    var workPayload = readPayloadById("work-analytics-chart-data", root);
    if (workPayload && workPayload.taskStatus) {
      upsertChart(CHART_IDS.taskStatus, function () {
        return {
          type: "bar",
          data: {
            labels: workPayload.taskStatus.labels || [],
            datasets: [{ label: "Tasks", data: workPayload.taskStatus.counts || [], backgroundColor: "#0d9488", borderRadius: 6, maxBarThickness: 28 }],
          },
          options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
        };
      });
    }
    var finPayload = readPayloadById("finance-analytics-chart-data", root);
    if (finPayload && finPayload.financeBreakdown) {
      upsertChart(CHART_IDS.financeBreakdown, function () {
        return {
          type: "bar",
          data: {
            labels: finPayload.financeBreakdown.labels || [],
            datasets: [{ label: finPayload.financeBreakdown.currency || "BDT", data: finPayload.financeBreakdown.values || [], backgroundColor: ["#0284c7", "#f59e0b", "#8b5cf6", "#10b981"], borderRadius: 6, maxBarThickness: 28 }],
          },
          options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } },
        };
      });
    }
    return true;
  }

  function upsertChart(canvasId, configBuilder) {
    if (typeof global.Chart === "undefined") return null;
    var canvas = document.getElementById(canvasId);
    if (!canvas || canvas.offsetParent === null) return null;

    var existing = global.Chart.getChart(canvas);
    var cfg = configBuilder();
    if (existing) {
      existing.data.labels = cfg.data.labels;
      existing.data.datasets = cfg.data.datasets;
      existing.options.animation = getAnimationConfig(true);
      existing.update("none");
      return existing;
    }
    cfg.options = cfg.options || {};
    cfg.options.animation = getAnimationConfig(false);
    return new global.Chart(canvas.getContext("2d"), cfg);
  }

  function renderAnalyticsCharts(root) {
    if (typeof global.Chart === "undefined") return false;
    var payload = readChartPayload(root);
    if (!payload) return false;

    if (payload.trend) {
      upsertChart(CHART_IDS.trend, function () {
        return {
          type: "line",
          data: {
            labels: payload.trend.labels || [],
            datasets: [
              {
                label: "Present (incl. Late)",
                data: payload.trend.present || [],
                borderColor: "#0284c7",
                backgroundColor: "rgba(2, 132, 199, 0.10)",
                borderWidth: 2,
                fill: true,
                tension: 0.25,
                pointRadius: 3,
              },
              {
                label: "Late (subset of Present)",
                data: payload.trend.late || [],
                borderColor: "#f59e0b",
                backgroundColor: "transparent",
                borderWidth: 2,
                borderDash: [4, 4],
                tension: 0.25,
                pointRadius: 2,
              },
              {
                label: "Absent (Finalized)",
                data: payload.trend.absent || [],
                borderColor: "#f43f5e",
                backgroundColor: "transparent",
                borderWidth: 2,
                tension: 0.25,
                pointRadius: 2,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                position: "bottom",
                labels: { boxWidth: 10, font: { size: 10 }, color: "#64748b" },
              },
            },
            scales: {
              x: { grid: { display: false }, ticks: { font: { size: 10 }, color: "#64748b" } },
              y: {
                beginAtZero: true,
                grid: { color: "#f1f5f9" },
                ticks: { precision: 0, font: { size: 10 }, color: "#64748b" },
              },
            },
          },
        };
      });
    }

    if (payload.groupBar) {
      upsertChart(CHART_IDS.groupBar, function () {
        return {
          type: "bar",
          data: {
            labels: payload.groupBar.labels || [],
            datasets: [
              {
                label: "Attendance Rate (%)",
                data: payload.groupBar.rates || [],
                backgroundColor: "#0d9488",
                borderRadius: 6,
                maxBarThickness: 28,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  afterLabel: function (ctx) {
                    var idx = ctx.dataIndex;
                    var p = (payload.groupBar.present || [])[idx] || 0;
                    var e = (payload.groupBar.eligible || [])[idx] || 0;
                    return "Present: " + p + " / Eligible: " + e;
                  },
                },
              },
            },
            scales: {
              x: { grid: { display: false }, ticks: { font: { size: 10 }, color: "#64748b" } },
              y: {
                beginAtZero: true,
                max: 100,
                grid: { color: "#f1f5f9" },
                ticks: { font: { size: 10 }, color: "#64748b" },
              },
            },
          },
        };
      });
    }

    if (payload.workMode) {
      upsertChart(CHART_IDS.workMode, function () {
        return {
          type: "doughnut",
          data: {
            labels: payload.workMode.labels || [],
            datasets: [
              {
                data: payload.workMode.values || [0, 0, 0],
                backgroundColor: ["#0284c7", "#8b5cf6", "#10b981"],
                borderWidth: 2,
                borderColor: "transparent",
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: "62%",
            plugins: {
              legend: {
                position: "bottom",
                labels: { boxWidth: 10, font: { size: 10 }, color: "#64748b" },
              },
            },
          },
        };
      });
    }

    return true;
  }

  function initWithRetry(attemptsLeft) {
    if (renderAnalyticsCharts(document)) return;
    if (attemptsLeft > 0) {
      setTimeout(function () {
        initWithRetry(attemptsLeft - 1);
      }, 100);
    }
  }

  function bindGlobalLifecycle() {
    if (global.__ftAnalyticsLifecycleBound) return;
    global.__ftAnalyticsLifecycleBound = true;

    var PANEL_TARGETS = {
      "attendance-analytics-section": true,
      "analytics-panel-people-leave-host": true,
      "analytics-panel-people-leave": true,
      "analytics-panel-work-host": true,
      "analytics-panel-work": true,
      "analytics-panel-finance-host": true,
      "analytics-panel-finance": true,
    };

    document.addEventListener("htmx:beforeSwap", function (evt) {
      var target = evt.detail && evt.detail.target;
      if (target && PANEL_TARGETS[target.id]) {
        if (target.id === "attendance-analytics-section") {
          destroyAllAnalyticsCharts();
        } else if (target.id.indexOf("people-leave") !== -1) {
          destroyChartById(CHART_IDS.leaveTypes);
        } else if (target.id.indexOf("work") !== -1) {
          destroyChartById(CHART_IDS.taskStatus);
        } else if (target.id.indexOf("finance") !== -1) {
          destroyChartById(CHART_IDS.financeBreakdown);
        }
      }
    });

    document.addEventListener("htmx:afterSwap", function (evt) {
      var target = evt.detail && evt.detail.target;
      if (target && (PANEL_TARGETS[target.id] || target.id === "dashboard-content")) {
        initWithRetry(10);
        renderPanelCharts(document);
      }
    });

    document.addEventListener("htmx:responseError", function (evt) {
      var target = evt.detail && evt.detail.target;
      if (target && target.id === "attendance-analytics-section") {
        var errBox = document.getElementById("analytics-error-banner");
        if (errBox) errBox.classList.remove("hidden");
      }
    });
  }

  if (typeof document !== "undefined") {
    bindGlobalLifecycle();
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        initWithRetry(15);
      });
    } else {
      initWithRetry(15);
    }
  }

  // Export for unit tests (CommonJS / browser global)
  var api = {
    CHART_IDS: CHART_IDS,
    prefersReducedMotion: prefersReducedMotion,
    getAnimationConfig: getAnimationConfig,
    readChartPayload: readChartPayload,
    destroyChartById: destroyChartById,
    destroyAllAnalyticsCharts: destroyAllAnalyticsCharts,
    renderAnalyticsCharts: renderAnalyticsCharts,
    bindGlobalLifecycle: bindGlobalLifecycle,
  };
  global.FTAttendanceAnalytics = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
