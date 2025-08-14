import os, json, datetime as dt
from flask import Blueprint, request, redirect, url_for, abort
from flask_login import login_required, current_user
import stripe
from models import db, Subscription

billing_bp = Blueprint("billing", __name__)
stripe.api_key = os.getenv("STRIPE_SECRET_KEY","")

@billing_bp.route("/checkout", methods=["POST"])
@login_required
def checkout():

    price_id = request.form.get("price_id") or os.getenv("STRIPE_PRICE_PRO_MONTHLY","")    
    if not stripe.api_key or not price_id:
        abort(400, "Stripe not configured")
    customer = None
    subs = Subscription.query.filter_by(user_id=current_user.id).first()
    if subs and subs.stripe_customer_id:
        customer = subs.stripe_customer_id
    else:
        customer_obj = stripe.Customer.create(email=current_user.email)
        customer = customer_obj["id"]
        if not subs:
            subs = Subscription(user_id=current_user.id, stripe_customer_id=customer, status="inactive")
            db.session.add(subs); db.session.commit()
        else:
            subs.stripe_customer_id = customer; db.session.commit()
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer,
        line_items=[{"price": price_id, "quantity":1}],
        success_url=url_for("dashboard", _external=True),
        cancel_url=url_for("pricing", _external=True),
    )
    return redirect(session.url, code=303)

@billing_bp.route("/portal")
@login_required
def portal():
    if not stripe.api_key:
        abort(400, "Stripe not configured")
    subs = Subscription.query.filter_by(user_id=current_user.id).first()
    if not subs or not subs.stripe_customer_id:
        abort(400, "No customer")
    sess = stripe.billing_portal.Session.create(
        customer=subs.stripe_customer_id,
        return_url=os.getenv("STRIPE_PORTAL_RETURN_URL", url_for("dashboard", _external=True))
    )
    return redirect(sess.url)

@billing_bp.route("/webhook", methods=["POST"])
def webhook():
    payload = request.data
    sig = request.headers.get("stripe-signature")
    whsec = os.getenv("STRIPE_WEBHOOK_SECRET","")
    event = None
    try:
        event = stripe.Webhook.construct_event(payload, sig, whsec) if whsec else json.loads(payload)
    except Exception:
        return "Invalid payload", 400

    # Handle subscription updates
    if event.get("type") in ["customer.subscription.created","customer.subscription.updated","checkout.session.completed"]:
        obj = event.get("data",{}).get("object",{})
        if event["type"] == "checkout.session.completed":
            customer_id = obj.get("customer")
            subscription_id = obj.get("subscription")
        else:
            customer_id = obj.get("customer")
            subscription_id = obj.get("id")
        subs = Subscription.query.filter_by(stripe_customer_id=customer_id).first()
        if subs:
            subs.stripe_subscription_id = subscription_id
            status = obj.get("status","active") if event["type"]!="checkout.session.completed" else "active"
            subs.status = status
            period_end = obj.get("current_period_end") or obj.get("trial_end")
            if period_end:
                subs.current_period_end = dt.datetime.utcfromtimestamp(period_end)
            db.session.commit()
    return "ok", 200
