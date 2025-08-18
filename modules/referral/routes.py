# modules/referral/routes.py

from flask import Blueprint, render_template, request, flash
from flask_login import login_required, current_user
from helpers import referral_messages
from limits import authorize_and_consume  # free first, fallback to ⭐ (but we won't expose deep here)

referral_bp = Blueprint("referral", __name__, template_folder="../../templates/referral")

@referral_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Referral Trainer — Free feature (KB). Pro 'Deep' tuning is coming soon.
    We still use authorize_and_consume('referral') to count free usage.
    """
    msgs = {}
    if request.method == "POST":
        contact = {
            "name": request.form.get("contact_name","").strip(),
            "role": request.form.get("contact_role","").strip(),
            "company": request.form.get("contact_company","").strip(),
            "email": request.form.get("contact_email","").strip(),
            "source": request.form.get("contact_source","").strip(),
        }
        profile = {
            "role": request.form.get("target_role","").strip(),
            "highlights": request.form.get("highlights","").strip(),
        }

        # Count usage (Free daily). If free is exhausted, this will return False.
        if not authorize_and_consume(current_user, "referral"):
            flash("You’ve used today’s free quota for Referral Trainer. Pro tuning is coming soon.", "warning")
        else:
            # Deep tuning is not exposed yet per KB; always call with deep=False
            msgs = referral_messages(contact, profile, deep=False)

    return render_template("referral/index.html", msgs=msgs)
