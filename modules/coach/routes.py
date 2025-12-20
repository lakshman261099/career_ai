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
    if getattr(current_user, "is_pro", False):
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
    last_date = user.last_daily_task_date

    # Weekly freeze reset
    if user.last_freeze_reset_date:
        days_since_reset = (today - user.last_freeze_reset_date).days
        if days_since_reset >= 7 and today.weekday() == 0:
            user.streak_freezes_remaining = 1
            user.last_freeze_reset_date = today
    else:
        user.last_freeze_reset_date = today
        user.streak_freezes_remaining = 1

    if not last_date:
        user.current_streak = 1
        user.longest_streak = max(user.longest_streak or 0, 1)
    elif last_date == today:
        pass
    elif last_date == today - timedelta(days=1):
        user.current_streak = (user.current_streak or 0) + 1
        user.longest_streak = max(user.longest_streak or 0, user.current_streak)
    else:
        days_missed = (today - last_date).days - 1
        if days_missed == 1 and (user.streak_freezes_remaining or 0) > 0:
            user.streak_freezes_remaining = (user.streak_freezes_remaining or 0) - 1
        else:
            user.current_streak = 1

    user.last_daily_task_date = today


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
            # session.html uses t.suggested_minutes
            if not hasattr(t, "suggested_minutes"):
                t.suggested_minutes = (
                    t.estimated_time_minutes
                    if getattr(t, "estimated_time_minutes", None) is not None
                    else getattr(t, "estimated_minutes", None)
                )
        except Exception:
            t.suggested_minutes = None

        try:
            # session.html uses t.guide optionally; keep None unless you store something elsewhere
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
    # Preferred path (if you added helper in credits engine)
    if callable(get_feature_cost_amount):
        try:
            return int(get_feature_cost_amount(feature, currency))  # type: ignore
        except Exception:
            pass

    # Fallback: read from Flask config (works if FEATURE_COSTS is set)
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

    # ✅ Soft check credits (works whether can_afford returns bool OR tuple)
    has_gold = False
    try:
        has_gold, _reason = _can_afford_safe(current_user, "dream_planner", currency="gold")
    except Exception:
        current_app.logger.exception("Coach: can_afford check failed on index().")

    user_streak = current_user.current_streak or 0
    user_ready_score = current_user.ready_score or 0

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
        # If current is not last, last_session should be most recent by date
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

        # ✅ legacy roadmap variables expected in your index.html
        today_session=today_session,
        last_session=last_session,
        feature_paths=getattr(current_app, "feature_paths", None),
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

    # Email verify
    if not _is_email_verified(current_user):
        flash("Please verify your email with a login code before using Weekly Coach.", "warning")
        return redirect(url_for("auth.otp_request"))

    # Pro gate
    if not _current_is_pro_user():
        flash("Weekly Coach is available for Pro ⭐ members only.", "warning")
        return redirect(url_for("billing.index"))

    # Gold gate (works whether can_afford returns bool OR tuple)
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

    # Deduct credits first
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

    # Dream plan context
    dream_context = _get_dream_plan_context(current_user.id, path_type)
    target_lpa = dream_context["target_lpa"]
    dream_plan = dream_context["dream_plan"] or {}

    # Generate dual-track month plan (one call)
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
            db.session.flush()  # session.id

            # Daily tasks (7)
            daily_tasks = week_obj.get("daily_tasks") or []
            for dt in daily_tasks[:7]:
                day = int(dt.get("day") or 1)
                day_number_in_month = (week_num - 1) * 7 + day

                est = dt.get("estimated_minutes")
                try:
                    est_int = int(est) if est is not None else 10
                except Exception:
                    est_int = 10

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

                    # ✅ write BOTH fields for your model
                    estimated_time_minutes=est_int,   # UI field (non-null)
                    estimated_minutes=est_int,        # AI metadata field

                    target_lpa_level=target_lpa,
                    tags=tags,
                    milestone_title=(dt.get("milestone_title") or "")[:255] if dt.get("milestone_title") else None,
                    milestone_step=(dt.get("milestone_step") or "")[:255] if dt.get("milestone_step") else None,
                )
                db.session.add(task)

            # Weekly task (1)
            wt = week_obj.get("weekly_task") or {}
            w_est = wt.get("estimated_minutes")
            try:
                w_est_int = int(w_est) if w_est is not None else 240
            except Exception:
                w_est_int = 240

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

        db.session.commit()
        flash(f"✅ 28-day plan created! {target_lpa} LPA track", "success")
        return redirect(url_for("coach.index", path_type=path_type))

    except Exception:
        current_app.logger.exception("Coach: generation failed")
        db.session.rollback()

        # ✅ Refund credits (works with your upgraded engine refund(amount=None) OR strict refund(amount=...))
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
                # If your engine supports amount=None and auto-refunds by feature cost, this still works:
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

    task.is_done = not task.is_done
    task.completed_at = datetime.utcnow() if task.is_done else None

    if task.is_done:
        if task.task_type == "daily":
            completed_app_day = _app_day(current_user, now_utc=datetime.utcnow())
            _update_user_streak(current_user, completed_app_day)

            current_user.ready_score = (current_user.ready_score or 0) + 1

        elif task.task_type == "weekly":
            current_user.ready_score = (current_user.ready_score or 0) + 14  # 10 + 4 badge
            current_user.weekly_milestones_completed = (current_user.weekly_milestones_completed or 0) + 1

            # Earn freeze by completing weekly task
            try:
                current_user.streak_freezes_remaining = (current_user.streak_freezes_remaining or 0) + 1
            except Exception:
                pass

    else:
        if task.task_type == "daily":
            current_user.ready_score = max((current_user.ready_score or 0) - 1, 0)

        elif task.task_type == "weekly":
            current_user.ready_score = max((current_user.ready_score or 0) - 14, 0)
            current_user.weekly_milestones_completed = max((current_user.weekly_milestones_completed or 0) - 1, 0)

            # Revert earned freeze
            try:
                current_user.streak_freezes_remaining = max((current_user.streak_freezes_remaining or 0) - 1, 0)
            except Exception:
                pass

    # Update session aggregates (safe recompute)
    _recalc_session_aggregates(session)

    db.session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(
            {
                "ok": True,
                "task_id": task_id,
                "is_done": task.is_done,
                "user_streak": current_user.current_streak,
                "user_ready_score": current_user.ready_score,
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

    # Build desired state from form
    desired_done = set()
    for k in request.form.keys():
        if k.startswith("task_"):
            try:
                tid = int(k.split("_", 1)[1])
                desired_done.add(tid)
            except Exception:
                continue

    # Apply deltas safely
    now = datetime.utcnow()
    for t in tasks:
        before = bool(t.is_done)
        after = (t.id in desired_done)

        if before == after:
            continue

        t.is_done = after
        t.completed_at = now if after else None

        # Adjust user scoring based on delta
        if after and not before:
            if t.task_type == "daily":
                completed_app_day = _app_day(current_user, now_utc=now)
                _update_user_streak(current_user, completed_app_day)
                current_user.ready_score = (current_user.ready_score or 0) + 1
            elif t.task_type == "weekly":
                current_user.ready_score = (current_user.ready_score or 0) + 14
                current_user.weekly_milestones_completed = (current_user.weekly_milestones_completed or 0) + 1
                try:
                    current_user.streak_freezes_remaining = (current_user.streak_freezes_remaining or 0) + 1
                except Exception:
                    pass

        if (not after) and before:
            if t.task_type == "daily":
                current_user.ready_score = max((current_user.ready_score or 0) - 1, 0)
            elif t.task_type == "weekly":
                current_user.ready_score = max((current_user.ready_score or 0) - 14, 0)
                current_user.weekly_milestones_completed = max((current_user.weekly_milestones_completed or 0) - 1, 0)
                try:
                    current_user.streak_freezes_remaining = max((current_user.streak_freezes_remaining or 0) - 1, 0)
                except Exception:
                    pass

    # Mark week done if requested
    if request.form.get("mark_done") == "1":
        session.is_closed = True

    # Recompute session aggregates
    _recalc_session_aggregates(session)

    db.session.commit()

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
    session.reflection = reflection

    db.session.commit()
    flash("✅ Reflection saved.", "success")
    return redirect(url_for("coach.session", session_id=session.id))


@coach_bp.route("/session/<int:session_id>", methods=["GET"], endpoint="session")
@login_required
def session(session_id):
    """View a specific week's tasks."""
    session = DailyCoachSession.query.get_or_404(session_id)

    if session.user_id != current_user.id:
        abort(403)

    tasks = (
        DailyCoachTask.query.filter_by(session_id=session.id)
        .order_by(DailyCoachTask.task_type.desc(), DailyCoachTask.sort_order)
        .all()
    )

    # ✅ attach aliases so your existing session.html works
    _apply_template_aliases(tasks)

    daily_tasks = [t for t in tasks if t.task_type == "daily"]
    weekly_task = next((t for t in tasks if t.task_type == "weekly"), None)

    path_type = session.path_type or "job"
    is_pro_user = _current_is_pro_user()
    profile_snapshot = load_profile_snapshot(current_user)

    streak_count = current_user.current_streak or 0

    return render_template(
        "coach/session.html",
        session=session,
        session_id=session.id,              # ✅ your template uses session_id in forms
        tasks=tasks,
        daily_tasks=daily_tasks,
        weekly_task=weekly_task,
        path_type=path_type,
        is_pro_user=is_pro_user,
        profile_snapshot=profile_snapshot,
        session_date=session.session_date.isoformat() if session.session_date else None,
        session_is_closed=session.is_closed,
        plan_title=session.plan_title,
        day_index=session.day_index,
        streak_count=streak_count,
        phase_label=f"Week {session.day_index}",
        week_label=f"Week {session.day_index}",
        session_scope_label="This week",
        ai_note=session.ai_note,
        reflection=session.reflection,      # ✅ your template expects reflection
        feature_paths=getattr(current_app, "feature_paths", None),
    )
