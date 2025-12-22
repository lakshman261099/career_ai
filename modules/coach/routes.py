# modules/coach/routes.py - COMPLETE SYNC UPGRADE (Keeping async workers)
"""
Weekly Coach with Dream→Coach Sync Integration

Hybrid approach:
- Keeps async processing if needed
- Reads coach_plan from locked Dream Plan
- Creates tasks with tips, task_category, skill_tags
- DevLog (proof of work) for weekly tasks
- Profile skill suggestions
- Dual-track system (daily + weekly tasks)
- LPA-aligned difficulty
- Project-specific tasks

Key Features:
1. Phase 2: Reads locked Dream Plan (selected_projects)
2. Phase 3: Creates tasks from coach_plan with expert tips
3. Phase 4: DevLog endpoint for proof of work
4. Phase 5: Auto-suggest skills to profile when tasks completed
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
    UserProfile,
    db,
)

# ✅ NEW: Import LearningLog and ProfileSkillSuggestion
try:
    from models import LearningLog, ProfileSkillSuggestion
except ImportError:
    # Fallback if models not yet migrated
    LearningLog = None  # type: ignore
    ProfileSkillSuggestion = None  # type: ignore

from modules.common.profile_loader import load_profile_snapshot

# ✅ Credits (tuple-safe can_afford + correct refund amount)
from modules.credits.engine import can_afford, deduct_pro, refund

# Optional helper
try:
    from modules.credits.engine import get_feature_cost_amount
except Exception:
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
    """Check if user's email is verified."""
    return bool(getattr(user, "verified", False))


# ===================================
# User Field Helpers (Defensive)
# ===================================

def _get_user_int(user: User, field: str, default: int = 0) -> int:
    """Safely get integer field from user."""
    try:
        val = getattr(user, field, default)
        return int(val) if val is not None else default
    except Exception:
        return default


def _set_user_int(user: User, field: str, value: int):
    """Safely set integer field on user."""
    try:
        if hasattr(user, field):
            setattr(user, field, value)
    except Exception:
        pass


def _get_user_date(user: User, field: str) -> date | None:
    """Safely get date field from user."""
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
    """Safely set date field on user."""
    try:
        if hasattr(user, field):
            setattr(user, field, value)
    except Exception:
        pass


# ===================================
# Dream Plan Context
# ===================================

def _get_dream_plan_context(user_id: int, path_type: str) -> dict:
    """
    Load latest Dream Plan for LPA + selected projects.
    
    Returns dict with:
      - target_lpa: "3", "6", "12", or "24"
      - selected_projects: list of project dicts
      - timeline_months: int
      - dream_plan: full plan JSON
      - plan_title: str
      - snapshot: DreamPlanSnapshot or None
      - is_locked: bool (has selected_projects)
      - coach_plan: dict (weeks data)
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
            "is_locked": False,
            "coach_plan": {},
        }

    try:
        plan_json = json.loads(latest_snapshot.plan_json or "{}")
    except Exception:
        plan_json = {}

    # Check if locked
    is_locked = "_locked_at" in plan_json and "selected_projects" in plan_json

    # Extract metadata
    meta = plan_json.get("meta", {})
    input_block = plan_json.get("input", {})
    
    # Target LPA (from meta or input)
    target_lpa = meta.get("target_lpa") or input_block.get("target_lpa", "12")
    if target_lpa not in ("3", "6", "12", "24"):
        target_lpa = "12"

    # Selected projects
    selected_projects = plan_json.get("selected_projects", [])
    if not isinstance(selected_projects, list):
        selected_projects = []

    # Fallback to proposed projects if no selection
    if not selected_projects:
        projects = plan_json.get("projects", [])
        if isinstance(projects, list) and len(projects) > 0:
            # Auto-select first 2 projects as fallback
            selected_projects = projects[:2]

    # Timeline
    timeline_months = input_block.get("timeline_months", 3)
    try:
        timeline_months = int(timeline_months)
    except Exception:
        timeline_months = 3

    # Coach plan
    coach_plan = plan_json.get("coach_plan", {})
    if not isinstance(coach_plan, dict):
        coach_plan = {}

    return {
        "target_lpa": target_lpa,
        "selected_projects": selected_projects,
        "timeline_months": timeline_months,
        "dream_plan": plan_json,
        "plan_title": latest_snapshot.plan_title,
        "snapshot": latest_snapshot,
        "is_locked": is_locked,
        "coach_plan": coach_plan,
    }


# ===================================
# Streak Logic
# ===================================

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

    # Weekly freeze reset
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
        pass  # Same day, no change
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


# ===================================
# Profile Sync Helpers
# ===================================

def _suggest_profile_skills(user: User, task: DailyCoachTask, skill_tags: list):
    """
    Phase 5: Create skill suggestions for profile.
    
    Called when user completes a weekly task with skill_tags.
    """
    if not ProfileSkillSuggestion:
        return  # Model not available
    
    for skill_name in skill_tags[:5]:  # Max 5 skills per task
        if not skill_name or not isinstance(skill_name, str):
            continue
        
        # Check if already suggested or exists in profile
        try:
            existing = ProfileSkillSuggestion.query.filter_by(
                user_id=user.id,
                skill_name=skill_name,
            ).first()
            
            if existing:
                continue
        except Exception:
            continue
        
        # Create suggestion
        try:
            suggestion = ProfileSkillSuggestion(
                user_id=user.id,
                source_type='coach_task',
                source_id=task.id,
                skill_name=skill_name,
                skill_category=task.category,
                proficiency_level='Beginner',
                status='pending',
                context_note=f"From completing: {task.title}",
            )
            db.session.add(suggestion)
        except Exception:
            pass
    
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


# ===================================
# ROUTES
# ===================================

@coach_bp.route("/", methods=["GET"], endpoint="index")
@login_required
def index():
    """
    Coach landing page with dual-track dashboard.
    
    Shows:
    - User streak, ready score, milestones
    - Today's daily task (if active cycle)
    - This week's weekly task
    - 28-day roadmap
    - Pending skill suggestions
    """
    path_type = _normalize_path_type(
        request.args.get("path_type") or request.form.get("path_type")
    )
    
    is_pro_user = _current_is_pro_user()
    profile_snapshot = load_profile_snapshot(current_user)
    today = _today_date()
    
    # Get user's current streak and ready score
    user_streak = _get_user_int(current_user, "current_streak", 0)
    user_ready_score = _get_user_int(current_user, "ready_score", 0)
    longest_streak = _get_user_int(current_user, "longest_streak", 0)
    weekly_milestones = _get_user_int(current_user, "weekly_milestones_completed", 0)
    
    # Find current month cycle
    month_cycle_id = f"user_{current_user.id}_path_{path_type}_month_{today.year}_{today.month:02d}"
    
    # Get all sessions in current cycle
    cycle_sessions = (
        DailyCoachSession.query.filter_by(
            user_id=current_user.id,
            month_cycle_id=month_cycle_id,
        )
        .order_by(DailyCoachSession.day_index)
        .all()
    )
    
    # Get today's daily task and current week data
    today_daily_task = None
    current_week_session = None
    current_weekly_task = None
    
    if cycle_sessions:
        # Find which week we're in
        today_day_of_month = today.day
        current_week_num = min(((today_day_of_month - 1) // 7) + 1, 4)
        
        # Find session for current week
        for sess in cycle_sessions:
            if sess.day_index == current_week_num:
                current_week_session = sess
                break
        
        if current_week_session:
            # Get today's daily task
            day_of_week = today.weekday() + 1  # 1-7
            day_number_in_month = (current_week_num - 1) * 7 + day_of_week
            
            today_daily_task = (
                DailyCoachTask.query.filter_by(
                    session_id=current_week_session.id,
                    task_type='daily',
                    day_number=day_number_in_month,
                ).first()
            )
            
            # Get weekly task for current week
            current_weekly_task = (
                DailyCoachTask.query.filter_by(
                    session_id=current_week_session.id,
                    task_type='weekly',
                ).first()
            )
    
    # Get pending skill suggestions (Phase 5)
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
    
    # Get Dream Plan context (for "Lock Plan" prompt if needed)
    dream_context = _get_dream_plan_context(current_user.id, path_type)
    
    return render_template(
        "coach/index.html",
        path_type=path_type,
        is_pro_user=is_pro_user,
        profile_snapshot=profile_snapshot,
        today=today,
        # Dual-track dashboard data
        user_streak=user_streak,
        user_ready_score=user_ready_score,
        longest_streak=longest_streak,
        weekly_milestones=weekly_milestones,
        cycle_sessions=cycle_sessions,
        today_daily_task=today_daily_task,
        current_week_session=current_week_session,
        current_weekly_task=current_weekly_task,
        month_cycle_id=month_cycle_id,
        # Phase 5: Skill suggestions
        pending_skills=pending_skills,
        # Dream Plan context
        dream_plan_locked=dream_context["is_locked"],
        dream_snapshot=dream_context["snapshot"],
    )


@coach_bp.route("/start", methods=["POST"], endpoint="start")
@login_required
def start():
    """
    Start Coach execution from locked Dream Plan.
    
    ✅ SYNC UPGRADE:
    - Reads coach_plan from DreamPlanSnapshot
    - Creates tasks with tips, task_category, skill_tags
    - Links tasks to selected projects
    - NO credits deducted (included in Dream Plan cost)
    """
    path_type = _normalize_path_type(
        request.form.get("path_type") or request.args.get("path_type")
    )
    
    # Email verification guard
    if not _is_email_verified(current_user):
        flash("Please verify your email before using Weekly Coach.", "warning")
        return redirect(url_for("auth.otp_request"))
    
    # Pro-only gate
    if not _current_is_pro_user():
        flash("Weekly Coach is available for Pro ⭐ members only.", "warning")
        return redirect(url_for("billing.index"))
    
    # NOTE: Credits were already deducted in Dream Planner
    # Coach execution is FREE (included in Dream Plan cost)
    
    today = _today_date()
    
    # Check if month cycle already exists
    month_cycle_id = f"user_{current_user.id}_path_{path_type}_month_{today.year}_{today.month:02d}"
    
    existing_cycle = DailyCoachSession.query.filter_by(
        user_id=current_user.id,
        month_cycle_id=month_cycle_id,
    ).first()
    
    if existing_cycle:
        flash("You already have a plan for this month!", "info")
        return redirect(url_for("coach.index", path_type=path_type))
    
    # ===================================
    # Load latest locked Dream Plan
    # ===================================
    
    dream_context = _get_dream_plan_context(current_user.id, path_type)
    
    if not dream_context["snapshot"]:
        flash("No Dream Plan found. Please create one first.", "warning")
        return redirect(url_for("dream.index", path_type=path_type))
    
    if not dream_context["is_locked"]:
        flash("Please lock your Dream Plan first (select projects).", "warning")
        return redirect(url_for("dream.result", snapshot_id=dream_context["snapshot"].id))
    
    # Extract coach plan
    coach_plan = dream_context["coach_plan"]
    if not coach_plan or not coach_plan.get("weeks"):
        flash("No Coach plan found in Dream Plan. Please regenerate your Dream Plan.", "danger")
        return redirect(url_for("dream.index", path_type=path_type))
    
    weeks = coach_plan.get("weeks", [])
    if not weeks:
        flash("Coach plan has no weeks.", "danger")
        return redirect(url_for("dream.index", path_type=path_type))
    
    # Extract context
    target_lpa = dream_context["target_lpa"]
    selected_projects = dream_context["selected_projects"]
    
    # ===================================
    # Create sessions + tasks from plan
    # ===================================
    
    try:
        for week_data in weeks[:12]:  # Max 12 weeks
            week_num = week_data.get("week_num")
            if not week_num or week_num < 1:
                continue
            
            theme = week_data.get("theme", f"Week {week_num}")
            daily_tasks_data = week_data.get("daily_tasks", [])
            weekly_tasks_data = week_data.get("weekly_tasks", [])
            
            # Create session for this week
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
            
            # Add plan reference fields if they exist
            if dream_context["snapshot"]:
                try:
                    session.plan_digest = dream_context["snapshot"].inputs_digest
                    session.plan_title = dream_context["plan_title"]
                except Exception:
                    pass
            
            db.session.add(session)
            db.session.flush()  # Get session.id
            
            # Create daily tasks (2 per week)
            for idx, daily_data in enumerate(daily_tasks_data[:2]):
                day_num = daily_data.get("day", idx + 1)
                
                daily_task = DailyCoachTask(
                    session_id=session.id,
                    task_type='daily',
                    week_number=week_num,
                    day_number=(week_num - 1) * 7 + day_num,
                    title=daily_data.get("title", f"Day {day_num} Task"),
                    detail=daily_data.get("description", ""),
                    category=daily_data.get("category", "general"),
                    estimated_time_minutes=daily_data.get("minutes", 15),
                    target_lpa_level=target_lpa,
                    is_done=False,
                    sort_order=idx,
                )
                db.session.add(daily_task)
            
            # Create weekly tasks (3 per week: Learn, Build, Document)
            for idx, weekly_data in enumerate(weekly_tasks_data[:3]):
                task_category = weekly_data.get("category", "Build")
                skill_tags = weekly_data.get("skill_tags", [])
                
                weekly_task = DailyCoachTask(
                    session_id=session.id,
                    task_type='weekly',
                    week_number=week_num,
                    day_number=None,
                    title=weekly_data.get("title", f"Week {week_num} Task {idx+1}"),
                    detail=weekly_data.get("description", ""),
                    category=weekly_data.get("category", "project"),
                    # ✅ NEW: Sync-specific fields
                    task_category=task_category,  # Learn/Build/Document
                    tips=weekly_data.get("tips"),  # Expert advice
                    skill_tags=json.dumps(skill_tags) if skill_tags else None,
                    sync_to_profile=True if skill_tags else False,
                    estimated_time_minutes=weekly_data.get("estimated_hours", 8) * 60,
                    target_lpa_level=target_lpa,
                    milestone_badge=f"{task_category} Master",
                    is_done=False,
                    sort_order=10 + idx,
                )
                db.session.add(weekly_task)
        
        db.session.commit()
        flash(f"✅ {len(weeks)}-week execution plan created!", "success")
        return redirect(url_for("coach.index", path_type=path_type))
        
    except Exception as e:
        current_app.logger.exception("Coach: task creation failed")
        db.session.rollback()
        flash("Failed to create Coach plan. Please try again.", "danger")
        return redirect(url_for("coach.index", path_type=path_type))

@coach_bp.route("/abort", methods=["POST"], endpoint="abort_plan")
@login_required
def abort_plan():
    """
    Abort (delete) current month's Coach cycle.
    
    This removes all sessions and tasks for the current month,
    allowing the student to restart with a new Dream Plan.
    """
    path_type = _normalize_path_type(
        request.form.get("path_type") or request.args.get("path_type")
    )
    month_cycle_id = request.form.get("month_cycle_id", "").strip()
    
    if not month_cycle_id:
        flash("Invalid cycle ID.", "danger")
        return redirect(url_for("coach.index", path_type=path_type))
    
    # Verify ownership and find sessions
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
        # Delete all tasks first (cascade should handle this, but be explicit)
        for session in sessions_to_delete:
            DailyCoachTask.query.filter_by(session_id=session.id).delete()
        
        # Delete all sessions
        for session in sessions_to_delete:
            db.session.delete(session)
        
        db.session.commit()
        
        flash(
            f"✅ Plan aborted successfully. {len(sessions_to_delete)} weeks removed. You can start fresh anytime!",
            "success"
        )
    except Exception as e:
        current_app.logger.exception("Coach: abort plan failed")
        db.session.rollback()
        flash("Failed to abort plan. Please try again.", "danger")
    
    return redirect(url_for("coach.index", path_type=path_type))

@coach_bp.route("/task/<int:task_id>/complete", methods=["POST"], endpoint="complete_task")
@login_required
def complete_task(task_id):
    """Mark a task as complete/incomplete and update streak/ready score."""
    task = DailyCoachTask.query.get_or_404(task_id)
    session = DailyCoachSession.query.get_or_404(task.session_id)
    
    # Verify ownership
    if session.user_id != current_user.id:
        abort(403)
    
    # Toggle completion
    was_done = task.is_done
    task.is_done = not task.is_done
    task.completed_at = datetime.utcnow() if task.is_done else None
    
    if task.is_done and not was_done:
        # Completing task
        if task.task_type == 'daily':
            _update_user_streak(current_user, _today_date())
            ready_score = _get_user_int(current_user, "ready_score", 0)
            _set_user_int(current_user, "ready_score", ready_score + 1)
        elif task.task_type == 'weekly':
            ready_score = _get_user_int(current_user, "ready_score", 0)
            milestones = _get_user_int(current_user, "weekly_milestones_completed", 0)
            _set_user_int(current_user, "ready_score", ready_score + 14)  # 10 + 4 badge
            _set_user_int(current_user, "weekly_milestones_completed", milestones + 1)
    elif not task.is_done and was_done:
        # Un-completing task
        if task.task_type == 'daily':
            ready_score = _get_user_int(current_user, "ready_score", 0)
            _set_user_int(current_user, "ready_score", max(ready_score - 1, 0))
        elif task.task_type == 'weekly':
            ready_score = _get_user_int(current_user, "ready_score", 0)
            milestones = _get_user_int(current_user, "weekly_milestones_completed", 0)
            _set_user_int(current_user, "ready_score", max(ready_score - 14, 0))
            _set_user_int(current_user, "weekly_milestones_completed", max(milestones - 1, 0))
    
    # Recalculate session progress
    _recalc_session_aggregates(session)
    
    db.session.commit()
    
    # Return JSON if AJAX request
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
    """View a specific week's tasks."""
    session = DailyCoachSession.query.get_or_404(session_id)
    
    if session.user_id != current_user.id:
        abort(403)
    
    tasks = (
        DailyCoachTask.query.filter_by(session_id=session.id)
        .order_by(DailyCoachTask.sort_order)
        .all()
    )
    
    # Separate daily and weekly
    daily_tasks = [t for t in tasks if t.task_type == 'daily']
    weekly_tasks = [t for t in tasks if t.task_type == 'weekly']
    
    path_type = session.path_type or 'job'
    is_pro_user = _current_is_pro_user()
    profile_snapshot = load_profile_snapshot(current_user)
    
    streak_count = _get_user_int(current_user, "current_streak", 0)
    
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
        session_is_closed=session.is_closed,
        plan_title=getattr(session, 'plan_title', None),
        day_index=session.day_index,
        streak_count=streak_count,
        phase_label=f"Week {session.day_index}",
        week_label=f"Week {session.day_index}",
        session_scope_label="This week",
    )


# ==========================================
# ✅ NEW: DevLog Endpoint (Phase 4)
# ==========================================

@coach_bp.route("/devlog/<int:task_id>", methods=["GET", "POST"], endpoint="devlog")
@login_required
def devlog(task_id):
    """
    Phase 4: DevLog (Proof of Work) for Weekly Tasks.
    
    GET: Show DevLog form
    POST: Save DevLog and mark task complete
    """
    if not LearningLog:
        flash("DevLog feature not available yet.", "warning")
        return redirect(url_for("coach.index"))
    
    task = DailyCoachTask.query.get_or_404(task_id)
    session = DailyCoachSession.query.get_or_404(task.session_id)
    
    # Verify ownership
    if session.user_id != current_user.id:
        abort(403)
    
    # Only for weekly tasks
    if task.task_type != 'weekly':
        flash("DevLog is only for Weekly Tasks.", "warning")
        return redirect(url_for("coach.session", session_id=session.id))
    
    if request.method == "POST":
        # Create DevLog entry
        try:
            devlog_entry = LearningLog(
                task_id=task.id,
                user_id=current_user.id,
                session_id=session.id,
                what_i_learned=request.form.get("what_i_learned", "").strip(),
                what_i_built=request.form.get("what_i_built", "").strip(),
                challenges_faced=request.form.get("challenges_faced", "").strip(),
                next_steps=request.form.get("next_steps", "").strip(),
                github_link=request.form.get("github_link", "").strip(),
                demo_link=request.form.get("demo_link", "").strip(),
            )
            
            # Optional fields
            try:
                devlog_entry.time_spent_minutes = int(request.form.get("time_spent_minutes", 0) or 0)
            except Exception:
                pass
            
            try:
                devlog_entry.difficulty_rating = int(request.form.get("difficulty_rating", 0) or 0)
            except Exception:
                pass
            
            db.session.add(devlog_entry)
            
            # Mark task as complete
            task.is_done = True
            task.completed_at = datetime.utcnow()
            
            # Update ready score
            ready_score = _get_user_int(current_user, "ready_score", 0)
            milestones = _get_user_int(current_user, "weekly_milestones_completed", 0)
            _set_user_int(current_user, "ready_score", ready_score + 14)  # 10 + 4 badge
            _set_user_int(current_user, "weekly_milestones_completed", milestones + 1)
            
            # Recalculate session progress
            _recalc_session_aggregates(session)
            
            db.session.commit()
            
            flash("✅ DevLog saved! Task marked complete.", "success")
            
            # Phase 5: Suggest profile sync if task has skill_tags
            if task.sync_to_profile and task.skill_tags:
                try:
                    skill_tags = json.loads(task.skill_tags) if isinstance(task.skill_tags, str) else []
                    if skill_tags:
                        _suggest_profile_skills(current_user, task, skill_tags)
                except Exception:
                    pass
            
            return redirect(url_for("coach.session", session_id=session.id))
            
        except Exception as e:
            current_app.logger.exception("DevLog save failed")
            db.session.rollback()
            flash("Failed to save DevLog. Please try again.", "danger")
    
    # GET: Show form
    # Check if DevLog already exists
    existing_log = None
    try:
        existing_log = LearningLog.query.filter_by(task_id=task.id).first()
    except Exception:
        pass
    
    return render_template(
        "coach/devlog.html",
        task=task,
        session=session,
        existing_log=existing_log,
    )


# ==========================================
# ✅ NEW: Profile Sync Endpoints (Phase 5)
# ==========================================

@coach_bp.route("/skills/accept/<int:suggestion_id>", methods=["POST"], endpoint="accept_skill")
@login_required
def accept_skill(suggestion_id):
    """
    Phase 5: Accept skill suggestion and add to profile.
    """
    if not ProfileSkillSuggestion:
        flash("Skill suggestions not available yet.", "warning")
        return redirect(url_for("coach.index"))
    
    suggestion = ProfileSkillSuggestion.query.get_or_404(suggestion_id)
    
    if suggestion.user_id != current_user.id:
        abort(403)
    
    # Add to profile
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        flash("Profile not found.", "danger")
        return redirect(url_for("coach.index"))
    
    try:
        # Get existing skills
        existing_skills = []
        if hasattr(profile, 'skills') and profile.skills:
            if isinstance(profile.skills, list):
                existing_skills = profile.skills
            elif isinstance(profile.skills, str):
                try:
                    existing_skills = json.loads(profile.skills)
                except Exception:
                    pass
        
        # Check if skill already exists
        skill_names = []
        for s in existing_skills:
            if isinstance(s, dict):
                skill_names.append(s.get("name", ""))
            elif isinstance(s, str):
                skill_names.append(s)
        
        if suggestion.skill_name not in skill_names:
            # Add new skill
            new_skill = {
                "name": suggestion.skill_name,
                "category": suggestion.skill_category or "Other",
                "proficiency": suggestion.proficiency_level or "Beginner",
            }
            existing_skills.append(new_skill)
            
            # Save back to profile
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
    
    except Exception as e:
        current_app.logger.exception("Profile skill add failed")
        db.session.rollback()
        flash("Failed to add skill to profile.", "danger")
    
    return redirect(request.referrer or url_for("coach.index"))


@coach_bp.route("/skills/reject/<int:suggestion_id>", methods=["POST"], endpoint="reject_skill")
@login_required
def reject_skill(suggestion_id):
    """
    Phase 5: Reject skill suggestion.
    """
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