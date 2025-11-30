# modules/billing/routes.py

import os
from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
    session,
)
from flask_login import current_user, login_required

from models import User, db, VoucherCampaign, VoucherRedemption
from modules.credits.config import SHOP_PACKAGES
from modules.credits import engine as credits_engine  # for top-ups / bonuses

billing_bp = Blueprint("billing", __name__, template_folder="../../templates")

# Stripe SDK (install: pip install stripe)
import stripe

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLIC_KEY")
STRIPE_PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO_MONTHLY_INR")  # subscription price
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# minimum starting Gold for Pro users; can be kept in sync with config.STARTING_BALANCES
INITIAL_PRO_COINS = 3000  # policy: Pro users must start with at least 3000 Gold


def _grant_initial_pro_coins(user: User, minimum: int = INITIAL_PRO_COINS) -> None:
    """
    Ensure a Pro user has at least `minimum` gold coins.
    - Does NOT reduce a higher existing balance.
    - Safe to call repeatedly.
    """
    try:
        current = int(user.coins_pro or 0)
    except Exception:
        current = 0
    if current < minimum:
        user.coins_pro = minimum


def _is_voucher_valid_for_user(campaign: VoucherCampaign, user: User) -> bool:
    """
    Check if a voucher campaign is currently applicable to this user:
    - is_active = True
    - not expired (expires_at >= now, if set)
    - max_uses not exceeded (if set)
    - if scoped to a university, user must belong to that university
    """
    if not campaign.is_active:
        return False

    now = datetime.utcnow()
    if campaign.expires_at and campaign.expires_at < now:
        return False

    if campaign.max_uses is not None:
        used = int(campaign.used_count or 0)
        if used >= campaign.max_uses:
            return False

    # University scope: if set, user must match
    if campaign.university_id:
        if not user.university_id or user.university_id != campaign.university_id:
            return False

    return True


# ------------------------------------------
# Pricing / Index (legacy)
# ------------------------------------------


@billing_bp.route("/", endpoint="index")
def index():
    """
    Main Billing entry-point.

    Redirects to the Coin Shop so all "Pricing" links land on /billing/shop.
    """
    return redirect(url_for("billing.shop"))


# Back-compat: /pricing -> /billing/shop
@billing_bp.route("/pricing", endpoint="pricing_legacy")
def pricing_legacy():
    return redirect(url_for("billing.shop"))


# ------------------------------------------
# Coins Shop (new)
# ------------------------------------------


@billing_bp.route("/shop", methods=["GET", "POST"], endpoint="shop")
@login_required
def shop():
    """
    Coins Shop & Pro upgrade UI.
    - GET: show shop
    - POST: apply voucher code (stores active voucher in session)
    """
    cfg = current_app.config.get("SHOP_PACKAGES", SHOP_PACKAGES)
    silver_packages = cfg.get("silver", [])
    gold_packages = cfg.get("gold", [])
    pro_plan_packages = cfg.get("pro_plans", [])

    active_voucher = None

    # Handle voucher apply form
    if request.method == "POST":
        code = (request.form.get("voucher_code") or "").strip().upper()
        if not code:
            flash("Please enter a voucher code.", "warning")
            # clear any previous voucher
            session.pop("active_voucher_id", None)
        else:
            campaign = VoucherCampaign.query.filter_by(code=code).first()
            if not campaign or not _is_voucher_valid_for_user(campaign, current_user):
                flash("That voucher code is invalid, expired, or not for your university.", "danger")
                session.pop("active_voucher_id", None)
            else:
                session["active_voucher_id"] = campaign.id
                active_voucher = campaign
                flash(f"Voucher {campaign.code} applied.", "success")

    # On GET (or after POST), reload any active voucher from session
    if not active_voucher:
        vid = session.get("active_voucher_id")
        if vid:
            campaign = VoucherCampaign.query.get(int(vid))
            if campaign and _is_voucher_valid_for_user(campaign, current_user):
                active_voucher = campaign
            else:
                # Stale / invalid, clear session
                session.pop("active_voucher_id", None)

    return render_template(
        "billing/shop.html",
        silver_packages=silver_packages,
        gold_packages=gold_packages,
        pro_plan_packages=pro_plan_packages,
        coins_free=getattr(current_user, "coins_free", 0),
        coins_pro=getattr(current_user, "coins_pro", 0),
        subscription_status=getattr(current_user, "subscription_status", "free"),
        active_voucher=active_voucher,
    )


@billing_bp.route("/shop/buy/<code>", methods=["POST"], endpoint="shop_buy")
@login_required
def shop_buy(code: str):
    """
    Entry point when a user clicks "Upgrade to Pro ⭐" or (later) a top-up.

    Now also:
      - Attaches any active voucher (from session) into Stripe metadata,
        so the webhook can grant bonus coins and mark redemption on success.
    """
    cfg = current_app.config.get("SHOP_PACKAGES", SHOP_PACKAGES)
    silver = cfg.get("silver", [])
    gold = cfg.get("gold", [])
    pro_plans = cfg.get("pro_plans", [])

    pkg = None
    for p in silver + gold + pro_plans:
        if p.get("code") == code:
            pkg = p
            break

    if not pkg:
        flash("Unknown package selected.", "danger")
        return redirect(url_for("billing.shop"))

    kind = pkg.get("kind")

    # Resolve any active voucher from session
    voucher = None
    vid = session.get("active_voucher_id")
    if vid:
        campaign = VoucherCampaign.query.get(int(vid))
        if campaign and _is_voucher_valid_for_user(campaign, current_user):
            voucher = campaign
        else:
            session.pop("active_voucher_id", None)

    # Silver/Gold top-ups – payments not wired yet
    if kind in ("silver", "gold"):
        flash(
            "Top-up payments are coming soon. For now, you can upgrade to Pro ⭐ or ask support for manual credits.",
            "info",
        )
        return redirect(url_for("billing.shop"))

    # Pro plan -> Stripe Checkout
    if kind == "pro_plan":
        if not STRIPE_PRICE_ID_PRO and not pkg.get("stripe_price_id"):
            flash(
                "Stripe is not configured for Pro plans. Please set STRIPE_* env vars.",
                "error",
            )
            return redirect(url_for("billing.shop"))

        price_id = pkg.get("stripe_price_id") or STRIPE_PRICE_ID_PRO

        # Build metadata for webhook
        metadata = {
            "user_id": str(current_user.id),
            "plan_code": pkg.get("code") or "pro_basic_monthly",
        }
        if voucher:
            metadata["voucher_id"] = str(voucher.id)
            metadata["voucher_code"] = voucher.code

        try:
            session_obj = stripe.checkout.Session.create(
                mode="subscription",
                payment_method_types=["card"],
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=url_for("billing.success", _external=True)
                + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=url_for("billing.cancel", _external=True),
                customer=current_user.stripe_customer_id or None,
                customer_email=(
                    current_user.email if not current_user.stripe_customer_id else None
                ),
                metadata=metadata,
                allow_promotion_codes=True,
            )
            return redirect(session_obj.url, code=303)
        except Exception as e:
            current_app.logger.exception("Stripe Checkout error (shop_buy)")
            flash(f"Unable to start checkout: {e}", "error")
            return redirect(url_for("billing.shop"))

    flash("Unsupported package type.", "danger")
    return redirect(url_for("billing.shop"))


# ------------------------------------------
# Create Stripe Checkout Session (Subscription)
# Legacy entrypoint: /billing/checkout/pro
# ------------------------------------------


@billing_bp.route("/checkout/pro", methods=["POST"], endpoint="checkout_pro")
@login_required
def checkout_pro():
    """
    Legacy: Creates a Stripe Checkout session in subscription mode for Pro.
    New flows should prefer /billing/shop + shop_buy("pro_basic_monthly").
    """
    if not STRIPE_PRICE_ID_PRO or not stripe.api_key:
        flash("Stripe is not configured. Please set STRIPE_* env vars.", "error")
        return redirect(url_for("billing.index"))

    try:
        session_obj = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID_PRO, "quantity": 1}],
            success_url=url_for("billing.success", _external=True)
            + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("billing.cancel", _external=True),
            customer=current_user.stripe_customer_id or None,
            customer_email=(
                current_user.email if not current_user.stripe_customer_id else None
            ),
            metadata={"user_id": str(current_user.id)},
            allow_promotion_codes=True,
        )
        return redirect(session_obj.url, code=303)
    except Exception as e:
        current_app.logger.exception("Stripe Checkout error")
        flash(f"Unable to start checkout: {e}", "error")
        return redirect(url_for("billing.index"))


@billing_bp.route("/success", endpoint="success")
@login_required
def success():
    flash(
        "Checkout started — we’ll update your Pro status once payment confirms.", "info"
    )
    return render_template("billing_success.html")


@billing_bp.route("/cancel", endpoint="cancel")
@login_required
def cancel():
    flash("Checkout canceled.", "warning")
    return redirect(url_for("billing.index"))


# ------------------------------------------
# Stripe Webhook (Pro activation/cancellation + vouchers)
# ------------------------------------------


@billing_bp.route("/webhook", methods=["POST"], endpoint="webhook")
def webhook():
    """
    Handle Stripe events. We:
      - On checkout.session.completed:
          * link customer/subscription to user
          * set user.subscription_status='pro'
          * grant >=3000 Gold
          * apply any attached voucher (bonus coins + redemption record)
      - On customer.subscription.updated:
          * keep status in sync (and if 'pro', ensure >=3000 Gold)
      - On customer.subscription.deleted:
          * set status 'canceled' (coins unchanged)
    """
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        else:
            # Dev fallback (NOT for production)
            event = stripe.Event.construct_from(request.get_json(), stripe.api_key)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    event_type = event["type"]

    try:
        if event_type == "checkout.session.completed":
            session_obj = event["data"]["object"]
            metadata = session_obj.get("metadata") or {}
            user_id = metadata.get("user_id")
            customer_id = session_obj.get("customer")
            subscription_id = session_obj.get("subscription")

            if user_id:
                user = User.query.get(int(user_id))
            else:
                customer_email = session_obj.get("customer_details", {}).get("email")
                user = User.query.filter_by(
                    email=(customer_email or "").lower()
                ).first()

            if user:
                # Link Stripe IDs
                user.stripe_customer_id = user.stripe_customer_id or customer_id
                user.stripe_subscription_id = subscription_id

                # Activate Pro + grant >=3000 Gold
                user.subscription_status = "pro"
                user.pro_since = datetime.utcnow()
                _grant_initial_pro_coins(user)

                db.session.commit()

                # ---- Apply voucher bonus (if any) ----
                voucher_id = metadata.get("voucher_id")
                if voucher_id:
                    try:
                        campaign = VoucherCampaign.query.get(int(voucher_id))
                    except Exception:
                        campaign = None

                    if campaign and _is_voucher_valid_for_user(campaign, user):
                        # Has this user already redeemed this campaign?
                        existing = VoucherRedemption.query.filter_by(
                            campaign_id=campaign.id, user_id=user.id
                        ).first()

                        if not existing:
                            # Grant bonus coins (no commit yet; we'll commit once at end)
                            bonus_silver = int(campaign.bonus_silver or 0)
                            bonus_gold = int(campaign.bonus_gold or 0)

                            if bonus_silver > 0:
                                credits_engine.add_free(
                                    user,
                                    bonus_silver,
                                    feature=f"voucher:{campaign.code}",
                                    run_id=session_obj.get("id"),
                                    commit=False,
                                )
                            if bonus_gold > 0:
                                credits_engine.add_pro(
                                    user,
                                    bonus_gold,
                                    feature=f"voucher:{campaign.code}",
                                    run_id=session_obj.get("id"),
                                    commit=False,
                                )

                            # Create redemption record & increment usage
                            redemption = VoucherRedemption(
                                campaign_id=campaign.id,
                                user_id=user.id,
                                context="checkout.session.completed",
                            )
                            db.session.add(redemption)
                            campaign.used_count = int(campaign.used_count or 0) + 1

                            db.session.commit()

        elif event_type == "customer.subscription.updated":
            sub = event["data"]["object"]
            customer_id = sub.get("customer")
            status = sub.get("status")  # active, past_due, canceled, etc.

            user = User.query.filter_by(stripe_customer_id=customer_id).first()
            if user:
                user.stripe_subscription_id = sub.get("id")
                if status in ("active", "trialing", "past_due"):
                    user.subscription_status = "pro"
                    user.pro_cancel_at = None
                    _grant_initial_pro_coins(user)  # ensure >=3000 on (re)activation
                elif status in ("canceled", "unpaid", "incomplete_expired"):
                    user.subscription_status = "canceled"
                    user.pro_cancel_at = datetime.utcnow()
                db.session.commit()

        elif event_type == "customer.subscription.deleted":
            sub = event["data"]["object"]
            customer_id = sub.get("customer")
            user = User.query.filter_by(stripe_customer_id=customer_id).first()
            if user:
                user.subscription_status = "canceled"
                user.pro_cancel_at = datetime.utcnow()
                db.session.commit()

        # (optional) invoice.payment_succeeded/failed for granular states

    except Exception as e:
        current_app.logger.exception("Error handling Stripe webhook")
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ok"}), 200


# ------------------------------------------
# Dev-only mock top-ups (kept, but aligned to policy)
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
    # Mark Pro and ensure >=3000 Gold; then add 10 for testing
    current_user.subscription_status = "pro"
    _grant_initial_pro_coins(current_user)
    current_user.coins_pro = (current_user.coins_pro or 0) + 10
    db.session.commit()
    flash("Set status=pro, ensured >=3000 Gold, and added +10 (dev).", "success")
    return redirect(url_for("billing.index"))
