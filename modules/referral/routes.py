from flask import Blueprint, render_template, request, flash
from flask_login import login_required, current_user
from helpers import referral_messages
from limits import can_use_free, consume_free, can_use_pro, consume_pro

referral_bp = Blueprint("referral", __name__, template_folder="../../templates/referral")

@referral_bp.route("/", methods=["GET","POST"])
@login_required
def index():
    msgs = {}
    if request.method == "POST":
        contact = {
            "name": request.form.get("contact_name",""),
            "role": request.form.get("contact_role",""),
            "company": request.form.get("contact_company",""),
            "email": request.form.get("contact_email",""),
            "source": request.form.get("contact_source",""),
        }
        profile = {
            "role": request.form.get("target_role",""),
            "highlights": request.form.get("highlights",""),
        }
        deep = request.form.get("mode") == "pro"
        feature = "referral"
        if deep:
            if not can_use_pro(current_user, feature):
                flash("Not enough Pro coins.", "error")
            else:
                consume_pro(current_user, feature)
                msgs = referral_messages(contact, profile, deep=True)
        else:
            if not can_use_free(current_user, feature):
                flash("Daily free limit reached.", "error")
            else:
                consume_free(current_user, feature)
                msgs = referral_messages(contact, profile, deep=False)
    return render_template("referral/index.html", msgs=msgs)
