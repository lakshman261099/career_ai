# modules/settings/routes.py

import re
from datetime import datetime
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, ResumeAsset, UserProfile
from limits import authorize_and_consume, can_use_pro, get_feature_limits

settings_bp = Blueprint("settings", __name__, template_folder="../../templates/settings")

# Resume text paste is no longer needed; only PDF uploads are allowed for Pro scan.
ALLOWED_RESUME_EXTS = {"pdf"}


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_RESUME_EXTS


# ---------------------------
# Profile helpers
# ---------------------------
def _ensure_profile():
    """Always return a UserProfile row for the current user (or None on hard failure)."""
    try:
        prof = UserProfile.query.filter_by(user_id=current_user.id).first()
        if not prof:
            prof = UserProfile(user_id=current_user.id, full_name=current_user.name or None)
            db.session.add(prof)
            db.session.commit()
        return prof
    except Exception:
        current_app.logger.exception("Failed ensuring profile")
        db.session.rollback()
        return None


def _parse_contacts_from_text(text: str) -> dict:
    """Very conservative contact extraction from resume marker/text."""
    out = {"links": {}}
    if not text:
        return out

    m_email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if m_email:
        out["links"]["email"] = m_email.group(0)

    m_phone = re.search(r"(\+?\d[\d\s\-()]{8,})", text)
    if m_phone:
        out["phone"] = m_phone.group(0).strip()

    low = text.lower()
    m_li = re.search(r"https?://[^\s]*linkedin[^\s]*", text, re.I) if "linkedin.com" in low else None
    if m_li:
        out["links"]["linkedin"] = m_li.group(0)
    m_gh = re.search(r"https?://[^\s]*github[^\s]*", text, re.I) if "github.com" in low else None
    if m_gh:
        out["links"]["github"] = m_gh.group(0)

    return out


def _seed_profile_from_parsed(profile: UserProfile, parsed: dict) -> bool:
    """Fill blanks only; return True if changed."""
    if not parsed or not profile:
        return False
    changed = False

    def set_if_blank(attr, val):
        nonlocal changed
        if val is None:
            return
        cur = getattr(profile, attr)
        if not cur:
            setattr(profile, attr, val)
            changed = True

    set_if_blank("phone", parsed.get("phone"))

    # links merge
    new_links = parsed.get("links") or {}
    if new_links:
        merged = dict(profile.links or {})
        added = False
        for k, v in new_links.items():
            if k not in merged and v:
                merged[k] = v
                added = True
        if added or not (profile.links or {}):
            profile.links = merged
            changed = True

    if changed:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed committing seeded profile")
            return False
    return changed


# ---------------------------
# Normalizers for safe templating
# ---------------------------
def _normalize_skills(raw):
    """Return list[{'name':str,'level':int(1..5)}]. Accept strings/dicts/mixed."""
    norm = []
    for item in (raw or []):
        if isinstance(item, dict):
            name = item.get("name") or item.get("skill") or item.get("title") or ""
            try:
                level = int(item.get("level", 3))
            except Exception:
                level = 3
        elif isinstance(item, str):
            name, level = item, 3
        else:
            continue
        name = (name or "").strip()
        if not name:
            continue
        level = min(5, max(1, level))
        norm.append({"name": name, "level": level})
    return norm


def _normalize_education(raw):
    """Return list[{'degree':str,'school':str,'year':str}]."""
    out = []
    for ed in (raw or []):
        if isinstance(ed, dict):
            out.append({
                "degree": (ed.get("degree") or "").strip(),
                "school": (ed.get("school") or "").strip(),
                "year": (str(ed.get("year") or "").strip()),
            })
    return out


def _normalize_certs(raw):
    """Return list[{'name':str,'year':str|None}]."""
    out = []
    for c in (raw or []):
        if isinstance(c, dict):
            out.append({
                "name": (c.get("name") or "").strip(),
                "year": (str(c.get("year") or "").strip()) or None,
            })
        elif isinstance(c, str):
            out.append({"name": c.strip(), "year": None})
    return out


def _normalize_links(raw):
    """Return dict[str->str]."""
    if isinstance(raw, dict):
        clean = {}
        for k, v in raw.items():
            key = (k or "").strip()
            val = (v or "").strip()
            if not key or not val:
                continue
            clean[key] = val
        return clean
    return {}


def _normalize_experience(raw):
    """
    Return list[{'role':str,'company':str,'start':str,'end':str|None,'bullets':list[str]}]
    """
    out = []
    for j in (raw or []):
        if not isinstance(j, dict):
            continue
        bullets = j.get("bullets")
        if isinstance(bullets, list):
            bl = [str(b).strip() for b in bullets if str(b).strip()]
        elif isinstance(bullets, str):
            bl = [b.strip() for b in bullets.split("\n") if b.strip()]
        else:
            bl = []
        out.append({
            "role": (j.get("role") or "").strip(),
            "company": (j.get("company") or "").strip(),
            "start": (j.get("start") or "").strip(),
            "end": (j.get("end") or "").strip() or None,
            "bullets": bl,
        })
    return out


def _build_profile_view(profile: UserProfile):
    """Normalize all JSON fields for safe templating."""
    return dict(
        id=profile.id,
        full_name=profile.full_name or current_user.name or "",
        headline=profile.headline or "",
        summary=profile.summary or "",
        location=profile.location or "",
        phone=profile.phone or "",
        skills=_normalize_skills(profile.skills),
        education=_normalize_education(profile.education),
        certifications=_normalize_certs(profile.certifications),
        links=_normalize_links(profile.links),
        experience=_normalize_experience(profile.experience),
    )


# ---------------------------
# Settings index (simple)
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
                db.session.rollback()
                current_app.logger.exception("Failed updating name")
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
# Unified Profile Portal (with Pro resume upload)
# ---------------------------
@settings_bp.route("/profile", methods=["GET", "POST"], endpoint="profile")
@login_required
def profile():
    prof = _ensure_profile()
    if not prof:
        flash("Could not load your profile. Please retry.", "error")
        return redirect(url_for("settings.index"))

    # --- If a PDF is present, treat as Pro upload path ---
    if request.method == "POST" and request.files.get("resume_file"):
        file = request.files.get("resume_file")
        if not file or not file.filename:
            flash("Choose a PDF to upload.", "error")
            return redirect(url_for("settings.profile"))

        if not _allowed_file(file.filename):
            flash("Only PDF files are allowed.", "error")
            return redirect(url_for("settings.profile"))

        # Pro gate & spend ⭐
        try:
            if not can_use_pro(current_user, "resume"):
                flash("Resume scan is a Pro feature.", "warning")
                return redirect(url_for("billing.index"))
        except Exception:
            current_app.logger.exception("can_use_pro failed")
            flash("Plan check failed. Try again.", "error")
            return redirect(url_for("settings.profile"))

        filename = secure_filename(file.filename)
        try:
            _ = file.read()  # we do not parse PDFs here; store a marker
            extracted_text = f"[PDF uploaded: {filename}]"
        except Exception:
            current_app.logger.exception("Failed reading uploaded PDF")
            flash("Could not read the uploaded file.", "error")
            return redirect(url_for("settings.profile"))

        try:
            ok = authorize_and_consume(current_user, "resume")
        except Exception:
            current_app.logger.exception("authorize_and_consume('resume') failed")
            ok = False

        if not ok:
            flash("Not enough Pro credits. Please manage your subscription.", "error")
            return redirect(url_for("billing.index"))

        try:
            asset = ResumeAsset(user_id=current_user.id, filename=filename, text=extracted_text)
            db.session.add(asset)
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Persisting ResumeAsset failed")
            flash("Could not save resume.", "error")
            return redirect(url_for("settings.profile"))

        # Seed profile with conservative contact fields
        try:
            seed = _parse_contacts_from_text(extracted_text)
            _seed_profile_from_parsed(prof, seed)
        except Exception:
            current_app.logger.exception("Seeding profile from resume failed")

        flash("Resume uploaded. Your Profile Portal has been updated.", "success")
        return redirect(url_for("settings.profile"))

    # --- Manual profile save (free path) ---
    if request.method == "POST" and not request.files.get("resume_file"):
        try:
            # Basic fields
            prof.full_name = (request.form.get("full_name") or "").strip() or prof.full_name
            prof.headline = (request.form.get("headline") or "").strip() or None
            prof.summary = (request.form.get("summary") or "").strip() or None
            prof.location = (request.form.get("location") or prof.location or "").strip() or None
            prof.phone = (request.form.get("phone") or prof.phone or "").strip() or None

            # Skills arrays -> [{name, level}]
            names = request.form.getlist("skills_names[]")
            levels = request.form.getlist("skills_levels[]")
            skills = []
            for i, n in enumerate(names):
                n = (n or "").strip()
                if not n:
                    continue
                try:
                    lvl = int(levels[i]) if i < len(levels) else 3
                except Exception:
                    lvl = 3
                lvl = min(5, max(1, lvl))
                skills.append({"name": n, "level": lvl})
            prof.skills = skills or None

            # Education arrays -> [{degree, school, year}]
            ed_deg = request.form.getlist("edu_degree[]")
            ed_sch = request.form.getlist("edu_school[]")
            ed_yr  = request.form.getlist("edu_year[]")
            education = []
            for i in range(max(len(ed_deg), len(ed_sch), len(ed_yr))):
                d = (ed_deg[i] if i < len(ed_deg) else "").strip()
                s = (ed_sch[i] if i < len(ed_sch) else "").strip()
                y = (ed_yr[i]  if i < len(ed_yr)  else "").strip()
                if d or s or y:
                    education.append({"degree": d, "school": s, "year": y})
            prof.education = education or None

            # Certifications arrays -> [{name, year}]
            c_name = request.form.getlist("cert_name[]")
            c_year = request.form.getlist("cert_year[]")
            certs = []
            for i in range(max(len(c_name), len(c_year))):
                n = (c_name[i] if i < len(c_name) else "").strip()
                y = (c_year[i] if i < len(c_year) else "").strip() or None
                if n or y:
                    certs.append({"name": n, "year": y})
            prof.certifications = certs or None

            # Links arrays -> dict
            link_keys = request.form.getlist("link_keys[]")
            link_urls = request.form.getlist("link_urls[]")
            links = {}
            for i, k in enumerate(link_keys):
                k = (k or "").strip()
                v = (link_urls[i] if i < len(link_urls) else "").strip()
                if k and v:
                    links[k] = v
            prof.links = links or None

            # Experience arrays -> [{role, company, start, end, bullets:[]}]
            role = request.form.getlist("exp_role[]")
            comp = request.form.getlist("exp_company[]")
            start = request.form.getlist("exp_start[]")
            end = request.form.getlist("exp_end[]")
            bullets = request.form.getlist("exp_bullets[]")
            experience = []
            rows = max(len(role), len(comp), len(start), len(end), len(bullets))
            for i in range(rows):
                r = (role[i] if i < len(role) else "").strip()
                c = (comp[i] if i < len(comp) else "").strip()
                st = (start[i] if i < len(start) else "").strip()
                en = (end[i] if i < len(end) else "").strip() or None
                bl = (bullets[i] if i < len(bullets) else "")
                bl_list = [b.strip() for b in (bl or "").splitlines() if b.strip()]
                if r or c or st or en or bl_list:
                    experience.append({"role": r, "company": c, "start": st, "end": en, "bullets": bl_list})
            prof.experience = experience or None

            prof.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Profile saved.", "success")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed saving profile")
            flash("Could not save profile. Try again.", "error")
        return redirect(url_for("settings.profile"))

    # GET: normalize and render
    latest_resume = (
        ResumeAsset.query.filter_by(user_id=current_user.id)
        .order_by(ResumeAsset.created_at.desc())
        .first()
    )
    view = _build_profile_view(prof)

    return render_template(
        "settings/profile.html",
        profile=prof,               # raw object for fallback titles
        latest_resume=latest_resume,
        skills=view["skills"],
        education=view["education"],
        certifications=view["certifications"],
        links=view["links"],
        experience=view["experience"],
    )


# ---------------------------
# Credits (unchanged)
# ---------------------------
@settings_bp.route("/credits", endpoint="credits")
@login_required
def credits():
    feature_keys = ["resume", "portfolio", "internships", "referral", "jobpack", "skillmapper"]
    features = {k: get_feature_limits(k) for k in feature_keys}
    return render_template(
        "settings/credits.html",
        coins_free=current_user.coins_free or 0,
        coins_pro=current_user.coins_pro or 0,
        subscription_status=(current_user.subscription_status or "free"),
        features=features,
    )
