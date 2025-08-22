import os, json, re
from datetime import datetime
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, ResumeAsset, UserProfile
from limits import authorize_and_consume, can_use_pro, get_feature_limits

settings_bp = Blueprint("settings", __name__, template_folder="../../templates/settings")

ALLOWED_RESUME_EXTS = {"pdf", "txt"}


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


def _naive_parse_from_text(text: str) -> dict:
    """Conservative seed from resume text."""
    if not text:
        return {}
    parsed = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    if lines:
        parsed["headline"] = lines[0][:200]

    m_email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if m_email:
        parsed.setdefault("links", {})
        parsed["links"]["email"] = m_email.group(0)

    m_phone = re.search(r"(\+?\d[\d\s\-()]{8,})", text)
    if m_phone:
        parsed["phone"] = m_phone.group(0).strip()

    for l in lines:
        if l.lower().startswith("skills"):
            rest = l.split(":", 1)[-1] if ":" in l else l[6:]
            skills = [s.strip() for s in rest.split(",") if s.strip()]
            if skills:
                # Convert to structured with default level=3
                parsed["skills"] = [{"name": s, "level": 3} for s in skills[:30]]
            break

    lowtxt = text.lower()
    if "linkedin.com" in lowtxt:
        parsed.setdefault("links", {})
        url = re.search(r"https?://[^\s]*linkedin[^\s]*", text, re.I)
        if url: parsed["links"]["linkedin"] = url.group(0)
    if "github.com" in lowtxt:
        parsed.setdefault("links", {})
        url = re.search(r"https?://[^\s]*github[^\s]*", text, re.I)
        if url: parsed["links"]["github"] = url.group(0)

    return parsed


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

    set_if_blank("headline", parsed.get("headline"))
    set_if_blank("summary", parsed.get("summary"))
    set_if_blank("phone", parsed.get("phone"))

    # links
    new_links = parsed.get("links") or {}
    if new_links:
        merged = dict(profile.links or {})
        added = False
        for k, v in new_links.items():
            if k not in merged:
                merged[k] = v
                added = True
        if added or not (profile.links or {}):
            profile.links = merged
            changed = True

    # skills (only if empty)
    if parsed.get("skills") and not (profile.skills or []):
        profile.skills = parsed["skills"]
        changed = True

    if changed:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed committing seeded profile")
            return False
    return changed


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
        if level < 1: level = 1
        if level > 5: level = 5
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
    """Return dict[str->str]. Accept dict only; else empty."""
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
# Settings index (kept simple, links to portal)
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

    resumes = []
    try:
        resumes = (
            ResumeAsset.query.filter_by(user_id=current_user.id)
            .order_by(ResumeAsset.created_at.desc())
            .limit(5)
            .all()
        )
    except Exception:
        current_app.logger.exception("Failed loading resumes")

    return render_template("settings/index.html", resumes=resumes)


# ---------------------------
# Unified Profile Portal (hero page)
# ---------------------------
@settings_bp.route("/profile", methods=["GET", "POST"], endpoint="profile")
@login_required
def profile():
    prof = _ensure_profile()
    if not prof:
        flash("Could not load your profile. Please retry.", "error")
        return redirect(url_for("settings.index"))

    if request.method == "POST":
        try:
            # Simple fields
            prof.full_name = (request.form.get("full_name") or "").strip() or prof.full_name
            prof.headline = (request.form.get("headline") or "").strip() or None
            prof.summary = (request.form.get("summary") or "").strip() or None
            # Optional, not shown in UI right now but kept for completeness
            prof.location = (request.form.get("location") or prof.location or "").strip() or None
            prof.phone = (request.form.get("phone") or prof.phone or "").strip() or None

            # ---- Structured arrays ----
            # Skills (parallel arrays)
            names = request.form.getlist("skills_names[]")
            levels = request.form.getlist("skills_levels[]")
            new_skills = []
            for i, nm in enumerate(names):
                nm = (nm or "").strip()
                if not nm:
                    continue
                try:
                    lv = int(levels[i]) if i < len(levels) else 3
                except Exception:
                    lv = 3
                if lv < 1: lv = 1
                if lv > 5: lv = 5
                new_skills.append({"name": nm, "level": lv})
            prof.skills = new_skills

            # Education (triples)
            edu_degree = request.form.getlist("edu_degree[]")
            edu_school = request.form.getlist("edu_school[]")
            edu_year = request.form.getlist("edu_year[]")
            new_edu = []
            for i in range(max(len(edu_degree), len(edu_school), len(edu_year))):
                deg = (edu_degree[i] if i < len(edu_degree) else "").strip()
                sch = (edu_school[i] if i < len(edu_school) else "").strip()
                yr  = (edu_year[i] if i < len(edu_year) else "").strip()
                if not (deg or sch or yr):
                    continue
                new_edu.append({"degree": deg, "school": sch, "year": yr})
            prof.education = new_edu

            # Certifications (pairs)
            cert_name = request.form.getlist("cert_name[]")
            cert_year = request.form.getlist("cert_year[]")
            new_certs = []
            for i in range(max(len(cert_name), len(cert_year))):
                cn = (cert_name[i] if i < len(cert_name) else "").strip()
                cy = (cert_year[i] if i < len(cert_year) else "").strip() or None
                if not cn:
                    continue
                new_certs.append({"name": cn, "year": cy})
            prof.certifications = new_certs

            # Links (pairs)
            link_keys = request.form.getlist("link_keys[]")
            link_urls = request.form.getlist("link_urls[]")
            links = {}
            for i in range(max(len(link_keys), len(link_urls))):
                k = (link_keys[i] if i < len(link_keys) else "").strip()
                v = (link_urls[i] if i < len(link_urls) else "").strip()
                if not (k and v):
                    continue
                links[k] = v
            prof.links = links

            # Experience (blocks)
            exp_role = request.form.getlist("exp_role[]")
            exp_company = request.form.getlist("exp_company[]")
            exp_start = request.form.getlist("exp_start[]")
            exp_end = request.form.getlist("exp_end[]")
            exp_bullets = request.form.getlist("exp_bullets[]")
            new_exp = []
            max_n = max(len(exp_role), len(exp_company), len(exp_start), len(exp_end), len(exp_bullets))
            for i in range(max_n):
                role = (exp_role[i] if i < len(exp_role) else "").strip()
                comp = (exp_company[i] if i < len(exp_company) else "").strip()
                st = (exp_start[i] if i < len(exp_start) else "").strip()
                en = (exp_end[i] if i < len(exp_end) else "").strip() or None
                bl_raw = (exp_bullets[i] if i < len(exp_bullets) else "")
                bl = [b.strip() for b in (bl_raw or "").split("\n") if b.strip()]
                if not (role or comp or st or en or bl):
                    continue
                new_exp.append({"role": role, "company": comp, "start": st, "end": en, "bullets": bl})
            prof.experience = new_exp

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
        profile=prof,               # raw (kept for ID/name fallback)
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