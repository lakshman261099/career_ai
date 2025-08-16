# modules/agent/routes.py
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from models import db, AgentJob, Subscription
from modules.jobpack.helpers import fast_jobpack_llm, deep_jobpack_llm
from limits import is_pro_user, can_consume_free, consume_free, client_ip, free_budget_blocked

agent_bp = Blueprint("agent", __name__)

def _is_pro(uid) -> bool:
    sub = Subscription.query.filter_by(user_id=uid).first()
    return bool(sub and sub.status == "active")

@agent_bp.route("", methods=["GET"])
@login_required
def index():
    jobs = AgentJob.query.filter_by(user_id=current_user.id).order_by(AgentJob.created_at.desc()).all()
    return render_template("agent_list.html", jobs=jobs)

@agent_bp.route("/run_now", methods=["POST"])
@login_required
def run_now():
    role = (request.form.get("role") or "Data Analyst Intern").strip()
    mode = (request.form.get("mode") or "fast").strip().lower()

    # Agent availability for Free
    if mode == "deep" and not _is_pro(current_user.id):
        flash("Deep Agent runs are Pro only.", "error")
        return redirect(url_for("pricing"))
    if not is_pro_user(current_user):
        if not current_app.config.get("FREE_AGENT_ENABLED", False):
            flash("Agent is Pro only on Free tier.", "error")
            return redirect(url_for("pricing"))
        # apply free caps
        if free_budget_blocked(): current_app.config["MOCK"] = True
        ip = client_ip()
        if not can_consume_free(current_user, ip):
            flash("Free daily limit reached (2/day). Upgrade to Pro for unlimited runs.", "error")
            return redirect(url_for("pricing"))
        consume_free(current_user, ip)

    mock_jobs = [
        {"title": f"{role}", "jd": "We need SQL, Python, dashboards.", "resume": ""},
        {"title": f"{role} - Growth", "jd": "A/B testing, Python, experimentation.", "resume": ""},
        {"title": f"{role} - Platform", "jd": "Data pipelines, ETL, APIs.", "resume": ""},
    ]

    packs = []
    for j in mock_jobs:
        f = deep_jobpack_llm if mode == "deep" else fast_jobpack_llm
        packs.append(f(role, j["jd"], j["resume"]))

    aj = AgentJob(user_id=current_user.id,
                  preferences_json=json.dumps({"role": role, "mode": mode}),
                  results_json=json.dumps(packs))
    db.session.add(aj); db.session.commit()
    flash("Agent run complete.", "success")
    return redirect(url_for("agent.index"))
