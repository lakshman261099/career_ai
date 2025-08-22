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
    prof = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not prof:
        prof = UserProfile(user_id=current_user.id, full_name=current_user.name or None)
        db.session.add(prof)
        db.session.commit()
    return prof


def _naive_parse_from_text(text: str) -> dict:
    """
    Light-touch extractor to seed profile fields. Conservative by design.
    """
    if not text:
        return {}

    parsed = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Headline: first non-empty line
    if lines:
        parsed["headline"] = lines[0][:200]

    # Email / phone
    m_email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if m_email:
        parsed.setdefault("links", {})
        parsed["links"]["email"] = m_email.group(0)

    m_phone = re.search(r"(\+?\d[\d\s\-()]{8,})", text)
    if m_phone:
        parsed["phone"] = m_phone.group(0).strip()

    # Skills line
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
        if url: parsed["links"]["linkedin"] = url.group(0)
    if "github.com" in lowtxt:
        parsed.setdefault("links", {})
        url = re.search(r"https?://[^\s]*github[^\s]*", text, re.I)
        if url: parsed["links"]["github"] = url.group(0)

    return parsed


def _seed_profile_from_parsed(profile: UserProfile, parsed: dict) -> bool:
    """Fill only blanks after resume upload; returns True if changed."""
    if not parsed:
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
                merged[k] = v; added = True
        if added or not (profile.links or {}):
            profile.links = merged
            changed = True

    # skills only if empty
    if parsed.get("skills") and not (profile.skills or []):
        profile.skills = parsed["skills"]
        changed = True

    if changed:
        db.session.commit()
    return changed


# ---------------------------
# Settings index
# ---------------------------
@settings_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    try:
        # ensure a profile row exists for current user (defensive)
        _ensure_profile()

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("Name cannot be empty.", "error")
            else:
                current_user.name = name
                db.session.commit()
                flash("Profile updated.", "success")
                return redirect(url_for("settings.index"))

        resumes = (
            ResumeAsset.query.filter_by(user_id=current_user.id)
            .order_by(ResumeAsset.created_at.desc())
            .limit(5)
            .all()
        )
        return render_template("settings/index.html", resumes=resumes)
    except Exception:
        current_app.logger.exception("/settings index failed")
        raise


# ---------------------------
# Profile Portal (Hiring Manager view + edit)
# ---------------------------
@settings_bp.route("/profile", methods=["GET", "POST"], endpoint="profile")
@login_required
def profile():
    try:
        prof = _ensure_profile()

        if request.method == "POST":
            # Simple fields
            prof.full_name = (request.form.get("full_name") or "").strip() or prof.full_name
            prof.headline = (request.form.get("headline") or "").strip() or None
            prof.summary = (request.form.get("summary") or "").strip() or None
            prof.location = (request.form.get("location") or "").strip() or None
            prof.phone = (request.form.get("phone") or "").strip() or None

            # Skills CSV
            skills_csv = (request.form.get("skills_csv") or "").strip()
            if skills_csv:
                prof.skills = [s.strip() for s in skills_csv.split(",") if s.strip()][:50]

            # JSON sections
            def parse_json_field(field, default):
                raw = (request.form.get(field) or "").strip()
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except Exception:
                    flash(f"{field.replace('_',' ').title()} must be valid JSON.", "warning")
                    return default

            links = parse_json_field("links_json", prof.links or {})
            education = parse_json_field("education_json", prof.education or [])
            experience = parse_json_field("experience_json", prof.experience or [])
            certs = parse_json_field("certifications_json", prof.certifications or [])

            if links is not None: prof.links = links
            if education is not None: prof.education = education
            if experience is not None: prof.experience = experience
            if certs is not None: prof.certifications = certs

            prof.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Profile saved.", "success")
            return redirect(url_for("settings.profile"))

        latest_resume = (
            ResumeAsset.query.filter_by(user_id=current_user.id)
            .order_by(ResumeAsset.created_at.desc())
            .first()
        )

        return render_template("settings/profile.html", profile=prof, latest_resume=latest_resume)
    except Exception:
        current_app.logger.exception("/settings/profile failed")
        raise


# ---------------------------
# Resume (Pro-only; consumes ⭐ via limits.py)
# ---------------------------
@settings_bp.route("/resume", methods=["GET", "POST"], endpoint="resume")
@login_required
def resume():
    """
    Pro-only resume profile upload.
    Consumes 'resume' feature credits (⭐) via authorize_and_consume.
    Seeds Profile fields conservatively.
    """
    if request.method == "POST":
        # Enforce Pro
        try:
            if not can_use_pro(current_user, "resume"):
                flash("Resume upload is a Pro feature.", "warning")
                try:
                    return redirect(url_for("billing.index"))
                except Exception:
                    return redirect(url_for("settings.index"))
        except Exception:
            current_app.logger.exception("Error checking Pro permission for resume upload")
            flash("Something went wrong while checking your plan. Please try again.", "error")
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
                current_app.logger.exception("Could not read uploaded resume file")
                flash("Could not read the uploaded file.", "error")
                return render_template("settings/resume.html")

        # Consume Pro credit now that we have valid input
        try:
            ok = authorize_and_consume(current_user, "resume")
        except Exception:
            current_app.logger.exception("authorize_and_consume('resume') failed")
            ok = False

        if not ok:
            flash("Not enough Pro credits. Please manage your subscription.", "error")
            try:
                return redirect(url_for("billing.index"))
            except Exception:
                return redirect(url_for("settings.index"))

        try:
            asset = ResumeAsset(user_id=current_user.id, filename=filename, text=extracted_text)
            db.session.add(asset)
            db.session.commit()
        except Exception:
            current_app.logger.exception("Failed to persist ResumeAsset")
            try:
                db.session.rollback()
            except Exception:
                pass
            flash("Failed to save your resume. Please try again.", "error")
            return render_template("settings/resume.html")

        # Seed the profile (create if missing), but only fill blanks
        try:
            prof = _ensure_profile()
            parsed = _naive_parse_from_text(extracted_text)
            _seed_profile_from_parsed(prof, parsed)
        except Exception:
            current_app.logger.exception("Failed to seed profile from resume")
            flash("Resume saved, but we couldn't update your profile automatically. You can edit it manually.", "warning")

        flash("Resume saved and your Profile Portal has been updated.", "success")
        return redirect(url_for("settings.profile"))

    return render_template("settings/resume.html")


# ---------------------------
# Credits dashboard
# ---------------------------
@settings_bp.route("/credits", endpoint="credits")
@login_required
def credits():
    """
    Show current 🪙 Silver and ⭐ Gold balances, with per-feature costs.
    """
    feature_keys = ["resume", "portfolio", "internships", "referral", "jobpack", "skillmapper"]
    features = {k: get_feature_limits(k) for k in feature_keys}
    return render_template(
        "settings/credits.html",
        coins_free=current_user.coins_free or 0,
        coins_pro=current_user.coins_pro or 0,
        subscription_status=(current_user.subscription_status or "free"),
        features=features,
    )

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
        current_app.logger.exception("DB debug failed")
        return {"error": str(e)}, 500
