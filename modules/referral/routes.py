from flask import Blueprint, render_template, request, flash
from flask_login import login_required, current_user

from limits import authorize_and_consume
from modules.common.ai import generate_referral_messages  # now from ai.py

referral_bp = Blueprint("referral", __name__, template_folder="../../templates/referral")

@referral_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Referral Trainer — Free feature only (Pro templates coming soon).
    Uses authorize_and_consume('referral') to deduct 🪙 Silver credits.
    """
    msgs = {}
    if request.method == "POST":
        contact = {
            "name": request.form.get("contact_name", "").strip(),
            "role": request.form.get("contact_role", "").strip(),
            "company": request.form.get("contact_company", "").strip(),
            "email": request.form.get("contact_email", "").strip(),
            "source": request.form.get("contact_source", "").strip(),
        }
        profile = {
            "role": request.form.get("target_role", "").strip(),
            "highlights": request.form.get("highlights", "").strip(),
        }

        if not authorize_and_consume(current_user, "referral"):
            flash("Not enough Silver credits for Referral Trainer. Pro templates are coming soon.", "warning")
        else:
            msgs = generate_referral_messages(contact, profile)

    return render_template("referral/index.html", msgs=msgs)
