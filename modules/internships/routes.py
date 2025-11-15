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
FEATURE_KEY = "internships"


@internships_bp.route("/", methods=["GET"], endpoint="index")
@login_required
def index():
    """
    Internship Analyzer landing page.
    Free: high-level learning & career impact summary.
    Pro: profile-aware, deeper pathway analysis.
    """
    return render_template(
        "internships/index.html",
        result=None,
        mode="free",
        is_pro=current_user.is_pro,
        updated_tag=CAREER_AI_VERSION,
        used_live_ai=None,
    )


@internships_bp.route("/analyse", methods=["POST"], endpoint="analyse")
@login_required
def analyse():
    """
    Run Internship Analyzer (Free or Pro).
    - Free uses Silver (🪙) via authorize_and_consume.
    - Pro uses Gold (⭐) accounting via consume_pro, but we do NOT hard-block
      on can_use_pro to avoid false negatives when the user is clearly Pro.
    """
    text = (request.form.get("text") or "").strip()
    mode = (request.form.get("mode") or "free").lower()
    text = text[:MAX_TEXT] if text else ""

    if not text:
        flash("Paste an internship description to analyze.", "warning")
        return redirect(url_for("internships.index"))

    is_pro_run = mode == "pro"
    used_live_ai = False
    data = None

    try:
        if is_pro_run:
            # 1) Hard gate on subscription status only
            if not current_user.is_pro:
                flash(
                    "Internship Analyzer Pro requires an active Pro ⭐ plan.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

            # 2) Soft check can_use_pro for logging only (no hard block)
            try:
                ok = can_use_pro(current_user, FEATURE_KEY)
                if not ok:
                    current_app.logger.warning(
                        "can_use_pro returned False for feature '%s' "
                        "but user.is_pro=%s (allowing run anyway).",
                        FEATURE_KEY,
                        getattr(current_user, "is_pro", None),
                    )
            except Exception as e:
                current_app.logger.warning(
                    "can_use_pro check failed for feature '%s': %s",
                    FEATURE_KEY,
                    e,
                )

            # Try to pull Profile Portal into the prompt (best-effort)
            profile_dict = {}
            try:
                profile = getattr(current_user, "profile", None)
                if profile and hasattr(profile, "to_dict"):
                    profile_dict = profile.to_dict()
            except Exception as e:
                current_app.logger.warning(
                    "Internship Analyzer: profile.to_dict() failed: %s", e
                )
                profile_dict = {}

            data, used_live_ai = generate_internship_analysis(
                pro_mode=True,
                internship_text=text,
                profile_json=profile_dict,
                return_source=True,
            )

            # Consume ⭐ only after a successful AI call (best-effort)
            try:
                consume_pro(current_user, FEATURE_KEY)
            except Exception as e:
                current_app.logger.warning(
                    "Pro credit consume failed after internship analysis (%s): %s",
                    FEATURE_KEY,
                    e,
                )

        else:
            # Free mode → Silver credits (hard gate)
            if not authorize_and_consume(current_user, FEATURE_KEY):
                flash(
                    "Not enough Silver 🪙 credits. Upgrade to Pro ⭐ for deeper, profile-aware insights.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

            data, used_live_ai = generate_internship_analysis(
                pro_mode=False,
                internship_text=text,
                return_source=True,
            )

        # AI data should follow INTERNSHIP_ANALYZER_JSON_SCHEMA, but we guard for safety
        if not isinstance(data, dict):
            data = {}

        # Ensure required keys exist so template never crashes
        data.setdefault("mode", "pro" if is_pro_run else "free")
        data.setdefault("summary", "")
        data.setdefault("skill_growth", [])
        data.setdefault("skill_enhancement", [])
        data.setdefault("career_impact", "")
        data.setdefault("new_paths", [])
        data.setdefault("resume_boost", [])
        data.setdefault("meta", {})

        return render_template(
            "internships/index.html",
            result=data,
            mode="pro" if is_pro_run else "free",
            is_pro=current_user.is_pro,
            updated_tag=CAREER_AI_VERSION,
            used_live_ai=used_live_ai,
        )

    except Exception as e:
        current_app.logger.exception("Internship analysis error: %s", e)
        # Schema-friendly error payload for template stability
        err_payload = {
            "mode": "pro" if is_pro_run else "free",
            "summary": "We couldn’t analyze that input. Try a simpler internship description.",
            "skill_growth": [],
            "skill_enhancement": [],
            "career_impact": "",
            "new_paths": [],
            "resume_boost": [],
            "meta": {"generated_at_utc": "", "inputs_digest": "sha256:error"},
        }
        return render_template(
            "internships/index.html",
            result=err_payload,
            mode="pro" if is_pro_run else "free",
            is_pro=current_user.is_pro,
            updated_tag=CAREER_AI_VERSION,
            used_live_ai=False,
        )
