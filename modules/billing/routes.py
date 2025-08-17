# modules/billing/routes.py
from flask import Blueprint, render_template

billing_bp = Blueprint("billing", __name__, template_folder="../../templates")

@billing_bp.route("/pricing")
def pricing():
    return render_template("pricing.html")
