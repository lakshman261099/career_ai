# modules/referral/routes.py
from __future__ import annotations

import os

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from modules.common.ai import generate_referral_messages  # AI-only

# Phase 4: central credits engine
from modules.credits.engine import can_afford, deduct_free

referral_bp = Blueprint(
    "referral", __name__, template_folder="../../templates/referral"
)

CAREER_AI_VERSION = os.getenv("CAREER_AI_VERSION", "2025-Q4")

# Phase 4: this must match FEATURE_COSTS key in modules/credits/config.py
# Referral Trainer is currently Free-only (Silver 🪙).
FEATURE_KEY = "referral_trainer_free"


@referral_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Referral Trainer — simple, single-mode feature.

    Current behavior:
      - Uses Silver (🪙) credits via central credits engine.
      - Generates 2–3 short scripts students can lightly edit:
        warm, cold, and follow-up outreach.

    Pro-only extra templates & tonality controls are coming soon.
    """
    msgs = {}
    used_live_ai = False

    if request.method == "POST":
        # 🔒 Email verification guard
        if not getattr(current_user, "verified", False):
            flash(
                "Please verify your email with a login code before using AI features.",
                "warning",
            )
            return redirect(url_for("auth.otp_request"))

        contact = {
            "name": (request.form.get("contact_name") or "").strip(),
            "role": (request.form.get("contact_role") or "").strip(),
            "company": (request.form.get("contact_company") or "").strip(),
            "email": (request.form.get("contact_email") or "").strip(),
            "source": (request.form.get("contact_source") or "").strip(),
        }
        profile = {
            "role": (request.form.get("target_role") or "").strip(),
            "highlights": (request.form.get("highlights") or "").strip(),
            # Extra context: job description (optional, so messages can reference the role)
            "job_description": (request.form.get("job_description") or "").strip(),
        }

        # Silver credit check BEFORE AI
        if not can_afford(current_user, FEATURE_KEY, currency="silver"):
            flash(
                "Not enough Silver 🪙 credits for Referral Trainer. "
                "Upgrade to Pro ⭐ or add more credits in the Coins Shop.",
                "warning",
            )
            return redirect(url_for("billing.index"))

        try:
            msgs, used_live_ai = generate_referral_messages(
                contact, profile, return_source=True
            )

            # Deduct Silver 🪙 AFTER successful AI
            try:
                if not deduct_free(current_user, FEATURE_KEY, run_id=None):
                    # Soft failure: user still gets messages, but credits may not have updated.
                    flash(
                        "Your outreach templates were generated, but we had trouble "
                        "updating your credits. Please contact support if this keeps happening.",
                        "warning",
                    )
            except Exception as e:
                # Log but don't break UX
                from flask import current_app

                current_app.logger.exception(
                    "Referral Trainer credit deduction error: %s", e
                )
                flash(
                    "Your outreach templates were generated, but we had trouble "
                    "updating your credits. Please contact support if this keeps happening.",
                    "warning",
                )

        except Exception as e:
            err = f"ERROR: {e}"
            msgs = {"warm": err, "cold": err, "follow": err}
            used_live_ai = False

    return render_template(
        "referral/index.html",
        msgs=msgs,
        used_live_ai=used_live_ai,
        updated_tag=CAREER_AI_VERSION,
    )
