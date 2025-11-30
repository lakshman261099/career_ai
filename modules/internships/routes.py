from __future__ import annotations

import os
import json

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

from modules.common.ai import generate_internship_analysis  # AI-only
from modules.auth.guards import require_verified_email

# Phase 4: central credits engine
from modules.credits.engine import can_afford, deduct_free, deduct_pro

# DB + models
from models import db, InternshipRecord

internships_bp = Blueprint(
    "internships", __name__, template_folder="../../templates/internships"
)

CAREER_AI_VERSION = os.getenv("CAREER_AI_VERSION", "2025-Q4")
MAX_TEXT = int(os.getenv("INTERNSHIP_MAX_TEXT", "12000"))  # safety cap

# This matches FEATURE_COSTS["internship_analyzer"] in modules/credits/config.py
FEATURE_KEY = "internship_analyzer"


@internships_bp.route("/", methods=["GET"], endpoint="index")
@login_required
def index():
    """
    Internship Analyzer landing page.
    Free: high-level learning & career impact summary (uses Silver 🪙).
    Pro: profile-aware, deeper pathway analysis (Pro users, Gold ⭐ cost).
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
@require_verified_email
def analyse():
    """
    Run Internship Analyzer (Free or Pro).

    Free mode:
      - Uses Silver 🪙 via central credits engine:
        - can_afford(current_user, FEATURE_KEY, "silver")
        - deduct_free(...) AFTER successful AI call.
    Pro mode:
      - Requires active Pro ⭐ plan.
      - Uses Gold ⭐ per run:
        - can_afford(current_user, FEATURE_KEY, "gold")
        - deduct_pro(...) AFTER successful AI call.
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
            # ------------------ Pro mode: Pro plan + Gold ⭐ credits ------------------
            if not current_user.is_pro:
                flash(
                    "Internship Analyzer Pro requires an active Pro ⭐ plan.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

            # Gold credit check BEFORE AI
            if not can_afford(current_user, FEATURE_KEY, currency="gold"):
                flash(
                    "Not enough Gold ⭐ credits. Add more in the Coins Shop or "
                    "adjust your usage.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

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

            # Deduct Gold ⭐ AFTER successful AI call
            try:
                if not deduct_pro(current_user, FEATURE_KEY, run_id=None):
                    current_app.logger.warning(
                        "Internship Analyzer Pro: deduct_pro failed after analysis "
                        "for user %s",
                        current_user.id,
                    )
                    flash(
                        "Your Pro analysis completed, but your Gold ⭐ credits could not "
                        "be updated correctly. Please contact support if this keeps happening.",
                        "warning",
                    )
            except Exception as e:
                current_app.logger.exception(
                    "Internship Analyzer Pro credit deduction error: %s", e
                )
                flash(
                    "Your Pro analysis completed, but we had trouble updating your "
                    "Gold ⭐ credits. Please contact support if this keeps happening.",
                    "warning",
                )

        else:
            # ------------------ Free mode: Silver 🪙 credits ------------------
            if not can_afford(current_user, FEATURE_KEY, currency="silver"):
                flash(
                    "Not enough Silver 🪙 credits. Upgrade to Pro ⭐ for deeper, "
                    "profile-aware internship insights and Gold ⭐ runs.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

            data, used_live_ai = generate_internship_analysis(
                pro_mode=False,
                internship_text=text,
                return_source=True,
            )

            # Deduct Silver 🪙 only AFTER successful AI call
            try:
                if not deduct_free(current_user, FEATURE_KEY, run_id=None):
                    current_app.logger.warning(
                        "Internship Analyzer: deduct_free failed after analysis "
                        "for user %s",
                        current_user.id,
                    )
            except Exception as e:
                current_app.logger.exception(
                    "Internship Analyzer credit deduction error: %s", e
                )
                # User still gets result; credits may not have updated.

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

        # Persist this run into InternshipRecord for history
        try:
            # Naive role guess: first 120 chars of summary or empty
            role_guess = (data.get("meta", {}) or {}).get("role_title") or ""
            if not role_guess and data.get("summary"):
                role_guess = (data["summary"][:120]).strip()

            record = InternshipRecord(
                user_id=current_user.id,
                role=role_guess or None,
                location=None,
                results_json=json.dumps(data),
            )
            db.session.add(record)
            db.session.commit()
        except Exception as e:
            current_app.logger.exception("Failed to save InternshipRecord: %s", e)
            try:
                db.session.rollback()
            except Exception:
                pass

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
        try:
            db.session.rollback()
        except Exception:
            pass
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


@internships_bp.route("/history", methods=["GET"], endpoint="history")
@login_required
def history():
    """
    Show a simple history of Internship Analyzer runs for this user.
    Uses InternshipRecord.results_json (stored as text).
    """
    page = request.args.get("page", 1, type=int)
    per_page = 10

    pagination = (
        InternshipRecord.query.filter_by(user_id=current_user.id)
        .order_by(InternshipRecord.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    records = pagination.items

    # We can pass records as-is; template can decide how much of results_json to show
    return render_template(
        "internships/history.html",
        records=records,
        pagination=pagination,
    )
