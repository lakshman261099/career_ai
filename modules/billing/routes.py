from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db

billing_bp = Blueprint("billing", __name__, template_folder="../../templates")

@billing_bp.route("/pricing")
def pricing():
    return render_template("pricing.html")

@billing_bp.route("/billing/mock-topup/free")
@login_required
def mock_topup_free():
    current_user.coins_free += 5
    db.session.commit()
    flash("Added 5 free coins (dev).", "success")
    return redirect(url_for("pricing"))

@billing_bp.route("/billing/mock-topup/pro")
@login_required
def mock_topup_pro():
    current_user.coins_pro += 10
    current_user.subscription_status = "pro"
    db.session.commit()
    flash("Added 10 pro coins (dev).", "success")
    return redirect(url_for("pricing"))
