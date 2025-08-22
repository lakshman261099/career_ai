import os, re
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


# --------------------------- Profile helpers ---------------------------
def _ensure_profile():
    """Return a UserProfile row for the current user (create if missing)."""
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
    """Very conservative seeding from plain text."""
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


# --------------------------- Settings index ---------------------------
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

    return render_template("settings/index.html")


# --------------------- Unified Profile Portal -------------------------
@settings_bp.route("/profile", methods=["GET", "POST"], endpoint="profile")
@login_required
def profile():
    prof = _ensure_profile()
    if not prof:
        flash("Could not load your profile. Please retry.", "error")
        return redirect(url_for("settings.index"))

    # POST: either resume upload OR manual edit
    if request.method == "POST":
        # 1) Resume upload path (Pro-gated)
        resume_file = request.files.get("resume_file")
        if resume_file and resume_file.filename:
            if not _allowed_file(resume_file.filename):
                flash("Only PDF or TXT files are allowed.", "error")
                return redirect(url_for("settings.profile"))

            try:
                if not can_use_pro(current_user, "resume"):
                    flash("Resume scanning is a Pro feature.", "warning")
                    return redirect(url_for("billing.index"))
            except Exception:
                current_app.logger.exception("Error checking Pro")
                flash("Something went wrong checking your plan.", "error")
                return redirect(url_for("settings.profile"))

            filename = secure_filename(resume_file.filename)
            try:
                content = resume_file.read() or b""
                if filename.lower().endswith(".txt"):
                    extracted_text = content.decode("utf-8", errors="ignore")
                else:
                    # PDF parsing can be integrated later; placeholder marker
                    extracted_text = f"[PDF uploaded: {filename}]"
            except Exception:
                current_app.logger.exception("Could not read uploaded resume")
                flash("Could not read the uploaded file.", "error")
                return redirect(url_for("settings.profile"))

            try:
                ok = authorize_and_consume(current_user, "resume")
            except Exception:
                current_app.logger.exception("authorize_and_consume failed")
                ok = False
            if not ok:
                flash("Not enough Pro credits.", "error")
                return redirect(url_for("billing.index"))

            try:
                asset = ResumeAsset(user_id=current_user.id, filename=filename, text=extracted_text)
                db.session.add(asset)
                db.session.commit()
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed saving ResumeAsset")
                flash("Could not save your resume.", "error")
                return redirect(url_for("settings.profile"))

            # Seed profile conservatively
            try:
                parsed = _naive_parse_from_text(extracted_text)
                _seed_profile_from_parsed(prof, parsed)
            except Exception:
                current_app.logger.exception("Seeding profile from resume failed")

            flash("Resume saved. Profile Portal updated.", "success")
            return redirect(url_for("settings.profile"))

        # 2) Manual edit path (always allowed)
        try:
            prof.full_name = (request.form.get("full_name") or "").strip() or prof.full_name
            prof.headline = (request.form.get("headline") or "").strip() or None
            prof.summary = (request.form.get("summary") or "").strip() or None

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
                lv = max(1, min(5, lv))
                new_skills.append({"name": nm, "level": lv})
            prof.skills = new_skills

            # Education
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

            # Certifications
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

            # Links
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

            # Experience
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

    # GET
    latest_resume = (
        ResumeAsset.query.filter_by(user_id=current_user.id)
        .order_by(ResumeAsset.created_at.desc())
        .first()
    )
    return render_template("settings/profile.html", profile=prof, latest_resume=latest_resume)


# --------------------------- Credits ---------------------------
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
