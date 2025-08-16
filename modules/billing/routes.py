# modules/billing/routes.py
import os, datetime as dt
from flask import Blueprint, request, redirect, url_for, flash, current_app, jsonify, render_template
from flask_login import login_required, current_user
import stripe
from models import db, Subscription

billing_bp = Blueprint("billing", __name__)
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

def _ensure_customer(user_id):
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if sub and sub.stripe_customer_id:
        return sub
    s = Subscription(user_id=user_id, status="inactive")
    db.session.add(s); db.session.commit()
    return s

@billing_bp.post("/checkout")
@login_required
def checkout():
    plan = (request.form.get("plan") or "pro_monthly").strip()
    price_id = os.getenv("STRIPE_PRICE_PRO_MONTHLY") if plan=="pro_monthly" else os.getenv("STRIPE_PRICE_PRO_YEARLY")
    if not stripe.api_key or not price_id:
        flash("Billing not configured. Contact admin.", "error")
        return redirect(url_for("pricing"))
    sub = _ensure_customer(current_user.id)
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=request.host_url.rstrip("/") + url_for("dashboard"),
            cancel_url=request.host_url.rstrip("/") + url_for("pricing"),
            customer_email=current_user.email,
            automatic_tax={"enabled": False}
        )
        return redirect(session.url, code=303)
    except Exception as e:
        current_app.logger.exception("Stripe checkout error")
        flash(f"Checkout failed: {e}", "error")
        return redirect(url_for("pricing"))

@billing_bp.get("/portal")
@login_required
def portal():
    if not stripe.api_key:
        flash("Billing not configured.", "error")
        return redirect(url_for("pricing"))
    # In demo, simply redirect back
    return redirect(url_for("pricing"))

@billing_bp.post("/webhook")
def webhook():
    payload = request.data
    sig = request.headers.get("Stripe-Signature", "")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        return jsonify({"ok": True})  # ignore in demo
    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    t = event["type"]
    data = event["data"]["object"]

    try:
        if t == "checkout.session.completed":
            email = data.get("customer_details", {}).get("email")
            sub_id = data.get("subscription")
            plan = "pro_monthly"
            user = None
            from models import User
            if email:
                user = User.query.filter_by(email=email.lower()).first()
            if user:
                rec = Subscription.query.filter_by(user_id=user.id).first() or Subscription(user_id=user.id)
                rec.stripe_subscription_id = sub_id
                rec.status = "active"
                rec.plan = plan
                rec.current_period_end = dt.datetime.utcnow() + dt.timedelta(days=30)
                db.session.add(rec); db.session.commit()
        elif t == "customer.subscription.updated":
            sub_id = data.get("id")
            status = data.get("status")
            rec = Subscription.query.filter_by(stripe_subscription_id=sub_id).first()
            if rec:
                rec.status = status
                db.session.add(rec); db.session.commit()
    except Exception:
        db.session.rollback()

    return jsonify({"ok": True})
