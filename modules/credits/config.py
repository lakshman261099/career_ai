# modules/credits/config.py
"""
Central configuration for CareerAI credits & pricing.

Everything here is meant to be easy to tweak WITHOUT touching routes:
- FEATURE_COSTS: how many Silver 🪙 / Gold ⭐ each feature uses.
- STARTING_BALANCES: how many coins a new user gets based on tier.
- PRO_MONTHLY_ALLOWANCES: monthly Gold ⭐ bundles for Pro plans.
- SHOP_PACKAGES: what appears on /billing/shop (labels, descriptions, etc).

IMPORTANT:
- Keys in FEATURE_COSTS **must match** the feature strings used in routes:
    Job Pack:
      - "jobpack_free"  -> Job Pack basic (Silver 🪙)
      - "jobpack_pro"   -> Job Pack Deep (Gold ⭐)

    Skill Mapper:
      - "skill_mapper_free" -> Skill Mapper Free (Silver 🪙)
      - "skill_mapper_pro"  -> Skill Mapper Pro (Gold ⭐)

    Internship Analyzer:
      - "internship_analyzer" -> Free + Pro (Silver 🪙 / Gold ⭐)

    Referral Trainer:
      - "referral_trainer_free" -> current single Silver 🪙 mode

    Portfolio Builder:
      - "portfolio_idea_free" -> Free (1 idea, Silver 🪙)
      - "portfolio_idea_pro"  -> Pro (3 ideas, Gold ⭐)
      - "portfolio_publish"   -> Publishing portfolio page (Gold ⭐)

    Dream Planner:
      - "dream_planner"       -> Dream Job / Dream Startup Planner (Gold ⭐, Pro-only)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Any

# -----------------------------
# Feature costs (per run)
# -----------------------------
# "silver" = 🪙 (Free credits)
# "gold"   = ⭐ (Pro credits)
#
# NOTE:
#   These keys are used directly by the routes via can_afford / deduct_free / deduct_pro.
#   If you change a key here, you must also update the routes, or add an alias.
FEATURE_COSTS: Dict[str, Dict[str, int]] = {
    # ---------------- Job Pack ----------------
    # Free/basic analysis (JD + resume, mini model)
    "jobpack_free": {
        "silver": 1,   # Free users + Pro both pay Silver for basic
        "gold": 0,
    },
    # Deep CareerAI evaluation (bigger model, more sections)
    "jobpack_pro": {
        "silver": 0,
        "gold": 3,     # Gold ⭐ cost per Deep run
    },

    # ---------------- Skill Mapper ----------------
    # Free snapshot: 1 main role, simple roadmap
    "skill_mapper_free": {
        "silver": 1,
        "gold": 0,
    },
    # Pro roadmap: 3 roles, richer roadmap, demand + salaries
    "skill_mapper_pro": {
        "silver": 0,
        "gold": 3,
    },

    # ---------------- Internship Analyzer ----------------
    # Both modes use the same feature key:
    #   - Free: Silver 🪙
    #   - Pro: Gold ⭐
    "internship_analyzer": {
        "silver": 1,   # Free mode
        "gold": 2,     # Pro deep analysis
    },

    # ---------------- Referral Trainer ----------------
    # Currently a single Free-like mode using Silver 🪙
    "referral_trainer_free": {
        "silver": 1,
        "gold": 0,
    },

    # ---------------- Portfolio Builder ----------------
    # Wizard: Free mode (1 idea)
    "portfolio_idea_free": {
        "silver": 1,
        "gold": 0,
    },
    # Wizard: Pro mode (3 deep ideas with Profile Portal)
    "portfolio_idea_pro": {
        "silver": 0,
        "gold": 2,
    },

    # Publishing portfolio page (Pro-only)
    "portfolio_publish": {
        "silver": 0,
        "gold": 2,
    },

    # ---------------- Dream Planner ----------------
    # Dream Job + Dream Startup Planner (Pro-only, Gold-based)
    "dream_planner": {
        "silver": 0,
        "gold": 3,    # Gold ⭐ cost per Dream Planner run
    },
}

# -----------------------------
# Starting balances
# -----------------------------
# These are suggested defaults. You can call the helper in engine.py
# from your signup logic to apply them.
STARTING_BALANCES = {
    "free": {
        "silver": 20,
        "gold": 0,
    },
    # Any active Pro plan — you can split later if you add more tiers
    "pro": {
        "silver": 20,
        "gold": 3000,  # minimum starting Gold ⭐
    },
}

# -----------------------------
# Pro monthly allowances
# -----------------------------
# When you implement monthly refresh, use these values in a cron/worker.
PRO_MONTHLY_ALLOWANCES = {
    "pro_basic": {
        "gold": 3000,
    },
    # You can add "pro_advanced" etc. later
}

# -----------------------------
# Shop packages (/billing/shop)
# -----------------------------
# This drives the Coins Shop UI. You can disable individual items by
# commenting them out or changing labels.
@dataclass
class ShopPackage:
    code: str               # stable code used in URLs, never show raw user input
    label: str
    description: str
    kind: str               # "silver" | "gold" | "pro_plan"
    silver: int = 0
    gold: int = 0
    price_inr: int | None = None
    price_display: str | None = None
    stripe_price_id: str | None = None  # for pro plans

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "description": self.description,
            "kind": self.kind,
            "silver": self.silver,
            "gold": self.gold,
            "price_inr": self.price_inr,
            "price_display": self.price_display,
            "stripe_price_id": self.stripe_price_id,
        }


# IMPORTANT:
# - These are *display* packages only.
# - For now, Silver/Gold top-ups are "Coming soon" in the UI.
# - Pro plans use Stripe checkout.
SHOP_PACKAGES = {
    "silver": [
        ShopPackage(
            code="silver_starter",
            label="Silver Starter",
            description="Good for light usage of free tools.",
            kind="silver",
            silver=20,
            price_inr=99,
        ).as_dict(),
        ShopPackage(
            code="silver_boost",
            label="Silver Boost",
            description="Extra runs for Job Pack basic & Skill Mapper Free.",
            kind="silver",
            silver=60,
            price_inr=199,
        ).as_dict(),
    ],
    "gold": [
        ShopPackage(
            code="gold_boost",
            label="Gold Boost",
            description="Extra Pro runs for Job Pack Deep, Skill Mapper Pro & Dream Planner.",
            kind="gold",
            gold=500,
            price_inr=299,
        ).as_dict(),
    ],
    "pro_plans": [
        ShopPackage(
            code="pro_basic_monthly",
            label="Pro Monthly ⭐",
            description="Best for serious applicants using CareerAI every week.",
            kind="pro_plan",
            gold=3000,
            price_inr=499,
            # Optional: if you create a dedicated Stripe Price for Pro,
            # put it here. Otherwise the billing route can fall back to
            # STRIPE_PRICE_ID_PRO_MONTHLY_INR env var.
            stripe_price_id=None,
        ).as_dict(),
    ],
}
