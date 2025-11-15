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


@bp.route("", methods=["GET"])
@login_required
def index():
    return render_template(
        "skillmapper/index.html",
        is_pro=current_user.is_pro,
        feature_key=FEATURE_KEY,
        updated_tag=CAREER_AI_VERSION,  # optional UI label
    )


@bp.route("/free", methods=["POST"])
@login_required
def run_free():
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
        free_text_skills = (payload.get("free_text_skills") or "").strip()
        target_domain = (payload.get("target_domain") or "").strip()

        if not free_text_skills:
            return (
                jsonify(
                    {"ok": False, "error": "Please paste your skills/interests text."}
                ),
                400,
            )

        # Cap size to keep prompts bounded
        if len(free_text_skills) > MAX_FREE_TEXT:
            free_text_skills = free_text_skills[:MAX_FREE_TEXT]

        # Pass a small hint for domain to the generator while keeping API stable
        free_hints = {}
        if target_domain:
            free_hints["target_domain"] = target_domain

        data, used_live_ai = generate_skillmap(
            pro_mode=False,
            free_text_skills=free_text_skills,
            return_source=True,
            # Safe extension: many implementations accept **kwargs; if not,
            # the helper will ignore embedded hints inside the data it returns.
            hints=free_hints,
        )

        log.info(
            "SM/free used_live_ai=%s text_len=%d domain_hint=%s",
            used_live_ai,
            len(free_text_skills),
            bool(target_domain),
        )

        # Persist snapshot (best-effort)
        try:
            snap = SkillMapSnapshot(
                user_id=current_user.id,
                source_title="Skill Mapper (Free)",
                input_text=(target_domain + "\n\n" if target_domain else "") + free_text_skills,
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
        time_horizon = (payload.get("time_horizon_months") or 6)
        try:
            time_horizon = int(time_horizon)
        except Exception:
            time_horizon = 6
        time_horizon = max(3, min(12, time_horizon))

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

        # Backward-compatible options pass-through:
        # embed non-breaking SM options into the profile payload so ai.py can
        # read them without changing generate_skillmap signature.
        if profile is None:
            profile = {}
        profile = dict(profile or {})
        profile.setdefault("_skillmapper_options", {})
        profile["_skillmapper_options"].update(
            {
                "region_sector": region_sector,
                "time_horizon_months": time_horizon,
                "use_profile": bool(use_profile),
            }
        )

        data, used_live_ai = generate_skillmap(
            pro_mode=True,
            profile_json=profile if use_profile else None,
            resume_text=resume_text,
            return_source=True,
        )

        log.info(
            "SM/pro used_live_ai=%s profile_keys=%s resume_len=%d horizon=%s region=%s",
            used_live_ai,
            list((profile or {}).keys()),
            len(resume_text or ""),
            time_horizon,
            bool(region_sector),
        )

        # Persist snapshot (best-effort)
        try:
            snap = SkillMapSnapshot(
                user_id=current_user.id,
                source_title="Skill Mapper (Pro)",
                input_text=f"profile={'on' if use_profile else 'off'}; region={region_sector or '-'}; horizon={time_horizon}m",
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
