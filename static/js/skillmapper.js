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
    return safeArray(data.top_roles || data.roles || data.role_matches);
  }

  function getHiringNowFromData(data) {
    if (!data || typeof data !== "object") return [];
    return safeArray(data.hiring_now || data.market_snapshot || data.demand_snapshot);
  }

  function getMetaFromData(data) {
    if (!data || typeof data !== "object") return {};
    return data.meta || {};
  }

  // ---------- Rendering helpers ----------

  function renderRoleCard(role) {
    if (!role) return "";

    var score =
      typeof role.match_score === "number" ? role.match_score : null;
    var name = escapeHtml(
      role.title || role.role_name || role.name || "Unknown role"
    );
    var shortDesc = escapeHtml(
      role.why_fit || role.short_description || role.description || ""
    );

    var matchLabel = "";
    if (score !== null) {
      if (score >= 80) {
        matchLabel = "Strong match (" + score + "%)";
      } else if (score >= 60) {
        matchLabel = "Moderate match (" + score + "%)";
      } else {
        matchLabel = "Explore (" + score + "%)";
      }
    }

    // Primary skills: flatten primary_skill_clusters[].skills
    var clusters = safeArray(role.primary_skill_clusters);
    var haveSkills = [];
    clusters.forEach(function (c) {
      safeArray(c.skills).forEach(function (s) {
        haveSkills.push(String(s));
      });
    });
    var matchedSkills = haveSkills
      .slice(0, 12)
      .map(function (s) {
        return (
          '<span class="sm-chip sm-chip-match">' +
          escapeHtml(s) +
          "</span>"
        );
      })
      .join("");

    // Gaps: from gaps[].skill
    var gaps = safeArray(role.gaps);
    var missingCore = gaps
      .slice(0, 8)
      .map(function (g) {
        var label = g && g.skill ? String(g.skill) : "";
        return (
          '<span class="sm-chip sm-chip-gap">' +
          escapeHtml(label) +
          "</span>"
        );
      })
      .join("");

    // Nice-to-have: use micro_projects titles as a hint
    var microProjects = safeArray(role.micro_projects);
    var niceToHave = microProjects
      .map(function (m) {
        return m && m.title ? String(m.title) : "";
      })
      .filter(Boolean)
      .slice(0, 6)
      .map(function (t) {
        return (
          '<span class="sm-chip sm-chip-nice">' +
          escapeHtml(t) +
          "</span>"
        );
      })
      .join("");

    // Learning areas: from gaps[].how_to_learn
    var learningAreas = gaps
      .map(function (g) {
        return g && g.how_to_learn ? String(g.how_to_learn) : "";
      })
      .filter(Boolean)
      .slice(0, 6)
      .map(function (item) {
        return "<li>" + escapeHtml(item) + "</li>";
      })
      .join("");

    // First steps: from micro_projects[].deliverables
    var firstStepsArr = [];
    microProjects.forEach(function (m) {
      safeArray(m.deliverables).forEach(function (d) {
        firstStepsArr.push(String(d));
      });
    });
    var firstSteps = firstStepsArr
      .slice(0, 6)
      .map(function (item) {
        return "<li>" + escapeHtml(item) + "</li>";
      })
      .join("");

    // Micro-project ideas: title + outcome
    var projects = microProjects
      .slice(0, 4)
      .map(function (m) {
        var t = m.title || "Project";
        var o = m.outcome || "";
        var label = t + (o ? " — " + o : "");
        return "<li>" + escapeHtml(label) + "</li>";
      })
      .join("");

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
        ? '      <div><div class="sm-label-small">You already show</div>' +
          matchedSkills +
          "</div>"
        : "") +
      (missingCore
        ? '      <div><div class="sm-label-small">Core gaps to build</div>' +
          missingCore +
          "</div>"
        : "") +
      (niceToHave
        ? '      <div><div class="sm-label-small">Nice-to-have / stretch</div>' +
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
            ? '    <div><h4>First 3–5 steps</h4><ul class="sm-list">' +
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
      "</article>"
    );
  }

  function renderHiringNow(hiringNow) {
    var items = safeArray(hiringNow);
    if (!items.length) return "";

    var cards = items
      .map(function (item) {
        var name = escapeHtml(
          item.role_group || item.name || "Role group"
        );
        var pctRaw = item.share_estimate_pct;
        var pct =
          typeof pctRaw === "number"
            ? (pctRaw.toFixed(1).replace(/\.0$/, "") || "0") + "%"
            : typeof pctRaw === "string"
            ? pctRaw
            : "";
        var notes = escapeHtml(item.note || item.description || "");
        var roles = safeArray(item.roles)
          .map(function (r) {
            return (
              '<code class="sm-code">' + escapeHtml(String(r)) + "</code>"
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
          (roles
            ? '  <p class="sm-hiring-keywords">' + roles + "</p>"
            : "") +
          "</div>"
        );
      })
      .join("");

    return '<div class="sm-hiring-grid">' + cards + "</div>";
  }

  function buildOverviewHTML(roles) {
    if (!roles || !roles.length) {
      return (
        '<section class="sm-section-card sm-section-overview">' +
        "  <h3>Where you stand today</h3>" +
        '  <p class="sm-section-sub">We couldn’t infer strong matches yet. Add a bit more detail into your Profile Portal or resume for better results.</p>' +
        "</section>"
      );
    }

    var primary = roles[0] || {};
    var alt1 = roles[1] || null;
    var alt2 = roles[2] || null;

    var score =
      typeof primary.match_score === "number" ? primary.match_score : null;
    var matchLabel = "";
    if (score !== null) {
      if (score >= 80) matchLabel = "Strong match (" + score + "%)";
      else if (score >= 60) matchLabel = "Moderate match (" + score + "%)";
      else matchLabel = "Explore (" + score + "%)";
    }

    var primaryTitle = escapeHtml(
      primary.title || primary.role_name || primary.name || "Role"
    );
    var why = escapeHtml(primary.why_fit || primary.description || "");

    // Key strengths: first few skills from clusters
    var clusters = safeArray(primary.primary_skill_clusters);
    var keySkills = [];
    clusters.forEach(function (c) {
      safeArray(c.skills).forEach(function (s) {
        keySkills.push(String(s));
      });
    });
    keySkills = keySkills.slice(0, 6);

    var strengthsLine = keySkills.length
      ? "Key strengths: " + keySkills.join(", ")
      : "";

    var altTitles = [];
    if (alt1 && (alt1.title || alt1.name)) {
      altTitles.push(alt1.title || alt1.name);
    }
    if (alt2 && (alt2.title || alt2.name)) {
      altTitles.push(alt2.title || alt2.name);
    }
    var altLine = altTitles.length
      ? "Alternative paths: " + altTitles.join(" · ")
      : "";

    var bullets = [];
    bullets.push(
      "Best-fit role right now: " +
        primaryTitle +
        (matchLabel ? " — " + matchLabel : "")
    );
    if (strengthsLine) bullets.push(strengthsLine);
    if (altLine) bullets.push(altLine);

    var bulletsHTML = bullets
      .map(function (b) {
        return "<li>" + escapeHtml(b) + "</li>";
      })
      .join("");

    return (
      '<section class="sm-section-card sm-section-overview">' +
      "  <h3>Where you stand today</h3>" +
      (why
        ? '  <p class="sm-section-sub">' + why + "</p>"
        : '  <p class="sm-section-sub">Overview of your current fit based on your Profile Portal and resume.</p>') +
      (bulletsHTML
        ? '  <ul class="sm-list sm-list-tight">' + bulletsHTML + "</ul>"
        : "") +
      "</section>"
    );
  }

  function renderFullSkillmapHTML(data) {
    var roles = getRolesFromData(data);
    var roleCards = roles.map(renderRoleCard).join("");

    var hiringNowInner = renderHiringNow(getHiringNowFromData(data));

    var meta = getMetaFromData(data);
    var source = escapeHtml(meta.source || "");
    var usingProfile = !!meta.using_profile;
    var region = escapeHtml(meta.region_focus || meta.region || "India");
    var version = escapeHtml(meta.version || "");
    var generated = escapeHtml(meta.generated_at_utc || "");

    var metaBits = [];
    if (source) metaBits.push(source === "pro" ? "Pro run" : "Free run");
    if (usingProfile) metaBits.push("Profile Portal + resume");
    metaBits.push("Region focus: " + region);
    if (version) metaBits.push("Model version: " + version);
    if (generated) metaBits.push("Generated: " + generated);
    var metaLine = metaBits.join(" · ");

    var cta = escapeHtml(data.call_to_action || "");

    // Overview section built from the roles
    var overviewHTML = buildOverviewHTML(roles);

    // Roles card section
    var rolesSectionHTML = roles.length
      ? '<section class="sm-section-card sm-section-roles">' +
        "  <h3>Top roles based on your current profile</h3>" +
        '  <p class="sm-section-sub">Three near-fit roles based on your current skills, projects, and resume.</p>' +
        '  <div class="sm-roles-grid">' +
        roleCards +
        "  </div>" +
        "</section>"
      : '<section class="sm-section-card sm-section-roles">' +
        "  <h3>Top roles based on your current profile</h3>" +
        '  <p class="sm-section-sub">No roles returned yet. Try adding more detail into your Profile Portal or the optional skills box.</p>' +
        "</section>";

    // Hiring now section
    var hiringSectionHTML = "";
    if (hiringNowInner) {
      hiringSectionHTML =
        '<section class="sm-section-card sm-section-hiring">' +
        "  <h3>What’s hiring now (India)</h3>" +
        '  <p class="sm-section-sub">Model estimates based on Indian tech & analytics roles. Not live-scraped.</p>' +
        hiringNowInner +
        "</section>";
    }

    // CTA section
    var nextSectionHTML = "";
    if (cta) {
      nextSectionHTML =
        '<section class="sm-section-card sm-section-next">' +
        "  <h3>What to do next</h3>" +
        '  <p class="sm-section-sub">' +
        cta +
        "</p>" +
        "</section>";
    }

    return (
      '<div class="sm-panel-full">' +
      '  <header class="sm-results-head">' +
      '    <div>' +
      "      <h2>Your role roadmap</h2>" +
      (metaLine ? '      <div class="sm-meta">' + metaLine + "</div>" : "") +
      "    </div>" +
      "  </header>" +
      '  <div class="sm-section-grid-main">' +
      overviewHTML +
      rolesSectionHTML +
      "  </div>" +
      hiringSectionHTML +
      nextSectionHTML +
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
        '  <p class="sm-section-sub">We could not infer a clear role. Try adding a bit more detail into your Profile Portal or the optional skills box.</p>' +
        "</div>"
      );
    }

    var name = escapeHtml(firstRole.title || firstRole.name || "Role");
    var shortDesc = escapeHtml(
      firstRole.why_fit || firstRole.description || ""
    );
    var clusters = safeArray(firstRole.primary_skill_clusters);
    var haveSkills = [];
    clusters.forEach(function (c) {
      safeArray(c.skills).forEach(function (s) {
        haveSkills.push(String(s));
      });
    });
    var matchedSkills = haveSkills
      .slice(0, 8)
      .map(function (s) {
        return (
          '<span class="sm-chip sm-chip-match">' +
          escapeHtml(s) +
          "</span>"
        );
      })
      .join("");

    var gaps = safeArray(firstRole.gaps);
    var missingCore = gaps
      .slice(0, 5)
      .map(function (g) {
        var label = g && g.skill ? String(g.skill) : "";
        return (
          '<span class="sm-chip sm-chip-gap">' +
          escapeHtml(label) +
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
        ? '        <div><div class="sm-label-small">You already show</div>' +
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
      '        <p class="sm-pro-lock-text">Pro shows full 3-role roadmap, India salary bands, and detailed “what to do next” roadmaps.</p>' +
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
