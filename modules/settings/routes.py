# modules/settings/routes.py

import os
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, ResumeAsset
from limits import authorize_and_consume, can_use_pro

settings_bp = Blueprint("settings", __name__, template_folder="../../templates/settings")

ALLOWED_RESUME_EXTS = {"pdf", "txt"}  # keep simple; parsing handled elsewhere


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_RESUME_EXTS


@settings_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Name cannot be empty.", "error")
        else:
            current_user.name = name
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("settings.index"))

    # Recent resume assets (if any)
    resumes = ResumeAsset.query.filter_by(user_id=current_user.id).order_by(ResumeAsset.created_at.desc()).limit(5).all()
    return render_template("settings/index.html", resumes=resumes)


@settings_bp.route("/resume", methods=["GET", "POST"], endpoint="resume")
@login_required
def resume():
    """
    Pro-only resume profile upload.
    Consumes 'resume' feature credits (⭐) via authorize_and_consume.
    """
    if request.method == "POST":
        # Enforce Pro (no free quota for resume per KB)
        if not can_use_pro(current_user, "resume"):
            flash("Resume upload is a Pro feature.", "warning")
            return redirect(url_for("billing.index"))

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
            # Keep files ephemeral; persist text only (KB-friendly)
            try:
                content = file.read()
                # If txt, decode; if pdf, keep placeholder text (parsing can be added later)
                if filename.lower().endswith(".txt"):
                    extracted_text = (content or b"").decode("utf-8", errors="ignore")
                else:
                    extracted_text = f"[PDF uploaded: {filename}]"
            except Exception:
                flash("Could not read the uploaded file.", "error")
                return render_template("settings/resume.html")

        # Consume Pro credit now that we have valid input
        if not authorize_and_consume(current_user, "resume"):
            flash("Not enough Pro credits.", "error")
            return redirect(url_for("billing.index"))

        asset = ResumeAsset(user_id=current_user.id, filename=filename, text=extracted_text)
        db.session.add(asset)
        db.session.commit()

        flash("Resume saved to your profile.", "success")
        return redirect(url_for("settings.index"))

    return render_template("settings/resume.html")
