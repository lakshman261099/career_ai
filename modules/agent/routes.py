from flask import Blueprint, render_template, request
from .helpers import sanitize, find_jobs_mock, generate_application_pack

bp = Blueprint("agent", __name__)

@bp.post("/run")
def run():
    role = sanitize(request.form.get("role"))
    location = sanitize(request.form.get("location"))
    skills = sanitize(request.form.get("skills"))

    if not role or not location:
        return render_template("dashboard.html", active_tab="agent", error="Please enter role and location.")

    jobs = find_jobs_mock(role, location)
    results = []
    for job in jobs:
        pack = generate_application_pack(job, skills)
        results.append({"job": job, "pack": pack})

    return render_template("dashboard.html", active_tab="agent", agent_result=results)
