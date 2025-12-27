# modules/common/readiness.py
from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Tuple, Optional

from sqlalchemy import desc

from models import db, User, UserProfile, Project, PortfolioPage, SkillMapSnapshot


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _profile_skill_names(prof: Optional[UserProfile]) -> List[str]:
    if not prof or not prof.skills:
        return []
    raw = prof.skills

    # Supports both:
    # - container format: {"list":[{"name":"Python","level":3}, ...], ...}
    # - legacy list: [{"name":"Python"}, "SQL", ...]
    if isinstance(raw, dict) and "list" in raw:
        raw = raw.get("list") or []

    out: List[str] = []
    for item in raw or []:
        if isinstance(item, dict):
            name = (item.get("name") or item.get("skill") or item.get("title") or "").strip()
        else:
            name = str(item).strip()
        if name:
            out.append(name.lower())
    # de-dupe
    return sorted(set(out))


def _extract_required_skills_from_skillmap(skillmap_json: str) -> List[str]:
    """
    Best-effort extraction.

    Ideal future format:
      roles[0].required_skills = ["python","sql",...]
    Fallbacks:
      roles[0].missing_skills (if present)
      roles[0].skills (if present)
      top-level required_skills (if present)
    """
    try:
        data = json.loads(skillmap_json or "{}")
    except Exception:
        return []

    if not isinstance(data, dict):
        return []

    # 1) top-level
    top = data.get("required_skills")
    if isinstance(top, list):
        return [str(x).strip().lower() for x in top if str(x).strip()]

    # 2) roles[0]
    roles = data.get("roles")
    if isinstance(roles, list) and roles:
        r0 = roles[0] if isinstance(roles[0], dict) else {}
        for key in ("required_skills", "skills", "top_skills"):
            val = r0.get(key)
            if isinstance(val, list) and val:
                return [str(x).strip().lower() for x in val if str(x).strip()]

        # Not ideal, but better than nothing:
        # if model gives "missing_skills", we can infer required = (have + missing)
        miss = r0.get("missing_skills")
        if isinstance(miss, list) and miss:
            return [str(x).strip().lower() for x in miss if str(x).strip()]

    return []


def _compute_profile_completeness_points(user: User, prof: Optional[UserProfile]) -> Tuple[int, Dict[str, Any]]:
    """
    0–10 points. 1 point per checklist item (cap 10).
    """
    checklist = {
        "full_name": bool((prof.full_name or "").strip()) if prof else False,
        "headline": bool((prof.headline or "").strip()) if prof else False,
        "summary": bool((prof.summary or "").strip()) if prof else False,
        "location": bool((prof.location or "").strip()) if prof else False,
        "phone": bool((prof.phone or "").strip()) if prof else False,
        "links_any": bool(prof.links) if prof else False,
        "linkedin": bool((prof.links or {}).get("linkedin")) if prof else False,
        "skills_5": len(_profile_skill_names(prof)) >= 5,
        "education_any": bool(prof.education) if prof else False,
        "verified": bool(getattr(user, "verified", False)),
    }
    points = sum(1 for v in checklist.values() if v)
    return int(_clamp(points, 0, 10)), {"checklist": checklist}


def _compute_portfolio_points(user_id: int) -> Tuple[int, Dict[str, Any]]:
    """
    0–30 points.
    “3 high-quality projects with live links” → full score.
    """
    projects = Project.query.filter_by(user_id=user_id).all()
    projects_with_links = 0
    for p in projects:
        links = getattr(p, "links", None) or []
        if isinstance(links, list) and any((l or {}).get("url") for l in links if isinstance(l, dict)):
            projects_with_links += 1

    # Optional: published portfolio pages count as proof too
    public_pages = 0
    try:
        public_pages = PortfolioPage.query.filter_by(user_id=user_id, is_public=True).count()
    except Exception:
        public_pages = 0

    proof_count = max(projects_with_links, public_pages)

    # 0,1,2,3+ mapped linearly to 0,10,20,30
    points = round(30 * _clamp(proof_count / 3.0, 0, 1))
    return int(points), {
        "projects_total": len(projects),
        "projects_with_links": projects_with_links,
        "public_portfolio_pages": public_pages,
        "proof_count_used": proof_count,
    }


def _compute_consistency_points(user: User) -> Tuple[int, Dict[str, Any]]:
    """
    0–20 points:
      - streak_points: 0–10 from current_streak
      - weekly_points: 0–10 from weekly_milestones_completed (x2, capped)
    """
    streak = int(getattr(user, "current_streak", 0) or 0)
    weekly = int(getattr(user, "weekly_milestones_completed", 0) or 0)

    streak_points = int(_clamp(streak, 0, 10))
    weekly_points = int(_clamp(weekly * 2, 0, 10))

    return streak_points + weekly_points, {
        "current_streak": streak,
        "weekly_milestones_completed": weekly,
        "streak_points": streak_points,
        "weekly_points": weekly_points,
    }


def _compute_skills_points(user_id: int, prof: Optional[UserProfile]) -> Tuple[int, Dict[str, Any]]:
    """
    0–40 points.

    Uses SkillMapSnapshot (latest) to find required skills.
    Falls back to profile skill count if required list missing.
    """
    have = set(_profile_skill_names(prof))

    snap = (
        SkillMapSnapshot.query.filter_by(user_id=user_id)
        .order_by(desc(SkillMapSnapshot.created_at))
        .first()
    )

    required = set()
    if snap and snap.skills_json:
        req_list = _extract_required_skills_from_skillmap(snap.skills_json)
        required = set([s for s in req_list if s])

    # If we have a required list, do a ratio match
    if required:
        matched = len(required.intersection(have))
        ratio = matched / max(1, len(required))
        points = round(40 * _clamp(ratio, 0, 1))
        return int(points), {
            "method": "required_vs_have",
            "required_count": len(required),
            "have_count": len(have),
            "matched_count": matched,
            "ratio": ratio,
            "snapshot_id": getattr(snap, "id", None),
        }

    # Fallback: 10 skills ≈ full score (tunable)
    points = int(_clamp(len(have) * 4, 0, 40))
    return points, {
        "method": "fallback_skill_count",
        "have_count": len(have),
        "snapshot_id": getattr(snap, "id", None) if snap else None,
    }


def score_to_tier(score: int) -> str:
    if score >= 80:
        return "Top Tier"
    if score >= 60:
        return "Job Ready"
    if score >= 40:
        return "Building"
    return "Getting Started"


def compute_recruiter_ready_score(user: User) -> Tuple[int, Dict[str, Any]]:
    """
    Returns:
      (score_0_100, breakdown_dict)
    """
    prof = UserProfile.query.filter_by(user_id=user.id).first()

    skills_pts, skills_meta = _compute_skills_points(user.id, prof)         # 0–40
    port_pts, port_meta = _compute_portfolio_points(user.id)               # 0–30
    cons_pts, cons_meta = _compute_consistency_points(user)                # 0–20
    prof_pts, prof_meta = _compute_profile_completeness_points(user, prof) # 0–10

    total = int(_clamp(skills_pts + port_pts + cons_pts + prof_pts, 0, 100))

    breakdown = {
        "total": total,
        "tier": score_to_tier(total),
        "skills": {"points": skills_pts, "max": 40, **skills_meta},
        "portfolio": {"points": port_pts, "max": 30, **port_meta},
        "consistency": {"points": cons_pts, "max": 20, **cons_meta},
        "profile": {"points": prof_pts, "max": 10, **prof_meta},
    }
    return total, breakdown


def update_user_ready_score(user: User) -> Tuple[int, Dict[str, Any]]:
    """
    Computes and persists User.ready_score.
    Caller decides when to commit (recommended: commit where you call it).
    """
    score, breakdown = compute_recruiter_ready_score(user)
    user.ready_score = score
    return score, breakdown
