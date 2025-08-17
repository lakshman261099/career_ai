import datetime as dt
from functools import wraps
from flask import current_app, request, redirect, url_for, flash
from flask_login import current_user
from models import db, FreeUsage, User, Subscription

# --- Pro check ---
def is_pro_user(user: User) -> bool:
    if not user or not user.is_authenticated:
        return False
    if (user.plan or "").lower().startswith("pro"):
        return True
    sub = Subscription.query.filter_by(user_id=user.id, status="active").first()
    return bool(sub)

# --- Free guardrail: per-feature per-day count ---
def enforce_free_feature(feature_name: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if is_pro_user(current_user):
                return fn(*args, **kwargs)
            # must be verified to use free runs
            if not current_user.is_verified:
                flash("Please verify your email to use free features.", "error")
                return redirect(url_for("settings.profile"))
            limit = int(current_app.config.get("FREE_RUNS_PER_FEATURE_PER_DAY", 1))
            today = dt.date.today()
            fu = FreeUsage.query.filter_by(user_id=current_user.id, feature=feature_name, ymd=today).first()
            if not fu:
                fu = FreeUsage(user_id=current_user.id, feature=feature_name, ymd=today, count=0)
                db.session.add(fu); db.session.commit()
            if fu.count >= limit:
                flash("Daily free limit reached for this feature. Upgrade to Pro for more runs.", "error")
                return redirect(url_for("settings.pricing"))
            # increment now (pre-charge)
            fu.count += 1
            db.session.commit()
            return fn(*args, **kwargs)
        return wrapper
    return deco

# --- Coins (silver/gold) ---

COIN_COSTS = {
    # feature: {mode: ("silver"|"gold"|None, amount)}
    "jobpack": {"fast": ("silver", 50), "deep": ("gold", 500)},
    "portfolio": {"fast": ("silver", 100), "deep": ("gold", 700)},
    "internships": {"fast": ("silver", 50), "deep": ("gold", 400)},
    "skillmapper": {"deep": ("gold", 600)},
    "referral": {"free": (None, 0)},
}

def spend_coins(user: User, feature: str, mode: str) -> (bool, str, dict):
    """
    Deducts coins if enabled. Returns (ok, err_msg, spend_record)
    spend_record = {"silver": x, "gold": y}
    """
    if not current_app.config.get("CREDITS_ENABLED", True):
        return True, "", {"silver":0,"gold":0}
    cost_map = COIN_COSTS.get(feature, {})
    coin_spec = cost_map.get(mode)
    if not coin_spec:
        # nothing to charge (e.g., referral free)
        return True, "", {"silver":0,"gold":0}
    coin_type, amount = coin_spec
    if coin_type == "silver":
        if user.silver_balance < amount:
            return False, "Not enough silver credits.", {"silver":0,"gold":0}
        user.silver_balance -= amount
        db.session.commit()
        return True, "", {"silver":amount,"gold":0}
    if coin_type == "gold":
        if user.gold_balance < amount:
            return False, "Not enough gold credits. Buy a pack or upgrade.", {"silver":0,"gold":0}
        user.gold_balance -= amount
        db.session.commit()
        return True, "", {"silver":0,"gold":amount}
    return True, "", {"silver":0,"gold":0}
