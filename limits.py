from datetime import date
from flask import current_app
from models import db, FreeUsage, User

FEATURES = {
    "resume": {"daily_free": 3, "pro_cost": 1},
    "portfolio": {"daily_free": 2, "pro_cost": 1},
    "internships": {"daily_free": 3, "pro_cost": 1},
    "referral": {"daily_free": 5, "pro_cost": 1},
    "jobpack": {"daily_free": 2, "pro_cost": 2},
    "skillmapper": {"daily_free": 2, "pro_cost": 1},
}

def init_limits(app):
    app.config.setdefault("FEATURE_LIMITS", FEATURES)

def _get_or_create_usage(user_id: int, feature: str) -> FreeUsage:
    usage = FreeUsage.query.filter_by(user_id=user_id, feature=feature, day=date.today()).first()
    if not usage:
        usage = FreeUsage(user_id=user_id, feature=feature, day=date.today(), count=0)
        db.session.add(usage)
        db.session.commit()
    return usage

def can_use_free(user: User, feature: str) -> bool:
    lim = current_app.config["FEATURE_LIMITS"].get(feature, {"daily_free": 0})
    usage = FreeUsage.query.filter_by(user_id=user.id, feature=feature, day=date.today()).first()
    used = usage.count if usage else 0
    return used < lim["daily_free"]

def consume_free(user: User, feature: str) -> None:
    usage = _get_or_create_usage(user.id, feature)
    usage.count += 1
    db.session.commit()

def can_use_pro(user: User, feature: str) -> bool:
    lim = current_app.config["FEATURE_LIMITS"].get(feature, {"pro_cost": 0})
    return user.coins_pro >= lim["pro_cost"]

def consume_pro(user: User, feature: str) -> None:
    lim = current_app.config["FEATURE_LIMITS"][feature]
    user.coins_pro -= lim["pro_cost"]
    db.session.commit()
