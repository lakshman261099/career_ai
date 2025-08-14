# modules/agent/routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, AgentJob

agent_bp = Blueprint("agent", __name__)

@agent_bp.route("", methods=["GET"])
@login_required
def index():
    # Render the advanced UI page we created earlier
    return render_template("agent_index.html")

@agent_bp.route("/save_prefs", methods=["POST"])
@login_required
def save_prefs():
    # Minimal MVP: store preferences as a single AgentJob row (could be its own table later)
    prefs = {
        "role": (request.form.get("role") or "").strip(),
        "location": (request.form.get("location") or "").strip(),
        "companies": (request.form.get("companies") or "").strip(),
    }
    # Persist as a “preferences_json” record
    job = AgentJob(user_id=current_user.id,
                   preferences_json=str(prefs),
                   results_json="{}")
    db.session.add(job)
    db.session.commit()
    flash("Preferences saved.", "success")
    return redirect(url_for("agent.index"))

@agent_bp.route("/run_now", methods=["POST"])
@login_required
def run_now():
    # MVP: just echo back something plausible; your helpers can generate real packs later
    results = {
        "summary": "Generated 3 fast packs for your preferences.",
        "items": [
            {"role": "Data Analyst Intern", "company": "Acme", "score": 82},
            {"role": "Product Intern", "company": "WidgetCo", "score": 77},
            {"role": "ML Intern", "company": "ModelWorks", "score": 74},
        ],
    }
    job = AgentJob(user_id=current_user.id,
                   preferences_json="{}",
                   results_json=str(results))
    db.session.add(job)
    db.session.commit()
    flash("Agent run complete (MVP).", "success")
    return redirect(url_for("agent.index"))
