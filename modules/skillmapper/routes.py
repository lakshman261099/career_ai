# modules/skillmapper/routes.py
from __future__ import annotations

import json as _json
import logging
import os
import sys
import traceback
from datetime import datetime

from flask import (
    jsonify,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
)
from flask_login import current_user, login_required
from sqlalchemy import desc

from models import ResumeAsset, SkillMapSnapshot, UserProfile, db
from modules.common.ai import generate_skillmap
from modules.common.profile_loader import load_profile_snapshot
from modules.auth.guards import require_verified_email

# Phase 4: central credits engine
from modules.credits.engine import can_afford, deduct_free, deduct_pro

from . import bp

log = logging.getLogger(__name__)

FEATURE_KEY = "skillmapper"
SHOW_ERRS = os.getenv("SHOW_SM_ERRORS", "0") == "1"
CAREER_AI_VERSION = os.getenv("CAREER_AI_VERSION", "2025-Q4")

# Max size guardrails (avoid huge pastes)
MAX_FREE_TEXT = int(os.getenv("SM_MAX_FREE_TEXT", "12000"))
MAX_RESUME_TEXT = int(os.getenv("SM_MAX_RESUME_TEXT", "24000"))


# ---------------------- helpers ----------------------


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


def json_dumps_safe(obj) -> str:
    try:
        return _json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"


def _normalize_roles(skillmap: dict) -> dict:
    """
    Make sure templates always see skillmap['roles'] as a list.

    Older / different model variants might return:
      - 'role_roadmap'
      - 'top_roles'
      - 'role_cards'
      - 'primary_roles'
    etc. We map the first one we find onto 'roles' if it's missing.
    """
    if not isinstance(skillmap, dict):
        return {}

    maybe_roles = skillmap.get("roles")
    if isinstance(maybe_roles, list):
        return skillmap

    for alt in ("role_roadmap", "top_roles", "role_cards", "primary_roles"):
        alt_val = skillmap.get(alt)
        if isinstance(alt_val, list):
            skillmap["roles"] = alt_val
            break

    return skillmap


def _current_is_pro_user() -> bool:
    """
    Helper: determine if user is Pro based on flags + subscription_status.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False
    if getattr(current_user, "is_pro", False):
        return True
    status = (getattr(current_user, "subscription_status", "free") or "free").lower()
    return status == "pro"


def _normalize_path_type(raw: str | None) -> str:
    """
    Normalize the path_type from form/query:
      'job' (default), 'startup', 'freelance'
    """
    val = (raw or "").strip().lower()
    if val in ("startup", "freelance"):
        return val
    return "job"


# ---------------------- new HTML-first flow ----------------------


@bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Skill Mapper — HTML-first flow (like Job Pack).

    Now supports three paths in one place:

    - Job Path: best-fit roles, gaps, difficulty, salary bands, timeline.
    - Startup Path: founder role, cofounders, MVP, stack, GTM, risks.
    - Freelance Path: services, pricing, platforms, client plan, projects.

    Free runs:
      - Use Profile Portal + resume + optional extra skills.
      - Cost Silver 🪙 (feature_key='skill_mapper_free') for ALL users.
      - Show 1 full role + Pro preview (blurred) of the rest.

    Pro runs:
      - Require Pro subscription + Gold ⭐ credits.
      - Deep roadmap with 3+ angles, micro-projects & trends.

    Credits are only deducted AFTER a successful AI call + snapshot save.
    """
    is_pro_user = _current_is_pro_user()
    mode = (request.form.get("mode") or request.args.get("mode") or "free").lower()
    if mode not in ("free", "pro"):
        mode = "free"
    pro_mode = mode == "pro"

    # path_type: job (default), startup, freelance
    path_type = _normalize_path_type(
        request.form.get("path_type") or request.args.get("path_type")
    )

    skillmap = None
    used_live_ai = False
    snapshot = None

    profile_snapshot = load_profile_snapshot(current_user)

    if request.method == "POST":
        # 🔒 Email verification guard (HTML flow)
        if not getattr(current_user, "verified", False):
            flash(
                "Please verify your email with a login code before using Skill Mapper.",
                "warning",
            )
            return redirect(url_for("auth.otp_request"))

        # Simple preference inputs
        extra_skills = (request.form.get("free_text_skills") or "").strip()
        target_domain = (request.form.get("target_domain") or "").strip()
        region_focus = (request.form.get("region_focus") or "").strip()
        time_horizon = (request.form.get("time_horizon") or "").strip()  # months

        # ------------------ Credits: check BEFORE AI, deduct AFTER success ------------------
        if pro_mode:
            # Pro mode requires Pro subscription + Gold ⭐ credits
            if not is_pro_user:
                flash(
                    "Skill Mapper Pro is available for Pro ⭐ members only.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

            if not can_afford(current_user, "skill_mapper_pro", currency="gold"):
                flash(
                    "You don’t have enough Gold ⭐ credits to run Skill Mapper Pro. "
                    "Upgrade your plan or add more credits in the Coins Shop.",
                    "warning",
                )
                return redirect(url_for("billing.index"))
        else:
            # Free/basic mode always uses Silver 🪙
            if not can_afford(current_user, "skill_mapper_free", currency="silver"):
                flash(
                    "You don’t have enough Silver 🪙 credits to run Skill Mapper. "
                    "Upgrade to Pro ⭐ or add more credits in the Coins Shop.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

        # Load Profile Portal + latest resume on file
        profile = _profile_json(current_user.id)
        resume_text = _latest_resume_text(current_user.id)
        if len(resume_text) > MAX_RESUME_TEXT:
            resume_text = resume_text[:MAX_RESUME_TEXT]

        if not profile and not resume_text and not extra_skills:
            flash(
                "We couldn’t find a Profile Portal or resume yet. "
                "Add your basics in Profile Portal, then try again.",
                "warning",
            )
            return render_template(
                "skillmapper/index.html",
                mode=mode,
                is_pro_user=is_pro_user,
                profile_snapshot=profile_snapshot,
                path_type=path_type,
                CAREER_AI_VERSION=CAREER_AI_VERSION,
            )

        try:
            if pro_mode:
                # For Pro, require some profile content (it’s a profile-tuned roadmap)
                if not profile:
                    flash(
                        "Your Profile Portal looks empty. "
                        "Please add basic details and skills first.",
                        "warning",
                    )
                    return render_template(
                        "skillmapper/index.html",
                        mode=mode,
                        is_pro_user=is_pro_user,
                        profile_snapshot=profile_snapshot,
                        path_type=path_type,
                        CAREER_AI_VERSION=CAREER_AI_VERSION,
                    )

                # Hints for Pro (Job / Startup / Freelance all use same engine with path_type)
                hints = {
                    "path_type": path_type,  # "job" | "startup" | "freelance"
                    "region_sector": region_focus
                    or "India · early-career tech roles",
                    "time_horizon_months": time_horizon or 6,
                    "focus": "current_snapshot",
                }
                skillmap, used_live_ai = generate_skillmap(
                    pro_mode=True,
                    profile_json=profile,
                    resume_text=resume_text,
                    return_source=True,
                    hints=hints,
                )
            else:
                if len(extra_skills) > MAX_FREE_TEXT:
                    extra_skills = extra_skills[:MAX_FREE_TEXT]

                # Hints for Free (also path-aware)
                hints = {
                    "path_type": path_type,  # "job" | "startup" | "freelance"
                    "region_focus": region_focus
                    or "India · early-career tech roles",
                    "target_domain": target_domain,
                    "focus": "current_snapshot",
                }
                skillmap, used_live_ai = generate_skillmap(
                    pro_mode=False,
                    profile_json=profile or None,
                    resume_text=resume_text,
                    free_text_skills=extra_skills,
                    return_source=True,
                    hints=hints,
                )

            log.info(
                "SkillMapper HTML run pro_mode=%s path_type=%s used_live_ai=%s "
                "has_profile=%s has_resume=%s region=%s target_domain=%s",
                pro_mode,
                path_type,
                used_live_ai,
                bool(profile),
                bool(resume_text),
                region_focus or ("India-default" if not pro_mode else "N/A"),
                target_domain,
            )
        except Exception as e:
            current_app.logger.exception("SkillMapper analysis failed: %s", e)
            flash(
                "Skill Mapper had a problem generating your roadmap. "
                "Please try again in a bit.",
                "danger",
            )
            # No credits deducted, because AI call failed.
            return render_template(
                "skillmapper/index.html",
                mode=mode,
                is_pro_user=is_pro_user,
                profile_snapshot=profile_snapshot,
                path_type=path_type,
                CAREER_AI_VERSION=CAREER_AI_VERSION,
            )

        # Ensure we have a dict and normalize roles for the templates
        if isinstance(skillmap, dict):
            skillmap = _normalize_roles(skillmap)

        # Persist snapshot (best-effort) BEFORE deducting credits
        snapshot = None
        try:
            snap = SkillMapSnapshot(
                user_id=current_user.id,
                source_title=(
                    f"Skill Mapper ({path_type.title()} · Pro)"
                    if pro_mode
                    else f"Skill Mapper ({path_type.title()} · Free)"
                ),
                input_text="\n".join(
                    part
                    for part in [
                        f"mode={mode}",
                        f"path_type={path_type}",
                        f"region={region_focus}" if region_focus else "",
                        f"target_domain={target_domain}" if target_domain else "",
                        f"time_horizon={time_horizon}" if time_horizon else "",
                        extra_skills,
                    ]
                    if part
                ),
                skills_json=json_dumps_safe(skillmap),
                created_at=datetime.utcnow(),
            )
            db.session.add(snap)
            db.session.commit()
            snapshot = snap
        except Exception:
            db.session.rollback()
            current_app.logger.warning(
                "SkillMapper snapshot save failed.", exc_info=True
            )

        # Deduct credits AFTER successful AI call + snapshot
        try:
            if pro_mode:
                if not deduct_pro(
                    current_user,
                    "skill_mapper_pro",
                    run_id=(snapshot.id if snapshot is not None else None),
                ):
                    log.warning(
                        "SkillMapper: deduct_pro failed after analysis for user %s",
                        current_user.id,
                    )
                    flash(
                        "Your Pro roadmap was generated, but your Pro credits "
                        "could not be updated correctly. Please contact support if this keeps happening.",
                        "warning",
                    )
            else:
                if not deduct_free(
                    current_user,
                    "skill_mapper_free",
                    run_id=(snapshot.id if snapshot is not None else None),
                ):
                    log.warning(
                        "SkillMapper: deduct_free failed after analysis for user %s",
                        current_user.id,
                    )
                    flash(
                        "Your Skill Mapper run completed, but your Silver credits "
                        "could not be updated correctly. Please contact support if this keeps happening.",
                        "warning",
                    )
        except Exception as e:
            current_app.logger.exception(
                "SkillMapper credit deduction error after analysis: %s", e
            )
            flash(
                "Your roadmap was generated, but we had trouble updating your credits. "
                "Please contact support if this keeps happening.",
                "warning",
            )

        # Enrich meta for the template
        if isinstance(skillmap, dict):
            meta = skillmap.get("meta") or {}
            if not isinstance(meta, dict):
                meta = {}
            meta.setdefault("run_mode", "pro" if pro_mode else "free")
            meta.setdefault("used_live_ai", bool(used_live_ai))
            meta.setdefault("path_type", path_type)
            if snapshot is not None:
                meta.setdefault("snapshot_id", snapshot.id)
            skillmap["meta"] = meta

        return render_template(
            "skillmapper/result.html",
            skillmap=skillmap,
            is_pro=pro_mode,  # deep vs free run (like Job Pack)
            is_pro_user=is_pro_user,
            mode=mode,
            from_history=False,
            snapshot=snapshot,
            profile_snapshot=profile_snapshot,
            CAREER_AI_VERSION=CAREER_AI_VERSION,
        )

    # GET
    return render_template(
        "skillmapper/index.html",
        mode=mode,
        is_pro_user=is_pro_user,
        profile_snapshot=profile_snapshot,
        path_type=path_type,
        CAREER_AI_VERSION=CAREER_AI_VERSION,
    )


# ---------------------- history + reopen snapshot ----------------------


@bp.route("/history", methods=["GET"], endpoint="history")
@login_required
def history():
    """
    List past Skill Mapper snapshots for the current user (most recent first).
    """
    page = request.args.get("page", 1, type=int)
    per_page = 10
    pagination = (
        SkillMapSnapshot.query.filter_by(user_id=current_user.id)
        .order_by(SkillMapSnapshot.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    snapshots = pagination.items
    return render_template(
        "skillmapper/history.html",
        snapshots=snapshots,
        pagination=pagination,
        CAREER_AI_VERSION=CAREER_AI_VERSION,
    )


@bp.route("/snapshot/<int:snapshot_id>", methods=["GET"], endpoint="snapshot")
@login_required
def snapshot(snapshot_id: int):
    """
    Reopen a previously saved Skill Mapper snapshot without re-running AI.
    """
    snap = (
        SkillMapSnapshot.query.filter_by(id=snapshot_id, user_id=current_user.id)
        .first_or_404()
    )

    try:
        raw = _json.loads(snap.skills_json or "{}")
    except Exception:
        raw = {}

    skillmap = raw if isinstance(raw, dict) else {}
    # Normalize roles when reopening from history too
    skillmap = _normalize_roles(skillmap)

    meta = skillmap.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("snapshot_id", snap.id)
    meta.setdefault("restored_from_history", True)
    # If path_type wasn't present yet, try to infer from source_title
    if "path_type" not in meta:
        st = (snap.source_title or "").lower()
        if "startup" in st:
            meta["path_type"] = "startup"
        elif "freelance" in st:
            meta["path_type"] = "freelance"
        else:
            meta["path_type"] = "job"
    skillmap["meta"] = meta

    pro_mode = (skillmap.get("mode") or "free") == "pro"
    profile_snapshot = load_profile_snapshot(current_user)

    return render_template(
        "skillmapper/result.html",
        skillmap=skillmap,
        is_pro=pro_mode,
        is_pro_user=_current_is_pro_user(),
        mode="pro" if pro_mode else "free",
        from_history=True,
        snapshot=snap,
        profile_snapshot=profile_snapshot,
        CAREER_AI_VERSION=CAREER_AI_VERSION,
    )


# ---------------------- legacy JSON endpoints (kept for compatibility) ----------------------


@bp.route("/free", methods=["POST"])
@login_required
@require_verified_email
def run_free():
    """
    Skill Mapper Free (JSON API):
    - Always uses Profile Portal + latest resume as the *primary* source.
    - Extra pasted text is just a hint (optional).
    - Costs Silver 🪙 (feature_key='skill_mapper_free') for ALL users.
    - Returns a compact roadmap JSON; Pro-only sections are *not* generated here.
    """
    try:
        # Silver credit check for all users (Free runs use 🪙)
        if not can_afford(current_user, "skill_mapper_free", currency="silver"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Not enough Silver credits to run Skill Mapper. "
                        "Upgrade to Pro ⭐ or add more credits in the Coins Shop.",
                    }
                ),
                402,
            )

        payload = request.get_json(silent=True) or {}
        extra_text = (payload.get("free_text_skills") or "").strip()
        target_domain = (payload.get("target_domain") or "").strip()

        # Cap size to keep prompts bounded (optional, since it's just a hint)
        if len(extra_text) > MAX_FREE_TEXT:
            extra_text = extra_text[:MAX_FREE_TEXT]

        # Main source: Profile Portal + latest resume
        profile = _profile_json(current_user.id)
        resume_text = _latest_resume_text(current_user.id)
        if len(resume_text) > MAX_RESUME_TEXT:
            resume_text = resume_text[:MAX_RESUME_TEXT]

        if not profile and not resume_text and not extra_text:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "We couldn’t find a Profile Portal or resume yet. "
                        "Add your basics in Profile Portal, then try again.",
                    }
                ),
                400,
            )

        free_hints = {
            "region_focus": "India · early-career tech roles",
            "focus": "current_snapshot",
        }
        if target_domain:
            free_hints["target_domain"] = target_domain

        data, used_live_ai = generate_skillmap(
            pro_mode=False,
            profile_json=profile or None,
            resume_text=resume_text,
            free_text_skills=extra_text,
            return_source=True,
            hints=free_hints,
        )

        # Normalize roles for API clients too
        if isinstance(data, dict):
            data = _normalize_roles(data)

        log.info(
            "SM/free used_live_ai=%s extra_len=%d domain_hint=%s has_profile=%s has_resume=%s",
            used_live_ai,
            len(extra_text),
            bool(target_domain),
            bool(profile),
            bool(resume_text),
        )

        # Persist snapshot (best-effort)
        snapshot = None
        try:
            snap = SkillMapSnapshot(
                user_id=current_user.id,
                source_title="Skill Mapper (Free API)",
                input_text="\n\n".join(
                    part
                    for part in [
                        f"target_domain={target_domain}" if target_domain else "",
                        extra_text,
                    ]
                    if part
                ),
                skills_json=json_dumps_safe(data),
                created_at=datetime.utcnow(),
            )
            db.session.add(snap)
            db.session.commit()
            snapshot = snap
        except Exception:
            db.session.rollback()
            log.warning("SkillMapper snapshot save failed (free).", exc_info=True)

        # Deduct Silver 🪙 AFTER successful AI + snapshot
        try:
            if not deduct_free(
                current_user,
                "skill_mapper_free",
                run_id=(snapshot.id if snapshot is not None else None),
            ):
                log.warning(
                    "SkillMapper /free: deduct_free failed after analysis for user %s",
                    current_user.id,
                )
        except Exception:
            log.exception("SkillMapper /free credit deduction error")

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
@require_verified_email
def run_pro():
    """
    Skill Mapper Pro (JSON API):
    - Uses Profile Portal + latest resume (or pasted override).
    - Requires Pro subscription + Gold ⭐ credits.
    - Focus is “current snapshot” for India by default.
    - Deep roadmap + richer India market context.
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

        # Gold credit check
        if not can_afford(current_user, "skill_mapper_pro", currency="gold"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "You don’t have enough Gold credits to run "
                        "Skill Mapper Pro. Upgrade your plan or add more credits "
                        "in the Coins Shop.",
                    }
                ),
                402,
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
                        "error": "Your Profile Portal looks empty. "
                        "Please add basic details and skills first.",
                    }
                ),
                400,
            )

        # Resume selection: pasted > latest on file
        resume_text = pasted_resume_text or _latest_resume_text(current_user.id)
        if len(resume_text) > MAX_RESUME_TEXT:
            resume_text = resume_text[:MAX_RESUME_TEXT]

        hints = {
            "region_sector": region_sector or "India · early-career tech roles",
            "use_profile": bool(use_profile),
            "focus": "current_snapshot",
        }

        data, used_live_ai = generate_skillmap(
            pro_mode=True,
            profile_json=profile if use_profile else None,
            resume_text=resume_text,
            return_source=True,
            hints=hints,
        )

        # Normalize roles for API clients
        if isinstance(data, dict):
            data = _normalize_roles(data)

        log.info(
            "SM/pro used_live_ai=%s profile_keys=%s resume_len=%d region=%s",
            used_live_ai,
            list((profile or {}).keys()),
            len(resume_text or ""),
            region_sector or "India-default",
        )

        # Persist snapshot (best-effort)
        snapshot = None
        try:
            snap = SkillMapSnapshot(
                user_id=current_user.id,
                source_title="Skill Mapper (Pro API)",
                input_text=(
                    f"profile={'on' if use_profile else 'off'}; "
                    f"region={region_sector or 'India-default'}"
                ),
                skills_json=json_dumps_safe(data),
                created_at=datetime.utcnow(),
            )
            db.session.add(snap)
            db.session.commit()
            snapshot = snap
        except Exception:
            db.session.rollback()
            log.warning("SkillMapper snapshot save failed (pro).", exc_info=True)

        # Deduct Gold ⭐ AFTER successful AI + snapshot
        try:
            if not deduct_pro(
                current_user,
                "skill_mapper_pro",
                run_id=(snapshot.id if snapshot is not None else None),
            ):
                log.warning(
                    "SkillMapper /pro: deduct_pro failed after analysis for user %s",
                    current_user.id,
                )
        except Exception:
            log.exception("SkillMapper /pro credit deduction error")

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
