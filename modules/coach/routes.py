# modules/coach/routes.py - DUAL-TRACK VERSION (MODEL-CORRECT + TEMPLATE-COMPAT)
"""
Weekly Coach with Dual-Track System:
- Daily Tasks (Maintenance): 5-15 min, build streak, +1 point each
- Weekly Tasks (Momentum): 3-5 hours, build portfolio, +10 points + 4 badge
- LPA-aligned difficulty (12/24/48 LPA)
- Project-specific tasks from Dream Planner
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
    User,
    db,
)
from modules.common.profile_loader import load_profile_snapshot

# ✅ Dual-track month plan generator (one call)
from modules.common.ai import generate_dualtrack_month_plan

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
# HELPERS
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
    Returns dict with: target_lpa, selected_projects, timeline_months, dream_plan
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
        }

    try:
        plan_json = json.loads(latest_snapshot.plan_json or "{}")
    except Exception:
        plan_json = {}

    input_block = plan_json.get("input", {}) if isinstance(plan_json.get("input", {}), dict) else {}

    # ✅ Prefer Dream Planner input key (your DreamPlanner prompt uses target_salary_lpa)
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

    # Selected projects: prefer explicit selection; fallback to resources.mini_projects
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

    month_cycle_id = f"user_{current_user.id}_path_{path_type}_month_{today.year}_{today.month:02d}"

    cycle_sessions = (
        DailyCoachSession.query.filter_by(user_id=current_user.id, month_cycle_id=month_cycle_id)
        .order_by(DailyCoachSession.day_index)
        .all()
    )

    today_daily_task = None
    current_week_session = None
    current_weekly_task = None

    if cycle_sessions:
        current_week_session = next((s for s in cycle_sessions if not s.is_closed), None) or cycle_sessions[-1]

        day_of_week = today.weekday() + 1  # 1-7
        day_number_in_month = (current_week_session.day_index - 1) * 7 + day_of_week

        today_daily_task = (
            DailyCoachTask.query.filter_by(
                session_id=current_week_session.id,
                task_type="daily",
                day_number=day_number_in_month,
            ).first()
        )

        current_weekly_task = (
            DailyCoachTask.query.filter_by(
                session_id=current_week_session.id,
                task_type="weekly",
            ).first()
        )

    recent_sessions = (
        DailyCoachSession.query.filter_by(user_id=current_user.id, path_type=path_type)
        .order_by(DailyCoachSession.created_at.desc())
        .limit(5)
        .all()
    )

    # ✅ Backward compat variables used by your longer index.html sections
    today_session = None
    last_session = None
    if cycle_sessions:
        today_session = current_week_session
        last_session = cycle_sessions[-1] if cycle_sessions else None
        try:
            last_session = (
                DailyCoachSession.query.filter_by(user_id=current_user.id, path_type=path_type)
                .order_by(DailyCoachSession.session_date.desc())
                .first()
            )
        except Exception:
            pass

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
        cycle_sessions=cycle_sessions,
        today_daily_task=today_daily_task,
        current_week_session=current_week_session,
        current_weekly_task=current_weekly_task,
        recent_sessions=recent_sessions,
        month_cycle_id=month_cycle_id,
        today_session=today_session,
        last_session=last_session,
        # ❌ DO NOT override feature_paths here; the context_processor already injects it
    )


@coach_bp.route("/start", methods=["POST"], endpoint="start")
@login_required
def start():
    """
    Generate 28-day (4-week) dual-track plan.

    Creates:
    - 4 DailyCoachSession (one per week)
    - 28 daily tasks (7 per week, task_type='daily')
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
    month_cycle_id = f"user_{current_user.id}_path_{path_type}_month_{today.year}_{today.month:02d}"

    existing_cycle = DailyCoachSession.query.filter_by(
        user_id=current_user.id,
        month_cycle_id=month_cycle_id,
    ).first()

    if existing_cycle:
        flash("You already have a plan for this month!", "info")
        return redirect(url_for("coach.index", path_type=path_type))

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

    try:
        plan, used_live_ai = generate_dualtrack_month_plan(
            path_type=path_type,
            target_lpa=target_lpa,
            dream_plan=dream_plan,
            month_cycle=month_cycle_id,
            return_source=True,
        )

        weeks = plan.get("weeks") or []
        month_ai_note = plan.get("ai_note") or ""

        for week_obj in weeks[:4]:
            week_num = int(week_obj.get("week_number") or 1)
            week_note = (week_obj.get("week_note") or f"Week {week_num} focus.").strip()

            session = DailyCoachSession(
                user_id=current_user.id,
                path_type=path_type,
                session_date=today + timedelta(weeks=week_num - 1),
                day_index=week_num,
                month_cycle_id=month_cycle_id,
                target_lpa=target_lpa,
                is_closed=False,
                ai_note=(week_note or month_ai_note),
                daily_tasks_completed=0,
                weekly_task_completed=False,
                progress_percent=0,
            )
            db.session.add(session)
            db.session.flush()

            daily_tasks = week_obj.get("daily_tasks") or []
            for dt in daily_tasks[:7]:
                day = int(dt.get("day") or 1)
                day_number_in_month = (week_num - 1) * 7 + day

                est = dt.get("estimated_minutes")
                est_int = _safe_int(est, 10)

                tags = dt.get("tags") if isinstance(dt.get("tags"), list) else None

                task = DailyCoachTask(
                    session_id=session.id,
                    task_type="daily",
                    week_number=week_num,
                    day_number=day_number_in_month,
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

        if not _safe_commit("start_plan"):
            # If commit failed, refund like failure path.
            raise RuntimeError("DB commit failed while saving plan")

        flash(f"✅ 28-day plan created! {target_lpa} LPA track", "success")
        return redirect(url_for("coach.index", path_type=path_type))

    except Exception:
        current_app.logger.exception("Coach: generation failed")
        db.session.rollback()

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
            current_app.logger.exception("Coach: refund failed")

        flash("Coach had an error generating your plan. Credits were refunded.", "danger")
        return redirect(url_for("coach.index", path_type=path_type))


@coach_bp.route("/task/<int:task_id>/complete", methods=["POST"], endpoint="complete_task")
@login_required
def complete_task(task_id):
    """Mark a task as complete and update streak/ready score."""
    task = DailyCoachTask.query.get_or_404(task_id)
    session = DailyCoachSession.query.get_or_404(task.session_id)

    if session.user_id != current_user.id:
        abort(403)

    task.is_done = not bool(task.is_done)
    task.completed_at = datetime.utcnow() if task.is_done else None

    ready_score = _get_user_int(current_user, "ready_score", 0)
    milestones = _get_user_int(current_user, "weekly_milestones_completed", 0)
    freezes = _get_user_int(current_user, "streak_freezes_remaining", 0)

    if task.is_done:
        if task.task_type == "daily":
            completed_app_day = _app_day(current_user, now_utc=datetime.utcnow())
            _update_user_streak(current_user, completed_app_day)
            ready_score = ready_score + 1

        elif task.task_type == "weekly":
            ready_score = ready_score + 14  # 10 + 4 badge
            milestones = milestones + 1
            freezes = freezes + 1  # earn freeze

    else:
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


@coach_bp.route("/session/<int:session_id>/update", methods=["POST"], endpoint="update_session")
@login_required
def update_session(session_id: int):
    """
    Session checklist save endpoint.

    Your session.html posts checkbox states here:
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


@coach_bp.route("/session/<int:session_id>/reflect", methods=["POST"], endpoint="reflect")
@login_required
def reflect(session_id: int):
    """Save reflection notes for a session."""
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
        session_date=session_obj.session_date.isoformat() if session_obj.session_date else None,
        session_is_closed=bool(session_obj.is_closed),
        plan_title=getattr(session_obj, "plan_title", None),
        day_index=getattr(session_obj, "day_index", None),
        streak_count=streak_count,
        phase_label=f"Week {getattr(session_obj, 'day_index', 1) or 1}",
        week_label=f"Week {getattr(session_obj, 'day_index', 1) or 1}",
        session_scope_label="This week",
        ai_note=getattr(session_obj, "ai_note", None),
        reflection=getattr(session_obj, "reflection", None),
        # ❌ again, don't pass feature_paths here
    )
