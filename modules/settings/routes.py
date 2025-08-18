from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from models import db

settings_bp = Blueprint("settings", __name__, template_folder="../../templates/settings")

@settings_bp.route("/", methods=["GET","POST"])
@login_required
def index():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        if name:
            current_user.name = name
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("settings.index"))
        else:
            flash("Name cannot be empty.", "error")
    return render_template("settings/index.html")
