# modules/billing/routes.py

import os
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from models import db, User

billing_bp = Blueprint("billing", __name__, template_folder="../../templates")

# Stripe SDK (install: pip install stripe)
import stripe

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLIC_KEY")
STRIPE_PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO_MONTHLY_INR")  # subscription price
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")


# ------------------------------------------
# Pricing / Index
# ------------------------------------------
@billing_bp.route("/", endpoint="index")
def index():
    """
    Pricing/Landing for billing. Show Pro plan details and a Checkout button.
    """
    return render_template(
        "pricing.html",
        stripe_public_key=STRIPE_PUBLISHABLE_KEY,
        price_id=STRIPE_PRICE_ID_PRO,
        is_pro=getattr(current_user, "is_pro", False),
        subscription_status=getattr(current_user, "subscription_status", "free"),
    )


# Back-compat: /pricing -> /billing/
@billing_bp.route("/pricing", endpoint="pricing_legacy")
def pricing_legacy():
    return redirect(url_for("billing.index"))


# ------------------------------------------
# Create Stripe Checkout Session (Subscription)
# ------------------------------------------
@billing_bp.route("/checkout/pro", methods=["POST"], endpoint="checkout_pro")
@login_required
def checkout_pro():
    """
    Creates a Stripe Checkout session in subscription mode for Pro.
    The session includes user_id in metadata for webhook linking.
    """
    if not STRIPE_PRICE_ID_PRO or not stripe.api_key:
        flash("Stripe is not configured. Please set STRIPE_* env vars.", "error")
        return redirect(url_for("billing.index"))

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID_PRO, "quantity": 1}],
            success_url=url_for("billing.success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("billing.cancel", _external=True),
            customer=current_user.stripe_customer_id or None,
            customer_email=current_user.email if not current_user.stripe_customer_id else None,
            metadata={"user_id": str(current_user.id)},
            allow_promotion_codes=True,
        )
        return redirect(session.url, code=303)
    except Exception as e:
        current_app.logger.exception("Stripe Checkout error")
        flash(f"Unable to start checkout: {e}", "error")
        return redirect(url_for("billing.index"))


@billing_bp.route("/success", endpoint="success")
@login_required
def success():
    flash("Checkout started — we’ll update your Pro status once payment confirms.", "info")
    return render_template("billing_success.html")


@billing_bp.route("/cancel", endpoint="cancel")
@login_required
def cancel():
    flash("Checkout canceled.", "warning")
    return redirect(url_for("billing.index"))


# ------------------------------------------
# Stripe Webhook (Pro activation/cancellation)
# ------------------------------------------
@billing_bp.route("/webhook", methods=["POST"], endpoint="webhook")
def webhook():
    """
    Handle Stripe events. We:
      - On checkout.session.completed: link customer/subscription to user, set user.subscription_status='pro'
      - On customer.subscription.updated/deleted: keep status in sync
    """
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            # Dev fallback (NOT for production)
            event = stripe.Event.construct_from(request.get_json(), stripe.api_key)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    event_type = event["type"]

    try:
        if event_type == "checkout.session.completed":
            session_obj = event["data"]["object"]
            user_id = (session_obj.get("metadata") or {}).get("user_id")
            customer_id = session_obj.get("customer")
            subscription_id = session_obj.get("subscription")

            if user_id:
                user = User.query.get(int(user_id))
            else:
                # Fallback: try to discover by customer_email
                customer_email = session_obj.get("customer_details", {}).get("email")
                user = User.query.filter_by(email=(customer_email or "").lower()).first()

            if user:
                user.stripe_customer_id = user.stripe_customer_id or customer_id
                user.stripe_subscription_id = subscription_id
                user.subscription_status = "pro"
                user.pro_since = datetime.utcnow()
                db.session.commit()

        elif event_type == "customer.subscription.updated":
            sub = event["data"]["object"]
            customer_id = sub.get("customer")
            status = sub.get("status")  # active, past_due, canceled, unpaid, incomplete, incomplete_expired, trialing

            user = User.query.filter_by(stripe_customer_id=customer_id).first()
            if user:
                user.stripe_subscription_id = sub.get("id")
                # Map Stripe status to our simple status field
                if status in ("active", "trialing", "past_due"):
                    user.subscription_status = "pro"
                    user.pro_cancel_at = None
                elif status in ("canceled", "unpaid", "incomplete_expired"):
                    user.subscription_status = "canceled"
                db.session.commit()

        elif event_type == "customer.subscription.deleted":
            sub = event["data"]["object"]
            customer_id = sub.get("customer")
            user = User.query.filter_by(stripe_customer_id=customer_id).first()
            if user:
                user.subscription_status = "canceled"
                user.pro_cancel_at = datetime.utcnow()
                db.session.commit()

        # You can handle invoice.payment_succeeded/failed if you want finer states

    except Exception as e:
        current_app.logger.exception("Error handling Stripe webhook")
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ok"}), 200


# ------------------------------------------
# Dev-only mock top-ups (kept)
# ------------------------------------------
@billing_bp.route("/mock-topup/free")
@login_required
def mock_topup_free():
    current_user.coins_free = (current_user.coins_free or 0) + 5
    db.session.commit()
    flash("Added 5 free coins (dev).", "success")
    return redirect(url_for("billing.index"))


@billing_bp.route("/mock-topup/pro")
@login_required
def mock_topup_pro():
    current_user.coins_pro = (current_user.coins_pro or 0) + 10
    current_user.subscription_status = "pro"
    db.session.commit()
    flash("Added 10 pro coins (dev) and set status=pro.", "success")
    return redirect(url_for("billing.index"))
