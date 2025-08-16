# limits.py
import datetime as dt
from functools import wraps
from typing import Optional, Callable
from flask import current_app, request, jsonify
from flask_login import current_user
from sqlalchemy import and_
from models import db, FreeFeatureUsage, Subscription

FEATURE_LIMITS = {
    "jobpack": 1,
    "internships": 1,
    "portfolio": 1,
}

def is_pro_user(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    plan = (getattr(user, "plan", "") or "").lower()
    if plan.startswith("pro"):
        return True
    sub = Subscription.query.filter_by(user_id=user.id, status="active").first()
    return bool(sub)

def client_ip() -> str:
    xf = request.headers.get("X-Forwarded-For", "")
    if xf: return xf.split(",")[0].strip()
    return (request.remote_addr or "0.0.0.0").strip()

def _usage_row(feature: str, user_id: Optional[int], ip: str, day: dt.date):
    return FreeFeatureUsage.query.filter(and_(
        FreeFeatureUsage.feature==feature,
        FreeFeatureUsage.user_id==user_id,
        FreeFeatureUsage.ip==ip,
        FreeFeatureUsage.day==day
    )).first()

def can_consume_feature(feature: str, user, ip: str) -> bool:
    limit = int(FEATURE_LIMITS.get(feature, 1))
    today = dt.date.today()
    uid = user.id if user and getattr(user, "is_authenticated", False) else None
    row = _usage_row(feature, uid, ip, today)
    count = row.count if row else 0
    return count < limit

def consume_feature(feature: str, user, ip: str) -> None:
    today = dt.date.today()
    uid = user.id if user and getattr(user, "is_authenticated", False) else None
    row = _usage_row(feature, uid, ip, today)
    if not row:
        row = FreeFeatureUsage(feature=feature, user_id=uid, ip=ip, day=today, count=0)
        db.session.add(row)
    row.count += 1
    db.session.commit()

def free_budget_blocked() -> bool:
    return str(current_app.config.get("FREE_KILLSWITCH","0")) == "1"

def enforce_free_feature(feature: str, error_code: int = 429) -> Callable:
    """Use on FREE endpoints that consume one per-day run for the given feature."""
    def _outer(fn):
        @wraps(fn)
        def _inner(*args, **kwargs):
            if is_pro_user(current_user):
                return fn(*args, **kwargs)
            if free_budget_blocked():
                current_app.config["MOCK"] = True
            ip = client_ip()
            if not can_consume_feature(feature, current_user, ip):
                return jsonify({"error": f"Free daily limit for {feature} reached (1/day). Upgrade to Pro for unlimited."}), error_code
            consume_feature(feature, current_user, ip)
            return fn(*args, **kwargs)
        return _inner
    return _outer
