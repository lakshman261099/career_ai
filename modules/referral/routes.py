# modules/referral/routes.py
from __future__ import annotations

import os

from flask import Blueprint, flash, render_template, request
from flask_login import current_user, login_required

from limits import authorize_and_consume
from modules.common.ai import generate_referral_messages  # AI-only

referral_bp = Blueprint(
    "referral", __name__, template_folder="../../templates/referral"
)

CAREER_AI_VERSION = os.getenv("CAREER_AI_VERSION", "2025-Q4")


@referral_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Referral Trainer — Free feature only (Pro templates coming soon).
    Uses authorize_and_consume('referral') to deduct Silver 🪙 credits.
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
        }

        if not authorize_and_consume(current_user, "referral"):
            flash(
                "Not enough Silver 🪙 credits for Referral Trainer. Pro templates are coming soon.",
                "warning",
            )
        else:
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
