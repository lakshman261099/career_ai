# modules/billing/routes.py
import os
from flask import Blueprint, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
import stripe
from models import db, Subscription

billing_bp = Blueprint("billing", __name__)

@billing_bp.post("/checkout")
@login_required
def checkout():
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    price = request.form.get("plan")  # "pro_monthly" | "pro_yearly"
    price_id = os.getenv("STRIPE_PRICE_PRO_MONTHLY") if price == "pro_monthly" else os.getenv("STRIPE_PRICE_PRO_YEARLY")
    if not stripe.api_key or not price_id:
        flash("Stripe is not configured.", "error"); return redirect(url_for("pricing"))
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=request.url_root.strip("/") + url_for("dashboard"),
        cancel_url=request.url_root.strip("/") + url_for("pricing"),
        customer_email=current_user.email
    )
    return redirect(session.url, code=303)

@billing_bp.get("/portal")
@login_required
def portal():
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    if not stripe.api_key:
        flash("Stripe is not configured.", "error"); return redirect(url_for("settings.page"))
    # Ensure a subscription exists; otherwise show pricing
    sub = Subscription.query.filter_by(user_id=current_user.id).order_by(Subscription.current_period_end.desc()).first()
    if not sub or not sub.stripe_customer_id:
        flash("No subscription found. Choose a plan.", "error")
        return redirect(url_for("pricing"))
    session = stripe.billing_portal.Session.create(
        customer=sub.stripe_customer_id,
        return_url=request.url_root.strip("/") + url_for("settings.page")
    )
    return redirect(session.url, code=303)
