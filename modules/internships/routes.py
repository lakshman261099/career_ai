# modules/internships/routes.py
from __future__ import annotations

import os

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from limits import authorize_and_consume, can_use_pro, consume_pro
from modules.common.ai import generate_internship_analysis  # AI-only

internships_bp = Blueprint(
    "internships", __name__, template_folder="../../templates/internships"
)

CAREER_AI_VERSION = os.getenv("CAREER_AI_VERSION", "2025-Q4")
MAX_TEXT = int(os.getenv("INTERNSHIP_MAX_TEXT", "12000"))  # safety cap


@internships_bp.route("/", methods=["GET"], endpoint="index")
@login_required
def index():
    return render_template(
        "internships/index.html",
        result=None,
        mode="free",
        updated_tag=CAREER_AI_VERSION,
    )


@internships_bp.route("/analyse", methods=["POST"], endpoint="analyse")
@login_required
def analyse():
    text = (request.form.get("text") or "").strip()
    mode = (request.form.get("mode") or "free").lower()
    text = text[:MAX_TEXT] if text else ""

    if not text:
        flash("Paste an internship description to analyze.", "warning")
        return redirect(url_for("internships.index"))

    try:
        if mode == "pro":
            if not can_use_pro(current_user, "internships"):
                flash(
                    "Not enough Pro ⭐ credits for deep internship analysis.", "warning"
                )
                return redirect(url_for("billing.index"))

            profile_dict = {}
            try:
                profile = getattr(current_user, "profile", None)
                if profile and hasattr(profile, "to_dict"):
                    profile_dict = profile.to_dict()
            except Exception:
                profile_dict = {}

            data, used_live_ai = generate_internship_analysis(
                pro_mode=True,
                internship_text=text,
                profile_json=profile_dict,
                return_source=True,
            )

            # Consume ⭐ only after a successful AI call
            try:
                consume_pro(current_user, "internships")
            except Exception as e:
                current_app.logger.warning(
                    "Pro credit consume failed after internship analysis: %s", e
                )

        else:
            if not authorize_and_consume(current_user, "internships"):
                flash(
                    "Not enough Silver 🪙 credits. Upgrade to Pro ⭐ for deeper insights.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

            data, used_live_ai = generate_internship_analysis(
                pro_mode=False,
                internship_text=text,
                return_source=True,
            )

        return render_template(
            "internships/index.html",
            result=data,
            mode=mode,
            used_live_ai=used_live_ai,
            updated_tag=CAREER_AI_VERSION,
        )

    except Exception as e:
        current_app.logger.exception("Internship analysis error: %s", e)
        # Schema-friendly error payload for template stability
        err_payload = {
            "mode": mode,
            "summary": "We couldn’t analyze that input. Try a simpler description.",
            "meta": {"generated_at_utc": "", "inputs_digest": "sha256:error"},
        }
        return render_template(
            "internships/index.html",
            result=err_payload,
            mode=mode,
            used_live_ai=False,
            updated_tag=CAREER_AI_VERSION,
        )
