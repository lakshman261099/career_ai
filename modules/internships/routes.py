from __future__ import annotations

import os
import json
import uuid

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
from modules.credits.engine import can_afford, deduct_free, deduct_pro, refund

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

    CREDIT FLOW (FIXED):
    1. Check credits BEFORE AI
    2. Deduct credits BEFORE AI (reserve them)
    3. Run AI
    4. On failure → refund credits
    5. On success → keep credits (already deducted)

    Free mode:
      - Uses Silver 🪙 via central credits engine
    Pro mode:
      - Requires active Pro ⭐ plan
      - Uses Gold ⭐ per run
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

    # Generate unique run ID for credit tracking
    run_id = f"intern_{current_user.id}_{uuid.uuid4().hex[:8]}"

    try:
        if is_pro_run:
            # ------------------ Pro mode: Pro plan + Gold ⭐ credits ------------------
            if not current_user.is_pro:
                flash(
                    "Internship Analyzer Pro requires an active Pro ⭐ plan.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

            # ✅ STEP 1: Check Gold credits BEFORE AI
            if not can_afford(current_user, FEATURE_KEY, currency="gold"):
                flash(
                    "Not enough Gold ⭐ credits. Add more in the Coins Shop or "
                    "adjust your usage.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

            # ✅ STEP 2: Deduct Gold ⭐ BEFORE AI call (reserve credits)
            try:
                if not deduct_pro(current_user, FEATURE_KEY, run_id=run_id):
                    flash(
                        "We couldn't deduct your Gold ⭐ credits right now. Please try again.",
                        "danger",
                    )
                    return redirect(url_for("internships.index"))
            except Exception as e:
                current_app.logger.exception("Internship Pro: deduct_pro failed before AI: %s", e)
                flash(
                    "We couldn't process your credits right now. Please try again.",
                    "danger",
                )
                return redirect(url_for("internships.index"))

            # ✅ STEP 3: Run AI (credits already deducted)
            try:
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
            except Exception as ai_error:
                # ✅ STEP 4: AI failed → refund credits
                current_app.logger.exception("Internship Pro: AI call failed: %s", ai_error)
                try:
                    refund(
                        current_user,
                        feature=FEATURE_KEY,
                        currency="gold",
                        amount=None,  # Will use feature cost from config
                        run_id=run_id,
                        commit=True,
                    )
                    flash(
                        "Analysis failed. Your Gold ⭐ credits were refunded. Please try again.",
                        "danger",
                    )
                except Exception as refund_error:
                    current_app.logger.exception("Internship Pro: refund failed: %s", refund_error)
                    flash(
                        "Analysis failed. Please contact support if credits were deducted.",
                        "danger",
                    )
                return redirect(url_for("internships.index"))

        else:
            # ------------------ Free mode: Silver 🪙 credits ------------------
            # ✅ STEP 1: Check Silver credits BEFORE AI
            if not can_afford(current_user, FEATURE_KEY, currency="silver"):
                flash(
                    "Not enough Silver 🪙 credits. Upgrade to Pro ⭐ for deeper, "
                    "profile-aware internship insights and Gold ⭐ runs.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

            # ✅ STEP 2: Deduct Silver 🪙 BEFORE AI call (reserve credits)
            try:
                if not deduct_free(current_user, FEATURE_KEY, run_id=run_id):
                    flash(
                        "We couldn't deduct your Silver 🪙 credits right now. Please try again.",
                        "danger",
                    )
                    return redirect(url_for("internships.index"))
            except Exception as e:
                current_app.logger.exception("Internship Free: deduct_free failed before AI: %s", e)
                flash(
                    "We couldn't process your credits right now. Please try again.",
                    "danger",
                )
                return redirect(url_for("internships.index"))

            # ✅ STEP 3: Run AI (credits already deducted)
            try:
                data, used_live_ai = generate_internship_analysis(
                    pro_mode=False,
                    internship_text=text,
                    return_source=True,
                )
            except Exception as ai_error:
                # ✅ STEP 4: AI failed → refund credits
                current_app.logger.exception("Internship Free: AI call failed: %s", ai_error)
                try:
                    refund(
                        current_user,
                        feature=FEATURE_KEY,
                        currency="silver",
                        amount=None,  # Will use feature cost from config
                        run_id=run_id,
                        commit=True,
                    )
                    flash(
                        "Analysis failed. Your Silver 🪙 credits were refunded. Please try again.",
                        "danger",
                    )
                except Exception as refund_error:
                    current_app.logger.exception("Internship Free: refund failed: %s", refund_error)
                    flash(
                        "Analysis failed. Please contact support if credits were deducted.",
                        "danger",
                    )
                return redirect(url_for("internships.index"))

        # ✅ STEP 5: Success! Credits already deducted, just show results
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
        # ⚠️ Unexpected error → refund if we deducted
        current_app.logger.exception("Internship analysis unexpected error: %s", e)
        
        # Try to refund (best effort)
        try:
            refund(
                current_user,
                feature=FEATURE_KEY,
                currency="gold" if is_pro_run else "silver",
                amount=None,
                run_id=run_id,
                commit=True,
            )
        except Exception:
            pass
        
        try:
            db.session.rollback()
        except Exception:
            pass
        
        # Schema-friendly error payload for template stability
        err_payload = {
            "mode": "pro" if is_pro_run else "free",
            "summary": "We couldn't analyze that input. Try a simpler internship description.",
            "skill_growth": [],
            "skill_enhancement": [],
            "career_impact": "",
            "new_paths": [],
            "resume_boost": [],
            "meta": {"generated_at_utc": "", "inputs_digest": "sha256:error"},
        }
        
        flash(
            "Analysis failed. If credits were deducted, they have been refunded. Please try again.",
            "danger"
        )
        
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