from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db

billing_bp = Blueprint("billing", __name__, template_folder="../../templates")

@billing_bp.route("/pricing")
def pricing():
    return render_template("pricing.html")

# --- Dev helpers so you can test Pro quickly ---
def _add_to_first_attr(model, obj, fields, amount):
    for f in fields:
        if hasattr(obj, f):
            try:
                cur = getattr(obj, f) or 0
                setattr(obj, f, cur + amount)
                return True
            except Exception:
                pass
    return False

@billing_bp.route("/mock-topup/pro")
@login_required
def mock_topup_pro():
    if _add_to_first_attr(type(current_user), current_user,
                          ["gold_balance", "paid_credits", "pro_credits", "credit_balance"], 100):
        if hasattr(current_user, "subscription_status"):
            current_user.subscription_status = "active"
        db.session.commit()
        flash("Added 100 Pro Coins (dev).", "success")
    else:
        flash("Could not top up (no pro coins field found).", "danger")
    return redirect(url_for("billing.pricing"))

@billing_bp.route("/mock-topup/free")
@login_required
def mock_topup_free():
    if _add_to_first_attr(type(current_user), current_user,
                          ["free_credits", "silver_balance", "credits_free", "free_balance"], 25):
        db.session.commit()
        flash("Added 25 Free Coins (dev).", "success")
    else:
        flash("Could not top up (no free coins field found).", "danger")
    return redirect(url_for("billing.pricing"))
