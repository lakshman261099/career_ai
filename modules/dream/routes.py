# modules/dream/routes.py
"""
Dream Planner Routes - ASYNC VERSION with SYNC UPGRADE

Hybrid approach:
- Keeps async processing via RQ workers (production-ready)
- Worker now uses generate_sync_plan for better AI output
- Added probability matrix, bold truth, project proposals
- Added lock_plan endpoint for project selection handshake
- Result page shows new sync data structure

Changes from previous version:
1. Worker uses generate_sync_plan (better AI output)
2. Added lock_plan endpoint
3. Updated result() to extract sync-specific data
4. Timeline validation (3/6/12/24 LPA)

✅ IMPORTANT (storage + no resurrection):
- Coach deletes saved plans permanently (hard delete)
- If a Coach plan is deleted, Coach stamps the Dream snapshot JSON with `_coach_deleted_at`
  so auto-promotion won't recreate it.
- Dream lock_plan will NOT "resurrect" any deleted Coach plan, and will not re-promote
  a snapshot that is stamped `_coach_deleted_at`. User must generate a NEW Dream plan if needed.
"""

from datetime import datetime
import re
import json
import uuid

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
from sqlalchemy import desc

from models import ResumeAsset, UserProfile, DreamPlanSnapshot, db
from modules.common.profile_loader import load_profile_snapshot
from modules.credits.engine import can_afford, deduct_pro

# ✅ Import async tasks
from modules.dream.tasks import enqueue_dream_plan_generation

dream_bp = Blueprint(
    "dream",
    __name__,
    template_folder="../../templates/dream",
)


# -----------------------------
# Helpers
# -----------------------------
def _current_is_pro_user() -> bool:
    """Helper: determine if user is Pro based on flags + subscription_status."""
    if not getattr(current_user, "is_authenticated", False):
        return False
    if getattr(current_user, "is_pro", False):
        return True
    status = (getattr(current_user, "subscription_status", "free") or "free").lower()
    return status == "pro"


def _current_is_verified_user() -> bool:
    """
    Email verification guard (schema-safe):
    supports either `email_verified` or legacy `verified`.
    """
    try:
        return bool(
            getattr(current_user, "email_verified", False)
            or getattr(current_user, "verified", False)
        )
    except Exception:
        return False


def _latest_resume_text(user_id: int) -> str:
    """Get the latest extracted resume text for richer Dream Planner context."""
    r = (
        ResumeAsset.query.filter_by(user_id=user_id)
        .order_by(desc(ResumeAsset.created_at))
        .first()
    )
    return (r.text or "") if r else ""


def _profile_json(user_id: int) -> dict:
    """Pack Profile Portal data into a JSON snapshot for AI context."""
    prof = UserProfile.query.filter_by(user_id=user_id).first()
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


def _skills_json_from_user() -> dict:
    """Optional: structured skills extracted from resume/profile."""
    try:
        return getattr(current_user, "skills_json", None) or {}
    except Exception:
        return {}


def _normalize_path_type(raw: str | None) -> str:
    """Normalize path_type input: 'startup' → startup planner, anything else → job planner."""
    v = (raw or "").strip().lower()
    return "startup" if v == "startup" else "job"


def _ensure_phases(raw_phases) -> list[dict]:
    """
    Accepts any raw structure and returns a list of:
        { "label": str, "items": [ ... ] }
    If invalid, returns a single empty phase to avoid UI crashes.
    """
    phases: list[dict] = []
    if isinstance(raw_phases, list):
        for ph in raw_phases:
            if isinstance(ph, dict):
                label = str(ph.get("label", "")).strip() or "Phase"
                items = ph.get("items") or []
                if isinstance(items, list):
                    phases.append({"label": label, "items": items})
                else:
                    phases.append({"label": label, "items": []})
    if not phases:
        phases = [{"label": "Phase 1", "items": []}]
    return phases


def _legacy_plan_core_from_phases(phases: list[dict]) -> dict:
    """
    Build a legacy 30/60/90-style plan_core from phase-based data
    so older template sections that still use plan.plan_core.* don't break.
    """
    base = {
        "focus_30": "",
        "focus_60": "",
        "focus_90": "",
        "weeks_30": [],
        "weeks_60": [],
        "weeks_90": [],
    }

    if not isinstance(phases, list):
        return base

    # Phase 1 → 30
    if len(phases) >= 1:
        p1 = phases[0] or {}
        base["focus_30"] = str(p1.get("label") or "")[:300]
        items = p1.get("items") or []
        if isinstance(items, list):
            base["weeks_30"] = [str(x).strip() for x in items if str(x).strip()]

    # Phase 2 → 60
    if len(phases) >= 2:
        p2 = phases[1] or {}
        base["focus_60"] = str(p2.get("label") or "")[:300]
        items = p2.get("items") or []
        if isinstance(items, list):
            base["weeks_60"] = [str(x).strip() for x in items if str(x).strip()]

    # Phase 3 → 90
    if len(phases) >= 3:
        p3 = phases[2] or {}
        base["focus_90"] = str(p3.get("label") or "")[:300]
        items = p3.get("items") or []
        if isinstance(items, list):
            base["weeks_90"] = [str(x).strip() for x in items if str(x).strip()]

    return base


def _split_to_list(val):
    """Helper to convert either list or semi-structured string into a clean list."""
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [p.strip() for p in re.split(r"[;•\n]", val) if p.strip()]
    return []


def _max_projects_for_timeline(months: int | None) -> int:
    """
    Map timeline_months → max number of projects the student should select.
      <= 3  → 1 project
      <= 6  → 2 projects
      <= 12 → 3 projects
      > 12  → 4 projects
    """
    try:
        m = int(months or 0)
    except Exception:
        m = 0
    if m <= 3:
        return 1
    if m <= 6:
        return 2
    if m <= 12:
        return 3
    return 4


# -----------------------------
# Routes - ASYNC VERSION
# -----------------------------

@dream_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Dream Planner (ASYNC flow with SYNC upgrade)

    - Pro-only feature (requires Pro subscription)
    - Consumes Gold ⭐ credits (feature key: 'dream_planner')
    - Two modes: 'job' (Dream Job Planner) or 'startup' (Dream Startup Planner)

    POST flow:
    1. Validate user is Pro + has credits
    2. Deduct credits FIRST (reserve them)
    3. Create empty snapshot in DB
    4. Enqueue background job (worker uses generate_sync_plan)
    5. Redirect to processing page
    """
    is_pro_user = _current_is_pro_user()
    path_type = _normalize_path_type(
        request.form.get("path_type") or request.args.get("path_type")
    )
    profile_snapshot = load_profile_snapshot(current_user)

    # -----------------------------------
    # POST — Generate Dream Plan (ASYNC)
    # -----------------------------------
    if request.method == "POST":

        # 🔒 Email verification guard (schema-safe)
        if not _current_is_verified_user():
            flash(
                "Please verify your email with a login code before using Dream Planner.",
                "warning",
            )
            return redirect(url_for("auth.otp_request"))

        # 🔒 Pro-only gate
        if not is_pro_user:
            flash(
                "Dream Planner is available for Pro ⭐ members only. "
                "Upgrade to unlock full Dream Job and Dream Startup plans.",
                "warning",
            )
            return redirect(url_for("billing.index"))

        # 🔒 Gold credit check (Dream Planner is Gold-based)
        try:
            if not can_afford(current_user, "dream_planner", currency="gold"):
                flash(
                    "You don't have enough Gold ⭐ credits to run Dream Planner.",
                    "warning",
                )
                return redirect(url_for("billing.index"))
        except Exception:
            current_app.logger.exception("Dream Planner: can_afford check failed.")
            flash(
                "We could not check your Gold credits right now. Please try again in a bit.",
                "danger",
            )
            return redirect(url_for("dream.index", path_type=path_type))

        # --------------------------
        # Collect inputs from form
        # --------------------------
        target_role = (request.form.get("target_role") or "").strip()
        target_lpa = (request.form.get("target_lpa") or "12").strip()
        timeline_raw = (request.form.get("timeline") or "3_months").strip()
        # hours_per_day is no longer an explicit field in UI; default to ~2h/day
        hours_raw = (request.form.get("hours_per_day") or "").strip()
        company_prefs = (request.form.get("company_prefs") or "").strip()
        extra_context = (request.form.get("extra_context") or "").strip()

        # Optional startup-specific fields (UI may or may not expose these)
        startup_budget_range = (
            request.form.get("startup_budget_range")
            or request.form.get("startup_budget")
            or ""
        ).strip()

        # ✅ Validate LPA (must be 3, 6, 12, or 24)
        if target_lpa not in ["3", "6", "12", "24"]:
            target_lpa = "12"  # Default

        # ✅ Validate timeline
        if timeline_raw not in ["28_days", "3_months"]:
            timeline_raw = "3_months"  # Default

        # Derive timeline_months for backward compatibility with worker
        timeline_months = 1 if timeline_raw == "28_days" else 3

        try:
            hours_per_day = int(hours_raw or 2)
        except Exception:
            hours_per_day = 2

        # Profile + resume context for AI
        profile = _profile_json(current_user.id)
        skills_json = _skills_json_from_user()
        resume_text = _latest_resume_text(current_user.id)

        # Shared AI inputs (passed to worker)
        ai_inputs = {
            "target_role": target_role,
            "target_salary_lpa": target_lpa,
            "timeline": timeline_raw,  # NEW: Pass raw timeline string
            "timeline_months": timeline_months,
            "hours_per_day": hours_per_day,
            "company_preferences": company_prefs,
            "extra_context": extra_context,
        }

        # Startup-specific hints
        if path_type == "startup":
            ai_inputs.update(
                {
                    "startup_theme": target_role,
                    "startup_notes": extra_context,
                    "startup_timeline_months": timeline_months,
                    "startup_budget_range": startup_budget_range,
                }
            )

        # -----------------------------------
        # ✅ STEP 1: Deduct credits BEFORE enqueue (hard gate)
        # -----------------------------------
        run_id = f"dream_{current_user.id}_{uuid.uuid4().hex[:8]}"
        try:
            ok = deduct_pro(current_user, "dream_planner", run_id=run_id)
            if not ok:
                flash(
                    "We couldn't deduct your Gold ⭐ credits right now. Please try again.",
                    "danger",
                )
                return redirect(url_for("dream.index", path_type=path_type))
        except Exception:
            current_app.logger.exception("Dream Planner: credit deduction failed.")
            flash(
                "We couldn't process your credits right now. Please try again later.",
                "danger",
            )
            return redirect(url_for("dream.index", path_type=path_type))

        # -----------------------------------
        # ✅ STEP 2: Create empty snapshot (marks job as "queued")
        # -----------------------------------
        try:
            if path_type == "job":
                plan_title = f"Dream Job: {target_role or 'Your Role'}"
            else:
                plan_title = f"Dream Startup: {target_role or 'Founder path'}"

            snapshot = DreamPlanSnapshot(
                user_id=current_user.id,
                path_type=path_type,
                plan_title=str(plan_title)[:255],
                plan_json=json.dumps({"_status": "queued", "_run_id": run_id}),
                inputs_digest=None,  # Will be set by worker
            )
            db.session.add(snapshot)
            db.session.flush()
            snapshot_id = snapshot.id
            db.session.commit()
        except Exception as e:
            current_app.logger.exception(
                "Dream Planner: failed to create snapshot: %s", e
            )
            db.session.rollback()

            # Refund credits since we can't proceed
            try:
                from modules.credits.engine import refund

                refund(
                    current_user,
                    "dream_planner",
                    currency="gold",
                    run_id=run_id,
                    commit=True,
                )
            except Exception:
                pass

            flash(
                "We couldn't start your Dream Plan right now. Your credits were refunded. Please try again.",
                "danger",
            )
            return redirect(url_for("dream.index", path_type=path_type))

        # -----------------------------------
        # ✅ STEP 3: Enqueue background job (worker uses generate_sync_plan)
        # -----------------------------------
        try:
            job_id = enqueue_dream_plan_generation(
                user_id=current_user.id,
                snapshot_id=snapshot_id,
                path_type=path_type,
                ai_inputs=ai_inputs,
                profile_json=profile,
                skills_json=skills_json,
                resume_text=resume_text,
                run_id=run_id,
            )
            current_app.logger.info(
                f"Dream Planner: enqueued job {job_id} for snapshot {snapshot_id}"
            )
        except Exception as e:
            current_app.logger.exception(
                "Dream Planner: failed to enqueue job: %s", e
            )

            # Mark snapshot as failed
            try:
                snapshot.plan_json = json.dumps(
                    {
                        "_status": "failed",
                        "_error": "Failed to enqueue background job",
                    }
                )
                db.session.commit()
            except Exception:
                db.session.rollback()

            # Refund credits
            try:
                from modules.credits.engine import refund

                refund(
                    current_user,
                    "dream_planner",
                    currency="gold",
                    run_id=run_id,
                    commit=True,
                )
            except Exception:
                pass

            flash(
                "We couldn't start your Dream Plan right now. Your credits were refunded. Please try again.",
                "danger",
            )
            return redirect(url_for("dream.index", path_type=path_type))

        # -----------------------------------
        # ✅ STEP 4: Redirect to processing page
        # -----------------------------------
        return redirect(url_for("dream.processing", snapshot_id=snapshot_id))

    # -----------------------------------
    # GET — Render form
    # -----------------------------------
    return render_template(
        "dream/index.html",
        path_type=path_type,
        is_pro_user=is_pro_user,
        profile_snapshot=profile_snapshot,
    )


@dream_bp.route("/processing/<int:snapshot_id>", methods=["GET"], endpoint="processing")
@login_required
def processing(snapshot_id):
    """
    Show a processing/loading page while the background job runs.
    Uses client-side polling to check status.
    """
    snapshot = DreamPlanSnapshot.query.filter_by(
        id=snapshot_id, user_id=current_user.id
    ).first()

    if not snapshot:
        flash("Dream Plan not found.", "warning")
        return redirect(url_for("dream.index"))

    # If already completed, redirect directly to result
    try:
        plan_data = json.loads(snapshot.plan_json or "{}")
        status = plan_data.get("_status", "queued")
        if status == "completed":
            return redirect(url_for("dream.result", snapshot_id=snapshot_id))
        if status == "failed":
            flash(
                "Dream Plan generation failed. Your credits were refunded.",
                "danger",
            )
            return redirect(url_for("dream.index", path_type=snapshot.path_type))
    except Exception:
        pass

    return render_template(
        "dream/processing.html",
        snapshot_id=snapshot_id,
        path_type=snapshot.path_type,
        # ✅ CRITICAL: your processing.html JS requires these
        status_url=url_for("dream.status_api", snapshot_id=snapshot_id),
        result_url=url_for("dream.result", snapshot_id=snapshot_id),
    )


@dream_bp.route("/api/status/<int:snapshot_id>", methods=["GET"], endpoint="status_api")
@login_required
def status_api(snapshot_id):
    """
    API endpoint for polling job status.
    Returns JSON: { "status": "queued|processing|completed|failed", "error": "..." }
    """
    snapshot = DreamPlanSnapshot.query.filter_by(
        id=snapshot_id, user_id=current_user.id
    ).first()

    if not snapshot:
        return jsonify({"status": "not_found", "error": "Snapshot not found"}), 404

    try:
        plan_data = json.loads(snapshot.plan_json or "{}")
        status = plan_data.get("_status", "queued")

        response = {
            "status": status,
            "snapshot_id": snapshot_id,
        }

        if status == "failed":
            # Use _error if present (matches how we set it elsewhere)
            response["error"] = plan_data.get(
                "_error", plan_data.get("error", "Unknown error")
            )

        return jsonify(response)

    except Exception as e:
        current_app.logger.exception("Dream status API error: %s", e)
        return jsonify(
            {
                "status": "error",
                "error": "Failed to check status",
            }
        ), 500


@dream_bp.route("/result/<int:snapshot_id>", methods=["GET"], endpoint="result")
@login_required
def result(snapshot_id):
    """
    Show completed Dream Plan with SYNC UPGRADE data:
    - Probability Matrix (current vs projected for 3/6/12/24 LPA)
    - Bold Truth (reality check)
    - Project Proposals
    - Lock Plan button (if not locked)
    """
    snapshot = DreamPlanSnapshot.query.filter_by(
        id=snapshot_id, user_id=current_user.id
    ).first()

    if not snapshot:
        flash("Dream Plan not found.", "warning")
        return redirect(url_for("dream.index"))

    try:
        plan_view = json.loads(snapshot.plan_json or "{}")
        status = plan_view.get("_status", "unknown")

        if status == "failed":
            flash(
                "This Dream Plan generation failed. Your credits were refunded. Please try again.",
                "danger",
            )
            return redirect(url_for("dream.index", path_type=snapshot.path_type))

        if status != "completed":
            # Still processing, redirect back to processing page
            return redirect(url_for("dream.processing", snapshot_id=snapshot_id))

        # Normalize phases for UI (similar to how we normalized coach tasks)
        raw_phases = plan_view.get("phases")
        normalized_phases = _ensure_phases(raw_phases)
        plan_view["phases"] = normalized_phases

        # Backwards-compatible 30/60/90 view if any code still uses plan_core
        if not plan_view.get("plan_core"):
            plan_view["plan_core"] = _legacy_plan_core_from_phases(normalized_phases)

        # Extract path_type from plan or snapshot
        path_type = (
            plan_view.get("mode")
            or plan_view.get("input", {}).get("path_type")
            or snapshot.path_type
        )

        # ✅ NEW: Extract sync-specific data
        try:
            sp = plan_view.get("selected_projects")
            is_locked = ("_locked_at" in plan_view) and isinstance(sp, list) and len(sp) > 0
        except Exception:
            is_locked = "_locked_at" in plan_view and "selected_projects" in plan_view

        # Analysis (probability matrix, bold truth)
        analysis = plan_view.get("analysis", {})
        probabilities = analysis.get("probabilities", {"3": 0, "6": 0, "12": 0, "24": 0})
        projected_probabilities = analysis.get(
            "projected_probabilities", {"3": 0, "6": 0, "12": 0, "24": 0}
        )
        bold_truth = analysis.get("bold_truth", "")
        missing_skills = analysis.get("missing_skills", [])

        # Projects (proposals from AI)
        projects = plan_view.get("projects", [])
        selected_projects = plan_view.get("selected_projects", [])

        # Coach plan metadata
        coach_plan = plan_view.get("coach_plan", {})
        total_weeks = coach_plan.get("total_weeks", 0)

        is_pro_user = _current_is_pro_user()
        profile_snapshot = load_profile_snapshot(current_user)

        return render_template(
            "dream/result.html",
            plan=plan_view,
            snapshot=snapshot,
            path_type=path_type,
            is_pro_user=is_pro_user,
            profile_snapshot=profile_snapshot,
            snapshot_id=snapshot_id,
            # ✅ NEW: Sync-specific template data
            probabilities=probabilities,
            projected_probabilities=projected_probabilities,
            bold_truth=bold_truth,
            missing_skills=missing_skills,
            projects=projects,
            selected_projects=selected_projects,
            total_weeks=total_weeks,
            is_locked=is_locked,
        )

    except Exception as e:
        current_app.logger.exception("Dream result render error: %s", e)
        flash("Could not load your Dream Plan. Please try again.", "error")
        return redirect(url_for("dream.index"))


# -----------------------------------
# ✅ NEW: Lock Plan Endpoint (Phase 2 Handshake)
# -----------------------------------

@dream_bp.route("/lock-plan/<int:snapshot_id>", methods=["POST"], endpoint="lock_plan")
@login_required
def lock_plan(snapshot_id):
    """
    Phase 2: Project Selection Handshake.

    User clicks "Lock Plan & Start Coach" after reviewing probability matrix.
    This saves selected_projects to the snapshot AND promotes it into Coach's
    selectable plan library (max 3 plans per user).

    ✅ No-resurrection policy:
    - If this snapshot has `_coach_deleted_at`, we still lock in Dream,
      but we do NOT promote/save into Coach again. User must generate a new Dream plan.
    - If a legacy soft-deleted CoachSavedPlan row exists, we HARD DELETE it
      (storage saving) and proceed normally (subject to max-3 rule).
    """
    snapshot = DreamPlanSnapshot.query.get_or_404(snapshot_id)

    # Verify ownership
    if snapshot.user_id != current_user.id:
        abort(403)

    # Load plan JSON
    try:
        plan_json = json.loads(snapshot.plan_json or "{}")
    except Exception:
        flash("Invalid plan data.", "danger")
        return redirect(url_for("dream.index"))

    # Get selected project indices from form
    selected_indices = request.form.getlist("selected_projects")  # e.g., ["0", "1"]

    if not selected_indices:
        flash("Please select at least one project.", "warning")
        return redirect(url_for("dream.result", snapshot_id=snapshot_id))

    # Extract selected projects from projects array
    projects = plan_json.get("projects", [])
    if not isinstance(projects, list):
        projects = []

    selected_projects = []
    for idx_str in selected_indices:
        try:
            idx = int(idx_str)
            if 0 <= idx < len(projects):
                selected_projects.append(projects[idx])
        except (ValueError, IndexError):
            pass

    if not selected_projects:
        flash("Invalid project selection.", "warning")
        return redirect(url_for("dream.result", snapshot_id=snapshot_id))

    # Save selected projects to plan JSON
    plan_json["selected_projects"] = selected_projects
    plan_json["_locked_at"] = datetime.utcnow().isoformat()

    # Helpful for Coach fallback redirects (safe)
    try:
        if "_snapshot_id" not in plan_json:
            plan_json["_snapshot_id"] = snapshot.id
    except Exception:
        pass

    # Persist back to Dream snapshot (LOCK ALWAYS SUCCEEDS OR FAILS IN DREAM)
    try:
        snapshot.plan_json = json.dumps(plan_json, ensure_ascii=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Could not lock plan. Please try again.", "danger")
        return redirect(url_for("dream.result", snapshot_id=snapshot_id))

    # ✅ No resurrection: if user deleted this plan from Coach earlier, do not re-save it there.
    try:
        if plan_json.get("_coach_deleted_at"):
            flash(
                "✅ Plan locked in Dream. You deleted this plan from Coach earlier, so it won’t be re-added there. "
                "Generate a new Dream plan if you want a new Coach plan slot.",
                "info",
            )
            return redirect(url_for("coach.manage_plans", path_type=snapshot.path_type))
    except Exception:
        pass

    # ✅ Promote into Coach plan library (max 3 plans per user)
    #    Hard-delete policy: we do NOT resurrect soft deleted rows. If legacy soft-deleted exists,
    #    we hard delete it to save storage and avoid uniqueness collisions.
    try:
        from models import CoachSavedPlan  # NEW model (may not exist yet)

        # Count active plans (hard delete preferred; legacy support excludes is_deleted=True)
        active_count = 0
        try:
            q = CoachSavedPlan.query.filter_by(user_id=current_user.id)
            try:
                # legacy schema
                q = q.filter_by(is_deleted=False)
            except Exception:
                pass
            active_count = q.count()
        except Exception:
            active_count = 0

        # Find existing record for this snapshot (do NOT filter is_deleted here)
        existing = None

        # Preferred schema: dream_snapshot_id
        try:
            existing = CoachSavedPlan.query.filter_by(
                user_id=current_user.id,
                dream_snapshot_id=snapshot.id,
            ).first()
        except Exception:
            existing = None

        # Alternate schema: snapshot_id (defensive)
        if not existing:
            try:
                existing = CoachSavedPlan.query.filter_by(
                    user_id=current_user.id,
                    snapshot_id=snapshot.id,
                ).first()
            except Exception:
                existing = None

        # Legacy cleanup: if a soft-deleted row exists, hard-delete it (storage saving)
        if existing:
            try:
                if bool(getattr(existing, "is_deleted", False)):
                    db.session.delete(existing)
                    db.session.flush()
                    existing = None
            except Exception:
                # if we can't inspect/delete, just treat as existing and update it
                pass

        if not existing:
            if active_count >= 3:
                flash(
                    "✅ Plan locked in Dream. You already have 3 saved Coach plans — delete one in Coach to save a new plan there.",
                    "warning",
                )
                return redirect(url_for("coach.manage_plans", path_type=snapshot.path_type))

            # Create new saved plan (field-safe)
            new_saved = CoachSavedPlan(
                user_id=current_user.id,
                path_type=snapshot.path_type,
                title=snapshot.plan_title or f"Dream Plan {snapshot.id}",
                plan_json=json.dumps(plan_json, ensure_ascii=False),
            )

            # attach snapshot id if the field exists
            if hasattr(new_saved, "dream_snapshot_id"):
                new_saved.dream_snapshot_id = snapshot.id
            if hasattr(new_saved, "snapshot_id"):
                new_saved.snapshot_id = snapshot.id

            # timestamps if present
            if hasattr(new_saved, "locked_at"):
                new_saved.locked_at = datetime.utcnow()
            if hasattr(new_saved, "created_at") and getattr(new_saved, "created_at", None) is None:
                new_saved.created_at = datetime.utcnow()
            if hasattr(new_saved, "updated_at"):
                new_saved.updated_at = datetime.utcnow()

            # legacy compat: keep false, but we never rely on soft delete anymore
            if hasattr(new_saved, "is_deleted"):
                try:
                    new_saved.is_deleted = False
                except Exception:
                    pass

            db.session.add(new_saved)

        else:
            # Update existing saved plan with the latest locked version
            try:
                existing.path_type = snapshot.path_type
            except Exception:
                pass
            try:
                existing.title = snapshot.plan_title or existing.title
            except Exception:
                pass
            try:
                existing.plan_json = json.dumps(plan_json, ensure_ascii=False)
            except Exception:
                pass
            if hasattr(existing, "locked_at"):
                existing.locked_at = datetime.utcnow()
            if hasattr(existing, "updated_at"):
                existing.updated_at = datetime.utcnow()

            # legacy: keep as active if column exists
            if hasattr(existing, "is_deleted"):
                try:
                    existing.is_deleted = False
                except Exception:
                    pass

        db.session.commit()

    except Exception:
        # If migration/model not applied yet, don't crash the app.
        try:
            db.session.rollback()
        except Exception:
            pass

    flash("✅ Plan locked & saved to Coach! Select it in Coach and press Start.", "success")
    return redirect(url_for("coach.manage_plans", path_type=snapshot.path_type))


# -----------------------------------
# Project Selection (Legacy endpoint - kept for compatibility)
# -----------------------------------

@dream_bp.route("/projects/select", methods=["POST"], endpoint="select_projects")
@login_required
def select_projects():
    """
    Handle project selection from the Dream Plan UI.

    Legacy endpoint - kept for backward compatibility.
    New code should use /lock-plan instead.
    """
    path_type = _normalize_path_type(request.form.get("path_type"))
    snapshot_id_raw = request.form.get("snapshot_id")

    # Basic validation for snapshot id
    try:
        snapshot_id = int(snapshot_id_raw)
    except Exception:
        flash(
            "We couldn't save your project selection. Please regenerate your Dream Plan and try again.",
            "warning",
        )
        return redirect(url_for("dream.index", path_type=path_type))

    snapshot = DreamPlanSnapshot.query.filter_by(
        id=snapshot_id, user_id=current_user.id
    ).first()
    if not snapshot:
        flash(
            "We couldn't find that Dream Plan to attach projects to. "
            "Please regenerate your plan.",
            "warning",
        )
        return redirect(url_for("dream.index", path_type=path_type))

    # Load plan JSON
    try:
        plan_json = json.loads(snapshot.plan_json or "{}")
    except Exception:
        plan_json = {}

    input_block = plan_json.get("input") or {}
    timeline_months = input_block.get("timeline_months", 6)
    max_projects = _max_projects_for_timeline(timeline_months)

    # Get selected project indices from form (new + old field names)
    selected_indices_raw = (
        request.form.getlist("selected_projects") or request.form.getlist("project_index")
    )
    selected_indices: list[int] = []
    for idx_str in selected_indices_raw:
        try:
            selected_indices.append(int(idx_str))
        except Exception:
            pass

    # Cap at max_projects
    if len(selected_indices) > max_projects:
        flash(
            f"You can only select up to {max_projects} projects based on your {timeline_months}-month timeline.",
            "warning",
        )
        selected_indices = selected_indices[:max_projects]

    # Extract selected projects from mini_projects list
    mini_projects = plan_json.get("mini_projects") or []
    selected_projects = []
    for idx in selected_indices:
        if 0 <= idx < len(mini_projects):
            proj = mini_projects[idx]
            if isinstance(proj, dict):
                selected_projects.append(
                    {
                        "title": proj.get("title", "Project"),
                        "description": "",
                        "index": idx,
                    }
                )
            elif isinstance(proj, str):
                selected_projects.append(
                    {
                        "title": proj,
                        "description": "",
                        "index": idx,
                    }
                )

    # Update plan_json with selected_projects
    plan_json["selected_projects"] = selected_projects
    plan_json["_selected_at"] = datetime.utcnow().isoformat()

    try:
        snapshot.plan_json = json.dumps(plan_json)
        db.session.commit()
        flash(
            f"✅ {len(selected_projects)} project(s) selected! Weekly Coach will focus on these.",
            "success",
        )
    except Exception as e:
        current_app.logger.exception("Dream project selection save error: %s", e)
        db.session.rollback()
        flash("Could not save your project selection. Please try again.", "error")

    return redirect(url_for("dream.result", snapshot_id=snapshot_id))


# -----------------------------------
# History / Plans List
# -----------------------------------

@dream_bp.route("/plans", methods=["GET"], endpoint="plans")
@login_required
def plans():
    """
    Show user's Dream Plan history.
    """
    try:
        snapshots = (
            DreamPlanSnapshot.query.filter_by(user_id=current_user.id)
            .order_by(DreamPlanSnapshot.created_at.desc())
            .limit(20)
            .all()
        )
    except Exception as e:
        current_app.logger.exception("Dream plans list error: %s", e)
        snapshots = []
        flash("Could not load your Dream Plans. Please refresh.", "warning")

    # Parse each snapshot to get status
    plans_data = []
    for snap in snapshots:
        try:
            plan_json = json.loads(snap.plan_json or "{}")
            status = plan_json.get("_status", "unknown")
            plans_data.append(
                {
                    "snapshot": snap,
                    "status": status,
                }
            )
        except Exception:
            plans_data.append(
                {
                    "snapshot": snap,
                    "status": "error",
                }
            )

    return render_template(
        "dream/plans.html",
        plans=plans_data,
        is_pro_user=_current_is_pro_user(),
    )
