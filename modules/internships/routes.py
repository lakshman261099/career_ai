from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, InternshipRecord, Subscription
from .helpers import mock_fetch, compute_learning_links, deep_enrich_jobs

internships_bp = Blueprint("internships", __name__)

def _is_pro(uid) -> bool:
    sub = Subscription.query.filter_by(user_id=uid).first()
    return bool(sub and sub.status == "active")

@internships_bp.route("", methods=["GET"])
@login_required
def index():
    return render_template("internships_index.html", jobs=None)

@internships_bp.route("/search", methods=["GET"])
@login_required
def search():
    role = request.args.get("role","Intern").strip()
    location = request.args.get("location","").strip()
    mode = request.args.get("mode","fast").strip().lower()

    if mode == "deep" and not _is_pro(current_user.id):
        flash("Deep mode is a Pro feature. Upgrade to unlock premium analysis.", "error")
        return redirect(url_for("pricing"))

    jobs = mock_fetch(role, location)
    # base enrich
    for j in jobs:
        j["learning_links"] = compute_learning_links(j.get("missing_skills", []))

    if mode == "deep":
        jobs = deep_enrich_jobs(jobs, role)

    # persist simple footprint
    for j in jobs:
        rec = InternshipRecord(
            user_id=current_user.id, role=role, location=location,
            source=j.get("source",""), title=j.get("title",""), company=j.get("company",""),
            link=j.get("link",""), match_score=int(j.get("match_score",0)),
            missing_skills=",".join(j.get("missing_skills",[]))
        )
        db.session.add(rec)
    db.session.commit()

    return render_template("internships_index.html", jobs=jobs, role=role, location=location, mode=mode)
