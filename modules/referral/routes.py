# modules/referral/routes.py
from __future__ import annotations

import os

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from limits import authorize_and_consume
from modules.common.ai import generate_referral_messages  # AI-only

referral_bp = Blueprint(
    "referral", __name__, template_folder="../../templates/referral"
)

CAREER_AI_VERSION = os.getenv("CAREER_AI_VERSION", "2025-Q4")
FEATURE_KEY = "referral"


@referral_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Referral Trainer — simple, single-mode feature.

    - Uses Silver (🪙) credits via authorize_and_consume('referral').
    - Generates 2–3 short scripts students can lightly edit:
      warm, cold, and follow-up outreach.
    """
    msgs = {}
    used_live_ai = False

    if request.method == "POST":
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

        # Silver credit check
        if not authorize_and_consume(current_user, FEATURE_KEY):
            flash(
                "Not enough Silver 🪙 credits for Referral Trainer. "
                "Upgrade to Pro ⭐ for more CareerAI features.",
                "warning",
            )
            return redirect(url_for("billing.index"))

        try:
            msgs, used_live_ai = generate_referral_messages(
                contact, profile, return_source=True
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
