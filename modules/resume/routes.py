# modules/resume/routes.py
import os
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user

resume_bp = Blueprint("resume", __name__, template_folder="../../templates")

@resume_bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            flash("Please choose a file.", "warning")
            return redirect(url_for("resume.upload"))
        if file.filename and len(file.read()) > 5 * 1024 * 1024:
            flash("Max file size is 5MB.", "danger")
            return redirect(url_for("resume.upload"))
        file.seek(0)
        # TODO: extract text + save if you want
        flash("Resume uploaded!", "success")
        return redirect(url_for("dashboard"))
    return render_template("resume/upload.html")
