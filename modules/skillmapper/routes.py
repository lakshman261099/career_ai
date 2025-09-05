# modules/skillmapper/routes.py
from __future__ import annotations

import json as _json
import logging, os, sys, traceback
from datetime import datetime

from flask import render_template, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import desc

from . import bp
from models import db, UserProfile, ResumeAsset, SkillMapSnapshot
from limits import authorize_and_consume
from modules.common.ai import generate_skillmap

log = logging.getLogger(__name__)
FEATURE_KEY = "skillmapper"
SHOW_ERRS = os.getenv("SHOW_SM_ERRORS", "0") == "1"


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
    )


@bp.route("/free", methods=["POST"])
@login_required
def run_free():
    try:
        if not current_user.is_pro:
            ok = authorize_and_consume(current_user, FEATURE_KEY)
            if not ok:
                return jsonify({"ok": False, "error": "Not enough Silver credits to run Skill Mapper."}), 402

        payload = (request.get_json(silent=True) or {})
        free_text_skills = (payload.get("free_text_skills") or "").strip()
        if not free_text_skills:
            return jsonify({"ok": False, "error": "Please paste your skills/interests text."}), 400

        data, used_live_ai = generate_skillmap(
            pro_mode=False,
            free_text_skills=free_text_skills,
            return_source=True,
        )

        log.info("SM/free used_live_ai=%s text_len=%d", used_live_ai, len(free_text_skills))

        snap = SkillMapSnapshot(
            user_id=current_user.id,
            source_title="Skill Mapper (Free)",
            input_text=free_text_skills,
            skills_json=json_dumps_safe(data),
            created_at=datetime.utcnow(),
        )
        db.session.add(snap)
        db.session.commit()

        return jsonify({"ok": True, "data": data, "used_live_ai": used_live_ai})
    except Exception as e:
        log.exception("SkillMapper /free failed")
        traceback.print_exc(file=sys.stderr)
        try: db.session.rollback()
        except Exception: pass
        msg = "Internal error (free). Check server logs."
        if SHOW_ERRS:
            msg += f" :: {e.__class__.__name__}: {e}"
        return jsonify({"ok": False, "error": msg}), 500


@bp.route("/pro", methods=["POST"])
@login_required
def run_pro():
    try:
        if not current_user.is_pro:
            return jsonify({"ok": False, "error": "Skill Mapper Pro requires a Pro subscription."}), 403

        profile = _profile_json(current_user.id)
        if not profile:
            return jsonify({"ok": False, "error": "Your Profile Portal looks empty. Please add basic details and skills first."}), 400

        resume_text = _latest_resume_text(current_user.id)

        data, used_live_ai = generate_skillmap(
            pro_mode=True,
            profile_json=profile,
            resume_text=resume_text,
            return_source=True,
        )

        log.info(
            "SM/pro used_live_ai=%s profile_keys=%s resume_len=%d",
            used_live_ai, list(profile.keys()), len(resume_text or "")
        )

        snap = SkillMapSnapshot(
            user_id=current_user.id,
            source_title="Skill Mapper (Pro)",
            input_text="profile+resume",
            skills_json=json_dumps_safe(data),
            created_at=datetime.utcnow(),
        )
        db.session.add(snap)
        db.session.commit()

        return jsonify({"ok": True, "data": data, "used_live_ai": used_live_ai})
    except Exception as e:
        log.exception("SkillMapper /pro failed")
        traceback.print_exc(file=sys.stderr)
        try: db.session.rollback()
        except Exception: pass
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
