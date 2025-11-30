// static/js/admin_analytics.js
(function () {
  if (typeof Chart === "undefined") {
    return;
  }

  const payload = window.CAREERAI_ANALYTICS || {};

  function makeBarChart(id, labels, data, labelText) {
    const el = document.getElementById(id);
    if (!el || !labels || !labels.length) return;

    const ctx = el.getContext("2d");
    new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: labelText,
            data: data,
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
        },
      },
    });
  }

  function makeLineChart(id, labels, silver, gold) {
    const el = document.getElementById(id);
    if (!el || !labels || !labels.length) return;

    const ctx = el.getContext("2d");
    new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Silver 🪙 debits",
            data: silver,
            tension: 0.25,
            borderWidth: 1.8,
          },
          {
            label: "Gold ⭐ debits",
            data: gold,
            tension: 0.25,
            borderWidth: 1.8,
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
        },
      },
    });
  }

  // ------------------ Skills Top ------------------
  if (Array.isArray(payload.skillsTop) && payload.skillsTop.length) {
    const labels = payload.skillsTop.map((s) => s.name);
    const data = payload.skillsTop.map((s) => s.count);
    makeBarChart("chartSkillsTop", labels, data, "Skill mention count");
  }

  // ------------------ Roles Top -------------------
  if (Array.isArray(payload.rolesTop) && payload.rolesTop.length) {
    const labels = payload.rolesTop.map((r) => r.name);
    const data = payload.rolesTop.map((r) => r.count);
    makeBarChart("chartRolesTop", labels, data, "Job Pack analyses");
  }

  // -------------- Internship Roles ----------------
  if (Array.isArray(payload.internshipRoles) && payload.internshipRoles.length) {
    const labels = payload.internshipRoles.map((r) => r.name);
    const data = payload.internshipRoles.map((r) => r.count);
    makeBarChart("chartInternshipRoles", labels, data, "Internship searches");
  }

  // -------------- Tool Debits (features) ----------
  if (Array.isArray(payload.toolDebits) && payload.toolDebits.length) {
    const labels = payload.toolDebits.map((t) => t.feature);
    const data = payload.toolDebits.map((t) => t.amount);
    makeBarChart("chartToolDebits", labels, data, "Credits spent");
  }

  // -------------- Daily Credits -------------------
  if (
    payload.dailyCredits &&
    Array.isArray(payload.dailyCredits.labels) &&
    payload.dailyCredits.labels.length
  ) {
    const labels = payload.dailyCredits.labels;
    const silver = payload.dailyCredits.silver || [];
    const gold = payload.dailyCredits.gold || [];
    makeLineChart("chartDailyCredits", labels, silver, gold);
  }
})();
