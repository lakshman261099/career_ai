# modules/agent/routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, AgentJob

# ✅ Define blueprint FIRST
agent_bp = Blueprint("agent", __name__)

@agent_bp.route("", methods=["GET"])
@login_required
def index():
    jobs = AgentJob.query.filter_by(user_id=current_user.id).order_by(AgentJob.created_at.desc()).all()
    return render_template("agent_list.html", jobs=jobs)

@agent_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        if not title:
            flash("Job title is required.", "error")
            return redirect(url_for("agent.add"))
        
        job = AgentJob(user_id=current_user.id, title=title, description=description)
        db.session.add(job)
        db.session.commit()
        flash("Agent job added successfully.", "success")
        return redirect(url_for("agent.index"))
    
    return render_template("agent_form.html")

@agent_bp.route("/delete/<int:job_id>", methods=["POST"])
@login_required
def delete(job_id):
    job = AgentJob.query.filter_by(id=job_id, user_id=current_user.id).first_or_404()
    db.session.delete(job)
    db.session.commit()
    flash("Agent job deleted.", "info")
    return redirect(url_for("agent.index"))
