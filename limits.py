# career_ai/limits.py
import datetime as dt
from functools import wraps
from typing import Optional, Callable

from flask import current_app, request, jsonify
from flask_login import current_user
from sqlalchemy import and_

from models import db, FreeUsage, Subscription


# -----------------------------
# Core identity & plan helpers
# -----------------------------
def is_pro_user(user) -> bool:
    """True if user has active Pro (plan startswith 'pro' OR subscription.status=='active')."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    # Direct plan flag
    plan = (getattr(user, "plan", "") or "").lower()
    if plan.startswith("pro"):
        return True
    # Subscription table check
    sub = Subscription.query.filter_by(user_id=user.id, status="active").first()
    return bool(sub)


def client_ip() -> str:
    """Best-effort client IP (honors X-Forwarded-For on Render)."""
    xf = request.headers.get("X-Forwarded-For", "")
    if xf:
        # Take first public IP
        return xf.split(",")[0].strip()
    return (request.remote_addr or "0.0.0.0").strip()


# -----------------------------
# Free usage counters (per day)
# -----------------------------
def _get_free_usage_row(user_id: Optional[int], ip: str, day: dt.date):
    return FreeUsage.query.filter(
        and_(
            FreeUsage.user_id == user_id,
            FreeUsage.ip == ip,
            FreeUsage.day == day,
        )
    ).first()


def can_consume_free(user, ip: str) -> bool:
    """True if user/IP has remaining free runs today."""
    limit = int(current_app.config.get("FREE_RUNS_PER_DAY", 2))
    today = dt.date.today()
    uid = user.id if user and getattr(user, "is_authenticated", False) else None
    row = _get_free_usage_row(uid, ip, today)
    count = row.count if row else 0
    return count < limit


def consume_free(user, ip: str) -> None:
    """Increment today's free counter for user/IP."""
    today = dt.date.today()
    uid = user.id if user and getattr(user, "is_authenticated", False) else None
    row = _get_free_usage_row(uid, ip, today)
    if not row:
        row = FreeUsage(user_id=uid, ip=ip, day=today, count=0)
        db.session.add(row)
    row.count += 1
    db.session.commit()


# -----------------------------
# Free budget kill-switch (global)
# -----------------------------
def free_budget_blocked() -> bool:
    """
    If your global free budget is exceeded, return True to auto-degrade Free to MOCK.
    For Phase-3 demo, you can flip via env:
       FREE_KILLSWITCH=1   -> forces True
    Later, you can sum costs monthly & compare to GLOBAL_FREE_BUDGET_INR.
    """
    if str(current_app.config.get("FREE_KILLSWITCH", "0")) == "1":
        return True
    # Placeholder: implement real metering if needed. Keep False by default.
    return False


def enable_mock_for_free_only():
    """
    Sets MOCK=True **only for Free** code paths on this request if kill-switch triggered.
    Your Fast endpoints should check current_app.config["MOCK"] when calling AI.
    """
    current_app.config["MOCK"] = True


# -----------------------------
# Decorator to enforce guardrails
# -----------------------------
def enforce_free_guardrails(error_code: int = 429) -> Callable:
    """
    Decorator to apply Free-tier rules on a route:
      • Pro users: pass through unchanged.
      • Free users: check global budget (may toggle MOCK) and per-day counter.
    Use **only** on costful Fast endpoints (Job Pack Fast, Internship Fast).
    """
    def _outer(fn):
        @wraps(fn)
        def _inner(*args, **kwargs):
            if is_pro_user(current_user):
                # Pro bypasses Free limits & MOCK degradation
                return fn(*args, **kwargs)

            # Free user path
            if free_budget_blocked():
                # Degrade Free AI calls to MOCK (Pro unaffected)
                enable_mock_for_free_only()

            ip = client_ip()
            if not can_consume_free(current_user, ip):
                return jsonify({"error": "Free daily limit reached (2/day). Upgrade to Pro for unlimited runs."}), error_code

            # Count now (pre-charge)
            consume_free(current_user, ip)
            return fn(*args, **kwargs)
        return _inner
    return _outer
