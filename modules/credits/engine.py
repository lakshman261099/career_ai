# modules/credits/engine.py
"""
Credits engine for CareerAI - UPGRADED with University Wallet Support.

Features:
- Automatic routing to UniversityWallet for university-managed users
- Personal wallet fallback for B2C users (user.coins_free, user.coins_pro)
- Full audit trail via CreditTransaction
- Legacy-compatible API: can_afford, deduct_free, deduct_pro, refund, add_free, add_pro
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Dict, Any, Tuple, List

from flask import current_app

from models import User, CreditTransaction, UniversityWallet, db


# Import config if it exists, otherwise use defaults
try:
    from .config import FEATURE_COSTS, STARTING_BALANCES, PRO_MONTHLY_ALLOWANCES
except ImportError:
    FEATURE_COSTS = {
        "job_pack": {"silver": 5, "gold": 0},
        "job_pack_pro": {"silver": 0, "gold": 3},
        "dream_planner": {"silver": 0, "gold": 5},
        "daily_coach": {"silver": 0, "gold": 2},
        "skill_mapper": {"silver": 0, "gold": 4},
    }
    STARTING_BALANCES = {
        "free": {"silver": 10, "gold": 0},
        "pro": {"silver": 20, "gold": 50},
    }
    PRO_MONTHLY_ALLOWANCES = {
        "pro_basic": {"gold": 100},
        "pro_advanced": {"gold": 300},
    }

Currency = Literal["silver", "gold"]
TransactionType = Literal["debit", "credit", "refund", "bonus", "renewal"]


__all__ = [
    # Query
    "get_balances",
    "get_wallet_info",
    "get_feature_cost_amount",
    "can_afford_reason",
    "can_afford",

    # Deduct
    "deduct_credits",
    "deduct_free",
    "deduct_pro",

    # Add / refund
    "add_credits",
    "add_free",
    "add_pro",
    "refund",

    # System
    "apply_starting_balances",
    "refill_monthly_pro",
    "renew_university_wallet",

    # History / stats
    "get_transaction_history",
    "get_university_usage_stats",
]


@dataclass
class FeatureCost:
    silver: int = 0
    gold: int = 0


# -----------------------------
# Internal helpers
# -----------------------------
def _feature_cost(feature: str) -> FeatureCost:
    """Get cost for a feature from config."""
    cfg = current_app.config.get("FEATURE_COSTS", FEATURE_COSTS)
    raw: Dict[str, Any] = (cfg.get(feature, {}) or {}) if isinstance(cfg, dict) else {}

    # Accept both new ("silver"/"gold") and legacy ("coins_free"/"coins_pro") keys
    silver = int(raw.get("silver") or raw.get("coins_free") or 0)
    gold = int(raw.get("gold") or raw.get("coins_pro") or 0)
    return FeatureCost(silver=silver, gold=gold)


def get_feature_cost_amount(feature: str, currency: Currency) -> int:
    """
    Public helper to get a feature cost amount for a specific currency.
    (Useful for refunds when you want to refund the exact configured cost.)
    """
    c = _feature_cost(feature)
    return int(c.silver if currency == "silver" else c.gold)


def _is_university_managed(user: User) -> bool:
    """
    Safer gate than assuming attribute exists.
    Uses:
      - user.is_university_managed if present
      - else: bool(user.university_id)
    """
    if hasattr(user, "is_university_managed"):
        try:
            return bool(user.is_university_managed)
        except Exception:
            pass
    return bool(getattr(user, "university_id", None))


def _get_or_create_university_wallet(university_id: int) -> UniversityWallet:
    wallet = UniversityWallet.query.filter_by(university_id=university_id).first()
    if wallet:
        return wallet

    wallet = UniversityWallet(
        university_id=university_id,
        silver_balance=0,
        gold_balance=0,
    )
    db.session.add(wallet)
    db.session.flush()
    return wallet


def _get_wallet_balances(user: User) -> Tuple[Dict[str, int], str]:
    """
    Get balances for a user from the correct wallet.

    Returns:
        (balances_dict, wallet_type) where wallet_type is "university" or "personal"
    """
    if _is_university_managed(user):
        uni_id = getattr(user, "university_id", None)
        if not uni_id:
            # If someone toggled is_university_managed True but no university_id
            # fallback safely to personal wallet.
            return {
                "silver": int(getattr(user, "coins_free", 0) or 0),
                "gold": int(getattr(user, "coins_pro", 0) or 0),
            }, "personal"

        wallet = _get_or_create_university_wallet(int(uni_id))
        return {
            "silver": int(wallet.silver_balance or 0),
            "gold": int(wallet.gold_balance or 0),
        }, "university"

    # Personal wallet
    return {
        "silver": int(getattr(user, "coins_free", 0) or 0),
        "gold": int(getattr(user, "coins_pro", 0) or 0),
    }, "personal"


def _set_wallet_balance(user: User, currency: Currency, new_balance: int, wallet_type: str) -> None:
    new_balance = max(0, int(new_balance))

    if wallet_type == "university":
        uni_id = getattr(user, "university_id", None)
        if not uni_id:
            raise ValueError("University wallet update requested but user has no university_id")
        wallet = _get_or_create_university_wallet(int(uni_id))
        if currency == "silver":
            wallet.silver_balance = new_balance
        else:
            wallet.gold_balance = new_balance
        return

    # Personal wallet
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
    tx_type: TransactionType,
    wallet_type: str,
    run_id: Optional[str] = None,
    meta_json: Optional[Dict[str, Any]] = None,
) -> CreditTransaction:
    """
    Create a CreditTransaction row and update wallet balance in-memory.
    Caller is responsible for committing.

    amount must be > 0
    """
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Transaction amount must be positive.")

    balances, detected_wallet_type = _get_wallet_balances(user)
    wallet_type = detected_wallet_type  # enforce detected type
    current = int(balances[currency] or 0)

    if tx_type == "debit":
        if current < amount:
            raise ValueError(f"Insufficient {currency} balance. Required: {amount}, Available: {current}")
        new_balance = current - amount
    else:
        new_balance = current + amount

    _set_wallet_balance(user, currency, new_balance, wallet_type)

    tx = CreditTransaction(
        user_id=user.id,
        university_id=getattr(user, "university_id", None) if wallet_type == "university" else None,
        feature=str(feature),
        amount=amount,
        currency="silver" if currency == "silver" else "gold",
        tx_type=tx_type,
        wallet_type=wallet_type,
        before_balance=current,
        after_balance=new_balance,
        run_id=str(run_id) if run_id is not None else None,
        status="completed",
        meta_json=meta_json or {},
    )
    db.session.add(tx)
    return tx


# -----------------------------
# Public API - Query Functions
# -----------------------------
def get_balances(user: User) -> Dict[str, int]:
    """
    Return current balances as {'silver': int, 'gold': int}.
    Automatically routes to correct wallet (university or personal).
    """
    balances, _wallet_type = _get_wallet_balances(user)
    return balances


def get_wallet_info(user: User) -> Dict[str, Any]:
    """
    Get detailed wallet information including type and caps (if university).

    Returns:
        {
            'wallet_type': 'personal' | 'university',
            'silver': int,
            'gold': int,
            'annual_cap': {'silver': ..., 'gold': ...} (if applicable),
            'renewal_date': ... (if applicable),
            'university_name': ... (if applicable),
        }
    """
    balances, wallet_type = _get_wallet_balances(user)
    info: Dict[str, Any] = {
        "wallet_type": wallet_type,
        "silver": balances["silver"],
        "gold": balances["gold"],
    }

    if wallet_type == "university":
        uni_id = getattr(user, "university_id", None)
        if uni_id:
            wallet = UniversityWallet.query.filter_by(university_id=int(uni_id)).first()
            if wallet:
                info["annual_cap"] = {
                    "silver": getattr(wallet, "silver_annual_cap", None),
                    "gold": getattr(wallet, "gold_annual_cap", None),
                }
                info["renewal_date"] = getattr(wallet, "renewal_date", None)

        # Optional relationship
        if hasattr(user, "university") and user.university:
            info["university_name"] = getattr(user.university, "name", None)

    return info


def can_afford_reason(user: User, feature: str, currency: Currency) -> Tuple[bool, str]:
    """
    Rich afford-check.

    Returns:
        (can_afford: bool, reason: str)
    """
    costs = _feature_cost(feature)
    needed = costs.silver if currency == "silver" else costs.gold

    if needed <= 0:
        return True, "Feature is free"

    balances, wallet_type = _get_wallet_balances(user)
    available = int(balances[currency] or 0)

    if available >= needed:
        return True, "Sufficient balance"
    return (
        False,
        f"Insufficient {currency} credits. Required: {needed}, Available: {available} (from {wallet_type} wallet)",
    )


def can_afford(user: User, feature: str, currency: Currency) -> bool:
    """
    ✅ Legacy boolean afford-check (IMPORTANT).
    Many routes do: if not can_afford(...): gate
    """
    ok, _reason = can_afford_reason(user, feature, currency)
    return bool(ok)


# -----------------------------
# Public API - Deduction Functions
# -----------------------------
def deduct_credits(
    user: User,
    feature: str,
    currency: Currency,
    *,
    run_id: Optional[str] = None,
    commit: bool = True,
) -> Optional[CreditTransaction]:
    """
    Universal credit deduction function.

    Returns CreditTransaction, or None if feature cost is 0 in that currency.
    Raises ValueError if insufficient.
    """
    amount = get_feature_cost_amount(feature, currency)
    if amount <= 0:
        return None

    can_pay, reason = can_afford_reason(user, feature, currency)
    if not can_pay:
        raise ValueError(reason)

    _, wallet_type = _get_wallet_balances(user)

    try:
        tx = _record_tx(
            user,
            feature=feature,
            currency=currency,
            amount=amount,
            tx_type="debit",
            wallet_type=wallet_type,
            run_id=run_id,
        )
        if commit:
            db.session.commit()
        return tx
    except Exception as e:
        db.session.rollback()
        raise ValueError(f"Failed to deduct credits: {str(e)}")


def deduct_free(
    user: User,
    feature: str,
    *,
    run_id: Optional[str] = None,
    commit: bool = True,
) -> bool:
    """Legacy wrapper: deduct silver."""
    try:
        deduct_credits(user, feature, "silver", run_id=run_id, commit=commit)
        return True
    except ValueError:
        return False


def deduct_pro(
    user: User,
    feature: str,
    *,
    run_id: Optional[str] = None,
    commit: bool = True,
) -> bool:
    """Legacy wrapper: deduct gold."""
    try:
        deduct_credits(user, feature, "gold", run_id=run_id, commit=commit)
        return True
    except ValueError:
        return False


# -----------------------------
# Public API - Credit Addition Functions
# -----------------------------
def add_credits(
    user: User,
    amount: int,
    currency: Currency,
    *,
    feature: str = "admin_adjust",
    tx_type: TransactionType = "credit",
    run_id: Optional[str] = None,
    commit: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
) -> CreditTransaction:
    """
    Universal credit addition function.
    Use for: admin grants, refunds, bonuses, renewals, etc.
    """
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Amount must be positive")

    _, wallet_type = _get_wallet_balances(user)

    try:
        tx = _record_tx(
            user,
            feature=feature,
            currency=currency,
            amount=amount,
            tx_type=tx_type,
            wallet_type=wallet_type,
            run_id=run_id,
            meta_json=metadata,
        )
        if commit:
            db.session.commit()
        return tx
    except Exception as e:
        db.session.rollback()
        raise ValueError(f"Failed to add credits: {str(e)}")


def add_free(
    user: User,
    amount: int,
    *,
    feature: str = "admin_adjust",
    run_id: Optional[str] = None,
    commit: bool = True,
) -> None:
    """Legacy wrapper: add silver."""
    if int(amount or 0) <= 0:
        return
    add_credits(user, int(amount), "silver", feature=feature, run_id=run_id, commit=commit)


def add_pro(
    user: User,
    amount: int,
    *,
    feature: str = "admin_adjust",
    run_id: Optional[str] = None,
    commit: bool = True,
) -> None:
    """Legacy wrapper: add gold."""
    if int(amount or 0) <= 0:
        return
    add_credits(user, int(amount), "gold", feature=feature, run_id=run_id, commit=commit)


def refund(
    user: User,
    feature: str,
    *,
    currency: Currency,
    run_id: Optional[str] = None,
    amount: Optional[int] = None,
    commit: bool = True,
) -> None:
    """
    Legacy-compatible refund helper.

    - If amount is None -> refunds configured feature cost for that currency.
    - If amount is given -> refunds that amount.
    """
    if amount is None:
        amount = get_feature_cost_amount(feature, currency)

    amount = int(amount or 0)
    if amount <= 0:
        return

    add_credits(
        user,
        amount,
        currency,
        feature=feature,
        tx_type="refund",
        run_id=run_id,
        commit=commit,
        metadata={"reason": "AI failure or manual refund"},
    )


# -----------------------------
# Admin & System Functions
# -----------------------------
def apply_starting_balances(user: User) -> None:
    """
    Apply starting balances based on user.subscription_status.
    Intended to be called once on signup / upgrade.

    NOTE: Only applies to personal wallets.
    """
    if _is_university_managed(user):
        return

    status = (getattr(user, "subscription_status", "free") or "free").lower()
    tier = "pro" if status == "pro" else "free"
    cfg = current_app.config.get("STARTING_BALANCES", STARTING_BALANCES)
    tier_cfg = cfg.get(tier, {}) if isinstance(cfg, dict) else {}

    silver = int(tier_cfg.get("silver", 0) or 0)
    gold = int(tier_cfg.get("gold", 0) or 0)

    if silver > 0:
        user.coins_free = max(int(getattr(user, "coins_free", 0) or 0), silver)
    if gold > 0:
        user.coins_pro = max(int(getattr(user, "coins_pro", 0) or 0), gold)


def refill_monthly_pro(
    user: User,
    plan_code: str = "pro_basic",
    commit: bool = True,
) -> None:
    """
    Monthly Pro allowance (Gold ⭐).
    NOTE: Only applies to personal wallets.
    """
    if _is_university_managed(user):
        return

    cfg = current_app.config.get("PRO_MONTHLY_ALLOWANCES", PRO_MONTHLY_ALLOWANCES)
    plan_cfg = cfg.get(plan_code) if isinstance(cfg, dict) else None
    plan_cfg = plan_cfg or {}
    gold = int(plan_cfg.get("gold", 0) or 0)
    if gold <= 0:
        return

    add_credits(
        user,
        gold,
        "gold",
        feature=f"monthly_{plan_code}",
        tx_type="credit",
        commit=commit,
    )


def renew_university_wallet(university_id: int, commit: bool = True) -> bool:
    """
    Renew a university wallet if due (reset to annual caps).
    Requires UniversityWallet.renew_if_due() to exist.
    """
    wallet = UniversityWallet.query.filter_by(university_id=university_id).first()
    if not wallet:
        raise ValueError(f"No wallet found for university_id={university_id}")

    renewed = wallet.renew_if_due()

    if renewed and commit:
        db.session.commit()

    return bool(renewed)


# -----------------------------
# Utility Functions
# -----------------------------
def get_transaction_history(
    user: User,
    limit: int = 50,
    feature: Optional[str] = None,
) -> List[CreditTransaction]:
    """Get credit transaction history for a user (newest first)."""
    query = CreditTransaction.query.filter_by(user_id=user.id)
    if feature:
        query = query.filter_by(feature=feature)
    return query.order_by(CreditTransaction.created_at.desc()).limit(int(limit)).all()


def get_university_usage_stats(university_id: int) -> Dict[str, Any]:
    """
    Aggregate usage statistics for a university.
    """
    from sqlalchemy import func
    from datetime import datetime, timedelta

    wallet = UniversityWallet.query.filter_by(university_id=university_id).first()

    total_debits = db.session.query(
        CreditTransaction.currency,
        func.sum(CreditTransaction.amount).label("total")
    ).filter(
        CreditTransaction.university_id == university_id,
        CreditTransaction.tx_type == "debit"
    ).group_by(CreditTransaction.currency).all()

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    active_users = db.session.query(
        func.count(func.distinct(CreditTransaction.user_id))
    ).filter(
        CreditTransaction.university_id == university_id,
        CreditTransaction.created_at >= thirty_days_ago
    ).scalar()

    total_users = User.query.filter_by(university_id=university_id).count()

    return {
        "wallet": {
            "silver_balance": getattr(wallet, "silver_balance", 0) if wallet else 0,
            "gold_balance": getattr(wallet, "gold_balance", 0) if wallet else 0,
            "silver_annual_cap": getattr(wallet, "silver_annual_cap", None) if wallet else None,
            "gold_annual_cap": getattr(wallet, "gold_annual_cap", None) if wallet else None,
            "renewal_date": getattr(wallet, "renewal_date", None) if wallet else None,
        },
        "total_debits": {row.currency: row.total for row in total_debits},
        "total_users": int(total_users or 0),
        "active_users_30d": int(active_users or 0),
    }
