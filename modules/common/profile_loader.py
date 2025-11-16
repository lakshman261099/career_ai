# modules/common/profile_loader.py

from __future__ import annotations
from typing import Any, Dict, List

from models import UserProfile, ResumeAsset, Project, db  # db kept for future extension

MAX_TEXT = 6000


def _coerce_skill_names(skills_any: Any) -> List[str]:
    out: List[str] = []
    if isinstance(skills_any, list):
        for s in skills_any:
            if isinstance(s, dict) and (s.get("name") or "").strip():
                out.append(s["name"].strip())
            elif isinstance(s, str) and s.strip():
                out.append(s.strip())
    return out


def _profile_to_resume_text(profile: UserProfile | None) -> str:
    if not profile:
        return ""

    lines: List[str] = []

    # Core identity
    if profile.full_name:
        lines.append(profile.full_name)
    if getattr(profile, "headline", None):
        lines.append(profile.headline)
    if getattr(profile, "location", None):
        lines.append(f"Location: {profile.location}")

    # Skills
    skills = _coerce_skill_names(getattr(profile, "skills", None))
    if skills:
        lines.append("Skills: " + ", ".join(skills))

    # Experience
    exp = getattr(profile, "experience", []) or []
    if exp:
        lines.append("")
        lines.append("Experience:")
        for item in exp[:8]:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "")
            company = item.get("company", "")
            dates = " • ".join(
                filter(None, [item.get("start", ""), item.get("end", "")])
            )
            if role or company:
                lines.append(f"- {role} at {company} ({dates})")
            for b in (item.get("bullets") or [])[:5]:
                if isinstance(b, str) and b.strip():
                    lines.append(f"  • {b.strip()}")

    # Projects
    user = getattr(profile, "user", None)
    projects = getattr(user, "projects", []) if user else []
    if projects:
        lines.append("")
        lines.append("Projects:")
        for p in projects[:5]:
            title = getattr(p, "title", "")
            desc = getattr(p, "short_desc", "")
            stack = ", ".join(getattr(p, "tech_stack", []) or [])
            if title:
                lines.append(f"- {title}")
            if desc:
                lines.append(f"  • {desc}")
            if stack:
                lines.append(f"  • Stack: {stack}")

    # Education
    edu = getattr(profile, "education", []) or []
    if edu:
        lines.append("")
        lines.append("Education:")
        for e in edu[:4]:
            if not isinstance(e, dict):
                continue
            school = e.get("school", "")
            degree = e.get("degree", "")
            year = str(e.get("year", "")).strip()
            line = " — ".join(filter(None, [school, degree, year]))
            if line:
                lines.append(" - " + line)

    return "\n".join(lines)[:MAX_TEXT]


def get_profile_resume_text(user) -> str:
    """
    Prefer latest ResumeAsset text; fall back to synthesized text from UserProfile.
    This is the main 'resume' input for all AI features.
    """
    try:
        asset = (
            ResumeAsset.query.filter(ResumeAsset.user_id == user.id)
            .order_by(ResumeAsset.created_at.desc())
            .first()
        )
        if asset and asset.text:
            return asset.text[:MAX_TEXT]
    except Exception:
        # best-effort; don't break the feature
        pass

    profile = getattr(user, "profile", None)
    return _profile_to_resume_text(profile)


def load_profile_snapshot(user) -> Dict[str, Any]:
    """
    Unified snapshot for all features.
    - resume_text: main text we send to AI
    - fields: full_name, headline, summary, etc
    - profile_strength_score: rough completion %
    - missing_sections: ['skills', 'experience', ...]
    """
    profile: UserProfile | None = getattr(user, "profile", None)
    resume_text = get_profile_resume_text(user)

    data: Dict[str, Any] = {
        "resume_text": resume_text,
        "full_name": getattr(profile, "full_name", "") if profile else "",
        "headline": getattr(profile, "headline", "") if profile else "",
        "summary": getattr(profile, "summary", "") if profile else "",
        "location": getattr(profile, "location", "") if profile else "",
        "skills": getattr(profile, "skills", []) if profile else [],
        "education": getattr(profile, "education", []) if profile else [],
        "experience": getattr(profile, "experience", []) if profile else [],
        "certifications": getattr(profile, "certifications", []) if profile else [],
        "links": getattr(profile, "links", {}) if profile else {},
        "raw_profile": profile,
    }

    # Basic profile strength heuristic
    strength = 0
    if data["full_name"]:
        strength += 15
    if data["headline"]:
        strength += 10
    if data["summary"]:
        strength += 15
    if data["skills"]:
        strength += 20
    if data["experience"]:
        strength += 20
    if data["education"]:
        strength += 20

    data["profile_strength_score"] = min(100, strength)

    missing = []
    if not data["headline"]:
        missing.append("headline")
    if not data["summary"]:
        missing.append("summary")
    if not data["skills"]:
        missing.append("skills")
    if not data["experience"]:
        missing.append("experience")
    if not data["education"]:
        missing.append("education")
    data["missing_sections"] = missing

    return data
