from flask import Blueprint, render_template, request, flash
from flask_login import login_required, current_user
from helpers import internships_search
from limits import can_use_free, consume_free, can_use_pro, consume_pro

internships_bp = Blueprint("internships", __name__, template_folder="../../templates/internships")

@internships_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    results = []
    if request.method == "POST":
        role = request.form.get("role","").strip() or "Software Engineer"
        location = request.form.get("location","").strip() or "Remote"
        deep = request.form.get("mode") == "pro"
        feature = "internships"
        if deep:
            if not can_use_pro(current_user, feature):
                flash("Not enough Pro coins.", "error")
            else:
                consume_pro(current_user, feature)
                results = internships_search(role, location)
        else:
            if not can_use_free(current_user, feature):
                flash("Daily free limit reached.", "error")
            else:
                consume_free(current_user, feature)
                results = internships_search(role, location)
    return render_template("internships/index.html", results=results)
