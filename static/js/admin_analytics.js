// static/js/admin_analytics.js
(function () {
  if (typeof Chart === "undefined") return;

  const payload = window.CAREERAI_ANALYTICS || {};

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

  function getCanvas(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    const ctx = el.getContext && el.getContext("2d");
    return ctx || null;
  }

  function chartExistsAndHasLabels(id, labels) {
    const ctx = getCanvas(id);
    if (!ctx) return null;
    if (!Array.isArray(labels) || labels.length === 0) return null;
    return ctx;
  }

  function makeBarChart(id, labels, data, labelText) {
    const ctx = chartExistsAndHasLabels(id, labels);
    if (!ctx) return;

    new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: labelText,
            data,
            borderWidth: 1.5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            ticks: { color: "#e5e7eb", font: { size: 10 } },
            grid: { display: false },
          },
          y: {
            ticks: { color: "#9ca3af", font: { size: 10 }, precision: 0 },
            grid: { color: "rgba(55,65,81,0.4)" },
          },
        },
        plugins: {
          legend: {
            labels: { color: "#e5e7eb", font: { size: 10 } },
          },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                const v = ctx.parsed && typeof ctx.parsed.y !== "undefined" ? ctx.parsed.y : ctx.raw;
                return `${ctx.dataset.label}: ${toNumber(v)}`;
              },
            },
          },
        },
      },
    });
  }

  function makeLineChart(id, labels, silver, gold) {
    const ctx = chartExistsAndHasLabels(id, labels);
    if (!ctx) return;

    new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Silver 🪙 debits",
            data: silver,
            tension: 0.25,
            borderWidth: 1.8,
            pointRadius: 1.5,
          },
          {
            label: "Gold ⭐ debits",
            data: gold,
            tension: 0.25,
            borderWidth: 1.8,
            pointRadius: 1.5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            ticks: { color: "#e5e7eb", font: { size: 10 } },
            grid: { display: false },
          },
          y: {
            ticks: { color: "#9ca3af", font: { size: 10 }, precision: 0 },
            grid: { color: "rgba(55,65,81,0.4)" },
          },
        },
        plugins: {
          legend: {
            labels: { color: "#e5e7eb", font: { size: 10 } },
          },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                const v = ctx.parsed && typeof ctx.parsed.y !== "undefined" ? ctx.parsed.y : ctx.raw;
                return `${ctx.dataset.label}: ${toNumber(v)}`;
              },
            },
          },
        },
      },
    });
  }

  function makeDoughnutChart(id, labels, data, labelText) {
    const ctx = chartExistsAndHasLabels(id, labels);
    if (!ctx) return;

    new Chart(ctx, {
      type: "doughnut",
      data: {
        labels,
        datasets: [
          {
            label: labelText,
            data,
            borderWidth: 1.2,
            hoverOffset: 6,
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
            labels: { color: "#e5e7eb", font: { size: 10 } },
          },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                const v = toNumber(ctx.raw);
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
  // Existing charts (kept)
  // ------------------------------------------------------------

  // ------------------ Skills Top ------------------
  if (Array.isArray(payload.skillsTop) && payload.skillsTop.length) {
    const labels = payload.skillsTop.map((s) => s.name);
    const data = payload.skillsTop.map((s) => toNumber(s.count));
    makeBarChart("chartSkillsTop", labels, data, "Skill mention count");
  }

  // ------------------ Roles Top -------------------
  if (Array.isArray(payload.rolesTop) && payload.rolesTop.length) {
    const labels = payload.rolesTop.map((r) => r.name);
    const data = payload.rolesTop.map((r) => toNumber(r.count));
    makeBarChart("chartRolesTop", labels, data, "Job Pack analyses");
  }

  // -------------- Internship Roles ----------------
  if (Array.isArray(payload.internshipRoles) && payload.internshipRoles.length) {
    const labels = payload.internshipRoles.map((r) => r.name);
    const data = payload.internshipRoles.map((r) => toNumber(r.count));
    makeBarChart("chartInternshipRoles", labels, data, "Internship searches");
  }

  // -------------- Tool Debits (features) ----------
  if (Array.isArray(payload.toolDebits) && payload.toolDebits.length) {
    const labels = payload.toolDebits.map((t) => t.feature || "unknown");
    const data = payload.toolDebits.map((t) => toNumber(t.amount));
    makeBarChart("chartToolDebits", labels, data, "Credits spent");
  }

  // -------------- Daily Credits -------------------
  if (
    payload.dailyCredits &&
    Array.isArray(payload.dailyCredits.labels) &&
    payload.dailyCredits.labels.length
  ) {
    const labels = payload.dailyCredits.labels;
    const silver = asArray(payload.dailyCredits.silver).map((v) => toNumber(v));
    const gold = asArray(payload.dailyCredits.gold).map((v) => toNumber(v));
    makeLineChart("chartDailyCredits", labels, silver, gold);
  }

  // ------------------------------------------------------------
  // NEW charts (Readiness)
  // ------------------------------------------------------------
  // Expected structure from backend:
  // payload.readinessChart = {
  //   buckets: { labels: ["0-9","10-19",...], counts: [..] },
  //   tiers: { labels: ["Top Tier (80+)", ...], counts: [..] },
  //   avg: <number>, median: <number>
  // }
  if (payload.readinessChart && payload.readinessChart.buckets) {
    const b = payload.readinessChart.buckets || {};
    const labels = asArray(b.labels);
    const counts = asArray(b.counts).map((v) => toNumber(v));
    makeBarChart("chartReadinessBuckets", labels, counts, "Students in bucket");
  }

  if (payload.readinessChart && payload.readinessChart.tiers) {
    const t = payload.readinessChart.tiers || {};
    const labels = asArray(t.labels);
    const counts = asArray(t.counts).map((v) => toNumber(v));
    makeDoughnutChart("chartReadinessTiers", labels, counts, "Students by tier");
  }

  // ------------------------------------------------------------
  // Optional: streak chart hook (template may add later)
  // If you later add <canvas id="chartStreakBuckets">, this will render.
  // ------------------------------------------------------------
  // Expected structure:
  // payload.streakChart = { buckets: { labels: ["0","1-2","3-6","7+"], counts: [...] }, avg_current, avg_longest }
  if (payload.streakChart && payload.streakChart.buckets) {
    const sb = payload.streakChart.buckets || {};
    const labels = asArray(sb.labels);
    const counts = asArray(sb.counts).map((v) => toNumber(v));
    makeBarChart("chartStreakBuckets", labels, counts, "Students by streak");
  }
})();
