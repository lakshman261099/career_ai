from flask import Blueprint, render_template, request, flash
from flask_login import login_required
from helpers import ai_resume_critique
from limits import can_use_free, consume_free, can_use_pro, consume_pro

skillmapper_bp = Blueprint("skillmapper", __name__, template_folder='../../templates/skillmapper')

@skillmapper_bp.route("/", methods=["GET","POST"])
@login_required
def index():
    suggestions = None
    if request.method == "POST":
        resume = request.form.get("resume","").strip()
        deep = request.form.get("mode") == "pro"
        feature = "skillmapper"
        if not resume:
            flash("Paste resume text.", "error")
        else:
            if deep:
                if not can_use_pro(current_user, feature):
                    flash("Not enough Pro coins.", "error")
                else:
                    consume_pro(current_user, feature)
                    suggestions = ai_resume_critique(resume, deep=True)
            else:
                if not can_use_free(current_user, feature):
                    flash("Daily free limit reached.", "error")
                else:
                    consume_free(current_user, feature)
                    suggestions = ai_resume_critique(resume, deep=False)
    return render_template("skillmapper/index.html", suggestions=suggestions)
