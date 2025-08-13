from flask import Blueprint, render_template, request
from .helpers import sanitize, find_internships_mock, suggest_learning_links

bp = Blueprint("internships", __name__)

@bp.post("/search")
def search():
    role = sanitize(request.form.get("role"))
    location = sanitize(request.form.get("location"))
    skills = sanitize(request.form.get("skills"))

    if not role or not location:
        return render_template("dashboard.html", active_tab="internships", error="Please enter role and location.")

    internships = find_internships_mock(role, location)

    for internship in internships:
        internship["learning_links"] = suggest_learning_links(internship["missing_skills"])

    return render_template("dashboard.html", active_tab="internships", internships_result=internships)
