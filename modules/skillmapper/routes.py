# modules/skillmapper/routes.py
from __future__ import annotations

import json as _json
import logging
import os
import sys
import traceback
from datetime import datetime

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import desc

from limits import authorize_and_consume
from models import ResumeAsset, SkillMapSnapshot, UserProfile, db
from modules.common.ai import generate_skillmap

from . import bp

log = logging.getLogger(__name__)
FEATURE_KEY = "skillmapper"
SHOW_ERRS = os.getenv("SHOW_SM_ERRORS", "0") == "1"
CAREER_AI_VERSION = os.getenv("CAREER_AI_VERSION", "2025-Q4")

# Max size guardrails (avoid huge pastes)
MAX_FREE_TEXT = int(os.getenv("SM_MAX_FREE_TEXT", "12000"))
MAX_RESUME_TEXT = int(os.getenv("SM_MAX_RESUME_TEXT", "24000"))


def _latest_resume_text(user_id: int) -> str:
    r = (
        ResumeAsset.query.filter_by(user_id=user_id)
        .order_by(desc(ResumeAsset.created_at))
        .first()
    )
    return (r.text or "") if r else ""


def _profile_json(user_id: int) -> dict:
    prof: UserProfile | None = UserProfile.query.filter_by(user_id=user_id).first()
    if not prof:
        return {}
    return {
        "identity": {
            "full_name": prof.full_name,
            "headline": prof.headline,
            "summary": prof.summary,
            "location": prof.location,
            "phone": prof.phone,
        },
        "links": prof.links or {},
        "skills": prof.skills or [],
        "education": prof.education or [],
        "experience": prof.experience or [],
        "certifications": prof.certifications or [],
    }


def _profile_snapshot_text(profile: dict) -> str:
    """
    Turn the Profile Portal JSON into a compact text snapshot that can be fed
    into the free SkillMapper prompt. This lets free mode still be
    profile-driven without changing the AI schema.
    """
    if not isinstance(profile, dict) or not profile:
        return ""

    ident = profile.get("identity") or {}
    pieces = []

    name = (ident.get("full_name") or "").strip()
    headline = (ident.get("headline") or "").strip()
    summary = (ident.get("summary") or "").strip()

    if headline:
        pieces.append(headline)
    if summary:
        pieces.append(summary)

    # Flatten skills into simple names
    skills_field = profile.get("skills") or []
    skill_names: list[str] = []
    if isinstance(skills_field, list):
        for s in skills_field:
            if isinstance(s, str) and s.strip():
                skill_names.append(s.strip())
            elif isinstance(s, dict):
                n = (s.get("name") or s.get("skill") or "").strip()
                if n:
                    skill_names.append(n)
    if skill_names:
        pieces.append("Core skills: " + ", ".join(skill_names[:30]))

    # Light-touch experience labels (no huge text)
    exp = profile.get("experience") or []
    if isinstance(exp, list) and exp:
        roles = []
        for item in exp[:4]:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or item.get("role") or "").strip()
            company = (item.get("company") or "").strip()
            if title and company:
                roles.append(f"{title} @ {company}")
            elif title:
                roles.append(title)
        if roles:
            pieces.append("Experience: " + "; ".join(roles))

    return (name + " — " if name else "") + " | ".join(pieces)


@bp.route("", methods=["GET"])
@login_required
def index():
    # feature_paths is likely injected via a context processor elsewhere
    return render_template(
        "skillmapper/index.html",
        is_pro=current_user.is_pro,
        feature_key=FEATURE_KEY,
        updated_tag=CAREER_AI_VERSION,  # optional UI label
    )


@bp.route("/free", methods=["POST"])
@login_required
def run_free():
    """
    Free Skill Mapper (basic run):
    - Uses Profile Portal + latest resume as the main source.
    - Optional extra skills/interests text nudges the roadmap.
    - India-first, early-career bias via hints.
    - Returns full JSON, but UI shows:
        - Basic panel (fully visible)
        - Pro-style preview panel (blurred) for Free users.
    """
    try:
        # Silver credit check for non-Pro users
        if not current_user.is_pro:
            ok = authorize_and_consume(current_user, FEATURE_KEY)
            if not ok:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "Not enough Silver credits to run Skill Mapper.",
                        }
                    ),
                    402,
                )

        payload = request.get_json(silent=True) or {}
        extra_text = (payload.get("free_text_skills") or "").strip()
        target_domain = (payload.get("target_domain") or "").strip()

        profile = _profile_json(current_user.id)
        resume_text = _latest_resume_text(current_user.id)

        profile_text = _profile_snapshot_text(profile)
        if len(resume_text) > MAX_RESUME_TEXT:
            resume_text = resume_text[:MAX_RESUME_TEXT]

        # Build a single compact text blob for the free prompt
        chunks: list[str] = []

        if profile_text:
            chunks.append("Profile snapshot:\n" + profile_text)

        if resume_text:
            # Only a slice — free mode doesn’t need the full resume
            chunks.append("Resume excerpt:\n" + resume_text[:3000])

        if extra_text:
            if len(extra_text) > MAX_FREE_TEXT:
                extra_text = extra_text[:MAX_FREE_TEXT]
            chunks.append("Extra skills & interests:\n" + extra_text)

        combined_text = "\n\n".join(chunks).strip()

        # Guard: if everything is empty, tell the user instead of calling AI
        if not combined_text:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": (
                            "We couldn’t find any data to map. "
                            "Please fill your Profile Portal or add a few skills/interests."
                        ),
                    }
                ),
                400,
            )

        # Hints for the AI generator (non-breaking)
        free_hints = {
            "region_focus": "India · early-career tech roles",
            "focus": "current_snapshot",
            "using_profile": bool(profile),
            "using_resume": bool(resume_text),
        }
        if target_domain:
            free_hints["target_domain"] = target_domain

        data, used_live_ai = generate_skillmap(
            pro_mode=False,
            free_text_skills=combined_text,
            return_source=True,
            hints=free_hints,
        )

        log.info(
            "SM/free used_live_ai=%s combined_len=%d profile=%s resume=%s domain_hint=%s",
            used_live_ai,
            len(combined_text),
            bool(profile),
            bool(resume_text),
            bool(target_domain),
        )

        # Persist snapshot (best-effort)
        try:
            snap = SkillMapSnapshot(
                user_id=current_user.id,
                source_title="Skill Mapper (Free)",
                input_text=combined_text,
                skills_json=json_dumps_safe(data),
                created_at=datetime.utcnow(),
            )
            db.session.add(snap)
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.warning("SkillMapper snapshot save failed (free).", exc_info=True)

        return jsonify({"ok": True, "data": data, "used_live_ai": used_live_ai})
    except Exception as e:
        log.exception("SkillMapper /free failed")
        traceback.print_exc(file=sys.stderr)
        try:
            db.session.rollback()
        except Exception:
            pass
        msg = "Internal error (free). Check server logs."
        if SHOW_ERRS:
            msg += f" :: {e.__class__.__name__}: {e}"
        return jsonify({"ok": False, "error": msg}), 500


@bp.route("/pro", methods=["POST"])
@login_required
def run_pro():
    """
    Pro Skill Mapper:
    - Uses Profile Portal + latest resume (or pasted override).
    - Focus is “current snapshot” (no more time horizon).
    - Region is biased to India by default, with optional region/sector text.
    """
    try:
        if not current_user.is_pro:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Skill Mapper Pro requires a Pro subscription.",
                    }
                ),
                403,
            )

        payload = request.get_json(silent=True) or {}
        use_profile = bool(payload.get("use_profile", True))
        pasted_resume_text = (payload.get("resume_text") or "").strip()
        region_sector = (payload.get("region_sector") or "").strip()

        profile = _profile_json(current_user.id) if use_profile else {}

        if use_profile and not profile:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Your Profile Portal looks empty. Please add basic details and skills first.",
                    }
                ),
                400,
            )

        # Resume selection: pasted > latest on file
        resume_text = pasted_resume_text or _latest_resume_text(current_user.id)
        if len(resume_text) > MAX_RESUME_TEXT:
            resume_text = resume_text[:MAX_RESUME_TEXT]

        # Embed SM options into the profile payload so ai.py can read them without
        # changing generate_skillmap signature.
        if profile is None:
            profile = {}
        profile = dict(profile or {})
        profile.setdefault("_skillmapper_options", {})
        profile["_skillmapper_options"].update(
            {
                # Bias to India + allow extra hint from UI
                "region_sector": region_sector or "India · early-career tech roles",
                "use_profile": bool(use_profile),
                "focus": "current_snapshot",
            }
        )

        data, used_live_ai = generate_skillmap(
            pro_mode=True,
            profile_json=profile if use_profile else None,
            resume_text=resume_text,
            return_source=True,
        )

        log.info(
            "SM/pro used_live_ai=%s profile_keys=%s resume_len=%d region=%s",
            used_live_ai,
            list((profile or {}).keys()),
            len(resume_text or ""),
            region_sector or "India-default",
        )

        # Persist snapshot (best-effort)
        try:
            snap = SkillMapSnapshot(
                user_id=current_user.id,
                source_title="Skill Mapper (Pro)",
                input_text=(
                    f"profile={'on' if use_profile else 'off'}; "
                    f"region={region_sector or 'India-default'}"
                ),
                skills_json=json_dumps_safe(data),
                created_at=datetime.utcnow(),
            )
            db.session.add(snap)
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.warning("SkillMapper snapshot save failed (pro).", exc_info=True)

        return jsonify({"ok": True, "data": data, "used_live_ai": used_live_ai})
    except Exception as e:
        log.exception("SkillMapper /pro failed")
        traceback.print_exc(file=sys.stderr)
        try:
            db.session.rollback()
        except Exception:
            pass
        msg = "Internal error (pro). Check server logs."
        if SHOW_ERRS:
            msg += f" :: {e.__class__.__name__}: {e}"
        return jsonify({"ok": False, "error": msg}), 500


# --------------- utils ----------------
def json_dumps_safe(obj) -> str:
    try:
        return _json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"
