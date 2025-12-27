// static/js/admin_analytics.js
(function () {
  "use strict";

  // ------------------------------------------------------------
  // Chart registry (prevents "Canvas is already in use" errors)
  // ------------------------------------------------------------
  const CHARTS = {}; // canvasId -> Chart instance

  const KNOWN_IDS = [
    "chartSkillsTop",
    "chartRolesTop",
    "chartInternshipRoles",
    "chartReadinessBuckets",
    "chartReadinessTiers",
    "chartProblemsSummary",
    "chartResumeMissingSkills",
    "chartRoadmapMissingSkills",
    "chartResumeBlockers",
    "chartResumeWarnings",
    "chartToolUsageRuns",
  ];

  function destroyChartById(id) {
    // Destroy our own reference
    if (CHARTS[id]) {
      try { CHARTS[id].destroy(); } catch (_) {}
      delete CHARTS[id];
    }

    // Also try Chart.js internal registry (Chart v3/v4)
    try {
      if (typeof Chart !== "undefined" && Chart.getChart) {
        const canvas = document.getElementById(id);
        if (canvas) {
          const existing = Chart.getChart(canvas);
          if (existing) existing.destroy();
        }
      }
    } catch (_) {}
  }

  function cleanupOrphanCharts() {
    Object.keys(CHARTS).forEach((id) => {
      if (!document.getElementById(id)) destroyChartById(id);
    });
  }

  // ------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------
  function asArray(v) {
    return Array.isArray(v) ? v : [];
  }

  function toNumber(v, fallback = 0) {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }

  function getCanvasCtx(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    const ctx = el.getContext && el.getContext("2d");
    return ctx || null;
  }

  function chartExistsAndHasLabels(id, labels) {
    const ctx = getCanvasCtx(id);
    if (!ctx) return null;
    if (!Array.isArray(labels) || labels.length === 0) return null;
    return ctx;
  }

  function normalizeSeries(labels, data) {
    const L = asArray(labels);
    const D = asArray(data).map((v) => toNumber(v));
    const n = Math.min(L.length, D.length);
    return {
      labels: L.slice(0, n),
      data: D.slice(0, n),
    };
  }

  // ✅ Axis-aware tooltip value extraction (fixes horizontal bars + doughnuts)
  function tooltipValue(ctx) {
    const parsed = ctx && ctx.parsed;

    // Doughnut/pie: parsed is a number
    if (typeof parsed === "number") return toNumber(parsed);

    // Bar/line: parsed can be {x,y}
    if (parsed && typeof parsed === "object") {
      const indexAxis = ctx?.chart?.options?.indexAxis; // "x" (default) or "y"
      if (indexAxis === "y" && typeof parsed.x !== "undefined") return toNumber(parsed.x);
      if (typeof parsed.y !== "undefined") return toNumber(parsed.y);
      if (typeof parsed.x !== "undefined") return toNumber(parsed.x);
    }

    return toNumber(ctx && typeof ctx.raw !== "undefined" ? ctx.raw : 0);
  }

  function baseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          ticks: { color: "#e5e7eb", font: { size: 10 }, precision: 0 },
          grid: { display: false },
        },
        y: {
          ticks: { color: "#9ca3af", font: { size: 10 }, precision: 0 },
          grid: { color: "rgba(55,65,81,0.4)" },
        },
      },
      plugins: {
        legend: { labels: { color: "#e5e7eb", font: { size: 10 } } },
        tooltip: {
          callbacks: {
            label: function (ctx) {
              return `${ctx.dataset.label}: ${tooltipValue(ctx)}`;
            },
          },
        },
      },
    };
  }

  function makeBarChart(id, labels, data, labelText) {
    const series = normalizeSeries(labels, data);
    const ctx = chartExistsAndHasLabels(id, series.labels);
    if (!ctx) return;

    destroyChartById(id);

    CHARTS[id] = new Chart(ctx, {
      type: "bar",
      data: {
        labels: series.labels,
        datasets: [{ label: labelText, data: series.data, borderWidth: 1.5 }],
      },
      options: baseOptions(),
    });
  }

  function makeHBarChart(id, labels, data, labelText) {
    const series = normalizeSeries(labels, data);
    const ctx = chartExistsAndHasLabels(id, series.labels);
    if (!ctx) return;

    destroyChartById(id);

    const opts = baseOptions();
    opts.indexAxis = "y";
    opts.scales.x.grid = { color: "rgba(55,65,81,0.25)" };
    opts.scales.y.grid = { display: false };

    CHARTS[id] = new Chart(ctx, {
      type: "bar",
      data: {
        labels: series.labels,
        datasets: [{ label: labelText, data: series.data, borderWidth: 1.5 }],
      },
      options: opts,
    });
  }

  function makeDoughnutChart(id, labels, data, labelText) {
    const series = normalizeSeries(labels, data);
    const ctx = chartExistsAndHasLabels(id, series.labels);
    if (!ctx) return;

    destroyChartById(id);

    CHARTS[id] = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: series.labels,
        datasets: [{ label: labelText, data: series.data, borderWidth: 1.2, hoverOffset: 6 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: {
          legend: { position: "bottom", labels: { color: "#e5e7eb", font: { size: 10 } } },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                const v = tooltipValue(ctx);
                const total = (ctx.dataset.data || []).reduce((a, b) => a + toNumber(b), 0);
                const pct = total > 0 ? Math.round((v / total) * 100) : 0;
                return `${ctx.label}: ${v} (${pct}%)`;
              },
            },
          },
        },
      },
    });
  }

  // ------------------------------------------------------------
  // Main init
  // ------------------------------------------------------------
  function initAdminAnalyticsCharts() {
    cleanupOrphanCharts();

    if (typeof Chart === "undefined") {
      console.warn("[admin_analytics] Chart.js not loaded — charts disabled.");
      return;
    }

    // ✅ Kill stale charts attached to canvases before rebuilding
    for (const id of KNOWN_IDS) {
      if (document.getElementById(id)) destroyChartById(id);
    }

    const payload = window.CAREERAI_ANALYTICS || {};

    // Skills Top
    if (Array.isArray(payload.skillsTop) && payload.skillsTop.length) {
      makeBarChart(
        "chartSkillsTop",
        payload.skillsTop.map((s) => s.name),
        payload.skillsTop.map((s) => toNumber(s.count)),
        "Skill mention count"
      );
    }

    // Roles Top
    if (Array.isArray(payload.rolesTop) && payload.rolesTop.length) {
      makeBarChart(
        "chartRolesTop",
        payload.rolesTop.map((r) => r.name),
        payload.rolesTop.map((r) => toNumber(r.count)),
        "Job Pack analyses"
      );
    }

    // Internship Roles
    if (Array.isArray(payload.internshipRoles) && payload.internshipRoles.length) {
      makeBarChart(
        "chartInternshipRoles",
        payload.internshipRoles.map((r) => r.name),
        payload.internshipRoles.map((r) => toNumber(r.count)),
        "Internship searches"
      );
    }

    // Readiness (support both schemas)
    if (payload.readinessChart) {
      const rc = payload.readinessChart || {};

      const bucketLabels = asArray((rc.buckets && rc.buckets.labels) || rc.labels);
      const bucketCounts = asArray((rc.buckets && rc.buckets.counts) || rc.counts).map(toNumber);
      if (bucketLabels.length) {
        makeBarChart("chartReadinessBuckets", bucketLabels, bucketCounts, "Students in bucket");
      }

      if (Array.isArray(rc.tiers)) {
        const tierLabels = rc.tiers.map((t) => t.tier);
        const tierCounts = rc.tiers.map((t) => toNumber(t.count));
        if (tierLabels.length) makeDoughnutChart("chartReadinessTiers", tierLabels, tierCounts, "Students by tier");
      } else if (rc.tiers && rc.tiers.labels) {
        const tierLabels = asArray(rc.tiers.labels);
        const tierCounts = asArray(rc.tiers.counts).map(toNumber);
        if (tierLabels.length) makeDoughnutChart("chartReadinessTiers", tierLabels, tierCounts, "Students by tier");
      }
    }

    // Dean charts
    if (Array.isArray(payload.problemsSummary) && payload.problemsSummary.length) {
      makeHBarChart(
        "chartProblemsSummary",
        payload.problemsSummary.map((p) => p.label),
        payload.problemsSummary.map((p) => toNumber(p.count)),
        "Students affected"
      );
    }

    if (Array.isArray(payload.resumeMissingSkillsTop) && payload.resumeMissingSkillsTop.length) {
      makeHBarChart(
        "chartResumeMissingSkills",
        payload.resumeMissingSkillsTop.map((x) => x.name),
        payload.resumeMissingSkillsTop.map((x) => toNumber(x.students)),
        "Students missing skill"
      );
    }

    if (Array.isArray(payload.roadmapMissingSkillsTop) && payload.roadmapMissingSkillsTop.length) {
      makeHBarChart(
        "chartRoadmapMissingSkills",
        payload.roadmapMissingSkillsTop.map((x) => x.name),
        payload.roadmapMissingSkillsTop.map((x) => toNumber(x.students)),
        "Students with gap"
      );
    }

    if (Array.isArray(payload.resumeBlockersTop) && payload.resumeBlockersTop.length) {
      makeHBarChart(
        "chartResumeBlockers",
        payload.resumeBlockersTop.map((x) => x.text),
        payload.resumeBlockersTop.map((x) => toNumber(x.students)),
        "Students impacted"
      );
    }

    if (Array.isArray(payload.resumeWarningsTop) && payload.resumeWarningsTop.length) {
      makeHBarChart(
        "chartResumeWarnings",
        payload.resumeWarningsTop.map((x) => x.text),
        payload.resumeWarningsTop.map((x) => toNumber(x.students)),
        "Students impacted"
      );
    }

    if (Array.isArray(payload.toolUsageRuns) && payload.toolUsageRuns.length) {
      makeBarChart(
        "chartToolUsageRuns",
        payload.toolUsageRuns.map((t) => t.tool),
        payload.toolUsageRuns.map((t) => toNumber(t.runs)),
        "Runs"
      );
    }
  }

  // ------------------------------------------------------------
  // Run init at the right times (supports normal + Turbo + HTMX)
  // ------------------------------------------------------------
  function runInitSoon() {
    setTimeout(initAdminAnalyticsCharts, 0);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", runInitSoon, { once: true });
  } else {
    runInitSoon();
  }

  document.addEventListener("turbo:load", runInitSoon);
  document.addEventListener("turbo:render", runInitSoon);

  document.addEventListener("htmx:afterSwap", function (e) {
    try {
      const root = (e && e.target) || document;
      // ✅ more robust: re-init if any chart canvas exists in swapped region
      if (root.querySelector && root.querySelector("canvas[id^='chart']")) runInitSoon();
    } catch (_) {}
  });

  window.addEventListener("pageshow", function (e) {
    if (e && e.persisted) runInitSoon();
  });
})();
