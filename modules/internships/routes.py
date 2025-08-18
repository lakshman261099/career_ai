# modules/internships/routes.py

import json
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user

from models import db, InternshipRecord
from limits import authorize_and_consume

# NOTE: KB rule: never scrape. We require users to paste internship descriptions.
# If you had a scraping helper before, remove it from this module.

internships_bp = Blueprint("internships", __name__, template_folder="../../templates/internships")


@internships_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Paste-only internship analyzer:
    - User pastes 1..N internship snippets (one per line or separated by ---).
    - We store the raw text as a record and return a lightweight structured view.
    - Credits: free daily first, fallback to ⭐ via authorize_and_consume("internships")
    """
    results = []
    raw_input = ""

    if request.method == "POST":
        raw_input = (request.form.get("descriptions") or "").strip()

        if not raw_input:
            flash("Paste at least one internship description.", "error")
            return render_template("internships/index.html", results=[], raw_input="")

        # Authorize (free first; fallback to Pro ⭐)
        if not authorize_and_consume(current_user, "internships"):
            flash("You’ve hit today’s free limit. Go Pro to continue.", "warning")
            return redirect(url_for("billing.index"))

        # Very light structuring: split by lines or '---'
        chunks = [c.strip() for c in raw_input.replace("\r\n", "\n").split("\n---\n")]
        if len(chunks) == 1:  # allow line-by-line
            chunks = [ln.strip() for ln in raw_input.splitlines() if ln.strip()]

        # Shape as list of {role, location, notes}
        shaped = []
        for c in chunks:
            shaped.append({
                "role": None,       # user-pasted; we don't infer to avoid hallucinations
                "location": None,   # user-pasted; can enhance later if you add NLP
                "notes": c[:1200],  # keep size predictable
            })
        results = shaped

        rec = InternshipRecord(
            user_id=current_user.id,
            role=None,
            location=None,
            results_json=json.dumps(results),
        )
        db.session.add(rec)
        db.session.commit()

        flash(f"Processed {len(results)} pasted item(s).", "success")

    return render_template("internships/index.html", results=results, raw_input=raw_input)
