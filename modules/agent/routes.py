# modules/agent/routes.py
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, AgentJob, Subscription
from modules.jobpack.helpers import fast_jobpack_llm, deep_jobpack_llm

agent_bp = Blueprint("agent", __name__)

def _is_pro(uid) -> bool:
    sub = Subscription.query.filter_by(user_id=uid, status="active").first()
    return bool(sub)

@agent_bp.route("", methods=["GET"])
@login_required
def index():
    if not _is_pro(current_user.id):
        # Show locked page instead of letting Free run anything
        return render_template("agent_list.html", jobs=[], locked=True)
    jobs = AgentJob.query.filter_by(user_id=current_user.id).order_by(AgentJob.created_at.desc()).all()
    return render_template("agent_list.html", jobs=jobs, locked=False)

@agent_bp.route("/run_now", methods=["POST"])
@login_required
def run_now():
    if not _is_pro(current_user.id):
        flash("Agent is Pro only.", "error")
        return redirect(url_for("pricing"))
    role = (request.form.get("role") or "Data Analyst Intern").strip()
    mode = "deep"  # Agent runs deep for Pro
    mock_jobs = [
        {"title": f"{role}", "jd": "We need SQL, Python, dashboards.", "resume": ""},
        {"title": f"{role} - Growth", "jd": "A/B testing, Python, experimentation.", "resume": ""},
        {"title": f"{role} - Platform", "jd": "Data pipelines, ETL, APIs.", "resume": ""},
    ]
    packs = []
    for j in mock_jobs:
        packs.append(deep_jobpack_llm(role, j["jd"], j["resume"]))
    aj = AgentJob(user_id=current_user.id,
                  preferences_json=json.dumps({"role": role, "mode": mode}),
                  results_json=json.dumps(packs))
    db.session.add(aj); db.session.commit()
    flash("Agent run complete.", "success")
    return redirect(url_for("agent.index"))
