from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user

from limits import authorize_and_consume, can_use_pro, consume_pro
from modules.common.ai import generate_internship_analysis  # now from ai.py
from models import db

internships_bp = Blueprint("internships", __name__, template_folder='../../templates/internships')

@internships_bp.route("/", methods=["GET"], endpoint="index")
@login_required
def index():
    return render_template("internships/index.html", result=None, mode="free")

@internships_bp.route("/analyse", methods=["POST"], endpoint="analyse")
@login_required
def analyse():
    text = (request.form.get("text") or "").strip()
    mode = (request.form.get("mode") or "free").lower()

    if not text:
        flash("Paste an internship description to analyze.", "warning")
        return redirect(url_for("internships.index"))

    try:
        if mode == "pro":
            if not can_use_pro(current_user, "internships"):
                flash("Not enough Pro credits for deep internship analysis.", "warning")
                return redirect(url_for("billing.index"))

            raw = generate_internship_analysis(
                pro_mode=True,
                internship_text=text,
                profile_json=(current_user.profile.to_dict() if current_user.profile else {}),
            )
            consume_pro(current_user, "internships")

        else:
            if not authorize_and_consume(current_user, "internships"):
                flash("Not enough Silver credits for Internship Analyzer. Upgrade to Pro for deeper insights.", "warning")
                return redirect(url_for("billing.index"))

            raw = generate_internship_analysis(
                pro_mode=False,
                internship_text=text,
            )

        return render_template("internships/index.html", result=raw, mode=mode)

    except Exception as e:
        current_app.logger.exception("Internship analysis error: %s", e)
        err = {"mode": mode, "summary": "We couldn’t analyze that input. Try a simpler description."}
        return render_template("internships/index.html", result=err, mode=mode)
