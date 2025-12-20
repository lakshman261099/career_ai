# modules/coach/tasks.py
"""
RQ background tasks for Weekly Coach (Dual-Track System).

This generates 28-day (4-week) plans with:
- Daily tasks (maintenance, 5-15 min)
- Weekly tasks (momentum, 3-5 hours)
- LPA-aligned difficulty
- Project-specific milestones
"""

from __future__ import annotations

import importlib
import json
import os
import traceback
from datetime import datetime, date
from typing import Any, Dict, Optional

from redis import Redis
from rq import Queue, get_current_job

from models import db, DailyCoachSession, DailyCoachTask, User
from modules.common.ai import generate_daily_coach_plan
from modules.credits.engine import refund


# ----------------------------
# Queue / Redis helpers
# ----------------------------

DEFAULT_QUEUE_NAME = os.getenv("RQ_QUEUE_NAME", "careerai_queue")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _redis() -> Redis:
    return Redis.from_url(REDIS_URL)


def get_queue(name: str = DEFAULT_QUEUE_NAME) -> Queue:
    return Queue(name, connection=_redis())


# ----------------------------
# Flask app context bootstrap
# ----------------------------

def _load_flask_app():
    """Load Flask app instance for worker context."""
    fl = (os.getenv("FLASK_APP") or "").strip()
    candidates = []
    if fl:
        candidates.append(fl)
    candidates.extend(["wsgi:app", "app:app", "wsgi", "app"])

    last_err = None
    for spec in candidates:
        try:
            if ":" in spec:
                mod_name, attr = spec.split(":", 1)
                mod = importlib.import_module(mod_name)
                return getattr(mod, attr)
            mod = importlib.import_module(spec)
            if hasattr(mod, "app"):
                return getattr(mod, "app")
        except Exception as e:
            last_err = e

    raise RuntimeError(
        f"Could not import Flask app for worker. "
        f"Set FLASK_APP=module:app. Last error: {last_err}"
    )


# ----------------------------
# Ready Score Calculation
# ----------------------------

def calculate_ready_score_delta(daily_completed: int, weekly_completed: bool) -> int:
    """
    Calculate ready score points for this session:
    - Daily task: +1 point each
    - Weekly task: +10 points
    - Milestone badge (from weekly): +4 points
    """
    points = daily_completed  # Each daily = 1 point
    
    if weekly_completed:
        points += 10  # Weekly task
        points += 4   # Milestone badge
    
    return points


# ----------------------------
# Streak Logic
# ----------------------------

def update_streak_for_user(user: User, task_completed_date: date):
    """
    Update user streak when a daily task is completed.
    
    Rules:
    - Consecutive day → increment streak
    - Same day → no change
    - Missed day(s) → reset streak (unless freeze available)
    - Weekly reset of freeze tokens
    """
    today = task_completed_date
    last_date = user.last_daily_task_date
    
    # Weekly freeze reset (every Monday)
    if user.last_freeze_reset_date:
        days_since_reset = (today - user.last_freeze_reset_date).days
        if days_since_reset >= 7:
            user.streak_freezes_remaining = 1
            user.last_freeze_reset_date = today
    else:
        user.last_freeze_reset_date = today
        user.streak_freezes_remaining = 1
    
    if not last_date:
        # First task ever
        user.current_streak = 1
        user.longest_streak = max(user.longest_streak or 0, 1)
    elif last_date == today:
        # Already completed today - no streak change
        pass
    elif last_date == today - timedelta(days=1):
        # Consecutive day!
        user.current_streak = (user.current_streak or 0) + 1
        user.longest_streak = max(user.longest_streak or 0, user.current_streak)
    else:
        # Missed one or more days
        days_missed = (today - last_date).days - 1
        
        if days_missed == 1 and user.streak_freezes_remaining > 0:
            # Use freeze to save streak
            user.streak_freezes_remaining -= 1
            # Streak continues but freeze is consumed
        else:
            # Streak broken
            user.current_streak = 1
    
    user.last_daily_task_date = today


# ----------------------------
# Public API
# ----------------------------

def enqueue_coach_generation(
    *,
    user_id: int,
    path_type: str,
    dream_plan: Optional[Dict[str, Any]],
    progress_history: list[Dict[str, Any]],
    target_lpa: str,
    selected_projects: list[Dict[str, Any]],
    timeline_months: int,
    run_id: str,
) -> str:
    """
    Enqueue 28-day coach generation task.
    Returns the RQ job_id (string).
    """
    q = get_queue(DEFAULT_QUEUE_NAME)

    from modules.coach.tasks import process_coach_generation

    job = q.enqueue(
        process_coach_generation,
        kwargs=dict(
            user_id=user_id,
            path_type=path_type,
            dream_plan=dream_plan,
            progress_history=progress_history,
            target_lpa=target_lpa,
            selected_projects=selected_projects,
            timeline_months=timeline_months,
            run_id=run_id,
        ),
        job_timeout=int(os.getenv("COACH_JOB_TIMEOUT", "900")),  # 15 min
        result_ttl=int(os.getenv("RQ_RESULT_TTL", "500")),
        failure_ttl=int(os.getenv("RQ_FAILURE_TTL", "3600")),
    )
    return job.id


def process_coach_generation(
    *,
    user_id: int,
    path_type: str,
    dream_plan: Optional[Dict[str, Any]],
    progress_history: list[Dict[str, Any]],
    target_lpa: str,
    selected_projects: list[Dict[str, Any]],
    timeline_months: int,
    run_id: str,
) -> Dict[str, Any]:
    """
    Worker-side execution:
    - Generate 28-day dual-track plan via AI
    - Create 4 DailyCoachSession records (one per week)
    - Create DailyCoachTask records (7 daily + 1 weekly per week)
    - Refund credits on failure
    """
    app = _load_flask_app()
    job = get_current_job()

    with app.app_context():
        user = User.query.filter_by(id=user_id).first()

        if not user:
            return {"ok": False, "error": "User not found"}

        try:
            # ===================================
            # STEP 1: Call AI to generate 28-day plan
            # ===================================
            
            # Build enhanced context for AI
            ai_context = {
                "path_type": path_type,
                "dream_plan": dream_plan,
                "progress_history": progress_history,
                "target_lpa": target_lpa,
                "selected_projects": selected_projects,
                "timeline_months": timeline_months,
                "current_streak": user.current_streak or 0,
                "ready_score": user.ready_score or 0,
            }
            
            # This will call our enhanced AI prompt (see coach_ai_prompt_spec.py)
            plan_data, used_live_ai = generate_daily_coach_plan(
                path_type=path_type,
                dream_plan=dream_plan,
                progress_history=progress_history,
                session_date=date.today().isoformat(),
                day_index=1,  # First week of new cycle
                return_source=True,
                # NEW: Pass LPA context
                extra_context=ai_context,
            )

            if not isinstance(plan_data, dict):
                raise ValueError("AI returned invalid response format")

            weeks = plan_data.get("weeks", [])
            if not weeks or len(weeks) != 4:
                raise ValueError(f"AI must return exactly 4 weeks, got {len(weeks)}")

            # ===================================
            # STEP 2: Create month cycle ID
            # ===================================
            
            today = date.today()
            month_cycle_id = f"user_{user_id}_path_{path_type}_month_{today.year}_{today.month:02d}"

            # ===================================
            # STEP 3: Create sessions + tasks for each week
            # ===================================
            
            created_sessions = []
            
            for week_data in weeks:
                week_number = week_data.get("week_number")
                weekly_task_data = week_data.get("weekly_task", {})
                daily_tasks_data = week_data.get("daily_tasks", [])
                
                if not week_number or week_number < 1 or week_number > 4:
                    current_app.logger.warning(f"Invalid week_number: {week_number}")
                    continue
                
                # Create session for this week
                session = DailyCoachSession(
                    user_id=user_id,
                    path_type=path_type,
                    session_date=today + timedelta(weeks=week_number - 1),
                    day_index=week_number,
                    month_cycle_id=month_cycle_id,
                    target_lpa=target_lpa,
                    is_closed=False,
                    ai_note=plan_data.get("motivation_note", ""),
                    daily_tasks_completed=0,
                    weekly_task_completed=False,
                    progress_percent=0,
                )
                db.session.add(session)
                db.session.flush()  # Get session.id
                
                # Create weekly task (momentum)
                if weekly_task_data:
                    weekly_task = DailyCoachTask(
                        session_id=session.id,
                        task_type='weekly',
                        week_number=week_number,
                        day_number=None,
                        title=weekly_task_data.get("title", "Weekly Goal"),
                        detail=weekly_task_data.get("description", ""),
                        category=weekly_task_data.get("category", "project"),
                        estimated_time_minutes=weekly_task_data.get("estimated_hours", 4) * 60,
                        target_lpa_level=target_lpa,
                        milestone_badge=weekly_task_data.get("milestone_badge"),
                        is_done=False,
                        sort_order=0,
                    )
                    db.session.add(weekly_task)
                
                # Create daily tasks (maintenance)
                for idx, daily_data in enumerate(daily_tasks_data[:7]):  # Max 7 days
                    day_num = daily_data.get("day", idx + 1)
                    
                    daily_task = DailyCoachTask(
                        session_id=session.id,
                        task_type='daily',
                        week_number=week_number,
                        day_number=(week_number - 1) * 7 + day_num,  # 1-28 for the month
                        title=daily_data.get("title", f"Day {day_num} Task"),
                        detail=daily_data.get("description", ""),
                        category=daily_data.get("category", "general"),
                        estimated_time_minutes=daily_data.get("estimated_minutes", 10),
                        target_lpa_level=target_lpa if daily_data.get("lpa_aligned") else None,
                        is_done=False,
                        sort_order=idx + 1,
                    )
                    db.session.add(daily_task)
                
                created_sessions.append(session.id)
            
            db.session.commit()
            
            return {
                "ok": True,
                "user_id": user_id,
                "month_cycle_id": month_cycle_id,
                "sessions_created": created_sessions,
                "weeks_count": len(weeks),
            }

        except Exception as e:
            err = f"{e.__class__.__name__}: {e}"
            tb = traceback.format_exc()
            
            current_app.logger.error(f"Coach generation failed: {err}\n{tb}")
            
            try:
                db.session.rollback()
            except Exception:
                pass

            # Refund credits (because we deducted BEFORE enqueue)
            try:
                refund(
                    user,
                    feature="dream_planner",  # Coach uses dream_planner pool
                    currency="gold",
                    amount=None,
                    run_id=run_id,
                    commit=True,
                    metadata={"reason": "coach_generation_failed", "error": err},
                )
            except Exception:
                current_app.logger.exception("Coach refund failed after AI error")

            return {"ok": False, "error": err, "traceback": tb}


def get_job_status(job_id: str) -> Dict[str, Any]:
    """
    Lightweight status helper for polling.
    """
    r = _redis()
    from rq.job import Job

    try:
        job = Job.fetch(job_id, connection=r)
    except Exception:
        return {"status": "not_found"}

    status = job.get_status()
    out: Dict[str, Any] = {"status": status, "job_id": job_id}

    if status == "failed":
        out["error"] = "Job failed. Check server logs."
    if status == "finished":
        out["result"] = job.result

    return out