from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, InternshipRecord, Subscription
from .helpers import mock_fetch, compute_learning_links

internships_bp = Blueprint("internships", __name__)

def _is_pro(user_id:int) -> bool:
    sub = Subscription.query.filter_by(user_id=user_id).first()
    return bool(sub and sub.status == "active")

@internships_bp.route("", methods=["GET"])
@login_required
def index():
    # Renders the dedicated UI page with the search form + results slot
    return render_template("internships_index.html", jobs=None)

@internships_bp.route("/search", methods=["GET"])
@login_required
def search():
    role = request.args.get("role", "Intern").strip()
    location = request.args.get("location", "").strip()
    mode = request.args.get("mode", "fast").strip().lower()

    # Pro‑gate Deep mode
    if mode == "deep" and not _is_pro(current_user.id):
        flash("Deep mode is a Pro feature. Upgrade to unlock premium analysis.", "error")
        return redirect(url_for("pricing"))

    # Fetch jobs (mock for now) and enrich
    jobs = mock_fetch(role, location)
    for j in jobs:
        j["learning_links"] = compute_learning_links(j.get("missing_skills", []))

        # Add premium-looking fields when Deep mode is requested
        if mode == "deep":
            # These are mock enrichments for now; when MOCK=0 you can replace
            # with an OpenAI-backed function that reasons per posting.
            j["portfolio_suggestions"] = [
                "Recreate a feature from the product with tests",
                "Design a KPI dashboard using public data",
                "Ship a small data pipeline + report demo"
            ]
            j["outreach_blurb"] = (
                "Built a mini‑project aligned to your stack—"
                "happy to share bullets and a quick 2‑min Loom if helpful."
            )

        # Persist a minimal record of the search result (as before)
        rec = InternshipRecord(
            user_id=current_user.id,
            role=role,
            location=location,
            source=j.get("source", "Mock"),
            title=j.get("title", ""),
            company=j.get("company", ""),
            link=j.get("link", ""),
            match_score=int(j.get("match_score", 0)),
            missing_skills=",".join(j.get("missing_skills", [])),
        )
        db.session.add(rec)
    db.session.commit()

    # Re-render the page with results
    return render_template(
        "internships_index.html",
        jobs=jobs,
        role=role,
        location=location,
        mode=mode,
    )
