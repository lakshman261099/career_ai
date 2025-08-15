from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from flask import current_app
import time, json, hashlib

from models import db, InternshipRecord, Subscription
from .helpers import mock_fetch, compute_learning_links, deep_enrich_jobs

# Free-tier guardrails (manual use so Deep doesn't get counted)
from limits import (
    is_pro_user, can_consume_free, consume_free,
    client_ip, free_budget_blocked
)

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
    role = (request.args.get("role") or "Intern").strip()
    location = (request.args.get("location") or "").strip()
    mode = (request.args.get("mode") or "fast").strip().lower()

    # Pro gate for Deep
    if mode == "deep" and not _is_pro(current_user.id):
        flash("Deep mode is a Pro feature. Upgrade to unlock premium analysis.", "error")
        return redirect(url_for("pricing"))

    # ---------- FREE GUARDRAILS (FAST ONLY) ----------
    if mode != "deep" and not is_pro_user(current_user):
        # Optional global kill-switch: degrade Free to MOCK
        if free_budget_blocked():
            current_app.config["MOCK"] = True
        # Per-user/day cap
        ip = client_ip()
        if not can_consume_free(current_user, ip):
            # For a GET page flow, show a gentle message
            flash("You've hit the free daily limit (2/day). Upgrade to Pro for unlimited runs.", "error")
            return redirect(url_for("pricing"))
        # Count now (pre-charge)
        consume_free(current_user, ip)
    # -------------------------------------------------

    # ---------- CACHE (FAST ONLY) ----------
    if mode != "deep":
        key = "INTFAST:" + hashlib.sha256(
            json.dumps([role.lower(), location.lower()]).encode()
        ).hexdigest()
        ttl = int(current_app.config.get("CACHE_TTL_INTERNSHIP_FAST_SEC", 3600))
        if not hasattr(current_app, "_int_cache"):
            current_app._int_cache = {}
        cached = current_app._int_cache.get(key)
        if cached and (time.time() - cached["ts"] < ttl):
            jobs = cached["val"]
        else:
            jobs = mock_fetch(role, location)  # your helper: respects MOCK vs live inside
            # base enrich
            for j in jobs:
                j["learning_links"] = compute_learning_links(j.get("missing_skills", []))
            # store
            current_app._int_cache[key] = {"ts": time.time(), "val": jobs}
    else:
        # Deep path (no cache; you can add one if wanted)
        jobs = mock_fetch(role, location)
        for j in jobs:
            j["learning_links"] = compute_learning_links(j.get("missing_skills", []))
        jobs = deep_enrich_jobs(jobs, role)

    # Persist simple footprint for analytics/history
    try:
        for j in jobs:
            rec = InternshipRecord(
                user_id=current_user.id, role=role, location=location,
                source=j.get("source",""), title=j.get("title",""), company=j.get("company",""),
                link=j.get("link",""), match_score=int(j.get("match_score",0)),
                missing_skills=",".join(j.get("missing_skills",[]))
            )
            db.session.add(rec)
        db.session.commit()
    except Exception:
        db.session.rollback()  # keep UX resilient; don't break page

    return render_template("internships_index.html", jobs=jobs, role=role, location=location, mode=mode)
