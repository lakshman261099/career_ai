from flask import Blueprint, render_template, request
from .helpers import sanitize, generate_project_brief

bp = Blueprint("portfolio", __name__)

@bp.post("/generate")
def generate():
    role = sanitize(request.form.get("role"))
    if not role:
        return render_template("dashboard.html", active_tab="portfolio", error="Please enter a target role.")

    brief = generate_project_brief(role)

    return render_template("dashboard.html", active_tab="portfolio", portfolio_result=brief)
