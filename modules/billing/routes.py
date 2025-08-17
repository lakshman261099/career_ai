import stripe, os
from flask import Blueprint, current_app, redirect, url_for, flash, request
from flask_login import login_required, current_user
from models import db, Subscription, User

billing_bp = Blueprint("billing", __name__, template_folder="../../templates")

@billing_bp.route("/subscribe")
@login_required
def subscribe():
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    price_id = os.getenv("STRIPE_PRICE_PRO_MONTHLY")
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer_email=current_user.email,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=url_for("settings.profile", _external=True),
            cancel_url=url_for("settings.pricing", _external=True),
        )
        return redirect(session.url)
    except Exception as e:
        flash(f"Stripe error: {e}","error")
        return redirect(url_for("settings.pricing"))

@billing_bp.route("/webhook", methods=["POST"])
def webhook():
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    payload = request.data
    sig = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, os.getenv("STRIPE_WEBHOOK_SECRET")
        )
    except Exception as e:
        return str(e),400

    if event["type"]=="checkout.session.completed":
        email = event["data"]["object"].get("customer_email")
        if email:
            u = User.query.filter_by(email=email).first()
            if u:
                # mark pro
                sub = Subscription.query.filter_by(user_id=u.id).first()
                if not sub:
                    sub = Subscription(user_id=u.id, plan="pro", status="active")
                    db.session.add(sub)
                else:
                    sub.status="active"
                    sub.plan="pro"
                u.plan="pro"
                db.session.commit()
    return "ok"
