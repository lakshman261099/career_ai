from flask import Blueprint, render_template
from flask_login import login_required, current_user
from limits import is_pro_user

settings_bp = Blueprint("settings", __name__, template_folder="../../templates")

@settings_bp.route("/profile")
@login_required
def profile():
    return render_template("settings/profile.html", user=current_user)

@settings_bp.route("/pricing")
@login_required
def pricing():
    return render_template("settings/pricing.html", user=current_user, is_pro=is_pro_user(current_user))
