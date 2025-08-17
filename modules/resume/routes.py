import PyPDF2, os
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from models import db, ResumeAsset

resume_bp = Blueprint("resume", __name__, template_folder="../../templates")

@resume_bp.route("/", methods=["GET","POST"])
@login_required
def upload_resume():
    if request.method=="POST":
        f = request.files.get("resume")
        if not f:
            flash("No file","error")
            return redirect(url_for("resume.upload_resume"))
        text=""
        if f.filename.lower().endswith(".pdf"):
            reader = PyPDF2.PdfReader(f)
            text="\n".join([p.extract_text() or "" for p in reader.pages])
        else:
            text=f.read().decode("utf-8","ignore")
        asset = ResumeAsset(user_id=current_user.id, filename=f.filename, mime=f.mimetype, content_text=text)
        db.session.add(asset); db.session.commit()
        flash("Resume uploaded.","success")
        return redirect(url_for("resume.upload_resume"))
    asset = ResumeAsset.query.filter_by(user_id=current_user.id).order_by(ResumeAsset.created_at.desc()).first()
    return render_template("resume/upload.html", asset=asset)
