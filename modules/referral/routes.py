from flask import Blueprint, render_template, request
from .helpers import sanitize, find_alumni_mock, generate_outreach_message

bp = Blueprint("referral", __name__)

@bp.post("/generate")
def generate():
    university = sanitize(request.form.get("university"))
    role = sanitize(request.form.get("target_role"))
    skills = sanitize(request.form.get("skills"))
    goal = sanitize(request.form.get("goal"))

    if not university or not role:
        return render_template("dashboard.html", active_tab="referral", error="Please enter university and role.")

    alumni_list = find_alumni_mock(university, role)
    messages = [
        {"person": alum, "message": generate_outreach_message(alum, skills, goal)}
        for alum in alumni_list
    ]

    return render_template("dashboard.html", active_tab="referral", referral_result=messages)
