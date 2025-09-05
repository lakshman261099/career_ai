// static/js/skillmapper.js
(function () {
  // NEW: read config injected by Flask safely (no VS Code squiggles)
  const smConfigEl = document.getElementById("sm-config");
  let smConfig = { is_pro: false, settings_path: "/settings#projects" };
  if (smConfigEl) {
    try { smConfig = JSON.parse(smConfigEl.textContent); } catch {}
  }
  window.__SM_IS_PRO__ = smConfig.is_pro;
  window.__SM_SETTINGS_PATH__ = smConfig.settings_path;

  const $ = (sel) => document.querySelector(sel);

  const toast = (msg, t = 2500) => {
    const el = $("#sm-toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.remove("hidden");
    setTimeout(() => el.classList.add("hidden"), t);
  };

  const freeBtn = $("#sm-free-btn");
  const proBtn = $("#sm-pro-btn");
  const input = $("#sm-free-input");
  const results = $("#sm-results");
  const rolesEl = $("#sm-roles");
  const metaEl = $("#sm-meta");
  const hiringStrip = $("#sm-hiring-strip");
  const addPortfolio = $("#sm-add-portfolio");

  function animateNumber(el, to, dur = 900) {
    if (!el) return;
    const start = 0;
    const t0 = performance.now();
    function frame(now) {
      const p = Math.min(1, (now - t0) / dur);
      const val = Math.round(start + (to - start) * p);
      el.textContent = val;
      if (p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function render(data) {
    if (!results) return;
    results.classList.remove("hidden");
    const when = (data.meta && data.meta.generated_at_utc) || "";
    if (metaEl) metaEl.textContent = when ? `Generated at ${when}` : "";

    // Roles
    rolesEl.innerHTML = "";
    (data.top_roles || []).forEach((r, idx) => {
      const chips = (r.primary_skill_clusters || [])
        .map((c) => (c.skills || []).slice(0, 3).map(s => `<span class="sm-chip">${s}</span>`).join(""))
        .join("");

      const gaps = (r.gaps || []).map(g => {
        const pct = Math.min(100, Math.max(0, (g.priority || 0) * 20));
        return `
          <li>
            <strong>${g.skill}</strong> — ${g.how_to_learn} <em>(${g.time_estimate_weeks}w)</em>
            <div class="sm-priority"><div style="width:0%" data-target="${pct}"></div></div>
          </li>`;
      }).join("");

      const micro = (r.micro_projects || []).map(m => `
        <li><strong>${m.title}:</strong> ${m.outcome}. Deliverables: ${(m.deliverables || []).join(", ")}.</li>
      `).join("");

      const exTitles = (r.example_titles || []).slice(0, 3).join(" · ");

      const card = document.createElement("div");
      card.className = "sm-role";
      card.innerHTML = `
        <div class="sm-role-head">
          <h3 class="sm-title">${r.title || "Role"}</h3>
          <span class="sm-badge">${(r.seniority_target || "").toString().toUpperCase()}</span>
        </div>
        <div class="sm-score"><span class="num">0</span><span class="unit">/100</span></div>
        <p class="sm-why">${r.why_fit || ""}</p>
        <div class="sm-chips">${chips}</div>

        <div class="sm-section">
          <h4>Gaps (fix next)</h4>
          <ol class="sm-list">${gaps}</ol>
        </div>

        <div class="sm-section">
          <h4>Micro-projects</h4>
          <ul class="sm-list">${micro}</ul>
        </div>

        <div class="sm-section">
          <h4>Example titles</h4>
          <div>${exTitles}</div>
        </div>
      `;
      rolesEl.appendChild(card);

      // animate score
      const scoreNum = card.querySelector(".sm-score .num");
      animateNumber(scoreNum, parseInt(r.match_score || 0, 10) || 0, 1000 + idx * 200);

      // animate gap bars
      card.querySelectorAll(".sm-priority > div").forEach((bar, i) => {
        const pct = parseInt(bar.getAttribute("data-target") || "0", 10);
        setTimeout(() => { bar.style.width = pct + "%"; }, 300 + i * 120);
      });
    });

    // Hiring now
    hiringStrip.innerHTML = "";
    (data.hiring_now || []).forEach(h => {
      const item = document.createElement("div");
      item.className = "sm-hiring-item";
      item.innerHTML = `
        <div class="sm-hiring-title">${h.role_group}</div>
        <div class="sm-hiring-metrics">
          <div class="sm-hiring-num"><span class="num">0</span></div>
          <div class="sm-hiring-pct"><span class="pct">0</span>% share</div>
        </div>
        <div class="sm-hiring-note">${h.note || ""}</div>
      `;
      hiringStrip.appendChild(item);

      // animate numbers
      animateNumber(item.querySelector(".num"), parseInt(h.est_count_estimate_global || 0, 10) || 0, 900);
      animateNumber(item.querySelector(".pct"), Math.round(parseFloat(h.share_estimate_pct || 0) || 0), 900);
    });
  }

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok || !json.ok) {
      throw new Error(json.error || `Request failed (${res.status})`);
    }
    return json;
  }

  const freeBtnInit = () => {
    if (!freeBtn) return;
    freeBtn.addEventListener("click", async () => {
      const txt = (input && input.value || "").trim();
      if (!txt) return toast("Paste a few skills first.");
      freeBtn.disabled = true; freeBtn.textContent = "Running...";
      try {
        const json = await post("/skillmapper/free", { free_text_skills: txt });
        render(json.data);
        toast(json.used_live_ai ? "AI: live" : "AI: mock");
      } catch (e) {
        toast(e.message || "Something went wrong.");
      } finally {
        freeBtn.disabled = false; freeBtn.textContent = "Run Skill Mapper";
      }
    });
  };

  const proBtnInit = () => {
    if (!proBtn) return;
    proBtn.addEventListener("click", async () => {
      proBtn.disabled = true; proBtn.textContent = "Analyzing...";
      try {
        const json = await post("/skillmapper/pro", {});
        render(json.data);
        toast(json.used_live_ai ? "AI: live" : "AI: mock");
      } catch (e) {
        toast(e.message || "Pro analysis failed.");
      } finally {
        proBtn.disabled = false; proBtn.textContent = "Analyze from Profile";
      }
    });
  };

  const addPortfolioInit = () => {
    if (!addPortfolio) return;
    addPortfolio.addEventListener("click", () => {
      if (!window.__SM_IS_PRO__) {
        toast("Upgrade to Pro to add micro-projects to your Portfolio.");
        return;
      }
      window.location.href = window.__SM_SETTINGS_PATH__ || "/settings#projects";
    });
  };

  freeBtnInit();
  proBtnInit();
  addPortfolioInit();
})();
