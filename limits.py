# limits.py — coin-based credits (Free = 🪙, Pro = ⭐)

from flask import current_app

from models import User, db

# ---------------------------------------------------------------------
# Pricing policy (easy to tweak later)
# - Free features cost Silver 🪙
# - Pro-only features cost Gold ⭐
# ---------------------------------------------------------------------
FEATURE_COSTS = {
    # Portfolio Builder (both tiers use 1 credit)
    "portfolio": {"coins_free": 1, "coins_pro": 1},
    # Internship Analyzer
    "internship_analyzer": {"coins_free": 1, "coins_pro": 1},
    # Referral Trainer (currently Free-only, Pro extensions coming soon)
    "referral_trainer": {"coins_free": 1},
    # Skill Mapper
    "skillmapper": {"coins_free": 1, "coins_pro": 1},
    # Pro-only features
    "resume": {"coins_pro": 1},
    "jobpack": {"coins_pro": 1},
    # Portfolio publishing (Pro-only)
    "portfolio_publish": {"coins_pro": 1},
}


def init_limits(app):
    """Attach default costs to app config if not provided."""
    app.config.setdefault("FEATURE_COSTS", FEATURE_COSTS)


def _cost(feature: str) -> dict:
    costs = current_app.config.get("FEATURE_COSTS", FEATURE_COSTS)
    return costs.get(feature, {})


# ---------------------------
# Free (Silver 🪙) helpers
# ---------------------------
def can_use_free(user: User, feature: str) -> bool:
    c = _cost(feature)
    need = int(c.get("coins_free", 0) or 0)
    return need > 0 and (user.coins_free or 0) >= need


def consume_free(user: User, feature: str) -> None:
    c = _cost(feature)
    need = int(c.get("coins_free", 0) or 0)
    if need <= 0:
        return
    if (user.coins_free or 0) < need:
        raise ValueError("Not enough Silver credits.")
    user.coins_free -= need
    db.session.commit()


# ---------------------------
# Pro (Gold ⭐) helpers
# ---------------------------
def can_use_pro(user: User, feature: str) -> bool:
    c = _cost(feature)
    need = int(c.get("coins_pro", 0) or 0)
    return (
        (user.subscription_status or "free").lower() == "pro"
        and need > 0
        and (user.coins_pro or 0) >= need
    )


def consume_pro(user: User, feature: str) -> None:
    c = _cost(feature)
    need = int(c.get("coins_pro", 0) or 0)
    if need <= 0:
        return
    if (user.coins_pro or 0) < need:
        raise ValueError("Not enough Gold credits.")
    user.coins_pro -= need
    db.session.commit()


# ---------------------------
# Unified helper (recommended)
# ---------------------------
def authorize_and_consume(user: User, feature: str) -> bool:
    """
    Deducts credits for the given feature.
    Free users spend 🪙 if defined, Pro users spend ⭐ if defined.
    Returns True if deduction succeeded, False otherwise.
    """
    c = _cost(feature)

    # Free (🪙) path
    if "coins_free" in c and not user.is_pro and can_use_free(user, feature):
        consume_free(user, feature)
        return True

    # Pro (⭐) path
    if "coins_pro" in c and user.is_pro and can_use_pro(user, feature):
        consume_pro(user, feature)
        return True

    return False


# ---------------------------
# Optional convenience (for UI labels)
# ---------------------------
def get_feature_limits(feature: str) -> dict:
    """
    Returns {'coins_free': int, 'coins_pro': int} for labeling.
    """
    c = _cost(feature)
    return {
        "coins_free": int(c.get("coins_free", 0) or 0),
        "coins_pro": int(c.get("coins_pro", 0) or 0),
    }
