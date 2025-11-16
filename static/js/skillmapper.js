// static/js/skillmapper.js
(function () {
  const root = document.getElementById("sm-root");
  if (!root) {
    console.warn("SkillMapper: root element not found");
    return;
  }

  const isProUser = root.dataset.isPro === "true";
  const billingPath = root.dataset.billingPath || "/pricing";

  const freeBtn = document.getElementById("sm-free-btn");
  const freeInput = document.getElementById("sm-free-input");
  const freeDomain = document.getElementById("sm-free-domain");

  const proBtn = document.getElementById("sm-pro-btn");
  const proUseProfile = document.getElementById("sm-pro-use-profile");
  const proRegion = document.getElementById("sm-pro-region");
  const proResume = document.getElementById("sm-pro-resume");

  const resultsEl = document.getElementById("sm-results");
  const resultsInnerEl = document.getElementById("sm-results-inner");
  const toastEl = document.getElementById("sm-toast");

  function showToast(message, kind) {
    if (!toastEl) return;
    const type = kind || "info";
    toastEl.textContent = message;
    toastEl.classList.remove(
      "hidden",
      "sm-toast-error",
      "sm-toast-success",
      "sm-toast-info"
    );
    toastEl.classList.add(
      type === "error"
        ? "sm-toast-error"
        : type === "success"
        ? "sm-toast-success"
        : "sm-toast-info"
    );
    setTimeout(function () {
      toastEl.classList.add("hidden");
    }, 3500);
  }

  function escapeHtml(s) {
    if (typeof s !== "string") return "";
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function safeArray(val) {
    return Array.isArray(val) ? val : [];
  }

  // ---------- Schema compatibility helpers ----------

  function getRolesFromData(data) {
    if (!data || typeof data !== "object") return [];
    return safeArray(
      data.top_roles ||
        data.roles ||
        data.role_matches ||
        data.top_3_roles ||
        data.mapped_roles
    );
  }

  function getHiringNowFromData(data) {
    if (!data || typeof data !== "object") return [];
    return safeArray(
      data.hiring_now_india ||
        data.hiring_now ||
        data.market_snapshot ||
        data.demand_snapshot
    );
  }

  function getHighPaidFromData(data) {
    if (!data || typeof data !== "object") return [];
    return safeArray(
      data.high_paid_roles_india ||
        data.high_paid_roles ||
        data.india_high_paid ||
        data.salary_snapshot
    );
  }

  function getMetaFromData(data) {
    if (!data || typeof data !== "object") return {};
    return data.meta || {};
  }

  // ---------- Rendering helpers ----------

  function renderRoleCard(role) {
    if (!role) return "";

    var name = escapeHtml(role.role_name || role.name || "Unknown role");
    var matchLabel = escapeHtml(role.match_label || role.match_level || "");
    var shortDesc = escapeHtml(
      role.short_description || role.description || role.summary || ""
    );

    var matchedSkills = safeArray(
      role.matched_skills || role.current_skills || role.have_skills
    )
      .map(function (s) {
        return (
          '<span class="sm-chip sm-chip-match">' +
          escapeHtml(String(s)) +
          "</span>"
        );
      })
      .join("");
    var missingCore = safeArray(
      role.missing_core_skills || role.gap_skills || role.missing_skills
    )
      .map(function (s) {
        return (
          '<span class="sm-chip sm-chip-gap">' +
          escapeHtml(String(s)) +
          "</span>"
        );
      })
      .join("");
    var niceToHave = safeArray(
      role.nice_to_have_skills || role.bonus_skills
    )
      .map(function (s) {
        return (
          '<span class="sm-chip sm-chip-nice">' +
          escapeHtml(String(s)) +
          "</span>"
        );
      })
      .join("");

    var learningAreas = safeArray(
      role.learning_focus_areas || role.learning_plan || role.study_plan
    )
      .map(function (item) {
        return "<li>" + escapeHtml(String(item)) + "</li>";
      })
      .join("");
    var firstSteps = safeArray(
      role.first_steps || role.next_steps || role.action_steps
    )
      .map(function (item) {
        return "<li>" + escapeHtml(String(item)) + "</li>";
      })
      .join("");
    var projects = safeArray(role.suggested_projects || role.micro_projects)
      .map(function (item) {
        return "<li>" + escapeHtml(String(item)) + "</li>";
      })
      .join("");

    var india = role.india_context || role.india || {};
    var indiaDemand = escapeHtml(
      india.india_hiring_demand || india.demand || ""
    );
    var indiaSalary = escapeHtml(
      india.india_fresher_salary_band || india.salary_band || india.salary || ""
    );
    var indiaCompanies = safeArray(
      india.typical_indian_companies ||
        india.common_companies ||
        india.example_companies
    )
      .map(function (c) {
        return escapeHtml(String(c));
      })
      .join(", ");
    var keywords = safeArray(
      india.keywords_for_job_portals ||
        india.search_keywords ||
        india.portal_keywords
    )
      .map(function (k) {
        return (
          '<code class="sm-code">' + escapeHtml(String(k)) + "</code>"
        );
      })
      .join(" ");

    return (
      '<article class="sm-role-card">' +
      '  <header class="sm-role-head">' +
      '    <div>' +
      '      <h3 class="sm-role-title">' +
      name +
      "</h3>" +
      (shortDesc ? '      <p class="sm-role-sub">' + shortDesc + "</p>" : "") +
      "    </div>" +
      (matchLabel
        ? '    <span class="sm-role-badge">' + matchLabel + "</span>"
        : "") +
      "  </header>" +
      '  <section class="sm-role-section">' +
      "    <h4>Skill match</h4>" +
      '    <div class="sm-role-skills">' +
      (matchedSkills
        ? '      <div><div class="sm-label-small">You already have</div>' +
          matchedSkills +
          "</div>"
        : "") +
      (missingCore
        ? '      <div><div class="sm-label-small">Core to build</div>' +
          missingCore +
          "</div>"
        : "") +
      (niceToHave
        ? '      <div><div class="sm-label-small">Nice to have</div>' +
          niceToHave +
          "</div>"
        : "") +
      "    </div>" +
      "  </section>" +
      (learningAreas || firstSteps || projects
        ? '  <section class="sm-role-section sm-role-grid">' +
          (learningAreas
            ? '    <div><h4>What to study next</h4><ul class="sm-list">' +
              learningAreas +
              "</ul></div>"
            : "") +
          (firstSteps
            ? '    <div><h4>First 3 steps</h4><ul class="sm-list">' +
              firstSteps +
              "</ul></div>"
            : "") +
          (projects
            ? '    <div><h4>Micro-project ideas</h4><ul class="sm-list">' +
              projects +
              "</ul></div>"
            : "") +
          "  </section>"
        : "") +
      (indiaDemand || indiaSalary || indiaCompanies || keywords
        ? '  <section class="sm-role-section">' +
          "    <h4>India market snapshot (model estimate)</h4>" +
          '    <div class="sm-role-india">' +
          (indiaDemand
            ? '      <p><span class="sm-label-small">Demand:</span> ' +
              indiaDemand +
              "</p>"
            : "") +
          (indiaSalary
            ? '      <p><span class="sm-label-small">Fresher salary:</span> ' +
              indiaSalary +
              "</p>"
            : "") +
          (indiaCompanies
            ? '      <p><span class="sm-label-small">Typical companies:</span> ' +
              indiaCompanies +
              "</p>"
            : "") +
          (keywords
            ? '      <p><span class="sm-label-small">Search keywords:</span> ' +
              keywords +
              "</p>"
            : "") +
          "    </div>" +
          '    <p class="sm-note">These are model estimates based on common patterns, not live-scraped data.</p>' +
          "  </section>"
        : "") +
      "</article>"
    );
  }

  function renderHiringNow(hiringNow) {
    var items = safeArray(hiringNow);
    if (!items.length) return "";

    var cards = items
      .map(function (item) {
        var name = escapeHtml(item.role_name || item.name || "Role");
        var pctRaw =
          item.demand_estimate_percent !== undefined
            ? item.demand_estimate_percent
            : item.estimate_percent !== undefined
            ? item.estimate_percent
            : item.share_percent;
        var pct =
          typeof pctRaw === "number"
            ? pctRaw + "%"
            : typeof pctRaw === "string"
            ? pctRaw
            : "";
        var notes = escapeHtml(item.notes || item.description || "");
        var keywords = safeArray(
          item.typical_keywords || item.search_keywords
        )
          .map(function (k) {
            return (
              '<code class="sm-code">' + escapeHtml(String(k)) + "</code>"
            );
          })
          .join(" ");

        return (
          '<div class="sm-hiring-card">' +
          '  <div class="sm-hiring-main">' +
          '    <div class="sm-hiring-role">' +
          name +
          "</div>" +
          (pct
            ? '    <div class="sm-hiring-pct">' +
              pct +
              " of postings (model estimate)</div>"
            : "") +
          "  </div>" +
          (notes ? '  <p class="sm-hiring-notes">' + notes + "</p>" : "") +
          (keywords ? '  <p class="sm-hiring-keywords">' + keywords + "</p>" : "") +
          "</div>"
        );
      })
      .join("");

    return (
      '<section class="sm-section">' +
      "  <h3>What’s hiring now (India)</h3>" +
      '  <p class="sm-section-sub">Rough model estimates of demand based on Indian tech & analytics roles.</p>' +
      '  <div class="sm-hiring-grid">' +
      cards +
      "  </div>" +
      "</section>"
    );
  }

  function renderHighPaid(highPaid) {
    var items = safeArray(highPaid);
    if (!items.length) return "";

    var cards = items
      .map(function (item) {
        var name = escapeHtml(item.role_name || item.name || "Role");
        var band = escapeHtml(
          item.typical_salary_band || item.salary_band || ""
        );
        var diff = escapeHtml(item.difficulty_label || item.competition || "");
        var requirements = safeArray(
          item.key_requirements || item.requirements
        )
          .map(function (r) {
            return "<li>" + escapeHtml(String(r)) + "</li>";
          })
          .join("");

        return (
          '<div class="sm-high-card">' +
          '  <div class="sm-high-head">' +
          '    <div class="sm-high-role">' +
          name +
          "</div>" +
          (diff ? '    <div class="sm-high-diff">' + diff + "</div>" : "") +
          "  </div>" +
          (band ? '  <p class="sm-high-band">' + band + "</p>" : "") +
          (requirements
            ? '  <ul class="sm-list sm-high-reqs">' + requirements + "</ul>"
            : "") +
          "</div>"
        );
      })
      .join("");

    return (
      '<section class="sm-section">' +
      "  <h3>High-paid roles (India, 0–3 years)</h3>" +
      '  <p class="sm-section-sub">Typical bands are for top product/startups and strong candidates. These are model estimates, not salary guarantees.</p>' +
      '  <div class="sm-high-grid">' +
      cards +
      "  </div>" +
      "</section>"
    );
  }

  function renderFullSkillmapHTML(data) {
    var roles = getRolesFromData(data);
    var roleCards = roles.map(renderRoleCard).join("");

    var hiringNowHTML = renderHiringNow(getHiringNowFromData(data));
    var highPaidHTML = renderHighPaid(getHighPaidFromData(data));

    var meta = getMetaFromData(data);
    var source = escapeHtml(meta.source || "");
    var usingProfile = !!meta.using_profile;
    var region = escapeHtml(meta.region_focus || meta.region || "India");
    var version = escapeHtml(meta.version || "");

    var metaBits = [];
    if (source) metaBits.push(source === "pro" ? "Pro run" : "Free run");
    if (usingProfile) metaBits.push("Profile + resume");
    metaBits.push("Region focus: " + region);
    if (version) metaBits.push("Model version: " + version);

    var metaLine = metaBits.join(" · ");

    return (
      '<div class="sm-panel-full">' +
      '  <header class="sm-results-head">' +
      '    <h2>Your role roadmap</h2>' +
      (metaLine ? '    <div class="sm-meta">' + metaLine + "</div>" : "") +
      "  </header>" +
      (roles.length
        ? '<section class="sm-section">' +
          "  <h3>Top roles based on your current profile</h3>" +
          '  <div class="sm-roles-grid">' +
          roleCards +
          "  </div>" +
          "</section>"
        : '<section class="sm-section"><p>No roles returned. Try adding more detail about your skills/interests.</p></section>') +
      hiringNowHTML +
      highPaidHTML +
      "</div>"
    );
  }

  function renderBasicPanelHTML(data) {
    var roles = getRolesFromData(data);
    var firstRole = roles[0];

    if (!firstRole) {
      return (
        '<div class="sm-panel-basic">' +
        "  <h2>Basic view (Free)</h2>" +
        '  <p class="sm-section-sub">We could not infer a clear role. Try adding more detail about your skills and interests.</p>' +
        "</div>"
      );
    }

    var name = escapeHtml(firstRole.role_name || firstRole.name || "Role");
    var shortDesc = escapeHtml(
      firstRole.short_description || firstRole.description || ""
    );
    var matchedSkills = safeArray(
      firstRole.matched_skills || firstRole.current_skills
    )
      .slice(0, 8)
      .map(function (s) {
        return (
          '<span class="sm-chip sm-chip-match">' +
          escapeHtml(String(s)) +
          "</span>"
        );
      })
      .join("");
    var missingCore = safeArray(
      firstRole.missing_core_skills || firstRole.gap_skills
    )
      .slice(0, 5)
      .map(function (s) {
        return (
          '<span class="sm-chip sm-chip-gap">' +
          escapeHtml(String(s)) +
          "</span>"
        );
      })
      .join("");

    return (
      '<div class="sm-panel-basic">' +
      "  <h2>Basic view (Free)</h2>" +
      '  <p class="sm-section-sub">Here’s one role that fits your current skills snapshot.</p>' +
      '  <article class="sm-role-card sm-role-basic">' +
      '    <header class="sm-role-head">' +
      "      <div>" +
      '        <h3 class="sm-role-title">' +
      name +
      "</h3>" +
      (shortDesc
        ? '        <p class="sm-role-sub">' + shortDesc + "</p>"
        : "") +
      "      </div>" +
      "    </header>" +
      '    <section class="sm-role-section">' +
      "      <h4>Skill match</h4>" +
      '      <div class="sm-role-skills">' +
      (matchedSkills
        ? '        <div><div class="sm-label-small">You already have</div>' +
          matchedSkills +
          "</div>"
        : "") +
      (missingCore
        ? '        <div><div class="sm-label-small">Core to build</div>' +
          missingCore +
          "</div>"
        : "") +
      "      </div>" +
      "    </section>" +
      "  </article>" +
      '  <p class="sm-note">Pro analysis goes deeper with 3 roles, India salary bands, and detailed roadmaps.</p>' +
      "</div>"
    );
  }

  function renderProPreviewHTML(data, billingHref) {
    var fullHTML = renderFullSkillmapHTML(data);
    var href = escapeHtml(billingHref || "/pricing");

    return (
      '<div class="sm-panel-pro-preview">' +
      '  <div class="sm-panel-pro-inner">' +
      fullHTML +
      '    <div class="sm-pro-blur-overlay">' +
      '      <div class="sm-pro-blur-content">' +
      '        <div class="sm-pro-lock-icon">🔒</div>' +
      '        <div class="sm-pro-lock-title">Skill Mapper Pro (preview)</div>' +
      '        <p class="sm-pro-lock-text">Pro shows full 3-role roadmap, India salary bands, and “What’s hiring now” in detail.</p>' +
      '        <a href="' +
      href +
      '" class="sm-btn sm-btn-upgrade">Unlock with Pro ⭐</a>' +
      "      </div>" +
      "    </div>" +
      "  </div>" +
      "</div>"
    );
  }

  // ---------- Render entry points ----------

  function renderFreeResult(data) {
    if (!resultsEl || !resultsInnerEl) return;
    resultsEl.classList.remove("hidden");
    console.log("SkillMapper Free data:", data);

    if (!isProUser) {
      var basicHTML = renderBasicPanelHTML(data);
      var previewHTML = renderProPreviewHTML(data, billingPath);

      resultsInnerEl.innerHTML =
        '<div class="sm-results-layout sm-results-layout-two">' +
        basicHTML +
        previewHTML +
        "</div>";
    } else {
      var fullHTML = renderFullSkillmapHTML(data);
      resultsInnerEl.innerHTML =
        '<div class="sm-results-layout">' + fullHTML + "</div>";
    }
  }

  function renderProResult(data) {
    if (!resultsEl || !resultsInnerEl) return;
    resultsEl.classList.remove("hidden");
    console.log("SkillMapper Pro data:", data);

    var fullHTML = renderFullSkillmapHTML(data);
    resultsInnerEl.innerHTML =
      '<div class="sm-results-layout">' + fullHTML + "</div>";
  }

  // ---------- Network helper ----------

  function postJSON(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest"
      },
      body: JSON.stringify(payload || {})
    }).then(function (resp) {
      return resp
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          return { status: resp.status, body: data };
        });
    });
  }

  // ---------- Event listeners ----------

  if (freeBtn) {
    freeBtn.addEventListener("click", function () {
      var text = (freeInput && freeInput.value) || "";
      var domain = (freeDomain && freeDomain.value) || "";

      if (!text.trim()) {
        showToast("Please paste your skills/interests text.", "error");
        return;
      }

      freeBtn.disabled = true;
      freeBtn.textContent = "Running…";

      postJSON("/skillmapper/free", {
        free_text_skills: text,
        target_domain: domain
      })
        .then(function (res) {
          var status = res.status;
          var body = res.body || {};
          if (!body || body.ok === false) {
            var errMsg =
              (body && body.error) ||
              (status === 402
                ? "Not enough Silver credits to run Skill Mapper."
                : "Skill Mapper Free run failed.");
            showToast(errMsg, "error");
            return;
          }
          renderFreeResult(body.data || {});
          showToast("Skill Mapper (Free) complete.", "success");
        })
        .catch(function (e) {
          console.error("SkillMapper /free error", e);
          showToast(
            "Something went wrong while running Skill Mapper.",
            "error"
          );
        })
        .finally(function () {
          freeBtn.disabled = false;
          freeBtn.textContent = "Run Skill Mapper (Free)";
        });
    });
  }

  if (proBtn) {
    proBtn.addEventListener("click", function () {
      if (!isProUser) {
        showToast("Skill Mapper Pro requires an active Pro plan.", "error");
        return;
      }

      var useProfile = proUseProfile ? !!proUseProfile.checked : true;
      var region = (proRegion && proRegion.value) || "";
      var resumeText = (proResume && proResume.value) || "";

      proBtn.disabled = true;
      proBtn.textContent = "Analyzing…";

      postJSON("/skillmapper/pro", {
        use_profile: useProfile,
        region_sector: region,
        resume_text: resumeText
      })
        .then(function (res) {
          var status = res.status;
          var body = res.body || {};
          if (!body || body.ok === false) {
            var errMsg =
              (body && body.error) ||
              (status === 403
                ? "Skill Mapper Pro requires an active Pro plan."
                : "Skill Mapper Pro run failed.");
            showToast(errMsg, "error");
            return;
          }
          renderProResult(body.data || {});
          showToast("Skill Mapper Pro analysis complete.", "success");
        })
        .catch(function (e) {
          console.error("SkillMapper /pro error", e);
          showToast(
            "Something went wrong while running Skill Mapper Pro.",
            "error"
          );
        })
        .finally(function () {
          proBtn.disabled = false;
          proBtn.textContent = "Analyze from Profile";
        });
    });
  }
})();
