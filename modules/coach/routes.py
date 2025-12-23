# modules/coach/routes.py - COMPLETE SYNC UPGRADE (Keeping async workers)
"""
Weekly Coach with Dream→Coach Sync Integration + Saved Plan Library (Max 3)

Upgrade-in-place:
- Dream lock promotes snapshot into CoachSavedPlan
- Coach shows selectable plans (max 3)
- Student selects plan → presses Start → tasks generated from locked plan.coach_plan
- Delete plans from Coach to free up slots

✅ IMPORTANT (storage + no resurrection):
- Deleting a plan is a HARD DELETE (permanent)
- When a plan is deleted, we stamp the linked Dream snapshot JSON with `_coach_deleted_at`
  so auto-promotion won't re-create it later (no resurrection).

✅ IMPORTANT (enforcement):
- Future weeks are LOCKED: user can view but cannot mark tasks complete.
- Weekly tasks are DevLog-gated: cannot be completed via checkbox toggle.
- DevLog requires min character proof across main sections.
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
import json
from typing import Any

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
    abort,
)
from flask_login import current_user, login_required

from models import (
    DailyCoachSession,
    DailyCoachTask,
    DreamPlanSnapshot,
    User,
    UserProfile,
    db,
)

# ✅ NEW: saved plan model (if migrated)
try:
    from models import CoachSavedPlan
except Exception:
    CoachSavedPlan = None  # type: ignore

# ✅ NEW: Import LearningLog and ProfileSkillSuggestion
try:
    from models import LearningLog, ProfileSkillSuggestion
except Exception:
    LearningLog = None  # type: ignore
    ProfileSkillSuggestion = None  # type: ignore

from modules.common.profile_loader import load_profile_snapshot

coach_bp = Blueprint(
    "coach",
    __name__,
    template_folder="../../templates/coach",
)

# DevLog proof requirement
DEVLOG_MIN_CHARS_DEFAULT = 120


# ===================================
# BASIC HELPERS
# ===================================

def _current_is_pro_user() -> bool:
    if not getattr(current_user, "is_authenticated", False):
        return False
    if bool(getattr(current_user, "is_pro", False)):
        return True
    status = (getattr(current_user, "subscription_status", "free") or "free").lower()
    return status == "pro"


def _normalize_path_type(raw: str | None) -> str:
    v = (raw or "").strip().lower()
    if v == "startup":
        return "startup"
    return "job"


def _today_date() -> date:
    # Keep UTC date for now (schema already uses Date)
    return datetime.utcnow().date()


def _is_email_verified(user: User) -> bool:
    # Schema-safe: supports either email_verified or legacy verified
    try:
        return bool(getattr(user, "email_verified", False) or getattr(user, "verified", False))
    except Exception:
        return False


def _clean_len(s: str | None) -> int:
    if not s:
        return 0
    try:
        return len(" ".join(str(s).split()).strip())
    except Exception:
        return 0


def _devlog_total_chars(form: dict) -> int:
    # Proof-of-work is across these four fields (matches your devlog template meter)
    fields = [
        form.get("what_i_built"),
        form.get("what_i_learned"),
        form.get("challenges_faced"),
        form.get("next_steps"),
    ]
    return sum(_clean_len(v) for v in fields)


# ===================================
# User Field Helpers (Defensive)
# ===================================

def _get_user_int(user: User, field: str, default: int = 0) -> int:
    try:
        val = getattr(user, field, default)
        return int(val) if val is not None else default
    except Exception:
        return default


def _set_user_int(user: User, field: str, value: int):
    try:
        if hasattr(user, field):
            setattr(user, field, value)
    except Exception:
        pass


def _get_user_date(user: User, field: str) -> date | None:
    try:
        val = getattr(user, field, None)
        if isinstance(val, date):
            return val
        if isinstance(val, datetime):
            return val.date()
        return None
    except Exception:
        return None


def _set_user_date(user: User, field: str, value: date | None):
    try:
        if hasattr(user, field):
            setattr(user, field, value)
    except Exception:
        pass


# ===================================
# Session Lock Helpers (NEW)
# ===================================

def _session_time_locked(session: DailyCoachSession, today: date) -> bool:
    """
    Lock future weeks:
    - If session.session_date is in the future => LOCKED
    """
    try:
        if session.session_date and session.session_date > today:
            return True
    except Exception:
        pass
    return False


def _session_actions_locked(session: DailyCoachSession, today: date) -> bool:
    """
    Actions are blocked when:
    - future week (time-locked) OR
    - session is closed
    """
    if _session_time_locked(session, today):
        return True
    try:
        if bool(getattr(session, "is_closed", False)):
            return True
    except Exception:
        pass
    return False


# ===================================
# Plan Library Helpers (NEW)
# ===================================

def _get_saved_plans(user_id: int, path_type: str) -> list:
    if not CoachSavedPlan:
        return []
    try:
        # Legacy support: if is_deleted exists, hide deleted rows
        try:
            return (
                CoachSavedPlan.query.filter_by(
                    user_id=user_id,
                    path_type=path_type,
                    is_deleted=False,
                )
                .order_by(CoachSavedPlan.created_at.desc())
                .all()
            )
        except Exception:
            return (
                CoachSavedPlan.query.filter_by(
                    user_id=user_id,
                    path_type=path_type,
                )
                .order_by(CoachSavedPlan.created_at.desc())
                .all()
            )
    except Exception:
        return []


def _count_active_saved_plans(user_id: int) -> int:
    if not CoachSavedPlan:
        return 0
    try:
        # Legacy support: if is_deleted exists, count only active rows
        try:
            return CoachSavedPlan.query.filter_by(user_id=user_id, is_deleted=False).count()
        except Exception:
            return CoachSavedPlan.query.filter_by(user_id=user_id).count()
    except Exception:
        return 0


def _load_saved_plan_or_none(user_id: int, plan_id: int) -> Any:
    if not CoachSavedPlan:
        return None
    try:
        plan = CoachSavedPlan.query.get(plan_id)
        if not plan:
            return None
        if plan.user_id != user_id:
            return None
        # Legacy safety: ignore soft-deleted rows if they exist
        try:
            if getattr(plan, "is_deleted", False):
                return None
        except Exception:
            pass
        return plan
    except Exception:
        return None


def _parse_plan_json(plan_json_text: str) -> dict:
    try:
        d = json.loads(plan_json_text or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _extract_dream_context_from_plan_json(plan_json: dict) -> dict:
    """
    Normalize plan JSON for coach generation. Works for your sync_v1_complete output.
    """
    meta = plan_json.get("meta", {}) if isinstance(plan_json.get("meta"), dict) else {}
    input_block = plan_json.get("input", {}) if isinstance(plan_json.get("input"), dict) else {}

    target_lpa = meta.get("target_lpa") or input_block.get("target_lpa") or "12"
    if str(target_lpa) not in ("3", "6", "12", "24"):
        target_lpa = "12"
    target_lpa = str(target_lpa)

    selected_projects = plan_json.get("selected_projects", [])
    if not isinstance(selected_projects, list):
        selected_projects = []

    coach_plan = plan_json.get("coach_plan", {})
    if not isinstance(coach_plan, dict):
        coach_plan = {}

    timeline_months = input_block.get("timeline_months", 3)
    try:
        timeline_months = int(timeline_months)
    except Exception:
        timeline_months = 3

    # ✅ FIXED: locked means _locked_at present AND selected_projects is a non-empty list
    is_locked = _is_locked_plan_json(plan_json)

    return {
        "target_lpa": target_lpa,
        "selected_projects": selected_projects,
        "timeline_months": timeline_months,
        "coach_plan": coach_plan,
        "is_locked": is_locked,
    }


# ===================================
# Dream → Coach Sync (AUTO PROMOTION)
# ===================================

def _is_locked_plan_json(plan_json: dict) -> bool:
    try:
        if not isinstance(plan_json, dict):
            return False
        if "_locked_at" not in plan_json:
            return False
        sp = plan_json.get("selected_projects")
        return isinstance(sp, list) and len(sp) > 0
    except Exception:
        return False


def _is_promotion_blocked_plan_json(plan_json: dict) -> bool:
    """
    If a saved plan was deleted in Coach, we stamp Dream snapshot JSON with `_coach_deleted_at`.
    That prevents auto re-promotion (no resurrection).
    """
    try:
        if not isinstance(plan_json, dict):
            return False
        return bool(plan_json.get("_coach_deleted_at"))
    except Exception:
        return False


def _derive_saved_title_from_snapshot(snapshot: DreamPlanSnapshot, plan_json: dict) -> str:
    # Prefer snapshot title if present; else target_role; else fallback
    try:
        if getattr(snapshot, "plan_title", None):
            return str(snapshot.plan_title)
    except Exception:
        pass
    try:
        input_block = plan_json.get("input", {}) if isinstance(plan_json.get("input"), dict) else {}
        tr = input_block.get("target_role")
        if tr:
            return str(tr)
    except Exception:
        pass
    return "Saved Career Plan"


def _saved_plan_exists_for_snapshot(user_id: int, snapshot_id: int) -> bool:
    if not CoachSavedPlan:
        return False

    # Do NOT filter out is_deleted rows: they still exist, and we do not resurrect.
    try:
        q = CoachSavedPlan.query.filter_by(user_id=user_id, dream_snapshot_id=snapshot_id)
        return bool(q.first())
    except Exception:
        pass

    try:
        q = CoachSavedPlan.query.filter_by(user_id=user_id, snapshot_id=snapshot_id)
        return bool(q.first())
    except Exception:
        pass

    return False


def _create_saved_plan_from_snapshot(snapshot: DreamPlanSnapshot, path_type: str) -> bool:
    """
    Promote a locked DreamPlanSnapshot into CoachSavedPlan.
    Returns True if created, False otherwise.
    """
    if not CoachSavedPlan:
        return False

    # Respect max 3 active (global across paths)
    if _count_active_saved_plans(snapshot.user_id) >= 3:
        return False

    plan_json = _parse_plan_json(getattr(snapshot, "plan_json", "") or "")
    if not _is_locked_plan_json(plan_json):
        return False

    # No resurrection
    if _is_promotion_blocked_plan_json(plan_json):
        return False

    if _saved_plan_exists_for_snapshot(snapshot.user_id, snapshot.id):
        return False

    title = _derive_saved_title_from_snapshot(snapshot, plan_json)

    try:
        saved = CoachSavedPlan(
            user_id=snapshot.user_id,
            path_type=path_type,
            title=title,
            plan_json=getattr(snapshot, "plan_json", "") or "{}",
        )

        try:
            if hasattr(saved, "is_deleted"):
                saved.is_deleted = False
        except Exception:
            pass

        if hasattr(saved, "dream_snapshot_id"):
            setattr(saved, "dream_snapshot_id", snapshot.id)

        try:
            if hasattr(saved, "created_at") and getattr(saved, "created_at", None) is None:
                saved.created_at = datetime.utcnow()
            if hasattr(saved, "updated_at"):
                saved.updated_at = datetime.utcnow()
        except Exception:
            pass

        db.session.add(saved)
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def _auto_promote_locked_snapshots(user_id: int, path_type: str):
    """
    Auto-sync: when user lands on coach pages, promote recent locked snapshots into saved plans
    until max 3 is reached.

    ✅ No resurrection: snapshots with `_coach_deleted_at` are never promoted again.
    """
    if not CoachSavedPlan:
        return
    try:
        if _count_active_saved_plans(user_id) >= 3:
            return

        recent = (
            DreamPlanSnapshot.query.filter_by(user_id=user_id, path_type=path_type)
            .order_by(DreamPlanSnapshot.created_at.desc())
            .limit(10)
            .all()
        )

        for snap in recent:
            if _count_active_saved_plans(user_id) >= 3:
                break
            try:
                pj = _parse_plan_json(getattr(snap, "plan_json", "") or "")
                if not _is_locked_plan_json(pj):
                    continue
                if _is_promotion_blocked_plan_json(pj):
                    continue
                if _saved_plan_exists_for_snapshot(user_id, snap.id):
                    continue
                _create_saved_plan_from_snapshot(snap, path_type)
            except Exception:
                continue

        return
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


# ===================================
# Streak Logic
# ===================================

def _update_user_streak(user: User, task_completed_date: date):
    today = task_completed_date
    last_date = _get_user_date(user, "last_daily_task_date")

    last_reset = _get_user_date(user, "last_freeze_reset_date")
    freezes = _get_user_int(user, "streak_freezes_remaining", 0)

    if last_reset:
        days_since_reset = (today - last_reset).days
        if days_since_reset >= 7 and today.weekday() == 0:
            freezes = 1
            _set_user_date(user, "last_freeze_reset_date", today)
    else:
        _set_user_date(user, "last_freeze_reset_date", today)
        freezes = 1

    current_streak = _get_user_int(user, "current_streak", 0)
    longest_streak = _get_user_int(user, "longest_streak", 0)

    if not last_date:
        current_streak = 1
        longest_streak = max(longest_streak, 1)
    elif last_date == today:
        pass
    elif last_date == today - timedelta(days=1):
        current_streak = current_streak + 1
        longest_streak = max(longest_streak, current_streak)
    else:
        days_missed = (today - last_date).days - 1
        if days_missed == 1 and freezes > 0:
            freezes = max(freezes - 1, 0)
        else:
            current_streak = 1

    _set_user_int(user, "current_streak", current_streak)
    _set_user_int(user, "longest_streak", longest_streak)
    _set_user_int(user, "streak_freezes_remaining", freezes)
    _set_user_date(user, "last_daily_task_date", today)


def _recalc_session_aggregates(session: DailyCoachSession):
    tasks = DailyCoachTask.query.filter_by(session_id=session.id).all()

    daily_done = sum(1 for t in tasks if t.task_type == "daily" and t.is_done)
    weekly_done = any(t.task_type == "weekly" and t.is_done for t in tasks)

    session.daily_tasks_completed = daily_done
    session.weekly_task_completed = bool(weekly_done)

    total_tasks = len(tasks)
    done_tasks = sum(1 for t in tasks if t.is_done)
    session.progress_percent = int((done_tasks / total_tasks) * 100) if total_tasks > 0 else 0


# ===================================
# Profile Sync Helpers
# ===================================

def _suggest_profile_skills(user: User, task: DailyCoachTask, skill_tags: list, commit: bool = True):
    """
    Creates ProfileSkillSuggestion records (pending) for up to 5 tags.
    commit=True keeps legacy behavior; commit=False allows outer txn to commit.
    """
    if not ProfileSkillSuggestion:
        return

    for skill_name in (skill_tags or [])[:5]:
        if not skill_name or not isinstance(skill_name, str):
            continue

        try:
            existing = ProfileSkillSuggestion.query.filter_by(
                user_id=user.id,
                skill_name=skill_name,
            ).first()
            if existing:
                continue
        except Exception:
            continue

        try:
            suggestion = ProfileSkillSuggestion(
                user_id=user.id,
                source_type='coach_task',
                source_id=task.id,
                skill_name=skill_name,
                skill_category=getattr(task, "category", None),
                proficiency_level='Beginner',
                status='pending',
                context_note=f"From completing: {task.title}",
            )
            db.session.add(suggestion)
        except Exception:
            pass

    if not commit:
        return

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


# ===================================
# Session Selection Helpers (FIXED)
# ===================================

def _pick_current_week_session(cycle_sessions: list[DailyCoachSession], today: date) -> DailyCoachSession | None:
    """
    Robust selection:
    - Prefer the latest session where session_date <= today
    - Else fallback to earliest session
    """
    if not cycle_sessions:
        return None

    try:
        sorted_sessions = sorted(
            cycle_sessions,
            key=lambda s: (s.session_date or date.min, s.day_index or 0)
        )
    except Exception:
        sorted_sessions = cycle_sessions

    eligible = []
    for s in sorted_sessions:
        try:
            if s.session_date and s.session_date <= today:
                eligible.append(s)
        except Exception:
            continue

    if eligible:
        return eligible[-1]
    return sorted_sessions[0]


def _compute_today_day_number_in_plan(session: DailyCoachSession, today: date) -> int | None:
    """
    Day-number used by your task rows: (week-1)*7 + day_in_week
    where day_in_week is 1..7 relative to session.session_date.
    """
    try:
        if not session.session_date or not session.day_index:
            return None
        delta_days = (today - session.session_date).days
        if delta_days < 0:
            delta_days = 0
        day_in_week = (delta_days % 7) + 1
        return (int(session.day_index) - 1) * 7 + int(day_in_week)
    except Exception:
        return None


# ===================================
# ROUTES
# ===================================

@coach_bp.route("/plans", methods=["GET"], endpoint="manage_plans")
@login_required
def manage_plans():
    path_type = _normalize_path_type(request.args.get("path_type") or request.form.get("path_type"))
    is_pro_user = _current_is_pro_user()
    profile_snapshot = load_profile_snapshot(current_user)

    _auto_promote_locked_snapshots(current_user.id, path_type)

    saved_plans = _get_saved_plans(current_user.id, path_type)
    active_count = 0
    if CoachSavedPlan:
        try:
            try:
                active_count = CoachSavedPlan.query.filter_by(user_id=current_user.id, is_deleted=False).count()
            except Exception:
                active_count = CoachSavedPlan.query.filter_by(user_id=current_user.id).count()
        except Exception:
            active_count = len(saved_plans)

    today = _today_date()
    month_cycle_id = f"user_{current_user.id}_path_{path_type}_month_{today.year}_{today.month:02d}"
    has_active_cycle = bool(
        DailyCoachSession.query.filter_by(user_id=current_user.id, month_cycle_id=month_cycle_id).first()
    )

    return render_template(
        "coach/plans.html",
        path_type=path_type,
        is_pro_user=is_pro_user,
        profile_snapshot=profile_snapshot,
        saved_plans=saved_plans,
        active_count=active_count,
        max_allowed=3,
        has_active_cycle=has_active_cycle,
        month_cycle_id=month_cycle_id,
    )


@coach_bp.route("/plans/promote/<int:snapshot_id>", methods=["POST"], endpoint="promote_snapshot")
@login_required
def promote_snapshot(snapshot_id: int):
    path_type = _normalize_path_type(request.form.get("path_type") or request.args.get("path_type"))

    if not CoachSavedPlan:
        flash("Saved plans not enabled yet.", "warning")
        return redirect(url_for("coach.manage_plans", path_type=path_type))

    snap = DreamPlanSnapshot.query.get(snapshot_id)
    if not snap or snap.user_id != current_user.id:
        flash("Snapshot not found.", "warning")
        return redirect(url_for("coach.manage_plans", path_type=path_type))

    plan_json = _parse_plan_json(getattr(snap, "plan_json", "") or "")
    if not _is_locked_plan_json(plan_json):
        flash("This Dream Plan is not locked yet. Select projects and lock it first.", "warning")
        return redirect(url_for("dream.result", snapshot_id=snapshot_id))

    if _is_promotion_blocked_plan_json(plan_json):
        flash("You deleted this plan from Coach earlier. Regenerate a new Dream plan to save again.", "info")
        return redirect(url_for("coach.manage_plans", path_type=path_type))

    if _count_active_saved_plans(current_user.id) >= 3:
        flash("You already have 3 saved plans. Delete one to free up a slot.", "info")
        return redirect(url_for("coach.manage_plans", path_type=path_type))

    if _saved_plan_exists_for_snapshot(current_user.id, snap.id):
        flash("This plan is already saved in Weekly Coach.", "info")
        return redirect(url_for("coach.manage_plans", path_type=path_type))

    created = _create_saved_plan_from_snapshot(snap, path_type)
    if created:
        flash("✅ Plan saved to Weekly Coach.", "success")
    else:
        flash("Could not save this plan. Try again.", "danger")

    return redirect(url_for("coach.manage_plans", path_type=path_type))


@coach_bp.route("/plans/delete/<int:plan_id>", methods=["POST"], endpoint="delete_plan")
@login_required
def delete_plan(plan_id):
    path_type = _normalize_path_type(request.form.get("path_type") or request.args.get("path_type"))
    plan = _load_saved_plan_or_none(current_user.id, plan_id)
    if not plan:
        flash("Plan not found.", "warning")
        return redirect(url_for("coach.manage_plans", path_type=path_type))

    snapshot_id = None
    try:
        snapshot_id = getattr(plan, "dream_snapshot_id", None)
    except Exception:
        snapshot_id = None

    try:
        if snapshot_id:
            snap = DreamPlanSnapshot.query.filter_by(id=snapshot_id, user_id=current_user.id).first()
            if snap:
                pj = _parse_plan_json(getattr(snap, "plan_json", "") or "")
                pj["_coach_deleted_at"] = datetime.utcnow().isoformat()
                try:
                    snap.plan_json = json.dumps(pj, ensure_ascii=False)
                except Exception:
                    pass

        db.session.delete(plan)
        db.session.commit()
        flash("✅ Plan deleted permanently. You can now lock a new Dream plan.", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to delete plan. Try again.", "danger")

    return redirect(url_for("coach.manage_plans", path_type=path_type))


@coach_bp.route("/", methods=["GET"], endpoint="index")
@login_required
def index():
    path_type = _normalize_path_type(request.args.get("path_type") or request.form.get("path_type"))

    is_pro_user = _current_is_pro_user()
    profile_snapshot = load_profile_snapshot(current_user)
    today = _today_date()

    _auto_promote_locked_snapshots(current_user.id, path_type)

    user_streak = _get_user_int(current_user, "current_streak", 0)
    user_ready_score = _get_user_int(current_user, "ready_score", 0)
    longest_streak = _get_user_int(current_user, "longest_streak", 0)
    weekly_milestones = _get_user_int(current_user, "weekly_milestones_completed", 0)

    month_cycle_id = f"user_{current_user.id}_path_{path_type}_month_{today.year}_{today.month:02d}"

    cycle_sessions = (
        DailyCoachSession.query.filter_by(
            user_id=current_user.id,
            month_cycle_id=month_cycle_id,
        )
        .order_by(DailyCoachSession.day_index)
        .all()
    )

    today_daily_task = None
    current_week_session = None
    current_weekly_task = None

    if cycle_sessions:
        current_week_session = _pick_current_week_session(cycle_sessions, today)

        if current_week_session:
            day_number = _compute_today_day_number_in_plan(current_week_session, today)

            if day_number is not None:
                today_daily_task = (
                    DailyCoachTask.query.filter_by(
                        session_id=current_week_session.id,
                        task_type='daily',
                        day_number=day_number,
                    ).first()
                )

            current_weekly_task = (
                DailyCoachTask.query.filter_by(
                    session_id=current_week_session.id,
                    task_type='weekly',
                ).order_by(DailyCoachTask.sort_order.asc()).first()
            )

    pending_skills = []
    if ProfileSkillSuggestion:
        try:
            pending_skills = (
                ProfileSkillSuggestion.query.filter_by(
                    user_id=current_user.id,
                    status='pending',
                )
                .order_by(ProfileSkillSuggestion.suggested_at.desc())
                .limit(5)
                .all()
            )
        except Exception:
            pass

    saved_plans = _get_saved_plans(current_user.id, path_type)

    return render_template(
        "coach/index.html",
        path_type=path_type,
        is_pro_user=is_pro_user,
        profile_snapshot=profile_snapshot,
        today=today,

        user_streak=user_streak,
        user_ready_score=user_ready_score,
        longest_streak=longest_streak,
        weekly_milestones=weekly_milestones,

        cycle_sessions=cycle_sessions,
        today_daily_task=today_daily_task,
        current_week_session=current_week_session,
        current_weekly_task=current_weekly_task,
        month_cycle_id=month_cycle_id,

        pending_skills=pending_skills,

        saved_plans=saved_plans,
        max_allowed=3,
    )


@coach_bp.route("/start", methods=["POST"], endpoint="start")
@login_required
def start():
    path_type = _normalize_path_type(request.form.get("path_type") or request.args.get("path_type"))

    plan_id_raw = (request.form.get("plan_id") or "").strip()
    plan_id = None
    try:
        plan_id = int(plan_id_raw) if plan_id_raw else None
    except Exception:
        plan_id = None

    if not _is_email_verified(current_user):
        flash("Please verify your email before using Weekly Coach.", "warning")
        return redirect(url_for("auth.otp_request"))

    if not _current_is_pro_user():
        flash("Weekly Coach is available for Pro ⭐ members only.", "warning")
        return redirect(url_for("billing.index"))

    today = _today_date()
    month_cycle_id = f"user_{current_user.id}_path_{path_type}_month_{today.year}_{today.month:02d}"

    existing_cycle = DailyCoachSession.query.filter_by(
        user_id=current_user.id,
        month_cycle_id=month_cycle_id,
    ).first()

    if existing_cycle:
        flash("You already have a plan for this month! Abort it if you want to start a different one.", "info")
        return redirect(url_for("coach.manage_plans", path_type=path_type))

    plan_json = {}
    plan_title = None
    plan_digest = None
    saved_plan_obj = None

    if plan_id and CoachSavedPlan:
        saved = _load_saved_plan_or_none(current_user.id, plan_id)
        if not saved:
            flash("Selected plan not found. Please pick a valid plan.", "warning")
            return redirect(url_for("coach.manage_plans", path_type=path_type))

        saved_plan_obj = saved
        plan_json = _parse_plan_json(saved.plan_json)
        plan_title = getattr(saved, "title", None)

        try:
            plan_digest = str(saved.dream_snapshot.inputs_digest) if saved.dream_snapshot else None
        except Exception:
            plan_digest = None

        try:
            if (saved.path_type or "job") != path_type:
                path_type = saved.path_type or path_type
        except Exception:
            pass

    if not plan_json:
        latest_snapshot = (
            DreamPlanSnapshot.query.filter_by(user_id=current_user.id, path_type=path_type)
            .order_by(DreamPlanSnapshot.created_at.desc())
            .first()
        )
        if not latest_snapshot:
            flash("No Dream Plan found. Please create and lock one first.", "warning")
            return redirect(url_for("dream.index", path_type=path_type))

        plan_json = _parse_plan_json(latest_snapshot.plan_json)
        plan_title = latest_snapshot.plan_title
        plan_digest = latest_snapshot.inputs_digest

    ctx = _extract_dream_context_from_plan_json(plan_json)

    if not ctx["is_locked"]:
        flash("Please lock your Dream Plan first (select projects).", "warning")
        snap_id = None
        try:
            snap_id = plan_json.get("_snapshot_id") or plan_json.get("snapshot_id")
        except Exception:
            snap_id = None
        if snap_id:
            return redirect(url_for("dream.result", snapshot_id=snap_id))
        return redirect(url_for("dream.index", path_type=path_type))

    coach_plan = ctx["coach_plan"]
    if not coach_plan or not coach_plan.get("weeks"):
        flash("No Coach plan found inside this saved plan. Regenerate + lock Dream Plan again.", "danger")
        return redirect(url_for("dream.index", path_type=path_type))

    weeks = coach_plan.get("weeks", [])
    if not isinstance(weeks, list) or not weeks:
        flash("Coach plan has no weeks.", "danger")
        return redirect(url_for("dream.index", path_type=path_type))

    target_lpa = ctx["target_lpa"]

    try:
        for week_data in weeks[:12]:
            if not isinstance(week_data, dict):
                continue

            week_num = week_data.get("week_num")
            if not week_num or int(week_num) < 1:
                continue
            week_num = int(week_num)

            theme = week_data.get("theme", f"Week {week_num}")

            daily_tasks_data = week_data.get("daily_tasks", [])
            if not isinstance(daily_tasks_data, list):
                daily_tasks_data = []

            weekly_tasks_data = week_data.get("weekly_tasks", [])
            if not isinstance(weekly_tasks_data, list):
                weekly_tasks_data = []

            session = DailyCoachSession(
                user_id=current_user.id,
                path_type=path_type,
                session_date=today + timedelta(weeks=week_num - 1),
                day_index=week_num,
                month_cycle_id=month_cycle_id,
                target_lpa=target_lpa,
                is_closed=False,
                ai_note=theme,
                daily_tasks_completed=0,
                weekly_task_completed=False,
                progress_percent=0,
            )

            try:
                if hasattr(session, "plan_digest"):
                    session.plan_digest = plan_digest
                if hasattr(session, "plan_title"):
                    session.plan_title = plan_title
                if saved_plan_obj is not None:
                    if hasattr(session, "saved_plan_id"):
                        session.saved_plan_id = getattr(saved_plan_obj, "id", None)
                    if hasattr(session, "coach_saved_plan_id"):
                        session.coach_saved_plan_id = getattr(saved_plan_obj, "id", None)
            except Exception:
                pass

            db.session.add(session)
            db.session.flush()

            # Daily tasks (keep 2 per week)
            for idx, daily_data in enumerate(daily_tasks_data[:2]):
                if not isinstance(daily_data, dict):
                    continue
                day_num = daily_data.get("day", idx + 1)
                try:
                    day_num = int(day_num)
                except Exception:
                    day_num = idx + 1

                daily_task = DailyCoachTask(
                    session_id=session.id,
                    task_type='daily',
                    week_number=week_num,
                    day_number=(week_num - 1) * 7 + day_num,
                    title=daily_data.get("title", f"Day {day_num} Task"),
                    detail=daily_data.get("description", ""),
                    category=daily_data.get("category", "general"),
                    estimated_time_minutes=int(daily_data.get("minutes", 15) or 15),
                    target_lpa_level=target_lpa,
                    is_done=False,
                    sort_order=idx,
                )
                db.session.add(daily_task)

            # Weekly tasks (DevLog-gated)
            for idx, weekly_data in enumerate(weekly_tasks_data[:3]):
                if not isinstance(weekly_data, dict):
                    continue

                task_category = weekly_data.get("category", "Build")
                skill_tags = weekly_data.get("skill_tags", [])
                if not isinstance(skill_tags, list):
                    skill_tags = []

                est_hours = weekly_data.get("estimated_hours", 8)
                try:
                    est_hours = int(est_hours)
                except Exception:
                    est_hours = 8

                weekly_task = DailyCoachTask(
                    session_id=session.id,
                    task_type='weekly',
                    week_number=week_num,
                    day_number=None,
                    title=weekly_data.get("title", f"Week {week_num} Task {idx+1}"),
                    detail=weekly_data.get("description", ""),
                    category=weekly_data.get("category", "project"),

                    task_category=task_category,
                    tips=weekly_data.get("tips"),
                    skill_tags=skill_tags if skill_tags else None,
                    sync_to_profile=True if skill_tags else False,

                    estimated_time_minutes=est_hours * 60,
                    target_lpa_level=target_lpa,
                    milestone_badge=f"{task_category} Master",
                    is_done=False,
                    sort_order=10 + idx,
                )
                db.session.add(weekly_task)

        db.session.commit()
        flash("✅ Execution plan created! Start completing tasks to build streak + momentum.", "success")
        return redirect(url_for("coach.index", path_type=path_type))

    except Exception:
        current_app.logger.exception("Coach: task creation failed")
        db.session.rollback()
        flash("Failed to create Coach plan. Please try again.", "danger")
        return redirect(url_for("coach.manage_plans", path_type=path_type))


@coach_bp.route("/abort", methods=["POST"], endpoint="abort_plan")
@login_required
def abort_plan():
    path_type = _normalize_path_type(request.form.get("path_type") or request.args.get("path_type"))
    month_cycle_id = request.form.get("month_cycle_id", "").strip()

    if not month_cycle_id:
        flash("Invalid cycle ID.", "danger")
        return redirect(url_for("coach.index", path_type=path_type))

    sessions_to_delete = (
        DailyCoachSession.query.filter_by(
            user_id=current_user.id,
            month_cycle_id=month_cycle_id,
        ).all()
    )

    if not sessions_to_delete:
        flash("No active plan found to abort.", "warning")
        return redirect(url_for("coach.index", path_type=path_type))

    try:
        for session in sessions_to_delete:
            DailyCoachTask.query.filter_by(session_id=session.id).delete()

        for session in sessions_to_delete:
            db.session.delete(session)

        db.session.commit()

        flash(
            f"✅ Plan aborted successfully. {len(sessions_to_delete)} weeks removed. You can start fresh anytime!",
            "success"
        )
    except Exception:
        current_app.logger.exception("Coach: abort plan failed")
        db.session.rollback()
        flash("Failed to abort plan. Please try again.", "danger")

    return redirect(url_for("coach.index", path_type=path_type))


@coach_bp.route("/task/<int:task_id>/complete", methods=["POST"], endpoint="complete_task")
@login_required
def complete_task(task_id):
    """
    ✅ ENFORCEMENT:
    - If session is in the future -> blocked
    - If session is closed -> blocked
    - Weekly tasks cannot be toggled here -> DevLog only
    """
    task = DailyCoachTask.query.get_or_404(task_id)
    session = DailyCoachSession.query.get_or_404(task.session_id)

    if session.user_id != current_user.id:
        abort(403)

    today = _today_date()

    # Block actions on future/closed sessions
    if _session_actions_locked(session, today):
        msg = "This week is locked. You can't complete tasks yet."
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"ok": False, "message": msg}), 200
        flash(msg, "warning")
        return redirect(request.referrer or url_for("coach.session", session_id=session.id))

    # Weekly tasks must go through DevLog
    if (task.task_type or "daily") == "weekly":
        msg = "Weekly tasks require a DevLog (proof-of-work) to complete."
        devlog_url = url_for("coach.devlog", task_id=task.id)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"ok": False, "message": msg, "devlog_url": devlog_url}), 200
        flash(msg, "info")
        return redirect(devlog_url)

    was_done = bool(task.is_done)
    task.is_done = not task.is_done
    task.completed_at = datetime.utcnow() if task.is_done else None

    # Daily streak + ready score
    if task.is_done and not was_done:
        _update_user_streak(current_user, today)
        ready_score = _get_user_int(current_user, "ready_score", 0)
        _set_user_int(current_user, "ready_score", ready_score + 1)

    elif (not task.is_done) and was_done:
        ready_score = _get_user_int(current_user, "ready_score", 0)
        _set_user_int(current_user, "ready_score", max(ready_score - 1, 0))

    _recalc_session_aggregates(session)
    db.session.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            "ok": True,
            "task_id": task_id,
            "is_done": task.is_done,
            "user_streak": _get_user_int(current_user, "current_streak", 0),
            "user_ready_score": _get_user_int(current_user, "ready_score", 0),
            "session_progress": session.progress_percent,
        })

    return redirect(request.referrer or url_for("coach.index"))


@coach_bp.route("/session/<int:session_id>", methods=["GET"], endpoint="session")
@login_required
def session(session_id):
    session = DailyCoachSession.query.get_or_404(session_id)

    if session.user_id != current_user.id:
        abort(403)

    tasks = (
        DailyCoachTask.query.filter_by(session_id=session.id)
        .order_by(DailyCoachTask.sort_order)
        .all()
    )

    daily_tasks = [t for t in tasks if (t.task_type or "daily") == 'daily']
    weekly_tasks = [t for t in tasks if (t.task_type or "daily") == 'weekly']

    path_type = session.path_type or 'job'
    is_pro_user = _current_is_pro_user()
    profile_snapshot = load_profile_snapshot(current_user)
    streak_count = _get_user_int(current_user, "current_streak", 0)

    # Compute current active week index for this month cycle (for UI)
    today = _today_date()
    current_week_index = None
    try:
        if session.month_cycle_id:
            cycle_sessions = (
                DailyCoachSession.query.filter_by(
                    user_id=current_user.id,
                    month_cycle_id=session.month_cycle_id,
                )
                .order_by(DailyCoachSession.day_index.asc())
                .all()
            )
            cur = _pick_current_week_session(cycle_sessions, today) if cycle_sessions else None
            current_week_index = getattr(cur, "day_index", None) if cur else None
    except Exception:
        current_week_index = None

    # Time-lock only for future sessions (closed is separate, already passed as session_is_closed)
    session_locked = _session_time_locked(session, today)

    return render_template(
        "coach/session.html",
        session=session,
        tasks=tasks,
        daily_tasks=daily_tasks,
        weekly_tasks=weekly_tasks,
        path_type=path_type,
        is_pro_user=is_pro_user,
        profile_snapshot=profile_snapshot,
        session_date=session.session_date.isoformat() if session.session_date else None,
        session_is_closed=bool(session.is_closed),
        plan_title=getattr(session, 'plan_title', None),
        day_index=session.day_index,
        streak_count=streak_count,
        phase_label=f"Week {session.day_index}",
        week_label=f"Week {session.day_index}",
        session_scope_label="This week",

        # ✅ NEW for template locking
        session_locked=session_locked,
        current_week_index=current_week_index,
        min_chars=DEVLOG_MIN_CHARS_DEFAULT,
    )


@coach_bp.route("/devlog/<int:task_id>", methods=["GET", "POST"], endpoint="devlog")
@login_required
def devlog(task_id):
    """
    ✅ ENFORCEMENT:
    - DevLog only for weekly tasks
    - DevLog POST blocked if future week / closed
    - Min chars proof required
    - UPSERT LearningLog (no duplicates)
    - Mark weekly task complete ONLY here
    """
    if not LearningLog:
        flash("DevLog feature not available yet.", "warning")
        return redirect(url_for("coach.index"))

    task = DailyCoachTask.query.get_or_404(task_id)
    session = DailyCoachSession.query.get_or_404(task.session_id)

    if session.user_id != current_user.id:
        abort(403)

    if (task.task_type or "daily") != 'weekly':
        flash("DevLog is only for Weekly Tasks.", "warning")
        return redirect(url_for("coach.session", session_id=session.id))

    today = _today_date()

    # Load existing log (used for GET and UPSERT on POST)
    existing_log = None
    try:
        existing_log = LearningLog.query.filter_by(task_id=task.id, user_id=current_user.id).first()
    except Exception:
        existing_log = None

    if request.method == "POST":
        # Block posting proof to future/closed sessions
        if _session_actions_locked(session, today):
            flash("This week is locked. You can't submit DevLog yet.", "warning")
            return redirect(url_for("coach.session", session_id=session.id))

        min_chars = DEVLOG_MIN_CHARS_DEFAULT
        total_chars = _devlog_total_chars(request.form)
        if total_chars < min_chars:
            flash(f"Please add more detail to your DevLog ({total_chars}/{min_chars} chars).", "warning")
            return redirect(url_for("coach.devlog", task_id=task.id))

        try:
            # Upsert LearningLog
            if existing_log:
                log = existing_log
            else:
                log = LearningLog(
                    task_id=task.id,
                    user_id=current_user.id,
                    session_id=session.id,
                )
                db.session.add(log)

            log.what_i_learned = (request.form.get("what_i_learned", "") or "").strip()
            log.what_i_built = (request.form.get("what_i_built", "") or "").strip()
            log.challenges_faced = (request.form.get("challenges_faced", "") or "").strip()
            log.next_steps = (request.form.get("next_steps", "") or "").strip()
            log.github_link = (request.form.get("github_link", "") or "").strip()
            log.demo_link = (request.form.get("demo_link", "") or "").strip()

            try:
                log.time_spent_minutes = int(request.form.get("time_spent_minutes", 0) or 0)
            except Exception:
                pass

            try:
                log.difficulty_rating = int(request.form.get("difficulty_rating", 0) or 0)
            except Exception:
                pass

            # Mark weekly task complete (no double-count)
            was_done = bool(task.is_done)
            task.is_done = True
            if not task.completed_at:
                task.completed_at = datetime.utcnow()

            if not was_done:
                ready_score = _get_user_int(current_user, "ready_score", 0)
                milestones = _get_user_int(current_user, "weekly_milestones_completed", 0)
                _set_user_int(current_user, "ready_score", ready_score + 14)
                _set_user_int(current_user, "weekly_milestones_completed", milestones + 1)

            _recalc_session_aggregates(session)
            db.session.commit()

            flash("✅ DevLog saved! Weekly task marked complete.", "success")

            # Suggest skills (no nested commit)
            try:
                if getattr(task, "sync_to_profile", False) and getattr(task, "skill_tags", None):
                    skill_tags = task.skill_tags
                    if isinstance(skill_tags, str):
                        try:
                            skill_tags = json.loads(skill_tags)
                        except Exception:
                            skill_tags = []
                    if isinstance(skill_tags, list) and skill_tags:
                        _suggest_profile_skills(current_user, task, skill_tags, commit=True)
            except Exception:
                pass

            return redirect(url_for("coach.session", session_id=session.id))

        except Exception:
            current_app.logger.exception("DevLog save failed")
            db.session.rollback()
            flash("Failed to save DevLog. Please try again.", "danger")
            return redirect(url_for("coach.devlog", task_id=task.id))

    return render_template(
        "coach/devlog.html",
        task=task,
        session=session,
        existing_log=existing_log,
        min_chars=DEVLOG_MIN_CHARS_DEFAULT,
    )


@coach_bp.route("/skills/accept/<int:suggestion_id>", methods=["POST"], endpoint="accept_skill")
@login_required
def accept_skill(suggestion_id):
    if not ProfileSkillSuggestion:
        flash("Skill suggestions not available yet.", "warning")
        return redirect(url_for("coach.index"))

    suggestion = ProfileSkillSuggestion.query.get_or_404(suggestion_id)
    if suggestion.user_id != current_user.id:
        abort(403)

    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        flash("Profile not found.", "danger")
        return redirect(url_for("coach.index"))

    try:
        existing_skills = []
        if hasattr(profile, 'skills') and profile.skills:
            if isinstance(profile.skills, list):
                existing_skills = profile.skills
            elif isinstance(profile.skills, str):
                try:
                    existing_skills = json.loads(profile.skills)
                except Exception:
                    pass

        skill_names = []
        for s in existing_skills:
            if isinstance(s, dict):
                skill_names.append(s.get("name", ""))
            elif isinstance(s, str):
                skill_names.append(s)

        if suggestion.skill_name not in skill_names:
            new_skill = {
                "name": suggestion.skill_name,
                "category": suggestion.skill_category or "Other",
                "proficiency": suggestion.proficiency_level or "Beginner",
            }
            existing_skills.append(new_skill)
            profile.skills = existing_skills

            suggestion.status = 'accepted'
            suggestion.responded_at = datetime.utcnow()

            db.session.commit()
            flash(f"✅ Added '{suggestion.skill_name}' to your profile!", "success")
        else:
            suggestion.status = 'already_has'
            suggestion.responded_at = datetime.utcnow()
            db.session.commit()
            flash(f"You already have '{suggestion.skill_name}' in your profile.", "info")

    except Exception:
        current_app.logger.exception("Profile skill add failed")
        db.session.rollback()
        flash("Failed to add skill to profile.", "danger")

    return redirect(request.referrer or url_for("coach.index"))


@coach_bp.route("/skills/reject/<int:suggestion_id>", methods=["POST"], endpoint="reject_skill")
@login_required
def reject_skill(suggestion_id):
    if not ProfileSkillSuggestion:
        flash("Skill suggestions not available yet.", "warning")
        return redirect(url_for("coach.index"))

    suggestion = ProfileSkillSuggestion.query.get_or_404(suggestion_id)
    if suggestion.user_id != current_user.id:
        abort(403)

    suggestion.status = 'rejected'
    suggestion.responded_at = datetime.utcnow()
    db.session.commit()

    flash("Skill suggestion dismissed.", "info")
    return redirect(request.referrer or url_for("coach.index"))
