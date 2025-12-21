# modules/coach/routes.py - DUAL-TRACK VERSION (MODEL-CORRECT + TEMPLATE-COMPAT)
"""
Weekly Coach with Dual-Track System:
- Daily Tasks (Maintenance): 5-15 min, build streak, +1 point each
- Weekly Tasks (Momentum): 3-5 hours, build portfolio, +10 points + 4 badge
- LPA-aligned difficulty (12/24/48 LPA)
- Project-specific tasks from Dream Planner (fallback if AI plan not available)
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
import json
from typing import Any, Tuple

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
    DreamPlanProject,
    ProjectMilestone,
    ProjectSubtask,
    User,
    db,
)
from modules.common.profile_loader import load_profile_snapshot

# ✅ Dual-track month plan generator (one call) – optional
try:  # make this safe if module/function is missing
    from modules.common.ai import generate_dualtrack_month_plan  # type: ignore
except Exception:  # pragma: no cover
    generate_dualtrack_month_plan = None  # type: ignore

# ✅ Credits (tuple-safe can_afford + correct refund amount)
from modules.credits.engine import can_afford, deduct_pro, refund

# Optional helper (if you added it in credits engine). We keep this import defensive.
try:
    from modules.credits.engine import get_feature_cost_amount
except Exception:  # pragma: no cover
    get_feature_cost_amount = None  # type: ignore


coach_bp = Blueprint(
    "coach",
    __name__,
    template_folder="../../templates/coach",
)


# ===================================
# BASIC HELPERS
# ===================================

def _current_is_pro_user() -> bool:
    """Helper: determine if user is Pro based on flags + subscription_status."""
    if not getattr(current_user, "is_authenticated", False):
        return False
    if bool(getattr(current_user, "is_pro", False)):
        return True
    status = (getattr(current_user, "subscription_status", "free") or "free").lower()
    return status == "pro"


def _normalize_path_type(raw: str | None) -> str:
    """
    Coach is anchored to a path type:
      - 'job'     → Dream Job plan
      - 'startup' → Dream Startup plan
    """
    v = (raw or "").strip().lower()
    if v == "startup":
        return "startup"
    return "job"


def _today_date() -> date:
    """For now we treat 'today' as UTC date."""
    return datetime.utcnow().date()


def _week_start(d: date) -> date:
    """Returns the Monday of the week for the given date."""
    return d - timedelta(days=d.weekday())


def _is_email_verified(user: User) -> bool:
    """
    Platform rule: user must have email_verified = True to run AI tools.
    Some older code uses 'verified'. We support both.
    """
    return bool(getattr(user, "email_verified", False) or getattr(user, "verified", False))


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _get_user_int(user: User, field: str, default: int = 0) -> int:
    return _safe_int(getattr(user, field, default), default)


def _set_user_int(user: User, field: str, value: int) -> None:
    # We avoid assuming the column exists. Setting may still fail at commit if DB is behind.
    try:
        setattr(user, field, int(value))
    except Exception:
        # swallow safely; commit wrapper will handle if needed
        pass


def _get_user_date(user: User, field: str) -> date | None:
    try:
        v = getattr(user, field, None)
        return v if isinstance(v, date) else None
    except Exception:
        return None


def _set_user_date(user: User, field: str, value: date | None) -> None:
    try:
        setattr(user, field, value)
    except Exception:
        pass


def _safe_commit(context: str) -> bool:
    """
    Commit with rollback safety. If DB schema is behind models,
    this prevents the app from getting stuck in a broken session.
    """
    try:
        db.session.commit()
        return True
    except Exception:
        current_app.logger.exception(f"Coach: commit failed ({context}). Possible schema mismatch.")
        db.session.rollback()
        flash(
            "We hit a database mismatch while saving Coach progress. "
            "Your model may have fields not yet migrated. Please run migrations or guard columns.",
            "danger",
        )
        return False


def _app_day(user: User, now_utc: datetime | None = None) -> date:
    """
    4:00 AM local cutoff day:
      - finishing at 1 AM counts for the previous "app day"
    If timezone is unavailable or pytz isn't installed, fallback to UTC with 4-hour shift.
    """
    now_utc = now_utc or datetime.utcnow()
    tz_name = getattr(user, "timezone", None)

    if not tz_name:
        shifted = now_utc - timedelta(hours=4)
        return shifted.date()

    try:
        import pytz
        tz = pytz.timezone(tz_name)
        local = pytz.utc.localize(now_utc).astimezone(tz)
        shifted = local - timedelta(hours=4)
        return shifted.date()
    except Exception:
        shifted = now_utc - timedelta(hours=4)
        return shifted.date()


def _pick_best_lpa_from_probabilities(probs: dict) -> str:
    """Pick the LPA tier with the highest probability (fallback)."""
    try:
        p12 = int(probs.get("lpa_12", 0) or 0)
        p24 = int(probs.get("lpa_24", 0) or 0)
        p48 = int(probs.get("lpa_48", 0) or 0)
    except Exception:
        return "12"
    return max([("12", p12), ("24", p24), ("48", p48)], key=lambda x: x[1])[0]


def _get_dream_plan_context(user_id: int, path_type: str) -> dict:
    """
    Load latest Dream Plan for LPA + selected projects.
    Returns dict with:
      - target_lpa
      - selected_projects
      - timeline_months
      - dream_plan (full JSON)
      - plan_title
      - snapshot (DreamPlanSnapshot or None)
    """
    latest_snapshot = (
        DreamPlanSnapshot.query.filter_by(user_id=user_id, path_type=path_type)
        .order_by(DreamPlanSnapshot.created_at.desc())
        .first()
    )

    if not latest_snapshot:
        return {
            "target_lpa": "12",
            "selected_projects": [],
            "timeline_months": 6,
            "dream_plan": None,
            "plan_title": None,
            "snapshot": None,
        }

    try:
        plan_json = json.loads(latest_snapshot.plan_json or "{}")
    except Exception:
        plan_json = {}

    input_block = plan_json.get("input", {}) if isinstance(plan_json.get("input", {}), dict) else {}

    # ✅ Prefer Dream Planner input key
    target_lpa = None
    if "target_salary_lpa" in input_block:
        target_lpa = str(input_block.get("target_salary_lpa") or "").strip()

    # Backward compat
    if not target_lpa and "target_lpa" in input_block:
        target_lpa = str(input_block.get("target_lpa") or "").strip()

    # Fallback: pick from probabilities
    if not target_lpa:
        probs = plan_json.get("probabilities") or {}
        target_lpa = _pick_best_lpa_from_probabilities(probs) if isinstance(probs, dict) else "12"

    if target_lpa not in ("12", "24", "48"):
        target_lpa = "12"

    selected_projects = plan_json.get("selected_projects", [])
    if not isinstance(selected_projects, list):
        selected_projects = []

    if not selected_projects:
        resources = plan_json.get("resources") or {}
        if isinstance(resources, dict):
            mini_projects = resources.get("mini_projects") or []
            if isinstance(mini_projects, list):
                selected_projects = mini_projects[:2]

    timeline_months = input_block.get("timeline_months", 6)
    try:
        timeline_months = int(timeline_months)
    except Exception:
        timeline_months = 6

    return {
        "target_lpa": target_lpa,
        "selected_projects": selected_projects,
        "timeline_months": timeline_months,
        "dream_plan": plan_json,
        "plan_title": latest_snapshot.plan_title,
        "snapshot": latest_snapshot,
    }


def _update_user_streak(user: User, task_completed_date: date):
    """
    Update user streak when a daily task is completed.

    Rules:
    - Consecutive day → increment streak
    - Same day → no change
    - Missed day(s) → reset streak (unless freeze available)
    - Weekly reset of freeze tokens (every Monday)
    """
    today = task_completed_date
    last_date = _get_user_date(user, "last_daily_task_date")

    # Weekly freeze reset (guarded)
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
    """Recompute session aggregate fields from task states."""
    tasks = DailyCoachTask.query.filter_by(session_id=session.id).all()

    daily_done = sum(1 for t in tasks if t.task_type == "daily" and t.is_done)
    weekly_done = any(t.task_type == "weekly" and t.is_done for t in tasks)

    session.daily_tasks_completed = daily_done
    session.weekly_task_completed = bool(weekly_done)

    total_tasks = len(tasks)
    done_tasks = sum(1 for t in tasks if t.is_done)
    session.progress_percent = int((done_tasks / total_tasks) * 100) if total_tasks > 0 else 0


def _apply_template_aliases(tasks: list[DailyCoachTask]):
    """
    Your templates expect some legacy names:
      - t.suggested_minutes
      - t.guide
    We attach runtime aliases without altering DB schema.
    """
    for t in tasks:
        try:
            if not hasattr(t, "suggested_minutes"):
                t.suggested_minutes = (
                    t.estimated_time_minutes
                    if getattr(t, "estimated_time_minutes", None) is not None
                    else getattr(t, "estimated_minutes", None)
                )
        except Exception:
            t.suggested_minutes = None

        try:
            if not hasattr(t, "guide"):
                t.guide = None
        except Exception:
            t.guide = None


def _can_afford_safe(user: User, feature: str, *, currency: str) -> Tuple[bool, str]:
    """
    ✅ Compatibility adapter.

    Supports BOTH engines:
      A) can_afford(...) -> bool
      B) can_afford(...) -> (bool, reason)

    Returns: (ok, reason)
    """
    res: Any = can_afford(user, feature, currency=currency)  # type: ignore[arg-type]
    if isinstance(res, tuple) and len(res) >= 1:
        ok = bool(res[0])
        reason = str(res[1]) if len(res) > 1 else ""
        return ok, reason
    return bool(res), ""


def _feature_cost_amount_safe(feature: str, currency: str) -> int:
    """
    Resolve feature cost amount safely without hardcoding.

    Prefers credits.engine.get_feature_cost_amount if available.
    Falls back to app config FEATURE_COSTS.
    """
    if callable(get_feature_cost_amount):
        try:
            return int(get_feature_cost_amount(feature, currency))  # type: ignore
        except Exception:
            pass

    try:
        cfg = current_app.config.get("FEATURE_COSTS") or {}
        raw = (cfg.get(feature) or {}) if isinstance(cfg, dict) else {}
        if currency == "silver":
            return int(raw.get("silver") or raw.get("coins_free") or 0)
        return int(raw.get("gold") or raw.get("coins_pro") or 0)
    except Exception:
        return 0


def _build_month_cycle_id(user_id: int, path_type: str, today: date) -> str:
    """Stable ID per user + path + calendar month."""
    return f"user_{user_id}_path_{path_type}_month_{today.year}_{today.month:02d}"


def _pick_project_and_milestone_for_week(
    user_id: int,
    path_type: str,
    week_index: int,
) -> tuple[DreamPlanProject | None, ProjectMilestone | None]:
    """
    Pick a DreamPlanProject + ProjectMilestone that should anchor this week.
    Uses week_start/week_end window if present; fallback to first project.
    """
    projects = (
        DreamPlanProject.query
        .filter_by(user_id=user_id, path_type=path_type)
        .all()
    )
    if not projects:
        return None, None

    def project_matches_week(p: DreamPlanProject) -> bool:
        if p.week_start is not None and week_index < p.week_start:
            return False
        if p.week_end is not None and week_index > p.week_end:
            return False
        return True

    matching = [p for p in projects if project_matches_week(p)]
    if not matching:
        matching = projects

    project = sorted(
        matching,
        key=lambda p: (p.week_start or 9999, p.id),
    )[0]

    milestones: list[ProjectMilestone] = []
    if project.project_template_id:
        milestones = (
            ProjectMilestone.query
            .filter_by(project_template_id=project.project_template_id)
            .order_by(ProjectMilestone.order.asc())
            .all()
        )

    milestone: ProjectMilestone | None = None
    if milestones:
        if (
            project.week_start is not None
            and project.week_end is not None
            and project.week_end >= project.week_start
        ):
            span = project.week_end - project.week_start + 1
            offset = max(0, min(span - 1, week_index - project.week_start))
        else:
            offset = week_index - 1
        idx = max(0, min(len(milestones) - 1, offset))
        milestone = milestones[idx]

    return project, milestone


def _generate_tasks_for_session_from_projects(
    session: DailyCoachSession,
    user: User,
    week_index: int,
    target_lpa: str | None,
) -> None:
    """
    Fallback generator using DreamPlanProject + ProjectMilestone/Subtask.

    Creates:
      - 1 weekly "Big Rock" (task_type='weekly')
      - 14 daily micro-tasks (2 per day, task_type='daily', day_number=1..7)
      - 2 support tasks (task_type='support')
    """
    project, milestone = _pick_project_and_milestone_for_week(
        user_id=user.id,
        path_type=session.path_type,
        week_index=week_index,
    )

    proj_name = (
        project.custom_title
        or (project.project_template.title if project and project.project_template else None)
    )

    # ---- Weekly Big Rock ----
    big_title = f"Week {week_index} · Move your main project forward"
    if proj_name:
        big_title = f"Week {week_index} · Progress on “{proj_name}”"

    big_detail_parts = ["Block 3–5 hours this week for deep work."]
    if milestone and milestone.title:
        big_detail_parts.append(f"Focus milestone: {milestone.title}.")
    big_detail = " ".join(big_detail_parts)

    big_rock = DailyCoachTask(
        session_id=session.id,
        title=big_title,
        detail=big_detail,
        category="Project",
        sort_order=0,
        is_done=False,
        project_id=project.id if project else None,
        milestone_id=milestone.id if milestone else None,
        milestone_title=milestone.title if milestone else None,
        milestone_step="Ship visible progress this week.",
        estimated_time_minutes=240,
        estimated_minutes=240,
        target_lpa_level=target_lpa,
        task_type="weekly",
        week_number=week_index,
        day_number=None,
        milestone_badge=(
            f"{proj_name} · {milestone.title}"
            if proj_name and milestone and milestone.title
            else None
        ),
    )
    db.session.add(big_rock)

    # ---- Daily micro-tasks (2 per day, 7 days → up to 14 tasks) ----
    subtasks: list[ProjectSubtask] = []
    if milestone:
        subtasks = (
            ProjectSubtask.query
            .filter_by(milestone_id=milestone.id)
            .order_by(ProjectSubtask.order.asc())
            .all()
        )

    daily_sort = 1
    PER_DAY = 2

    for day_num in range(1, 8):
        for slot in range(PER_DAY):
            # Try to map subtasks sequentially: 0,1 → day1; 2,3 → day2; ...
            st_idx = (day_num - 1) * PER_DAY + slot
            st: ProjectSubtask | None = subtasks[st_idx] if st_idx < len(subtasks) else None

            if st:
                title = st.title
                detail = st.description or "Push this subtask forward."
                minutes = st.minutes or 10
                tags = st.tags
                subtask_id = st.id
            else:
                # Fallback generic micro-step if not enough subtasks
                title = f"Day {day_num}: 10-minute progress on your main project"
                detail = (
                    "Open your project and do one tiny, concrete improvement "
                    "(fix one bug, refactor one function, or write one test)."
                )
                minutes = 10
                tags = []
                subtask_id = None

            task = DailyCoachTask(
                session_id=session.id,
                title=title,
                detail=detail,
                category="Daily",
                sort_order=daily_sort,
                is_done=False,
                project_id=project.id if project else None,
                milestone_id=milestone.id if milestone else None,
                subtask_id=subtask_id,
                milestone_title=milestone.title if milestone else None,
                milestone_step=f"Day {day_num} micro-step",
                estimated_time_minutes=minutes,
                estimated_minutes=minutes,
                target_lpa_level=target_lpa,
                difficulty=None,
                tags=tags,
                task_type="daily",
                week_number=week_index,
                day_number=day_num,
            )
            daily_sort += 1
            db.session.add(task)

    # ---- Optional support / checklist tasks ----
    support_templates = [
        "Review yesterday's changes & clean up anything messy.",
        "Write or update README / documentation for your project.",
    ]
    for i, text in enumerate(support_templates, start=1):
        support = DailyCoachTask(
            session_id=session.id,
            title=f"Support task {i} · Week {week_index}",
            detail=text,
            category="Support",
            sort_order=daily_sort + i,
            is_done=False,
            project_id=project.id if project else None,
            milestone_id=milestone.id if milestone else None,
            milestone_title=milestone.title if milestone else None,
            estimated_time_minutes=30,
            estimated_minutes=30,
            target_lpa_level=target_lpa,
            task_type="support",
            week_number=week_index,
            day_number=None,
        )
        db.session.add(support)


def _pick_active_session(
    sessions: list[DailyCoachSession],
    today: date,
) -> DailyCoachSession | None:
    """
    Heuristic: pick the most recent non-closed session whose session_date <= today.
    Fallback: last session by date.
    """
    if not sessions:
        return None

    best = None
    for s in sessions:
        if s.session_date and s.session_date <= today and not s.is_closed:
            if best is None or s.session_date > best.session_date:
                best = s

    if best:
        return best
    return sessions[-1]


def _get_today_daily_tasks_for_session(
    session: DailyCoachSession,
    today: date,
    limit: int = 2,
) -> list[DailyCoachTask]:
    """
    Return up to `limit` daily micro-tasks for 'Today’s Pair'.

    Logic:
    - Compute day_number 1–7 based on session.session_date.
    - Prefer tasks with that day_number.
    - If fewer than `limit`, fill from other NOT-DONE daily tasks.
    - If still fewer, fill from remaining daily tasks (even if done) so UI still has content.
    """
    if limit <= 0:
        return []

    # Get all daily tasks, ordered
    all_daily = list(
        DailyCoachTask.query
        .filter_by(session_id=session.id, task_type="daily")
        .order_by(DailyCoachTask.day_number.asc(), DailyCoachTask.sort_order.asc())
        .all()
    )
    if not all_daily:
        return []

    # If session_date is missing, just return first N not-done tasks
    if not session.session_date:
        not_done = [t for t in all_daily if not t.is_done]
        if len(not_done) >= limit:
            return not_done[:limit]
        # mix with done to reach limit if needed
        result = list(not_done)
        for t in all_daily:
            if t not in result:
                result.append(t)
                if len(result) >= limit:
                    break
        return result[:limit]

    # Compute 1–7 day index within this session
    delta_days = (today - session.session_date).days
    if delta_days < 0:
        day_number = 1
    else:
        day_number = delta_days + 1
    if day_number > 7:
        day_number = 7

    result: list[DailyCoachTask] = []

    # 1) Prefer tasks for today's day_number
    today_candidates = [t for t in all_daily if (t.day_number or 0) == day_number]
    for t in today_candidates:
        if len(result) >= limit:
            break
        result.append(t)

    # 2) Fill with NOT-DONE tasks from other days
    if len(result) < limit:
        for t in all_daily:
            if t in result:
                continue
            if not t.is_done:
                result.append(t)
                if len(result) >= limit:
                    break

    # 3) If still short, fill with remaining tasks (even if done)
    if len(result) < limit:
        for t in all_daily:
            if t in result:
                continue
            result.append(t)
            if len(result) >= limit:
                break

    return result[:limit]


def _get_today_daily_task_for_session(
    session: DailyCoachSession,
    today: date,
) -> DailyCoachTask | None:
    """
    Backward-compatible helper: return a single 'today' daily task.

    Internally uses _get_today_daily_tasks_for_session(limit=1).
    """
    tasks = _get_today_daily_tasks_for_session(session, today, limit=1)
    return tasks[0] if tasks else None


def _generate_month_cycle_from_projects(
    user: User,
    path_type: str,
    month_cycle_id: str,
    base_date: date,
    target_lpa: str | None,
    snapshot: DreamPlanSnapshot | None,
    plan_title: str | None,
) -> list[DailyCoachSession]:
    """
    Fallback 4-week (28-day) generator using the P3 project system.
    """
    sessions: list[DailyCoachSession] = []

    if not plan_title:
        plan_title = f"{path_type.capitalize()} path · {target_lpa or '12'} LPA"

    for week in range(1, 5):
        session_date = base_date + timedelta(days=(week - 1) * 7)

        ai_note = (
            f"This week is part of your {path_type} roadmap. "
            f"Focus on moving one project milestone forward and touching it on at least 3 days."
        )

        sess = DailyCoachSession(
            user_id=user.id,
            path_type=path_type,
            session_date=session_date,
            day_index=week,
            month_cycle_id=month_cycle_id,
            target_lpa=target_lpa,
            is_closed=False,
            ai_note=ai_note,
            daily_tasks_completed=0,
            weekly_task_completed=False,
            progress_percent=0,
            plan_title=plan_title,
            plan_digest=snapshot.inputs_digest if snapshot else None,
        )
        db.session.add(sess)
        db.session.flush()

        _generate_tasks_for_session_from_projects(
            session=sess,
            user=user,
            week_index=week,
            target_lpa=target_lpa,
        )

        sessions.append(sess)

    return sessions


# ===================================
# ROUTES
# ===================================

@coach_bp.route("/", methods=["GET"], endpoint="index")
@login_required
def index():
    """
    Coach landing page.
    Shows dual-track dashboard if active month cycle exists.
    """
    path_type = _normalize_path_type(request.args.get("path_type") or request.form.get("path_type"))
    is_pro_user = _current_is_pro_user()
    profile_snapshot = load_profile_snapshot(current_user)
    today = _today_date()

    # ✅ Soft check credits
    has_gold = False
    try:
        has_gold, _reason = _can_afford_safe(current_user, "dream_planner", currency="gold")
    except Exception:
        current_app.logger.exception("Coach: can_afford check failed on index().")

    user_streak = _get_user_int(current_user, "current_streak", 0)
    user_ready_score = _get_user_int(current_user, "ready_score", 0)

    # All sessions for this path (for roadmap + today/last session)
    all_sessions = (
        DailyCoachSession.query.filter_by(user_id=current_user.id, path_type=path_type)
        .order_by(DailyCoachSession.session_date.asc(), DailyCoachSession.day_index.asc())
        .all()
    )

    last_session = all_sessions[-1] if all_sessions else None
    today_session = _pick_active_session(all_sessions, today) if all_sessions else None

    # Current month cycle (4-week / 28-day roadmap)
    month_cycle_id = _build_month_cycle_id(current_user.id, path_type, today)
    cycle_sessions = (
        DailyCoachSession.query.filter_by(
            user_id=current_user.id,
            path_type=path_type,
            month_cycle_id=month_cycle_id,
        )
        .order_by(DailyCoachSession.day_index.asc())
        .all()
    )

    # Today’s daily micro-tasks (pair)
    today_daily_tasks: list[DailyCoachTask] = []
    today_daily_task: DailyCoachTask | None = None
    daily_total = 0
    daily_completed = 0

    if today_session:
        today_daily_tasks = _get_today_daily_tasks_for_session(today_session, today, limit=2)
        today_daily_task = today_daily_tasks[0] if today_daily_tasks else None
        daily_total = len(today_daily_tasks)
        daily_completed = sum(1 for t in today_daily_tasks if t.is_done)

    current_week_session = today_session
    current_weekly_task = None
    if current_week_session:
        current_weekly_task = (
            DailyCoachTask.query.filter_by(
                session_id=current_week_session.id,
                task_type="weekly",
            )
            .order_by(DailyCoachTask.sort_order.asc())
            .first()
        )

    # Recent sessions for history + multi-phase roadmap (max 12)
    recent_sessions = (
        DailyCoachSession.query.filter_by(user_id=current_user.id, path_type=path_type)
        .order_by(DailyCoachSession.session_date.desc(), DailyCoachSession.day_index.desc())
        .limit(12)
        .all()
    )

    return render_template(
        "coach/index.html",
        path_type=path_type,
        is_pro_user=is_pro_user,
        has_gold=has_gold,
        profile_snapshot=profile_snapshot,
        today=today,
        user_streak=user_streak,
        streak_count=user_streak,
        user_ready_score=user_ready_score,
        cycle_sessions=cycle_sessions if cycle_sessions else None,
        # New: pair data
        today_daily_tasks=today_daily_tasks,
        today_daily_task=today_daily_task,  # legacy single-task usage
        daily_total=daily_total,
        daily_completed=daily_completed,
        current_week_session=current_week_session,
        current_weekly_task=current_weekly_task,
        recent_sessions=recent_sessions if recent_sessions else None,
        month_cycle_id=month_cycle_id,
        today_session=today_session,
        last_session=last_session,
        # feature_paths comes from context_processor
    )


@coach_bp.route("/start", methods=["POST"], endpoint="start")
@login_required
def start():
    """
    Generate 28-day (4-week) dual-track plan.

    Creates:
    - 4 DailyCoachSession (one per week)
    - Daily/support tasks per session (task_type='daily'/'support')
    - 4 weekly tasks (1 per week, task_type='weekly')
    """
    path_type = _normalize_path_type(request.form.get("path_type") or request.args.get("path_type"))

    if not _is_email_verified(current_user):
        flash("Please verify your email with a login code before using Weekly Coach.", "warning")
        return redirect(url_for("auth.otp_request"))

    if not _current_is_pro_user():
        flash("Weekly Coach is available for Pro ⭐ members only.", "warning")
        return redirect(url_for("billing.index"))

    try:
        can_pay, reason = _can_afford_safe(current_user, "dream_planner", currency="gold")
        if not can_pay:
            flash("You don't have enough Gold ⭐ credits to use Weekly Coach.", "warning")
            if reason:
                current_app.logger.info(f"Coach: gold gate failed: {reason}")
            return redirect(url_for("billing.index"))
    except Exception:
        current_app.logger.exception("Coach: can_afford check failed in /start.")
        flash("We could not check your Gold credits right now. Please try again.", "danger")
        return redirect(url_for("coach.index", path_type=path_type))

    today = _today_date()
    month_cycle_id = _build_month_cycle_id(current_user.id, path_type, today)

    existing_cycle = DailyCoachSession.query.filter_by(
        user_id=current_user.id,
        month_cycle_id=month_cycle_id,
        path_type=path_type,
    ).first()

    if existing_cycle:
        flash("You already have a plan for this month!", "info")
        return redirect(url_for("coach.index", path_type=path_type))

    # Charge credits up-front for this month cycle
    run_id = f"coach_{current_user.id}_{path_type}_{today.isoformat()}"
    try:
        ok = deduct_pro(current_user, "dream_planner", run_id=run_id)
        if not ok:
            flash("We couldn't deduct your Gold ⭐ credits right now. Please try again.", "danger")
            return redirect(url_for("coach.index", path_type=path_type))
    except Exception:
        current_app.logger.exception("Coach: credit deduction failed.")
        flash("We couldn't process your credits right now. Please try again.", "danger")
        return redirect(url_for("coach.index", path_type=path_type))

    dream_context = _get_dream_plan_context(current_user.id, path_type)
    target_lpa = dream_context["target_lpa"]
    dream_plan = dream_context["dream_plan"] or {}
    plan_title = dream_context.get("plan_title") or None
    snapshot = dream_context.get("snapshot")

    sessions_created: list[DailyCoachSession] = []

    # ---- Primary path: AI month planner, if available ----
    used_ai = False
    if callable(generate_dualtrack_month_plan):  # type: ignore[truthy-function]
        try:
            plan, used_live_ai = generate_dualtrack_month_plan(  # type: ignore[assignment]
                path_type=path_type,
                target_lpa=target_lpa,
                dream_plan=dream_plan,
                month_cycle=month_cycle_id,
                return_source=True,
            )
            used_ai = bool(used_live_ai)

            weeks = plan.get("weeks") or []
            month_ai_note = plan.get("ai_note") or ""

            for week_obj in weeks[:4]:
                week_num = int(week_obj.get("week_number") or 1)
                week_note = (week_obj.get("week_note") or f"Week {week_num} focus.").strip()

                session = DailyCoachSession(
                    user_id=current_user.id,
                    path_type=path_type,
                    session_date=today + timedelta(days=(week_num - 1) * 7),
                    day_index=week_num,
                    month_cycle_id=month_cycle_id,
                    target_lpa=target_lpa,
                    is_closed=False,
                    ai_note=(week_note or month_ai_note),
                    daily_tasks_completed=0,
                    weekly_task_completed=False,
                    progress_percent=0,
                    plan_title=plan_title,
                    plan_digest=snapshot.inputs_digest if snapshot else None,
                )
                db.session.add(session)
                db.session.flush()

                # Daily tasks – store with day_number 1–7 within the week
                daily_tasks = week_obj.get("daily_tasks") or []
                for dt in daily_tasks:
                    day = int(dt.get("day") or 1)
                    if day < 1:
                        day = 1
                    if day > 7:
                        day = 7

                    est = dt.get("estimated_minutes")
                    est_int = _safe_int(est, 10)

                    tags = dt.get("tags") if isinstance(dt.get("tags"), list) else None

                    task = DailyCoachTask(
                        session_id=session.id,
                        task_type="daily",
                        week_number=week_num,
                        day_number=day,
                        title=(dt.get("title") or f"Day {day} task")[:255],
                        detail=(dt.get("detail") or ""),
                        category=(dt.get("category") or "skills")[:64],
                        sort_order=day,
                        is_done=False,
                        estimated_time_minutes=est_int,
                        estimated_minutes=est_int,
                        target_lpa_level=target_lpa,
                        tags=tags,
                        milestone_title=(dt.get("milestone_title") or "")[:255] if dt.get("milestone_title") else None,
                        milestone_step=(dt.get("milestone_step") or "")[:255] if dt.get("milestone_step") else None,
                    )
                    db.session.add(task)

                # Weekly Big Rock
                wt = week_obj.get("weekly_task") or {}
                w_est_int = _safe_int(wt.get("estimated_minutes"), 240)

                weekly_task = DailyCoachTask(
                    session_id=session.id,
                    task_type="weekly",
                    week_number=week_num,
                    day_number=None,
                    title=(wt.get("title") or f"Week {week_num} milestone")[:255],
                    detail=(wt.get("detail") or ""),
                    category=(wt.get("category") or "projects")[:64],
                    sort_order=999,
                    is_done=False,
                    estimated_time_minutes=w_est_int,
                    estimated_minutes=w_est_int,
                    target_lpa_level=target_lpa,
                    milestone_badge=(wt.get("milestone_badge") or f"Week {week_num} Master")[:100],
                    milestone_title=(wt.get("milestone_title") or "")[:255] if wt.get("milestone_title") else None,
                    milestone_step=(wt.get("milestone_step") or "")[:255] if wt.get("milestone_step") else None,
                    tags=wt.get("tags") if isinstance(wt.get("tags"), list) else None,
                )
                db.session.add(weekly_task)

                sessions_created.append(session)

        except Exception:
            current_app.logger.exception("Coach: AI month plan generation failed – will fallback to project-based.")
            db.session.rollback()
            sessions_created = []

    # ---- Fallback path: P3 project-based generator ----
    if not sessions_created:
        try:
            sessions_created = _generate_month_cycle_from_projects(
                user=current_user,
                path_type=path_type,
                month_cycle_id=month_cycle_id,
                base_date=today,
                target_lpa=target_lpa,
                snapshot=snapshot,
                plan_title=plan_title,
            )
        except Exception:
            current_app.logger.exception("Coach: project-based fallback generation failed.")
            db.session.rollback()
            sessions_created = []

    # If both AI and fallback failed → refund
    if not sessions_created:
        try:
            amt = _feature_cost_amount_safe("dream_planner", "gold")
            if amt > 0:
                refund(
                    current_user,
                    "dream_planner",
                    currency="gold",
                    amount=amt,
                    run_id=run_id,
                )
            else:
                refund(
                    current_user,
                    "dream_planner",
                    currency="gold",
                    run_id=run_id,
                )
        except Exception:
            current_app.logger.exception("Coach: refund failed after generation failure.")

        flash("Coach had an error generating your plan. Credits were refunded.", "danger")
        return redirect(url_for("coach.index", path_type=path_type))

    if not _safe_commit("start_plan"):
        # If commit itself fails, refund as well
        try:
            amt = _feature_cost_amount_safe("dream_planner", "gold")
            if amt > 0:
                refund(
                    current_user,
                    "dream_planner",
                    currency="gold",
                    amount=amt,
                    run_id=run_id,
                )
            else:
                refund(
                    current_user,
                    "dream_planner",
                    currency="gold",
                    run_id=run_id,
                )
        except Exception:
            current_app.logger.exception("Coach: refund failed after commit error.")
        return redirect(url_for("coach.index", path_type=path_type))

    flash(f"✅ 28-day plan created! {target_lpa} LPA track", "success")
    return redirect(url_for("coach.index", path_type=path_type))


@coach_bp.route("/task/<int:task_id>/complete", methods=["POST"], endpoint="complete_task")
@login_required
def complete_task(task_id):
    """Toggle a task as complete/incomplete and update streak/ready score."""
    task = DailyCoachTask.query.get_or_404(task_id)
    session = DailyCoachSession.query.get_or_404(task.session_id)

    if session.user_id != current_user.id:
        abort(403)

    before = bool(task.is_done)
    task.is_done = not before
    task.completed_at = datetime.utcnow() if task.is_done else None

    ready_score = _get_user_int(current_user, "ready_score", 0)
    milestones = _get_user_int(current_user, "weekly_milestones_completed", 0)
    freezes = _get_user_int(current_user, "streak_freezes_remaining", 0)

    if task.is_done and not before:
        if task.task_type == "daily":
            completed_app_day = _app_day(current_user, now_utc=datetime.utcnow())
            _update_user_streak(current_user, completed_app_day)
            ready_score = ready_score + 1

        elif task.task_type == "weekly":
            # +10 for weekly +4 for badge as per your copy
            ready_score = ready_score + 14
            milestones = milestones + 1
            freezes = freezes + 1  # earn extra freeze when completing weekly

    elif (not task.is_done) and before:
        # Simple "undo" behaviour – small penalty
        if task.task_type == "daily":
            ready_score = max(ready_score - 1, 0)

        elif task.task_type == "weekly":
            ready_score = max(ready_score - 14, 0)
            milestones = max(milestones - 1, 0)
            freezes = max(freezes - 1, 0)

    _set_user_int(current_user, "ready_score", ready_score)
    _set_user_int(current_user, "weekly_milestones_completed", milestones)
    _set_user_int(current_user, "streak_freezes_remaining", freezes)

    _recalc_session_aggregates(session)

    if not _safe_commit("complete_task"):
        return redirect(request.referrer or url_for("coach.index"))

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(
            {
                "ok": True,
                "task_id": task_id,
                "is_done": task.is_done,
                "user_streak": _get_user_int(current_user, "current_streak", 0),
                "user_ready_score": _get_user_int(current_user, "ready_score", 0),
                "session_progress": session.progress_percent,
            }
        )

    return redirect(request.referrer or url_for("coach.index"))


@coach_bp.route(
    "/session/<int:session_id>/update",
    methods=["POST"],
    endpoint="update_session",
)
@login_required
def update_session(session_id: int):
    """
    Session checklist save endpoint (kept for backward compatibility).

    If you ever wire a non-AJAX form from the session page, it can post here:
      - task_<id> = "done" when checked
      - mark_done=1 when user clicks "Save & mark week done"
    """
    session = DailyCoachSession.query.get_or_404(session_id)
    if session.user_id != current_user.id:
        abort(403)

    tasks = (
        DailyCoachTask.query.filter_by(session_id=session.id)
        .order_by(DailyCoachTask.task_type.desc(), DailyCoachTask.sort_order)
        .all()
    )

    desired_done = set()
    for k in request.form.keys():
        if k.startswith("task_"):
            try:
                tid = int(k.split("_", 1)[1])
                desired_done.add(tid)
            except Exception:
                continue

    now = datetime.utcnow()

    ready_score = _get_user_int(current_user, "ready_score", 0)
    milestones = _get_user_int(current_user, "weekly_milestones_completed", 0)
    freezes = _get_user_int(current_user, "streak_freezes_remaining", 0)

    for t in tasks:
        before = bool(t.is_done)
        after = (t.id in desired_done)

        if before == after:
            continue

        t.is_done = after
        t.completed_at = now if after else None

        if after and not before:
            if t.task_type == "daily":
                completed_app_day = _app_day(current_user, now_utc=now)
                _update_user_streak(current_user, completed_app_day)
                ready_score = ready_score + 1
            elif t.task_type == "weekly":
                ready_score = ready_score + 14
                milestones = milestones + 1
                freezes = freezes + 1

        if (not after) and before:
            if t.task_type == "daily":
                ready_score = max(ready_score - 1, 0)
            elif t.task_type == "weekly":
                ready_score = max(ready_score - 14, 0)
                milestones = max(milestones - 1, 0)
                freezes = max(freezes - 1, 0)

    if request.form.get("mark_done") == "1":
        session.is_closed = True

    _set_user_int(current_user, "ready_score", ready_score)
    _set_user_int(current_user, "weekly_milestones_completed", milestones)
    _set_user_int(current_user, "streak_freezes_remaining", freezes)

    _recalc_session_aggregates(session)

    if not _safe_commit("update_session"):
        return redirect(url_for("coach.session", session_id=session.id))

    flash("✅ Progress saved.", "success")
    return redirect(url_for("coach.session", session_id=session.id))


@coach_bp.route(
    "/session/<int:session_id>/reflect",
    methods=["POST"],
    endpoint="reflect",
)
@login_required
def reflect(session_id: int):
    """Save reflection notes for a session (if you ever wire a server-side form)."""
    session = DailyCoachSession.query.get_or_404(session_id)
    if session.user_id != current_user.id:
        abort(403)

    reflection = (request.form.get("reflection") or "").strip()
    try:
        session.reflection = reflection
    except Exception:
        pass

    if not _safe_commit("reflect"):
        return redirect(url_for("coach.session", session_id=session.id))

    flash("✅ Reflection saved.", "success")
    return redirect(url_for("coach.session", session_id=session.id))


@coach_bp.route("/session/<int:session_id>", methods=["GET"], endpoint="session")
@login_required
def session(session_id):
    """View a specific week's tasks."""
    session_obj = DailyCoachSession.query.get_or_404(session_id)

    if session_obj.user_id != current_user.id:
        abort(403)

    tasks = (
        DailyCoachTask.query.filter_by(session_id=session_obj.id)
        .order_by(DailyCoachTask.task_type.desc(), DailyCoachTask.sort_order)
        .all()
    )

    _apply_template_aliases(tasks)

    daily_tasks = [t for t in tasks if t.task_type == "daily"]
    weekly_task = next((t for t in tasks if t.task_type == "weekly"), None)

    path_type = session_obj.path_type or "job"
    is_pro_user = _current_is_pro_user()
    profile_snapshot = load_profile_snapshot(current_user)

    streak_count = _get_user_int(current_user, "current_streak", 0)
    day_index = getattr(session_obj, "day_index", 1) or 1

    return render_template(
        "coach/session.html",
        session=session_obj,
        session_id=session_obj.id,
        tasks=tasks,
        daily_tasks=daily_tasks,
        weekly_task=weekly_task,
        path_type=path_type,
        is_pro_user=is_pro_user,
        profile_snapshot=profile_snapshot,
        session_date=session_obj.session_date.strftime("%d %b %Y") if session_obj.session_date else None,
        session_is_closed=bool(session_obj.is_closed),
        plan_title=getattr(session_obj, "plan_title", None),
        day_index=day_index,
        streak_count=streak_count,
        phase_label="Phase 1 · Foundation" if day_index <= 4 else (
            "Phase 2 · Projects & depth" if day_index <= 8 else "Phase 3 · Showcase & applications"
        ),
        week_label=f"Week {day_index}",
        session_scope_label="This week",
        ai_note=getattr(session_obj, "ai_note", None),
        reflection=getattr(session_obj, "reflection", None),
        # feature_paths from context_processor
    )
