# limits.py

from datetime import date
from flask import current_app
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError

from models import db, FreeUsage, User

# Feature matrix (KB-aligned):
# - Resume upload/profile lives under Settings and is Pro-only.
# - Free tier tracks daily usage via FreeUsage; Pro consumes Gold ⭐ (coins_pro).
FEATURES = {
    # Pro-only resume profile (no free quota)
    "resume":      {"daily_free": 0, "pro_cost": 1},

    # Core features
    "portfolio":   {"daily_free": 2, "pro_cost": 1},
    "internships": {"daily_free": 3, "pro_cost": 1},  # paste-only
    "referral":    {"daily_free": 5, "pro_cost": 1},  # Free-only feature in KB UI; keep cost for future Pro
    "jobpack":     {"daily_free": 2, "pro_cost": 2},
    "skillmapper": {"daily_free": 2, "pro_cost": 1},
}


def init_limits(app):
    """Attach default limits to app config if not provided."""
    app.config.setdefault("FEATURE_LIMITS", FEATURES)


def _get_limits(feature: str) -> dict:
    limits = current_app.config.get("FEATURE_LIMITS", FEATURES)
    return limits.get(feature, {"daily_free": 0, "pro_cost": 0})


def _get_or_create_usage(user_id: int, feature: str) -> FreeUsage:
    """Fetch today's usage row; create if missing (race-safe)."""
    today = date.today()
    usage = FreeUsage.query.filter(
        and_(
            FreeUsage.user_id == user_id,
            FreeUsage.feature == feature,
            FreeUsage.day == today,
        )
    ).first()
    if usage:
        return usage

    usage = FreeUsage(user_id=user_id, feature=feature, day=today, count=0)
    db.session.add(usage)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Row was created by another request; re-fetch
        usage = FreeUsage.query.filter_by(user_id=user_id, feature=feature, day=today).first()
    return usage


# ---------------------------
# Free tier helpers (daily)
# ---------------------------
def can_use_free(user: User, feature: str) -> bool:
    lim = _get_limits(feature)
    if lim["daily_free"] <= 0:
        return False
    usage = FreeUsage.query.filter_by(user_id=user.id, feature=feature, day=date.today()).first()
    used = usage.count if usage else 0
    return used < lim["daily_free"]


def consume_free(user: User, feature: str) -> None:
    usage = _get_or_create_usage(user.id, feature)
    usage.count += 1
    db.session.commit()


# ---------------------------
# Pro (Gold ⭐) helpers
# ---------------------------
def can_use_pro(user: User, feature: str) -> bool:
    lim = _get_limits(feature)
    cost = max(int(lim.get("pro_cost", 0)), 0)
    return (user.coins_pro or 0) >= cost


def consume_pro(user: User, feature: str) -> None:
    lim = _get_limits(feature)
    cost = max(int(lim.get("pro_cost", 0)), 0)
    if cost <= 0:
        return
    if (user.coins_pro or 0) < cost:
        raise ValueError("Not enough Gold credits for this action.")
    user.coins_pro -= cost
    if user.coins_pro < 0:
        user.coins_pro = 0  # clamp (defensive)
    db.session.commit()


# ---------------------------
# Unified helper (recommended)
# ---------------------------
def authorize_and_consume(user: User, feature: str) -> bool:
    """
    Preferred path:
    - If free quota remains, consume free.
    - Else if Pro has enough Gold ⭐, consume pro.
    - Else deny.
    Returns True if consumption succeeded, False otherwise.
    """
    # Free first
    if can_use_free(user, feature):
        consume_free(user, feature)
        return True

    # Pro fallback
    if can_use_pro(user, feature):
        consume_pro(user, feature)
        return True

    return False


# Optional convenience (for UI labels)
def get_feature_limits(feature: str) -> dict:
    """
    Returns {'daily_free': int, 'pro_cost': int} even if feature unknown.
    Useful for showing limits/cost in templates.
    """
    lim = _get_limits(feature)
    return {
        "daily_free": int(lim.get("daily_free", 0) or 0),
        "pro_cost": int(lim.get("pro_cost", 0) or 0),
    }
