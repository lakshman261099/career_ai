from datetime import datetime, date, timedelta
import json

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from models import (
    DailyCoachSession,
    DailyCoachTask,
    DreamPlanProject,
    DreamPlanSnapshot,
    SessionProjectLink,
    db,
)
from modules.common.profile_loader import load_profile_snapshot
from modules.common.ai import generate_daily_coach_plan
from modules.credits.engine import can_afford, deduct_pro

coach_bp = Blueprint(
    "coach",
    __name__,
    template_folder="../../templates/coach",
)


# --------- helpers ---------


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
    Coach is anchored to a path type:

      - 'job'     → Dream Job plan
      - 'startup' → Dream Startup plan
    """
    v = (raw or "").strip().lower()
    if v == "startup":
        return "startup"
    return "job"


def _today_date() -> date:
    """
    For now we treat 'today' as UTC date.
    In future you can make this timezone-aware per user/tenant.
    """
    return datetime.utcnow().date()


def _week_start(d: date) -> date:
    """
    Returns the Monday of the week for the given date.
    Used for streak calculations.
    """
    return d - timedelta(days=d.weekday())


def _progress_history_for_user(user_id: int, path_type: str) -> list[dict]:
    """
    Build a lightweight progress history for AI context.
    Last ~10 sessions for this path_type.
    Each session is effectively a "week" now.
    """
    sessions = (
        DailyCoachSession.query.filter_by(user_id=user_id, path_type=path_type)
        .order_by(DailyCoachSession.session_date.desc(), DailyCoachSession.id.desc())
        .limit(10)
        .all()
    )

    history: list[dict] = []
    for s in reversed(sessions):  # oldest first
        total_tasks = len(s.tasks or [])
        done_tasks = sum(1 for t in (s.tasks or []) if t.is_done)
        history.append(
            {
                "session_id": s.id,
                "session_date": s.session_date.isoformat() if s.session_date else None,
                "path_type": s.path_type,
                # Day index is interpreted as "week index" inside the AI + UI
                "day_index": s.day_index,
                "tasks_total": total_tasks,
                "tasks_done": done_tasks,
                "is_closed": bool(s.is_closed),
            }
        )
    return history


def _compute_streak(user_id: int, path_type: str) -> int:
    """
    Compute streak of consecutive CLOSED sessions on a WEEK basis.

    Logic:
      - Each closed DailyCoachSession is treated as one "week".
      - We look at the Monday of each week (week_start).
      - Streak counts how many sessions are in consecutive weeks
        from the most recent one backwards.
    """
    sessions = (
        DailyCoachSession.query.filter_by(user_id=user_id, path_type=path_type)
        .filter(DailyCoachSession.is_closed.is_(True))
        .order_by(DailyCoachSession.session_date.desc(), DailyCoachSession.id.desc())
        .all()
    )
    if not sessions:
        return 0

    def _safe_week_start(s: DailyCoachSession) -> date | None:
        if not s.session_date:
            return None
        return _week_start(s.session_date)

    first_week_start = _safe_week_start(sessions[0])
    if first_week_start is None:
        return 0

    streak = 1
    prev_week_start = first_week_start

    for s in sessions[1:]:
        ws = _safe_week_start(s)
        if ws is None:
            continue
        diff_days = (prev_week_start - ws).days
        # Exactly one week apart = consecutive
        if diff_days == 7:
            streak += 1
            prev_week_start = ws
        else:
            break

    return streak


def _find_active_session(user_id: int, path_type: str) -> DailyCoachSession | None:
    """
    Find the current ACTIVE session for this user + path type.

    We no longer tie sessions strictly to a single calendar day.
    Instead:
      - If there is an open session (is_closed=False), reuse it.
      - Otherwise, no active session (user can start a new one).
    """
    return (
        DailyCoachSession.query.filter_by(
            user_id=user_id,
            path_type=path_type,
            is_closed=False,
        )
        .order_by(DailyCoachSession.session_date.desc(), DailyCoachSession.id.desc())
        .first()
    )


# --------- routes ---------


@coach_bp.route("/", methods=["GET"], endpoint="index")
@login_required
def index():
    """
    Weekly Coach — landing page.

    - Built on top of Dream Planner.
    - Pro-only, Gold-based feature.
    - Shows:
        - Pro vs Free gating
        - Gold credit status
        - "Continue current session" if exists
        - Last session summary (if any)
    """
    is_pro_user = _current_is_pro_user()
    path_type = _normalize_path_type(
        request.args.get("path_type") or request.args.get("plan_type")
    )
    today = _today_date()

    profile_snapshot = load_profile_snapshot(current_user)

    # Soft check for Gold credits (we don't block the page, just show a hint)
    has_gold = False
    try:
        has_gold = can_afford(current_user, "dream_planner", currency="gold")
    except Exception:
        current_app.logger.exception("Coach: can_afford check failed on index().")

    # Find active (open) session and last session overall
    active_session = _find_active_session(current_user.id, path_type)

    last_session = (
        DailyCoachSession.query.filter_by(user_id=current_user.id, path_type=path_type)
        .order_by(DailyCoachSession.session_date.desc(), DailyCoachSession.id.desc())
        .first()
    )

    # Recent sessions list for history rail (max 5)
    recent_sessions = (
        DailyCoachSession.query.filter_by(user_id=current_user.id, path_type=path_type)
        .order_by(DailyCoachSession.session_date.desc(), DailyCoachSession.id.desc())
        .limit(5)
        .all()
    )

    streak_count = _compute_streak(current_user.id, path_type) if is_pro_user else 0

    # NOTE: we do NOT pass `feature_paths` here; we rely on app.context_processor
    # so navbar links stay consistent across all pages.
    return render_template(
        "coach/index.html",
        path_type=path_type,
        is_pro_user=is_pro_user,
        has_gold=has_gold,
        profile_snapshot=profile_snapshot,
        today=today,
        today_session=active_session,  # kept name for template compatibility
        last_session=last_session,
        recent_sessions=recent_sessions,
        streak_count=streak_count,
    )


@coach_bp.route("/start", methods=["POST"], endpoint="start")
@login_required
def start():
    """
    Entry point when a user clicks "Start Coach".

    Treated as a WEEKLY execution view:
      - Enforces email verification
      - Enforces Pro-only
      - Enforces Gold-credit availability
      - If a current active (open) session exists → redirect to it
      - Else:
          - Calls AI engine to generate this week's plan
          - Creates DailyCoachSession + DailyCoachTask rows
          - Deducts Gold credits using feature key "dream_planner"
          - Redirects to the session view
    """
    path_type = _normalize_path_type(
        request.form.get("path_type") or request.args.get("path_type")
    )

    # 🔒 Email verification guard
    if not getattr(current_user, "verified", False):
        flash(
            "Please verify your email with a login code before using Weekly Coach.",
            "warning",
        )
        return redirect(url_for("auth.otp_request"))

    # 🔒 Pro-only gate
    if not _current_is_pro_user():
        flash(
            "Weekly Coach is available for Pro ⭐ members only. "
            "Upgrade to unlock guided execution from your Dream Plan.",
            "warning",
        )
        return redirect(url_for("billing.index"))

    # 🔒 Gold-based gate (uses Dream Planner's Gold pool for now)
    try:
        if not can_afford(current_user, "dream_planner", currency="gold"):
            flash(
                "You don’t have enough Gold ⭐ credits to use Weekly Coach. "
                "Upgrade your plan or add more credits in the Coins Shop.",
                "warning",
            )
            return redirect(url_for("billing.index"))
    except Exception:
        current_app.logger.exception("Coach: can_afford check failed in /start.")
        flash(
            "We could not check your Gold credits right now. Please try again in a bit.",
            "danger",
        )
        return redirect(url_for("coach.index", path_type=path_type))

    today = _today_date()
    profile_snapshot = load_profile_snapshot(current_user)

    # If an active session already exists for this path_type, just continue it
    existing_active = _find_active_session(current_user.id, path_type)
    if existing_active:
        return redirect(url_for("coach.session", session_id=existing_active.id))

    # Load latest Dream Plan snapshot (if any) for this path_type
    dream_plan = None
    latest_snapshot = (
        DreamPlanSnapshot.query.filter_by(
            user_id=current_user.id,
            path_type=path_type,
        )
        .order_by(DreamPlanSnapshot.created_at.desc(), DreamPlanSnapshot.id.desc())
        .first()
    )

    if latest_snapshot:
        try:
            dream_plan = json.loads(latest_snapshot.plan_json or "{}")
        except Exception:
            current_app.logger.exception(
                "Coach: failed to decode DreamPlanSnapshot JSON."
            )
            dream_plan = None

    # Simple plan title – later you can pipe more info from Dream Planner
    if latest_snapshot and latest_snapshot.plan_title:
        plan_title = latest_snapshot.plan_title
    else:
        if path_type == "startup":
            plan_title = "Weekly Coach — Startup path"
        else:
            plan_title = "Weekly Coach — Job path"

    # Build progress history for AI
    history = _progress_history_for_user(current_user.id, path_type)

    # Determine "week index" inside their longer roadmap.
    last_for_path = (
        DailyCoachSession.query.filter_by(user_id=current_user.id, path_type=path_type)
        .order_by(DailyCoachSession.session_date.desc(), DailyCoachSession.id.desc())
        .first()
    )
    next_week_index = (last_for_path.day_index or 0) + 1 if last_for_path else 1

    try:
        plan_dict, used_live_ai = generate_daily_coach_plan(
            path_type=path_type,
            dream_plan=dream_plan,
            progress_history=history,
            session_date=today.isoformat(),
            day_index=next_week_index,
            return_source=True,
        )
    except Exception:
        current_app.logger.exception("Coach: AI generation failed.")
        db.session.rollback()
        flash(
            "Weekly Coach had an internal error while generating your checklist. "
            "Please try again in a bit.",
            "danger",
        )
        return redirect(url_for("coach.index", path_type=path_type))

    # Normalize plan_dict
    session_date_str = plan_dict.get("session_date") or today.isoformat()
    ai_note = plan_dict.get("ai_note") or ""
    tasks_data = plan_dict.get("tasks") or []
    meta = plan_dict.get("meta") or {}
    plan_digest = meta.get("inputs_digest")

    # Prefer Dream Plan snapshot digest if available
    if latest_snapshot and latest_snapshot.inputs_digest:
        plan_digest = latest_snapshot.inputs_digest

    # Optional project-aware metadata from AI
    # Shape is intentionally loose so older AI versions won't break anything.
    project_links_data = plan_dict.get("project_links") or []

    try:
        # Parse date
        try:
            session_date_obj = date.fromisoformat(session_date_str[:10])
        except Exception:
            session_date_obj = today

        # Create session row
        session = DailyCoachSession(
            user_id=current_user.id,
            path_type=path_type,
            plan_digest=plan_digest,
            plan_title=plan_title,
            session_date=session_date_obj,
            day_index=plan_dict.get("day_index") or next_week_index,
            ai_note=ai_note,
            reflection=None,
            is_closed=False,
        )
        db.session.add(session)
        db.session.flush()  # get session.id

        # Create task rows (with optional project mapping)
        for t in tasks_data:
            title = (t.get("title") or "").strip()
            if not title:
                continue

            # Optional project fields coming from the AI
            raw_project_id = t.get("project_id")
            milestone_title = (t.get("milestone_title") or "").strip() or None
            milestone_step = (t.get("milestone_step") or "").strip() or None

            # Map AI "suggested_minutes" → model "estimated_minutes"
            suggested_raw = t.get("suggested_minutes")
            estimated_minutes = suggested_raw if isinstance(suggested_raw, int) else None

            # Map AI difficulty string → 1–5 scale
            difficulty_str = (t.get("difficulty") or "").strip().lower()
            difficulty_map = {"easy": 1, "medium": 3, "hard": 5}
            difficulty_val = difficulty_map.get(difficulty_str)

            # Tags list → JSON
            tags_val = t.get("tags") or []
            if isinstance(tags_val, list):
                tags_clean = [str(x)[:32] for x in tags_val[:8] if str(x).strip()]
            else:
                tags_clean = None

            dp_project = None
            if raw_project_id:
                try:
                    dp_project = (
                        DreamPlanProject.query.filter_by(
                            id=int(raw_project_id), user_id=current_user.id
                        )
                        .first()
                    )
                except Exception:
                    dp_project = None

            task = DailyCoachTask(
                session_id=session.id,
                title=title,
                detail=(t.get("detail") or "").strip(),
                category=(t.get("category") or "").strip() or None,
                sort_order=t.get("sort_order"),
                is_done=bool(t.get("is_done", False)),
                project_id=dp_project.id if dp_project else None,
                milestone_title=milestone_title,
                milestone_step=milestone_step,
                estimated_minutes=estimated_minutes,
                difficulty=difficulty_val,
                tags=tags_clean,
            )
            db.session.add(task)

        # Create SessionProjectLink rows if AI provided them
        for pl in project_links_data:
            try:
                dp_id = pl.get("dream_plan_project_id") or pl.get("project_id")
                if not dp_id:
                    continue

                dp = (
                    DreamPlanProject.query.filter_by(
                        id=int(dp_id), user_id=current_user.id
                    )
                    .first()
                )
                if not dp:
                    continue

                link = SessionProjectLink(
                    session_id=session.id,
                    dream_plan_project_id=dp.id,
                    week_index=session.day_index or next_week_index,
                    milestone_title=(pl.get("milestone_title") or "").strip() or None,
                    milestone_detail=(pl.get("milestone_detail") or "").strip() or None,
                    is_completed=bool(pl.get("is_completed", False)),
                )
                db.session.add(link)
            except Exception:
                # Don't break the whole session if one project link is malformed
                current_app.logger.exception(
                    "Coach: error creating SessionProjectLink from AI payload."
                )

        db.session.commit()

    except Exception:
        current_app.logger.exception("Coach: DB error creating session/tasks.")
        db.session.rollback()
        flash(
            "We could not save your Weekly Coach session. "
            "Please try again in a bit.",
            "danger",
        )
        return redirect(url_for("coach.index", path_type=path_type))

    # 🔻 Deduct Gold credits AFTER successful generation & DB save
    try:
        run_id = plan_digest or f"coach_{session.id}"
        if not deduct_pro(current_user, "dream_planner", run_id=run_id):
            current_app.logger.warning(
                "Coach: deduct_pro failed after session creation for user %s",
                current_user.id,
            )
            flash(
                "Your Weekly Coach session was created, but your Pro credits could not be "
                "updated correctly. Please contact support if this keeps happening.",
                "warning",
            )
    except Exception as e:
        current_app.logger.exception("Coach credit deduction error: %s", e)
        flash(
            "Your Weekly Coach session was created, but we had trouble updating your credits. "
            "Please contact support if this keeps happening.",
            "warning",
        )

    return redirect(url_for("coach.session", session_id=session.id))


@coach_bp.route("/session/<int:session_id>", methods=["GET"], endpoint="session")
@login_required
def session_view(session_id: int):
    """
    View an existing Weekly Coach session.
    """
    session = DailyCoachSession.query.filter_by(
        id=session_id, user_id=current_user.id
    ).first()

    if not session:
        flash("That Coach session could not be found.", "warning")
        return redirect(url_for("coach.index"))

    path_type = session.path_type or "job"
    profile_snapshot = load_profile_snapshot(current_user)

    tasks = session.tasks or []
    streak = _compute_streak(current_user.id, path_type)

    # Phase / week labels for the upgraded UI.
    # For now we infer phase from week index (3-phase roadmap):
    #   Weeks 1–4   → Phase 1
    #   Weeks 5–8   → Phase 2
    #   Weeks 9+    → Phase 3
    week_index = session.day_index or 1
    if week_index <= 4:
        phase_label = "Phase 1 · Foundation"
    elif week_index <= 8:
        phase_label = "Phase 2 · Projects & Depth"
    else:
        phase_label = "Phase 3 · Showcase & Applications"

    week_label = f"Week {week_index}"
    session_scope_label = "This week"

    # NOTE: do NOT pass feature_paths here; use global from context_processor
    return render_template(
        "coach/session.html",
        path_type=path_type,
        plan_title=session.plan_title,
        session_id=session.id,
        session_date=session.session_date.strftime("%d %b %Y")
        if session.session_date
        else None,
        day_index=week_index,
        streak_count=streak,
        tasks=tasks,
        ai_note=session.ai_note,
        reflection=session.reflection,
        profile_snapshot=profile_snapshot,
        phase_label=phase_label,
        week_label=week_label,
        session_scope_label=session_scope_label,
        session_is_closed=bool(session.is_closed),
    )


@coach_bp.route(
    "/session/<int:session_id>/update", methods=["POST"], endpoint="update_session"
)
@login_required
def update_session(session_id: int):
    """
    Update this week's tasks completion state.
    """
    session = DailyCoachSession.query.filter_by(
        id=session_id, user_id=current_user.id
    ).first()

    if not session:
        flash("That Coach session could not be found.", "warning")
        return redirect(url_for("coach.index"))

    # Update task completion based on checkboxes
    form = request.form
    try:
        for t in session.tasks or []:
            key = f"task_{t.id}"
            t.is_done = key in form

        # Mark week as closed if user clicked the "mark done" button
        mark_done_flag = form.get("mark_done")
        if mark_done_flag:
            session.is_closed = True

        db.session.commit()
        flash("This week’s progress was saved.", "success")
    except Exception:
        current_app.logger.exception("Coach: error updating tasks.")
        db.session.rollback()
        flash(
            "We had trouble saving your progress. Please try again.",
            "danger",
        )

    return redirect(url_for("coach.session", session_id=session.id))


@coach_bp.route(
    "/session/<int:session_id>/reflect", methods=["POST"], endpoint="reflect"
)
@login_required
def reflect(session_id: int):
    """
    Save reflection note for this Coach session.
    """
    session = DailyCoachSession.query.filter_by(
        id=session_id, user_id=current_user.id
    ).first()

    if not session:
        flash("That Coach session could not be found.", "warning")
        return redirect(url_for("coach.index"))

    text = (request.form.get("reflection") or "").strip()

    try:
        session.reflection = text or None
        db.session.commit()
        flash("Reflection saved.", "success")
    except Exception:
        current_app.logger.exception("Coach: error saving reflection.")
        db.session.rollback()
        flash(
            "We had trouble saving your reflection. Please try again.",
            "danger",
        )

    return redirect(url_for("coach.session", session_id=session.id))
