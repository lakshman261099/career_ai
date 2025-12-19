# modules/settings/routes.py

from datetime import date as ddate
from datetime import datetime
from datetime import datetime as dt

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from models import Project, ResumeAsset, UserProfile, db

# Resume helpers
from modules.resume.utils import extract_text_from_pdf
from modules.resume.parser import parse_resume_to_profile
from modules.resume.skills_categorizer import categorize_skills  # NEW

settings_bp = Blueprint(
    "settings", __name__, template_folder="../../templates/settings"
)

ALLOWED_RESUME_EXTS = {"pdf"}  # PDF only


def _allowed_file(fname: str) -> bool:
    return "." in fname and fname.rsplit(".", 1)[1].lower() in ALLOWED_RESUME_EXTS


# ---------------------------
# Skills helpers (container v2 with categorizer)
# ---------------------------
def _categorize_skills_for_container(skills_list):
    """
    Use central categorizer to map skills into:
      Programming, Data Libraries, Visualization, Data Engineering,
      Databases, Tools, Cloud, Soft Skills, Other.
    """
    return categorize_skills(skills_list)


def _build_skills_container(skills_list):
    """
    Build the canonical skills container shape:

      {
        "list": [ { "name": "...", "level": 3 }, ... ],
        "raw": "React, Next.js, PostgreSQL",
        "structured": {
          "Programming": [...],
          "Databases": [...],
          ...,
          "Other": [...]
        }
      }

    - `skills_list` is expected to be a list of dicts or strings.
    """
    # Ensure list is a list of {name, level}
    normalized = _normalize_skills(skills_list)
    names = [s["name"] for s in normalized]

    raw_str = ", ".join(names) if names else ""

    structured = _categorize_skills_for_container(normalized)

    return {
        "list": normalized,
        "raw": raw_str,
        "structured": structured,
    }


# ---------------------------
# Normalizers
# ---------------------------
def _normalize_skills(raw):
    """
    Normalize profile.skills into a stable list of {name, level} dicts.

    Accepts:
      - legacy list: [{"name":...,"level":...}, "Python", ...]
      - new container: {"list":[...], "raw":"...", "structured":{...}}
    """
    # If we're already in the new container format, unwrap the list
    if isinstance(raw, dict) and "list" in raw:
        raw = raw.get("list") or []

    out = []
    for item in raw or []:
        name, level = "", 3
        if isinstance(item, dict):
            name = (
                item.get("name") or item.get("skill") or item.get("title") or ""
            ).strip()
            try:
                level = int(item.get("level", 3))
            except Exception:
                level = 3
        elif isinstance(item, str):
            name = item.strip()
        if not name:
            continue
        level = max(1, min(5, level))
        out.append({"name": name, "level": level})
    return out


def _normalize_education(raw):
    return [
        {
            "degree": (ed.get("degree") or "").strip(),
            "school": (ed.get("school") or "").strip(),
            "year": (str(ed.get("year") or "").strip()),
        }
        for ed in (raw or [])
        if isinstance(ed, dict)
    ]


def _normalize_certs(raw):
    out = []
    for c in raw or []:
        if isinstance(c, dict):
            out.append(
                {
                    "name": (c.get("name") or "").strip(),
                    "year": (str(c.get("year") or "").strip()) or None,
                }
            )
        elif isinstance(c, str):
            out.append({"name": c.strip(), "year": None})
    return out


def _normalize_links(raw):
    res = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            k2, v2 = (k or "").strip(), (v or "").strip()
            if k2 and v2:
                res[k2] = v2
    return res


def _normalize_experience(raw):
    out = []
    for j in raw or []:
        if not isinstance(j, dict):
            continue
        bullets_raw = j.get("bullets")
        if isinstance(bullets_raw, list):
            bullets = [str(b).strip() for b in bullets_raw if str(b).strip()]
        elif isinstance(bullets_raw, str):
            bullets = [b.strip() for b in bullets_raw.split("\n") if b.strip()]
        else:
            bullets = []
        out.append(
            {
                "role": (j.get("role") or "").strip(),
                "company": (j.get("company") or "").strip(),
                "start": (j.get("start") or "").strip(),
                "end": (j.get("end") or "").strip() or None,
                "bullets": bullets,
            }
        )
    return out


def _build_view(prof: UserProfile, projects: list):
    """
    Build a safe view model for the template, regardless of how
    UserProfile.skills/education/... are stored.
    """
    return dict(
        skills=_normalize_skills(prof.skills or []),
        education=_normalize_education(prof.education or []),
        certifications=_normalize_certs(prof.certifications or []),
        links=_normalize_links(prof.links or {}),
        experience=_normalize_experience(prof.experience or []),
        projects=projects,
    )


def _ensure_profile():
    try:
        prof = UserProfile.query.filter_by(user_id=current_user.id).first()
        if not prof:
            prof = UserProfile(
                user_id=current_user.id, full_name=current_user.name or None
            )
            db.session.add(prof)
            db.session.commit()
        return prof
    except Exception:
        current_app.logger.exception("ensure_profile failed")
        db.session.rollback()
        return None


def _try_parse_date(val: str):
    """Accept YYYY-MM-DD or YYYY-MM; return date or None."""
    if not val:
        return None
    s = val.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            if fmt == "%Y-%m":
                return ddate.fromisoformat(s + "-01")
            return ddate.fromisoformat(s)
        except Exception:
            try:
                return dt.strptime(s, fmt).date()
            except Exception:
                continue
    return None


def _parse_project_payload(data):
    """Parse form or JSON payload into Project fields dict."""
    title = (data.get("title") or data.get("proj_title") or "").strip()
    if not title:
        return None, "Project title is required."

    short_desc = (
        data.get("short_desc") or data.get("proj_short_desc") or ""
    ).strip() or None
    role = (data.get("role") or data.get("proj_role") or "").strip() or None

    start_raw = (data.get("start_date") or data.get("proj_start_date") or "").strip()
    end_raw = (data.get("end_date") or data.get("proj_end_date") or "").strip()
    start_date = _try_parse_date(start_raw)
    end_date = _try_parse_date(end_raw)

    # tech_stack: comma-separated or list
    ts = data.get("tech_stack") or data.get("proj_stack")
    if isinstance(ts, str):
        tech_stack = [t.strip() for t in ts.split(",") if t.strip()]
    elif isinstance(ts, list):
        tech_stack = [str(t).strip() for t in ts if str(t).strip()]
    else:
        tech_stack = []

    # bullets: newline or list
    br = data.get("bullets") or data.get("proj_bullets")
    if isinstance(br, str):
        bullets = [b.strip() for b in br.split("\n") if b.strip()]
    elif isinstance(br, list):
        bullets = [str(b).strip() for b in br if str(b).strip()]
    else:
        bullets = []

    # links: pairs (label,url)
    links = []
    labels = data.get("link_labels") or data.get("proj_link_labels") or []
    urls = data.get("link_urls") or data.get("proj_link_urls") or []
    if isinstance(labels, str):
        labels = [labels]
    if isinstance(urls, str):
        urls = [urls]
    for i in range(max(len(labels), len(urls))):
        lab = (labels[i] if i < len(labels) else "").strip() or "Link"
        url = (urls[i] if i < len(urls) else "").strip()
        if url:
            links.append({"label": lab, "url": url})

    return {
        "title": title,
        "short_desc": short_desc,
        "role": role,
        "start_date": start_date,
        "end_date": end_date,
        "tech_stack": tech_stack,
        "bullets": bullets,
        "links": links,
    }, None


# ---------------------------
# Settings Home
# ---------------------------
@settings_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    _ensure_profile()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Name cannot be empty.", "error")
        else:
            try:
                current_user.name = name
                db.session.commit()
                flash("Profile updated.", "success")
            except Exception:
                current_app.logger.exception("Failed updating name")
                db.session.rollback()
                flash("Could not update profile. Try again.", "error")
        return redirect(url_for("settings.index"))

    try:
        resumes = (
            ResumeAsset.query.filter_by(user_id=current_user.id)
            .order_by(ResumeAsset.created_at.desc())
            .limit(5)
            .all()
        )
    except Exception:
        current_app.logger.exception("Failed loading resumes")
        resumes = []

    return render_template("settings/index.html", resumes=resumes)


# ---------------------------
# Profile Portal (FREE for all) + Projects CRUD
# ---------------------------
@settings_bp.route("/profile", methods=["GET", "POST"], endpoint="profile")
@login_required
def profile():
    # Profile Portal is available to all users (Free + Pro).
    prof = _ensure_profile()
    if not prof:
        flash("Could not load your profile. Please reload.", "error")
        return redirect(url_for("settings.index"))

    # Projects list for this user
    try:
        projects = (
            Project.query.filter_by(user_id=current_user.id)
            .order_by(Project.start_date.desc().nullslast(), Project.id.desc())
            .all()
        )
    except Exception:
        current_app.logger.exception("Failed loading projects")
        projects = []

    if request.method == "POST":
        action = (request.form.get("action") or "").lower()

        # Upload resume (PDF; AI parse)
        if action == "upload":
            file = request.files.get("file")
            if not file or not file.filename:
                flash("Please choose a PDF file.", "error")
                return redirect(url_for("settings.profile"))
            if not _allowed_file(file.filename):
                flash("Only PDF files are allowed.", "error")
                return redirect(url_for("settings.profile"))

            filename = secure_filename(file.filename)

            try:
                # 1) Extract text from PDF
                resume_text = extract_text_from_pdf(file)
                if not resume_text:
                    flash(
                        "We couldn't read text from that PDF. "
                        "Please try another file or paste your resume.",
                        "error",
                    )
                    return redirect(url_for("settings.profile"))

                # 2) Store resume asset (for Job Pack + other features)
                asset = ResumeAsset(
                    user_id=current_user.id,
                    filename=filename,
                    text=resume_text,
                )
                db.session.add(asset)

                # Ensure profile exists
                prof = _ensure_profile()
                if not prof:
                    db.session.commit()
                    flash("Resume uploaded, but profile could not be loaded.", "warning")
                    return redirect(url_for("settings.profile"))

                # 3) Call AI parser to suggest profile fields
                parsed = parse_resume_to_profile(resume_text) or {}

                applied_any = False  # track whether we actually changed something

                # 4) Apply parsed fields non-destructively
                if not prof.full_name and parsed.get("full_name"):
                    prof.full_name = parsed["full_name"].strip()
                    applied_any = True

                if not prof.headline and parsed.get("headline"):
                    prof.headline = parsed["headline"].strip()
                    applied_any = True

                if not prof.summary and parsed.get("summary"):
                    prof.summary = parsed["summary"].strip()
                    applied_any = True

                if not prof.location and parsed.get("location"):
                    prof.location = parsed["location"].strip()
                    applied_any = True

                if not prof.phone and parsed.get("phone"):
                    prof.phone = parsed["phone"].strip()
                    applied_any = True

                # Links: merge, but do not overwrite existing keys
                existing_links = prof.links or {}
                parsed_links = parsed.get("links") or {}
                if isinstance(parsed_links, dict):
                    for k, v in parsed_links.items():
                        k2 = (k or "").strip()
                        v2 = (v or "").strip()
                        if not k2 or not v2:
                            continue
                        if k2 not in existing_links or not existing_links.get(k2):
                            existing_links[k2] = v2
                            applied_any = True
                prof.links = existing_links

                # Skills / education / certifications / experience:
                # if user has nothing yet, seed from parsed
                parsed_skills = parsed.get("skills")
                if not (prof.skills or []) and parsed_skills:
                    # Normalize parsed skills and store as container
                    norm_skills = _normalize_skills(parsed_skills)
                    prof.skills = _build_skills_container(norm_skills)
                    applied_any = True

                if not (prof.education or []) and parsed.get("education"):
                    prof.education = parsed["education"]
                    applied_any = True

                if not (prof.certifications or []) and parsed.get("certifications"):
                    prof.certifications = parsed["certifications"]
                    applied_any = True

                if not (prof.experience or []) and parsed.get("experience"):
                    prof.experience = parsed["experience"]
                    applied_any = True

                prof.updated_at = datetime.utcnow()

                db.session.commit()

                if applied_any:
                    flash(
                        "Resume uploaded and profile auto-filled. "
                        "Review and edit any fields before saving.",
                        "success",
                    )
                else:
                    flash(
                        "Resume uploaded, but we couldn't auto-fill any fields. "
                        "You can still edit your profile manually.",
                        "warning",
                    )

            except Exception:
                current_app.logger.exception("Resume upload/parse failed")
                db.session.rollback()
                flash(
                    "We saved your resume, but could not auto-fill your profile. "
                    "You can still edit fields manually.",
                    "error",
                )

            return redirect(url_for("settings.profile"))

        # Save profile edits
        if action == "save":
            try:
                prof.full_name = (
                    request.form.get("full_name") or ""
                ).strip() or prof.full_name
                prof.headline = (request.form.get("headline") or "").strip() or None
                prof.summary = (request.form.get("summary") or "").strip() or None
                prof.location = (request.form.get("location") or "").strip() or None
                prof.phone = (request.form.get("phone") or "").strip() or None

                # Contact links
                links = {}
                for key in ["email", "website", "linkedin", "github"]:
                    val = (request.form.get(f"contact_{key}") or "").strip()
                    if val:
                        links[key] = val
                lkeys = request.form.getlist("link_keys[]")
                lurls = request.form.getlist("link_urls[]")
                for i in range(max(len(lkeys), len(lurls))):
                    k = (lkeys[i] if i < len(lkeys) else "").strip()
                    v = (lurls[i] if i < len(lurls) else "").strip()
                    if k and v:
                        links[k] = v
                prof.links = links

                # Skills (from form → container)
                names = request.form.getlist("skills_names[]")
                levels = request.form.getlist("skills_levels[]")
                skills_raw_list = []
                for i, nm in enumerate(names or []):
                    nm = (nm or "").strip()
                    if not nm:
                        continue
                    try:
                        lv = int(levels[i])
                    except Exception:
                        lv = 3
                    lv = max(1, min(5, lv))
                    skills_raw_list.append({"name": nm, "level": lv})

                # Store as container {list, raw, structured}
                prof.skills = _build_skills_container(skills_raw_list)

                # Education
                edu_degree = request.form.getlist("edu_degree[]")
                edu_school = request.form.getlist("edu_school[]")
                edu_year = request.form.getlist("edu_year[]")
                education = []
                for i in range(max(len(edu_degree), len(edu_school), len(edu_year))):
                    deg = (edu_degree[i] if i < len(edu_degree) else "").strip()
                    sch = (edu_school[i] if i < len(edu_school) else "").strip()
                    yr = (edu_year[i] if i < len(edu_year) else "").strip()
                    if deg or sch or yr:
                        education.append({"degree": deg, "school": sch, "year": yr})
                prof.education = education

                # Certifications
                certs = []
                cert_name = request.form.getlist("cert_name[]")
                cert_year = request.form.getlist("cert_year[]")
                for i in range(max(len(cert_name), len(cert_year))):
                    cn = (cert_name[i] if i < len(cert_name) else "").strip()
                    cy = (cert_year[i] if i < len(cert_year) else "").strip() or None
                    if cn:
                        certs.append({"name": cn, "year": cy})
                prof.certifications = certs

                # Experience
                exp_role = request.form.getlist("exp_role[]")
                exp_company = request.form.getlist("exp_company[]")
                exp_start = request.form.getlist("exp_start[]")
                exp_end = request.form.getlist("exp_end[]")
                exp_bullets = request.form.getlist("exp_bullets[]")
                experience = []
                for i in range(
                    max(
                        len(exp_role),
                        len(exp_company),
                        len(exp_start),
                        len(exp_end),
                        len(exp_bullets),
                    )
                ):
                    role = (exp_role[i] if i < len(exp_role) else "").strip()
                    comp = (exp_company[i] if i < len(exp_company) else "").strip()
                    st = (exp_start[i] if i < len(exp_start) else "").strip()
                    en = (exp_end[i] if i < len(exp_end) else "").strip() or None
                    blr = exp_bullets[i] if i < len(exp_bullets) else ""
                    bl = [b.strip() for b in (blr or "").split("\n") if b.strip()]
                    if role or comp or st or en or bl:
                        experience.append(
                            {
                                "role": role,
                                "company": comp,
                                "start": st,
                                "end": en,
                                "bullets": bl,
                            }
                        )
                prof.experience = experience

                prof.updated_at = datetime.utcnow()
                db.session.commit()
                flash("Profile saved.", "success")
            except Exception:
                current_app.logger.exception("Failed saving profile")
                db.session.rollback()
                flash("Could not save profile. Please try again.", "error")

        # Create a new Project
        if action == "project_new":
            payload = request.get_json(silent=True) or request.form
            data, err = _parse_project_payload(payload)
            if err:
                flash(err, "error")
                return redirect(url_for("settings.profile"))
            try:
                p = Project(user_id=current_user.id, **data)
                db.session.add(p)
                db.session.commit()
                flash("Project added.", "success")
            except Exception:
                current_app.logger.exception("Failed creating project")
                db.session.rollback()
                flash("Could not add project.", "error")

        # Update an existing Project
        if action == "project_update":
            pid_raw = (
                request.form.get("project_id")
                or (request.get_json(silent=True) or {}).get("project_id")
                or ""
            ).strip()
            if not pid_raw.isdigit():
                flash("Invalid project id.", "error")
                return redirect(url_for("settings.profile"))
            pid = int(pid_raw)
            payload = request.get_json(silent=True) or request.form
            data, err = _parse_project_payload(payload)
            if err:
                flash(err, "error")
                return redirect(url_for("settings.profile"))
            try:
                p = Project.query.filter_by(
                    id=pid, user_id=current_user.id
                ).first_or_404()
                for k, v in data.items():
                    setattr(p, k, v)
                db.session.commit()
                flash("Project updated.", "success")
            except Exception:
                current_app.logger.exception("Failed updating project")
                db.session.rollback()
                flash("Could not update project.", "error")

        # Delete a Project
        if action == "project_delete":
            pid_raw = (request.form.get("project_id") or "").strip()
            if not pid_raw.isdigit():
                flash("Invalid project id.", "error")
                return redirect(url_for("settings.profile"))
            pid = int(pid_raw)
            try:
                p = Project.query.filter_by(
                    id=pid, user_id=current_user.id
                ).first_or_404()
                db.session.delete(p)
                db.session.commit()
                flash("Project deleted.", "success")
            except Exception:
                current_app.logger.exception("Failed deleting project")
                db.session.rollback()
                flash("Could not delete project.", "error")

        return redirect(url_for("settings.profile"))

    # GET
    try:
        latest_resume = (
            ResumeAsset.query.filter_by(user_id=current_user.id)
            .order_by(ResumeAsset.created_at.desc())
            .first()
        )
    except Exception:
        current_app.logger.exception("Failed fetching latest resume")
        latest_resume = None

    view = _build_view(prof, projects)

    return render_template(
        "settings/profile.html",
        profile=prof,
        skills=view["skills"],
        education=view["education"],
        certifications=view["certifications"],
        links=view["links"],
        experience=view["experience"],
        projects=view["projects"],
        latest_resume=latest_resume,
    )
