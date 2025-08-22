import os, re, json
from datetime import datetime
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, ResumeAsset, UserProfile

settings_bp = Blueprint("settings", __name__, template_folder="../../templates/settings")

ALLOWED_RESUME_EXTS = {"pdf"}  # PDF only


def _allowed_file(fname: str) -> bool:
    return "." in fname and fname.rsplit(".", 1)[1].lower() in ALLOWED_RESUME_EXTS


# ---------------------------
# Normalizers
# ---------------------------
def _normalize_skills(raw):
    out = []
    for item in (raw or []):
        name, level = "", 3
        if isinstance(item, dict):
            name = (item.get("name") or item.get("skill") or item.get("title") or "").strip()
            try: level = int(item.get("level", 3))
            except Exception: level = 3
        elif isinstance(item, str):
            name = item.strip()
        if not name: continue
        level = max(1, min(5, level))
        out.append({"name": name, "level": level})
    return out


def _normalize_education(raw):
    return [
        {"degree": (ed.get("degree") or "").strip(),
         "school": (ed.get("school") or "").strip(),
         "year": (str(ed.get("year") or "").strip())}
        for ed in (raw or []) if isinstance(ed, dict)
    ]


def _normalize_certs(raw):
    out = []
    for c in (raw or []):
        if isinstance(c, dict):
            out.append({"name": (c.get("name") or "").strip(),
                        "year": (str(c.get("year") or "").strip()) or None})
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
    for j in (raw or []):
        if not isinstance(j, dict): continue
        bullets_raw = j.get("bullets")
        if isinstance(bullets_raw, list):
            bullets = [str(b).strip() for b in bullets_raw if str(b).strip()]
        elif isinstance(bullets_raw, str):
            bullets = [b.strip() for b in bullets_raw.split("\n") if b.strip()]
        else:
            bullets = []
        out.append({
            "role": (j.get("role") or "").strip(),
            "company": (j.get("company") or "").strip(),
            "start": (j.get("start") or "").strip(),
            "end": (j.get("end") or "").strip() or None,
            "bullets": bullets,
        })
    return out


def _build_view(prof: UserProfile):
    return dict(
        skills=_normalize_skills(prof.skills or []),
        education=_normalize_education(prof.education or []),
        certifications=_normalize_certs(prof.certifications or []),
        links=_normalize_links(prof.links or {}),
        experience=_normalize_experience(prof.experience or []),
    )


def _ensure_profile():
    try:
        prof = UserProfile.query.filter_by(user_id=current_user.id).first()
        if not prof:
            prof = UserProfile(user_id=current_user.id, full_name=current_user.name or None)
            db.session.add(prof)
            db.session.commit()
        return prof
    except Exception:
        current_app.logger.exception("ensure_profile failed")
        db.session.rollback()
        return None


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
        resumes = (ResumeAsset.query
                   .filter_by(user_id=current_user.id)
                   .order_by(ResumeAsset.created_at.desc())
                   .limit(5).all())
    except Exception:
        current_app.logger.exception("Failed loading resumes")
        resumes = []

    return render_template("settings/index.html", resumes=resumes)


# ---------------------------
# Profile Portal (Pro only)
# ---------------------------
@settings_bp.route("/profile", methods=["GET", "POST"], endpoint="profile")
@login_required
def profile():
    # ✅ HARD GATE: Free → Pricing
    if getattr(current_user, "subscription_status", "free") != "pro":
        flash("Profile Portal is a Pro feature. Upgrade to unlock auto-scan and editing.", "warning")
        return redirect(url_for("billing.index"))

    prof = _ensure_profile()
    if not prof:
        flash("Could not load your profile. Please reload.", "error")
        return redirect(url_for("billing.index"))

    if request.method == "POST":
        action = (request.form.get("action") or "").lower()

        # Upload resume
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
                asset = ResumeAsset(user_id=current_user.id, filename=filename,
                                    text=f"[PDF uploaded: {filename}]")
                db.session.add(asset)
                db.session.commit()
                flash("Resume uploaded. We’ll auto-fill what we can.", "success")
            except Exception:
                current_app.logger.exception("Resume save failed")
                db.session.rollback()
                flash("Could not save resume file.", "error")

        # Save profile edits
        if action == "save":
            try:
                prof.full_name = (request.form.get("full_name") or "").strip() or prof.full_name
                prof.headline  = (request.form.get("headline") or "").strip() or None
                prof.summary   = (request.form.get("summary") or "").strip() or None
                prof.location  = (request.form.get("location") or "").strip() or None
                prof.phone     = (request.form.get("phone") or "").strip() or None

                # Contact links
                links = {}
                for key in ["email", "website", "linkedin", "github"]:
                    val = (request.form.get(f"contact_{key}") or "").strip()
                    if val: links[key] = val
                lkeys = request.form.getlist("link_keys[]")
                lurls = request.form.getlist("link_urls[]")
                for i in range(max(len(lkeys), len(lurls))):
                    k = (lkeys[i] if i < len(lkeys) else "").strip()
                    v = (lurls[i] if i < len(lurls) else "").strip()
                    if k and v: links[k] = v
                prof.links = links

                # Skills
                names  = request.form.getlist("skills_names[]")
                levels = request.form.getlist("skills_levels[]")
                skills = []
                for i, nm in enumerate(names or []):
                    nm = (nm or "").strip()
                    if not nm: continue
                    try: lv = int(levels[i])
                    except Exception: lv = 3
                    lv = max(1, min(5, lv))
                    skills.append({"name": nm, "level": lv})
                prof.skills = skills

                # Education
                edu_degree = request.form.getlist("edu_degree[]")
                edu_school = request.form.getlist("edu_school[]")
                edu_year   = request.form.getlist("edu_year[]")
                education = []
                for i in range(max(len(edu_degree), len(edu_school), len(edu_year))):
                    deg = (edu_degree[i] if i < len(edu_degree) else "").strip()
                    sch = (edu_school[i] if i < len(edu_school) else "").strip()
                    yr  = (edu_year[i] if i < len(edu_year) else "").strip()
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
                    if cn: certs.append({"name": cn, "year": cy})
                prof.certifications = certs

                # Experience
                exp_role    = request.form.getlist("exp_role[]")
                exp_company = request.form.getlist("exp_company[]")
                exp_start   = request.form.getlist("exp_start[]")
                exp_end     = request.form.getlist("exp_end[]")
                exp_bullets = request.form.getlist("exp_bullets[]")
                experience = []
                for i in range(max(len(exp_role), len(exp_company), len(exp_start), len(exp_end), len(exp_bullets))):
                    role = (exp_role[i] if i < len(exp_role) else "").strip()
                    comp = (exp_company[i] if i < len(exp_company) else "").strip()
                    st   = (exp_start[i] if i < len(exp_start) else "").strip()
                    en   = (exp_end[i] if i < len(exp_end) else "").strip() or None
                    blr  = (exp_bullets[i] if i < len(exp_bullets) else "")
                    bl   = [b.strip() for b in (blr or "").split("\n") if b.strip()]
                    if role or comp or st or en or bl:
                        experience.append({"role": role, "company": comp, "start": st, "end": en, "bullets": bl})
                prof.experience = experience

                prof.updated_at = datetime.utcnow()
                db.session.commit()
                flash("Profile saved.", "success")
            except Exception:
                current_app.logger.exception("Failed saving profile")
                db.session.rollback()
                flash("Could not save profile. Please try again.", "error")

        return redirect(url_for("settings.profile"))

    # GET
    try:
        latest_resume = (ResumeAsset.query
                         .filter_by(user_id=current_user.id)
                         .order_by(ResumeAsset.created_at.desc())
                         .first())
    except Exception:
        current_app.logger.exception("Failed fetching latest resume")
        latest_resume = None

    view = _build_view(prof)

    return render_template(
        "settings/profile.html",
        profile=prof,
        skills=view["skills"],
        education=view["education"],
        certifications=view["certifications"],
        links=view["links"],
        experience=view["experience"],
    )
