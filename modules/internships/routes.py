# modules/internships/routes.py
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user

from limits import authorize_and_consume, can_use_pro, consume_pro
from models import db

# Use whichever helper you actually have defined.
# If you only have `internships_search` in helpers.py, this alias will wire it up.
try:
    from helpers import internships_analyze  # preferred name if present
except ImportError:
    from helpers import internships_search as internships_analyze  # alias to your existing function

internships_bp = Blueprint("internships", __name__, template_folder='../../templates/internships')


def _safe_result(raw):
    """
    Normalize helper output so Jinja never crashes.
    Final shape:
      { "ok": bool, "roles": [...], "companies": [...], "links": [...], "notes": str }
    """
    base = {"ok": True, "roles": [], "companies": [], "links": [], "notes": ""}

    if isinstance(raw, dict):
        base["roles"] = raw.get("roles") or []
        base["companies"] = raw.get("companies") or []
        base["links"] = raw.get("links") or []
        base["notes"] = (raw.get("notes") or raw.get("summary") or "")[:4000]
        return base

    if isinstance(raw, list):
        # e.g., a list of URLs/strings
        base["links"] = [str(x) for x in raw][:20]
        return base

    if isinstance(raw, str):
        base["notes"] = raw[:4000]
        return base

    base["notes"] = str(raw)[:4000]
    return base


@internships_bp.route("/", methods=["GET"], endpoint="index")
@login_required
def index():
    return render_template("internships/index.html", r=None, mode="basic")


@internships_bp.route("/analyse", methods=["POST"], endpoint="analyse")
@login_required
def analyse():
    text = (request.form.get("text") or request.form.get("query") or "").strip()
    mode = (request.form.get("mode") or "basic").lower()

    if not text:
        flash("Paste a role or a short query (e.g., 'VR internships London').", "warning")
        return redirect(url_for("internships.index"))

    try:
        # Credits: free for basic; pro when explicitly requested
        if mode == "pro":
            if not can_use_pro(current_user, "internships"):
                flash("Not enough Pro credits for deep search.", "warning")
                return redirect(url_for("billing.index"))
            raw = internships_analyze(text)
            r = _safe_result(raw)
            consume_pro(current_user, "internships")
        else:
            if not authorize_and_consume(current_user, "internships"):
                flash("You’ve hit today’s free limit. Upgrade to Pro to continue.", "warning")
                return redirect(url_for("billing.index"))
            raw = internships_analyze(text)
            r = _safe_result(raw)

        # Never let empty arrays look like failure — show something helpful
        if not (r["roles"] or r["companies"] or r["links"]):
            r["notes"] = r["notes"] or "No specific results detected. Try role + city (e.g., 'Backend internships London')."

        return render_template("internships/index.html", r=r, mode=mode)

    except Exception as e:
        current_app.logger.exception("Internships analyse error: %s", e)
        # Show a friendly page with guidance instead of 500
        r = _safe_result({"notes": "We couldn’t analyze that input. Try a simpler query (e.g., 'Game design internships London')."})
        return render_template("internships/index.html", r=r, mode=mode)
