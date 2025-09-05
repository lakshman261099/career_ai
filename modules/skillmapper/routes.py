# modules/skillmapper/routes.py
from __future__ import annotations

from flask import render_template, request, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy import desc
from datetime import datetime

from . import bp
from models import db, UserProfile, ResumeAsset, SkillMapSnapshot
from limits import authorize_and_consume
from modules.common.ai import generate_skillmap

FEATURE_KEY = "skillmapper"

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
    # If user is NOT pro, consume 🪙. Pro users bypass coin checks here.
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

@bp.route("/pro", methods=["POST"])
@login_required
def run_pro():
    if not current_user.is_pro:
        abort(403, description="Skill Mapper Pro requires a Pro subscription.")

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

# --------------- utils ----------------
import json as _json
def json_dumps_safe(obj) -> str:
    try:
        return _json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"
