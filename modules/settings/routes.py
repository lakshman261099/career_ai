# modules/settings/routes.py

import os, re, json
from datetime import datetime
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, ResumeAsset, UserProfile

settings_bp = Blueprint("settings", __name__, template_folder="../../templates/settings")

ALLOWED_RESUME_EXTS = {"pdf"}  # unified portal: PDF only (no paste)

def _allowed_file(fname: str) -> bool:
    return "." in fname and fname.rsplit(".", 1)[1].lower() in ALLOWED_RESUME_EXTS


# ---------------------------
# Data helpers (robust/forgiving)
# ---------------------------
def _ensure_profile():
    """Return a UserProfile row for the current user, creating if missing."""
    try:
        prof = UserProfile.query.filter_by(user_id=current_user.id).first()
        if not prof:
            prof = UserProfile(user_id=current_user.id, full_name=current_user.name or None)
            db.session.add(prof)
            db.session.commit()
        return prof
    except Exception:
        current_app.logger.exception("ensure_profile failed")
        try: db.session.rollback()
        except Exception: pass
        return None


def _normalize_skills(raw):
    """Return list of {'name': str, 'level': int 1..5} from mixed input."""
    out = []
    if not raw:
        return out
    for item in raw:
        name, level = "", 3
        if isinstance(item, dict):
            name = (item.get("name") or item.get("skill") or item.get("title") or "").strip()
            try:
                level = int(item.get("level", 3))
            except Exception:
                level = 3
        elif isinstance(item, str):
            name = item.strip()
        if not name:
            continue
        if level < 1: level = 1
        if level > 5: level = 5
        out.append({"name": name, "level": level})
    return out


def _normalize_education(raw):
    out = []
    if not raw:
        return out
    for ed in raw:
        if not isinstance(ed, dict):
            continue
        out.append({
            "degree": (ed.get("degree") or "").strip(),
            "school": (ed.get("school") or "").strip(),
            "year": (str(ed.get("year") or "").strip()),
        })
    return out


def _normalize_certs(raw):
    out = []
    if not raw:
        return out
    for c in raw:
        if isinstance(c, dict):
            out.append({"name": (c.get("name") or "").strip(),
                        "year": (str(c.get("year") or "").strip()) or None})
        elif isinstance(c, str):
            out.append({"name": c.strip(), "year": None})
    return out


def _normalize_links(raw):
    """Return dict[str -> str]."""
    res = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            k2 = (k or "").strip()
            v2 = (v or "").strip()
            if k2 and v2:
                res[k2] = v2
    return res


def _normalize_experience(raw):
    out = []
    if not raw:
        return out
    for j in raw:
        if not isinstance(j, dict):
            continue
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
    """Produce safe, template-friendly structures."""
    return dict(
        skills=_normalize_skills(prof.skills or []),
        education=_normalize_education(prof.education or []),
        certifications=_normalize_certs(prof.certifications or []),
        links=_normalize_links(prof.links or {}),
        experience=_normalize_experience(prof.experience or []),
    )


# ---------------------------
# Settings index (unchanged)
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
                try: db.session.rollback()
                except Exception: pass
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
# Unified Profile Portal (with optional PDF upload)
# ---------------------------
@settings_bp.route("/profile", methods=["GET", "POST"], endpoint="profile")
@login_required
def profile():
    prof = _ensure_profile()
    if not prof:
        flash("Could not load your profile. Please reload.", "error")
        return redirect(url_for("settings.index"))

    if request.method == "POST":
        # 1) Optional resume PDF
        file = request.files.get("resume_pdf")
        if file and file.filename:
            if not _allowed_file(file.filename):
                flash("Only PDF files are allowed.", "error")
                return redirect(url_for("settings.profile"))
            filename = secure_filename(file.filename)
            try:
                # we store the file name and a marker; real parsing can be added later
                asset = ResumeAsset(user_id=current_user.id, filename=filename, text=f"[PDF uploaded: {filename}]")
                db.session.add(asset)
                db.session.commit()
                flash("Resume uploaded. We’ll auto-fill what we can.", "success")
            except Exception:
                current_app.logger.exception("Failed to persist ResumeAsset")
                try: db.session.rollback()
                except Exception: pass
                flash("Could not save resume file.", "error")
                return redirect(url_for("settings.profile"))

        # 2) Basic fields
        try:
            prof.full_name = (request.form.get("full_name") or "").strip() or prof.full_name
            prof.headline  = (request.form.get("headline") or "").strip() or None
            prof.summary   = (request.form.get("summary") or "").strip() or None
        except Exception:
            current_app.logger.exception("Failed to set basic fields")
            try: db.session.rollback()
            except Exception: pass
            flash("Could not save profile basics.", "error")
            return redirect(url_for("settings.profile"))

        # 3) Structured fields
        try:
            # Skills
            names  = request.form.getlist("skills_names[]")
            levels = request.form.getlist("skills_levels[]")
            skills = []
            for i, nm in enumerate(names or []):
                nm = (nm or "").strip()
                if not nm:
                    continue
                try:
                    lv = int(levels[i]) if i < len(levels) else 3
                except Exception:
                    lv = 3
                if lv < 1: lv = 1
                if lv > 5: lv = 5
                skills.append({"name": nm, "level": lv})
            prof.skills = skills

            # Education
            edu_degree = request.form.getlist("edu_degree[]")
            edu_school = request.form.getlist("edu_school[]")
            edu_year   = request.form.getlist("edu_year[]")
            education = []
            n_edu = max(len(edu_degree), len(edu_school), len(edu_year))
            for i in range(n_edu):
                deg = (edu_degree[i] if i < len(edu_degree) else "").strip()
                sch = (edu_school[i] if i < len(edu_school) else "").strip()
                yr  = (edu_year[i] if i < len(edu_year) else "").strip()
                if deg or sch or yr:
                    education.append({"degree": deg, "school": sch, "year": yr})
            prof.education = education

            # Certifications
            cert_name = request.form.getlist("cert_name[]")
            cert_year = request.form.getlist("cert_year[]")
            certs = []
            n_c = max(len(cert_name), len(cert_year))
            for i in range(n_c):
                cn = (cert_name[i] if i < len(cert_name) else "").strip()
                cy = (cert_year[i] if i < len(cert_year) else "").strip() or None
                if cn:
                    certs.append({"name": cn, "year": cy})
            prof.certifications = certs

            # Links
            link_keys = request.form.getlist("link_keys[]")
            link_urls = request.form.getlist("link_urls[]")
            links = {}
            n_l = max(len(link_keys), len(link_urls))
            for i in range(n_l):
                k = (link_keys[i] if i < len(link_keys) else "").strip()
                v = (link_urls[i] if i < len(link_urls) else "").strip()
                if k and v:
                    links[k] = v
            prof.links = links

            # Experience
            exp_role    = request.form.getlist("exp_role[]")
            exp_company = request.form.getlist("exp_company[]")
            exp_start   = request.form.getlist("exp_start[]")
            exp_end     = request.form.getlist("exp_end[]")
            exp_bullets = request.form.getlist("exp_bullets[]")
            experience = []
            n_e = max(len(exp_role), len(exp_company), len(exp_start), len(exp_end), len(exp_bullets))
            for i in range(n_e):
                role = (exp_role[i]    if i < len(exp_role)    else "").strip()
                comp = (exp_company[i] if i < len(exp_company) else "").strip()
                st   = (exp_start[i]   if i < len(exp_start)   else "").strip()
                en   = (exp_end[i]     if i < len(exp_end)     else "").strip() or None
                blr  = (exp_bullets[i] if i < len(exp_bullets) else "")
                bl   = [b.strip() for b in (blr or "").split("\n") if b.strip()]
                if role or comp or st or en or bl:
                    experience.append({"role": role, "company": comp, "start": st, "end": en, "bullets": bl})
            prof.experience = experience

            prof.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Profile saved.", "success")
        except Exception:
            current_app.logger.exception("Failed saving structured profile fields")
            try: db.session.rollback()
            except Exception: pass
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

    # IMPORTANT: pass normalized variables expected by the template
    return render_template(
        "settings/profile.html",
        profile=prof,
        latest_resume=latest_resume,
        skills=view["skills"],
        education=view["education"],
        certifications=view["certifications"],
        links=view["links"],
        experience=view["experience"],
    )


# ---------------------------
# Credits (kept)
# ---------------------------
@settings_bp.route("/credits", endpoint="credits")
@login_required
def credits():
    # You can wire this to your limits module if you want; keeping simple here.
    features = {}
    return render_template(
        "settings/credits.html",
        coins_free=getattr(current_user, "coins_free", 0) or 0,
        coins_pro=getattr(current_user, "coins_pro", 0) or 0,
        subscription_status=(getattr(current_user, "subscription_status", "free") or "free"),
        features=features,
    )
