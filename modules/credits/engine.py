# modules/credits/engine.py
"""
Credits engine for CareerAI.

Responsibilities:
- Read feature costs from modules.credits.config or app.config["FEATURE_COSTS"].
- Check balances (can_afford).
- Deduct or add Silver 🪙 / Gold ⭐ and log every move in CreditTransaction.
- Helpers for starting balances and monthly Pro refills.

This module is designed to be reusable from:
- Feature routes (Job Pack, Skill Mapper, etc.)
- Admin tools (/admin/credits)
- Billing flows (Stripe webhooks, future top-ups)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Dict, Any

from flask import current_app

from models import User, CreditTransaction, db

from .config import FEATURE_COSTS, STARTING_BALANCES, PRO_MONTHLY_ALLOWANCES

Currency = Literal["silver", "gold"]


@dataclass
class FeatureCost:
    silver: int = 0
    gold: int = 0


# -----------------------------
# Internal helpers
# -----------------------------
def _feature_cost(feature: str) -> FeatureCost:
    cfg = current_app.config.get("FEATURE_COSTS", FEATURE_COSTS)
    raw: Dict[str, Any] = cfg.get(feature, {}) or {}
    # Accept both new ("silver"/"gold") and legacy ("coins_free"/"coins_pro") keys
    silver = int(raw.get("silver") or raw.get("coins_free") or 0)
    gold = int(raw.get("gold") or raw.get("coins_pro") or 0)
    return FeatureCost(silver=silver, gold=gold)


def _balances(user: User) -> Dict[str, int]:
    return {
        "silver": int(getattr(user, "coins_free", 0) or 0),
        "gold": int(getattr(user, "coins_pro", 0) or 0),
    }


def _set_balance(user: User, currency: Currency, new_balance: int) -> None:
    new_balance = max(0, int(new_balance))
    if currency == "silver":
        user.coins_free = new_balance
    else:
        user.coins_pro = new_balance


def _record_tx(
    user: User,
    *,
    feature: str,
    currency: Currency,
    amount: int,
    tx_type: Literal["debit", "credit", "refund"],
    run_id: Optional[str] = None,
) -> CreditTransaction:
    """
    Create a CreditTransaction row and update user balance in-memory.
    Caller is responsible for committing the session.
    """
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Transaction amount must be positive.")

    current = _balances(user)[currency]
    if tx_type == "debit":
        if current < amount:
            raise ValueError("Insufficient balance.")
        new_balance = current - amount
    else:  # "credit" or "refund"
        new_balance = current + amount

    _set_balance(user, currency, new_balance)

    tx = CreditTransaction(
        user_id=user.id,
        university_id=getattr(user, "university_id", None),
        feature=feature,
        amount=amount,
        currency="silver" if currency == "silver" else "gold",
        tx_type=tx_type,
        run_id=str(run_id) if run_id is not None else None,
        before_balance=current,
        after_balance=new_balance,
    )
    db.session.add(tx)
    return tx


# -----------------------------
# Public API
# -----------------------------
def get_balances(user: User) -> Dict[str, int]:
    """Return current balances as {'silver': int, 'gold': int}."""
    return _balances(user)


def can_afford(user: User, feature: str, currency: Currency) -> bool:
    costs = _feature_cost(feature)
    bal = _balances(user)
    needed = costs.silver if currency == "silver" else costs.gold
    if needed <= 0:
        return True  # free feature for this currency
    return bal[currency] >= needed


def deduct_free(
    user: User,
    feature: str,
    *,
    run_id: Optional[str] = None,
    commit: bool = True,
) -> bool:
    """Deduct Silver 🪙 for a feature. Returns True if success, False if not enough."""
    costs = _feature_cost(feature)
    if costs.silver <= 0:
        return True  # no silver cost defined

    if not can_afford(user, feature, "silver"):
        return False

    try:
        _record_tx(
            user,
            feature=feature,
            currency="silver",
            amount=costs.silver,
            tx_type="debit",
            run_id=run_id,
        )
        if commit:
            db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        raise


def deduct_pro(
    user: User,
    feature: str,
    *,
    run_id: Optional[str] = None,
    commit: bool = True,
) -> bool:
    """Deduct Gold ⭐ for a feature. Returns True if success, False if not enough."""
    costs = _feature_cost(feature)
    if costs.gold <= 0:
        return True  # no gold cost defined

    if not can_afford(user, feature, "gold"):
        return False

    try:
        _record_tx(
            user,
            feature=feature,
            currency="gold",
            amount=costs.gold,
            tx_type="debit",
            run_id=run_id,
        )
        if commit:
            db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        raise


def add_free(
    user: User,
    amount: int,
    *,
    feature: str = "admin_adjust",
    run_id: Optional[str] = None,
    commit: bool = True,
) -> None:
    """Credit Silver 🪙 to a user (admin / promo / bug refund, etc.)."""
    amount = int(amount)
    if amount <= 0:
        return
    try:
        _record_tx(
            user,
            feature=feature,
            currency="silver",
            amount=amount,
            tx_type="credit",
            run_id=run_id,
        )
        if commit:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def add_pro(
    user: User,
    amount: int,
    *,
    feature: str = "admin_adjust",
    run_id: Optional[str] = None,
    commit: bool = True,
) -> None:
    """Credit Gold ⭐ to a user (Pro bundle, admin grant, etc.)."""
    amount = int(amount)
    if amount <= 0:
        return
    try:
        _record_tx(
            user,
            feature=feature,
            currency="gold",
            amount=amount,
            tx_type="credit",
            run_id=run_id,
        )
        if commit:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def refund(
    user: User,
    feature: str,
    *,
    currency: Currency,
    amount: int,
    run_id: Optional[str] = None,
    commit: bool = True,
) -> None:
    """
    Explicit refund helper. Use when an AI run fails AFTER a debit or
    when you want to undo something manually.
    """
    amount = int(amount)
    if amount <= 0:
        return
    try:
        _record_tx(
            user,
            feature=feature,
            currency=currency,
            amount=amount,
            tx_type="refund",
            run_id=run_id,
        )
        if commit:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def apply_starting_balances(user: User) -> None:
    """
    Apply starting balances based on user.subscription_status.
    Intended to be called once on signup (or when first upgrading to Pro).
    """
    status = (getattr(user, "subscription_status", "free") or "free").lower()
    tier = "pro" if status == "pro" else "free"
    cfg = current_app.config.get("STARTING_BALANCES", STARTING_BALANCES)
    tier_cfg = cfg.get(tier, {})

    silver = int(tier_cfg.get("silver", 0))
    gold = int(tier_cfg.get("gold", 0))

    if silver > 0:
        user.coins_free = max(user.coins_free or 0, silver)
    if gold > 0:
        user.coins_pro = max(user.coins_pro or 0, gold)


def refill_monthly_pro(
    user: User,
    plan_code: str = "pro_basic",
    commit: bool = True,
) -> None:
    """
    Monthly Pro allowance (Gold ⭐). You can call this from a cron/worker.
    For now it's a simple additive credit based on PRO_MONTHLY_ALLOWANCES.
    """
    cfg = current_app.config.get("PRO_MONTHLY_ALLOWANCES", PRO_MONTHLY_ALLOWANCES)
    plan_cfg = cfg.get(plan_code) or {}
    gold = int(plan_cfg.get("gold", 0) or 0)
    if gold <= 0:
        return

    try:
        _record_tx(
            user,
            feature=f"monthly_{plan_code}",
            currency="gold",
            amount=gold,
            tx_type="credit",
            run_id=None,
        )
        if commit:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise
