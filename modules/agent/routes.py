import json
from flask import Blueprint, request, redirect, url_for, render_template_string, flash
from flask_login import login_required, current_user
from models import db, AgentJob, Subscription
from modules.jobpack.helpers import mock_jobpack

agent_bp = Blueprint("agent", __name__)

def is_pro(user_id):
    from models import Subscription
    s = Subscription.query.filter_by(user_id=user_id).first()
    return s and s.status == "active"

@agent_bp.route("/save_prefs", methods=["POST"])
@login_required
def save_prefs():
    prefs = {
        "role": request.form.get("role",""),
        "location": request.form.get("location",""),
        "companies": [c.strip() for c in request.form.get("companies","").split(",") if c.strip()]
    }
    job = AgentJob(user_id=current_user.id, preferences_json=json.dumps(prefs), results_json="")
    db.session.add(job); db.session.commit()
    flash("Preferences saved.", "success")
    return redirect(url_for("dashboard"))

@agent_bp.route("/run_now", methods=["POST"])
@login_required
def run_now():
    if not is_pro(current_user.id):
        flash("AI Career Agent is Pro only.", "error")
        return redirect(url_for("pricing"))
    # Fetch a few mock jobs and generate 3 fast packs
    prefs = AgentJob.query.filter_by(user_id=current_user.id).order_by(AgentJob.created_at.desc()).first()
    base_jobs = [
        {"role":"Data Analyst Intern","jd":"Analyze dashboards and experiments","resume":""},
        {"role":"Product Intern","jd":"Help PM team with research and specs","resume":""},
        {"role":"ML Intern","jd":"Assist with model evaluation and tooling","resume":""},
        {"role":"Software Engineer Intern","jd":"Build features and tests","resume":""},
    ]
    picks = base_jobs[:3]
    packs = [mock_jobpack(j["role"], j["jd"], j["resume"], mode="fast") for j in picks]
    aj = AgentJob(user_id=current_user.id, preferences_json=prefs.preferences_json if prefs else "{}", results_json=json.dumps(packs))
    db.session.add(aj); db.session.commit()
    HTML = """
    {% extends "base.html" %}
    {% block content %}
    <a href="/dashboard" class="underline text-sm">← Back</a>
    <h2 class="text-3xl font-bold mb-4">AI Career Agent — Results</h2>
    <div class="space-y-3">
      {% for p in packs %}
        <div class="glass p-4 card">
          <div class="font-bold">{{ p.meta.role }} — {{ p.overall_verdict.status }}</div>
          <div class="text-sm opacity-80">Summary: {{ p.overall_verdict.summary }}</div>
        </div>
      {% endfor %}
    </div>
    {% endblock %}
    """
    return render_template_string(HTML, packs=packs)
