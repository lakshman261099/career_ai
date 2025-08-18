import os, uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from models import db, ResumeAsset
from helpers import extract_text_from_file, ai_resume_critique
from limits import can_use_free, consume_free, can_use_pro, consume_pro

resume_bp = Blueprint("resume", __name__, template_folder="../../templates/resume")
ALLOWED = {".pdf", ".docx", ".txt"}

@resume_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    critique = None
    if request.method == "POST":
        deep = request.form.get("mode") == "pro"
        feature = "resume"
        if deep:
            if not can_use_pro(current_user, feature):
                flash("Not enough Pro coins.", "error")
                return render_template("resume/index.html")
            consume_pro(current_user, feature)
        else:
            if not can_use_free(current_user, feature):
                flash("Daily free limit reached.", "error")
                return render_template("resume/index.html")
            consume_free(current_user, feature)

        text = request.form.get("resume_text","").strip()
        if not text and "resume_file" in request.files:
            f = request.files["resume_file"]
            if f and os.path.splitext(f.filename)[1].lower() in ALLOWED:
                updir = os.path.join(current_app.root_path, "instance", "uploads")
                os.makedirs(updir, exist_ok=True)
                path = os.path.join(updir, str(uuid.uuid4()) + os.path.splitext(f.filename)[1].lower())
                f.save(path)
                text = extract_text_from_file(path)
                asset = ResumeAsset(user_id=current_user.id, filename=f.filename, text=text)
                db.session.add(asset); db.session.commit()
        if not text:
            flash("Provide resume text or upload a file.", "error")
            return render_template("resume/index.html")
        critique = ai_resume_critique(text, deep=deep)
    return render_template("resume/index.html", critique=critique)
