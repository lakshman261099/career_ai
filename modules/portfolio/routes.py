from flask import Blueprint, render_template, request, flash
from flask_login import login_required, current_user
from helpers import portfolio_suggestions
from limits import can_use_free, consume_free, can_use_pro, consume_pro

portfolio_bp = Blueprint("portfolio", __name__, template_folder="../../templates/portfolio")

@portfolio_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    ideas = []
    if request.method == "POST":
        role = request.form.get("role","").strip() or "Software Engineer Intern"
        deep = request.form.get("mode") == "pro"
        feature = "portfolio"
        if deep:
            if not can_use_pro(current_user, feature):
                flash("Not enough Pro coins.", "error")
            else:
                consume_pro(current_user, feature)
                ideas = portfolio_suggestions(current_user.name, role, deep=True)
        else:
            if not can_use_free(current_user, feature):
                flash("Daily free limit reached.", "error")
            else:
                consume_free(current_user, feature)
                ideas = portfolio_suggestions(current_user.name, role, deep=False)
    return render_template("portfolio/index.html", ideas=ideas)
