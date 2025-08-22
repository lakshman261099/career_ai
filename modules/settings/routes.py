import os, json, re
from datetime import datetime
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, ResumeAsset, UserProfile
from limits import authorize_and_consume, can_use_pro, get_feature_limits

settings_bp = Blueprint("settings", __name__, template_folder="../../templates/settings")

ALLOWED_RESUME_EXTS = {"pdf", "txt"}  # keep simple; parsing handled elsewhere


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_RESUME_EXTS


# ---------------------------
# Helpers for Profile seeding
# ---------------------------
def _ensure_profile():
    """Always return a UserProfile row for the current user."""
    try:
        prof = UserProfile.query.filter_by(user_id=current_user.id).first()
        if not prof:
            prof = UserProfile(user_id=current_user.id, full_name=current_user.name or None)
            db.session.add(prof)
            db.session.commit()
        return prof
    except Exception:
        current_app.logger.error("Failed ensuring profile", exc_info=True)
        db.session.rollback()
        return None


def _naive_parse_from_text(text: str) -> dict:
    """Light-touch extractor to seed profile fields."""
    if not text:
        return {}

    parsed = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    if lines:
        parsed["headline"] = lines[0][:200]

    # Email
    m_email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if m_email:
        parsed.setdefault("links", {})
        parsed["links"]["email"] = m_email.group(0)

    # Phone
    m_phone = re.search(r"(\+?\d[\d\s\-()]{8,})", text)
    if m_phone:
        parsed["phone"] = m_phone.group(0).strip()

    # Skills
    for l in lines:
        if l.lower().startswith("skills"):
            rest = l.split(":", 1)[-1] if ":" in l else l[6:]
            skills = [s.strip() for s in rest.split(",") if s.strip()]
            if skills:
                parsed["skills"] = skills[:30]
            break

    # Links
    lowtxt = text.lower()
    if "linkedin.com" in lowtxt:
        parsed.setdefault("links", {})
        url = re.search(r"https?://[^\s]*linkedin[^\s]*", text, re.I)
        if url:
            parsed["links"]["linkedin"] = url.group(0)
    if "github.com" in lowtxt:
        parsed.setdefault("links", {})
        url = re.search(r"https?://[^\s]*github[^\s]*", text, re.I)
        if url:
            parsed["links"]["github"] = url.group(0)

    return parsed


def _seed_profile_from_parsed(profile: UserProfile, parsed: dict) -> bool:
    """Fill only blanks after resume upload; returns True if changed."""
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

    # merge links
    plinks = parsed.get("links") or {}
    if plinks:
        merged = dict(profile.links or {})
        added = False
        for k, v in plinks.items():
            if k not in merged:
                merged[k] = v
                added = True
        if added or not (profile.links or {}):
            profile.links = merged
            changed = True

    # skills only if empty
    if parsed.get("skills") and not (profile.skills or []):
        profile.skills = parsed["skills"]
        changed = True

    if changed:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.error("Failed committing seeded profile", exc_info=True)
            return False
    return changed


# ---------------------------
# Settings index
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
                current_app.logger.error("Failed updating name", exc_info=True)
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
        current_app.logger.error("Failed loading resumes", exc_info=True)

    return render_template("settings/index.html", resumes=resumes)


# ---------------------------
# Profile Portal
# ---------------------------
@settings_bp.route("/profile", methods=["GET", "POST"], endpoint="profile")
@login_required
def profile():
    prof = _ensure_profile()

    if request.method == "POST":
        try:
            prof.full_name = (request.form.get("full_name") or "").strip() or prof.full_name
            prof.headline = (request.form.get("headline") or "").strip() or None
            prof.summary = (request.form.get("summary") or "").strip() or None
            prof.location = (request.form.get("location") or "").strip() or None
            prof.phone = (request.form.get("phone") or "").strip() or None

            skills_csv = (request.form.get("skills_csv") or "").strip()
            if skills_csv:
                prof.skills = [s.strip() for s in skills_csv.split(",") if s.strip()][:50]

            def parse_json_field(field, default):
                raw = (request.form.get(field) or "").strip()
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except Exception:
                    flash(f"{field.replace('_',' ').title()} must be valid JSON.", "warning")
                    return default

            prof.links = parse_json_field("links_json", prof.links or {}) or prof.links
            prof.education = parse_json_field("education_json", prof.education or []) or prof.education
            prof.experience = parse_json_field("experience_json", prof.experience or []) or prof.experience
            prof.certifications = parse_json_field("certifications_json", prof.certifications or []) or prof.certifications

            prof.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Profile saved.", "success")
        except Exception:
            db.session.rollback()
            current_app.logger.error("Failed saving profile", exc_info=True)
            flash("Could not save profile. Try again.", "error")
        return redirect(url_for("settings.profile"))

    latest_resume = None
    try:
        latest_resume = (
            ResumeAsset.query.filter_by(user_id=current_user.id)
            .order_by(ResumeAsset.created_at.desc())
            .first()
        )
    except Exception:
        current_app.logger.error("Failed fetching latest resume", exc_info=True)

    return render_template("settings/profile.html", profile=prof, latest_resume=latest_resume)


# ---------------------------
# Resume Upload (Pro-only)
# ---------------------------
@settings_bp.route("/resume", methods=["GET", "POST"], endpoint="resume")
@login_required
def resume():
    if request.method == "POST":
        try:
            if not can_use_pro(current_user, "resume"):
                flash("Resume upload is a Pro feature.", "warning")
                return redirect(url_for("billing.index"))
        except Exception:
            current_app.logger.error("Error checking Pro permission", exc_info=True)
            flash("Something went wrong checking your plan.", "error")
            return redirect(url_for("settings.index"))

        file = request.files.get("file")
        text = (request.form.get("text") or "").strip()

        if not file and not text:
            flash("Upload a PDF/TXT or paste text.", "error")
            return render_template("settings/resume.html")

        filename = None
        extracted_text = text

        if file and file.filename:
            if not _allowed_file(file.filename):
                flash("Only PDF or TXT files are allowed.", "error")
                return render_template("settings/resume.html")
            filename = secure_filename(file.filename)
            try:
                content = file.read()
                if filename.lower().endswith(".txt"):
                    extracted_text = (content or b"").decode("utf-8", errors="ignore")
                else:
                    extracted_text = f"[PDF uploaded: {filename}]"
            except Exception:
                current_app.logger.error("Could not read uploaded resume", exc_info=True)
                flash("Could not read uploaded file.", "error")
                return render_template("settings/resume.html")

        try:
            ok = authorize_and_consume(current_user, "resume")
        except Exception:
            current_app.logger.error("authorize_and_consume failed", exc_info=True)
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
            current_app.logger.error("Failed persisting ResumeAsset", exc_info=True)
            flash("Could not save resume.", "error")
            return render_template("settings/resume.html")

        try:
            prof = _ensure_profile()
            parsed = _naive_parse_from_text(extracted_text)
            _seed_profile_from_parsed(prof, parsed)
        except Exception:
            current_app.logger.error("Failed seeding profile from resume", exc_info=True)
            flash("Resume saved, but profile not updated. Please edit manually.", "warning")

        flash("Resume saved and Profile Portal updated.", "success")
        return redirect(url_for("settings.profile"))

    return render_template("settings/resume.html")


# ---------------------------
# Credits
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


# ---------------------------
# Debug: DB Tables
# ---------------------------
@settings_bp.route("/_debug/db", methods=["GET"])
@login_required
def _debug_db():
    from sqlalchemy import text
    try:
        rows = db.session.execute(text("""
            select table_name
            from information_schema.tables
            where table_schema='public'
              and table_name in ('user_profile','resume_asset','portfolio_page','free_usage','user')
            order by 1
        """)).fetchall()
        return {"tables": [r[0] for r in rows]}, 200
    except Exception as e:
        current_app.logger.error("DB debug failed", exc_info=True)
        return {"error": str(e)}, 500
