# limits.py — coin-based credits (Free = 🪙 Silver, Pro = ⭐ Gold)

import os
from flask import current_app

from models import User, db

# ---------------------------------------------------------------------
# Global credit + plan configuration
#  - Phase 1: values are placeholders; real numbers will be set later.
#  - Phase 3: we'll wire this into Stripe & monthly refresh logic.
# ---------------------------------------------------------------------

# Signup + daily refill for Silver 🪙 (Free)
SIGNUP_SILVER_BONUS = int(os.getenv("SIGNUP_SILVER_BONUS", 0))
DAILY_SILVER_REFILL = int(os.getenv("DAILY_SILVER_REFILL", 0))

# Monthly Gold ⭐ allocation for Pro plans
PRO_BASIC_GOLD = int(os.getenv("PRO_BASIC_GOLD", 0))        # e.g. 200 later
PRO_ADVANCED_GOLD = int(os.getenv("PRO_ADVANCED_GOLD", 0))  # e.g. 400 later

PLANS = {
    "free": {
        "name": "Free",
        "gold_included": 0,
    },
    "pro_basic": {
        "name": "Pro Basic",
        "gold_included": PRO_BASIC_GOLD,
    },
    "pro_advanced": {
        "name": "Pro Advanced",
        "gold_included": PRO_ADVANCED_GOLD,
    },
}

# ---------------------------------------------------------------------
# Feature pricing policy (central for credits engine)
#
# Keys here MUST match the feature keys used in routes:
#   - Referral Trainer:          "referral_trainer_free"
#   - Portfolio wizard (free):   "portfolio_idea_free"
#   - Portfolio wizard (pro):    "portfolio_idea_pro"
#   - Portfolio publish:         "portfolio_publish"
#   - Job Pack basic:            "jobpack_free"
#   - Job Pack deep (Pro):       "jobpack_pro"
#   - Skill Mapper free:         "skill_mapper_free"
#   - Skill Mapper Pro:          "skill_mapper_pro"
#   - Internship Analyzer free:  "internship_analyzer"
#
# The credits engine supports both "silver"/"gold" and
# legacy "coins_free"/"coins_pro" keys. We’ll use "silver"/"gold".
# ---------------------------------------------------------------------
FEATURE_COSTS = {
    # --------------------------
    # Job Pack
    # --------------------------
    # Basic analysis (Silver 🪙)
    "jobpack_free": {
        "silver": 1,
    },
    # Deep evaluation (Gold ⭐)
    "jobpack_pro": {
        "gold": 3,
    },

    # Legacy alias (if any old code still uses this)
    "jobpack": {
        "silver": 1,
        "gold": 3,
    },

    # --------------------------
    # Skill Mapper
    # --------------------------
    # HTML + JSON Free mode
    "skill_mapper_free": {
        "silver": 1,
    },
    # Pro roadmap (Gold ⭐)
    "skill_mapper_pro": {
        "gold": 3,
    },

    # Legacy alias
    "skillmapper": {
        "silver": 1,
        "gold": 3,
    },

    # --------------------------
    # Internship Analyzer
    # --------------------------
    # Free mode uses Silver 🪙
    # Pro mode is subscription-gated only (no per-run cost),
    # so we do NOT define a separate "internship_analyzer_pro".
    "internship_analyzer": {
        "silver": 1,
    },

    # --------------------------
    # Referral Trainer
    # --------------------------
    # This must match FEATURE_KEY in modules/referral/routes.py
    "referral_trainer_free": {
        "silver": 1,
    },

    # Optional legacy alias
    "referral": {
        "silver": 1,
    },

    # --------------------------
    # Portfolio Builder (wizard)
    # --------------------------
    # Free: 1 idea (Silver 🪙)
    "portfolio_idea_free": {
        "silver": 1,
    },
    # Pro: 3 deep ideas (Gold ⭐)
    "portfolio_idea_pro": {
        "gold": 2,
    },

    # --------------------------
    # Portfolio publishing (Pro)
    # --------------------------
    "portfolio_publish": {
        "gold": 2,
    },

    # Optional legacy aliases (if any old code uses these)
    "portfolio_free": {
        "silver": 1,
    },
    "portfolio_pro": {
        "gold": 2,
    },
}


def init_limits(app):
    """Attach default costs & plan config to app config if not provided."""
    app.config.setdefault("FEATURE_COSTS", FEATURE_COSTS)
    app.config.setdefault("PLANS", PLANS)
    app.config.setdefault("SIGNUP_SILVER_BONUS", SIGNUP_SILVER_BONUS)
    app.config.setdefault("DAILY_SILVER_REFILL", DAILY_SILVER_REFILL)


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
    # subscription_status / is_pro can be cleaned up later;
    # for now we support both patterns for compatibility.
    is_pro = getattr(user, "is_pro", False) or (
        (user.subscription_status or "").lower() == "pro"
    )
    return is_pro and need > 0 and (user.coins_pro or 0) >= need


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

    - Free users spend Silver 🪙 if coins_free is defined.
    - Pro users spend Gold ⭐ if coins_pro is defined.

    Returns True if deduction succeeded, False otherwise.
    """
    c = _cost(feature)

    # Free (🪙) path
    if "coins_free" in c and not getattr(user, "is_pro", False) and can_use_free(user, feature):
        consume_free(user, feature)
        return True

    # Pro (⭐) path
    if "coins_pro" in c and getattr(user, "is_pro", False) and can_use_pro(user, feature):
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
