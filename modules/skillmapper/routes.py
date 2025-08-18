# modules/skillmapper/routes.py

import json
from datetime import datetime
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user

from models import db, SkillMapSnapshot
from limits import authorize_and_consume

# Expect a helper that extracts skills from pasted text.
# Implement it in helpers.py (e.g., using simple regex or your LLM).
# Signature: skillmap_analyze(text: str) -> dict like {"skills": [...], "categories": {...}}
from helpers import skillmap_analyze  # make sure this exists

skillmapper_bp = Blueprint("skillmapper", __name__, template_folder='../../templates/skillmapper')


@skillmapper_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Paste one or more JDs or text blocks and generate a skills map.
    Credits: free first, fallback to ⭐ using authorize_and_consume("skillmapper").
    """
    result = None
    raw_text = ""

    if request.method == "POST":
        raw_text = (request.form.get("text") or "").strip()
        title = (request.form.get("title") or "").strip() or "Untitled Skill Map"

        if not raw_text:
            flash("Paste some text (e.g., a JD).", "error")
            return render_template("skillmapper/index.html", result=None, raw_text="", title=title)

        if not authorize_and_consume(current_user, "skillmapper"):
            flash("You’ve hit today’s free limit. Go Pro to continue.", "warning")
            return redirect(url_for("billing.index"))

        # Analyze
        result = skillmap_analyze(raw_text) or {}
        # Store snapshot
        snap = SkillMapSnapshot(
            user_id=current_user.id,
            source_title=title[:200],
            input_text=raw_text[:5000],
            skills_json=json.dumps(result),
        )
        db.session.add(snap)
        db.session.commit()

        flash("Skill map created.", "success")

    return render_template("skillmapper/index.html", result=result, raw_text=raw_text)
