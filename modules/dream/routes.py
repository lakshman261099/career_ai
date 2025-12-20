# modules/dream/routes.py
"""
Dream Planner Routes - ASYNC VERSION

Changes from sync version:
1. Added async processing via RQ workers
2. POST now creates snapshot → enqueues job → shows processing page
3. Added /api/status/<snapshot_id> endpoint for polling
4. Added /result/<snapshot_id> endpoint for viewing completed plans
5. Credits still deducted BEFORE enqueue (refunded on failure by worker)
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
# Helpers (unchanged)
# -----------------------------
def _current_is_pro_user() -> bool:
    """Helper: determine if user is Pro based on flags + subscription_status."""
    if not getattr(current_user, "is_authenticated", False):
        return False
    if getattr(current_user, "is_pro", False):
        return True
    status = (getattr(current_user, "subscription_status", "free") or "free").lower()
    return status == "pro"


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
    Dream Planner (ASYNC flow)

    - Pro-only feature (requires Pro subscription)
    - Consumes Gold ⭐ credits (feature key: 'dream_planner')
    - Two modes: 'job' (Dream Job Planner) or 'startup' (Dream Startup Planner)
    
    POST flow:
    1. Validate user is Pro + has credits
    2. Deduct credits FIRST (reserve them)
    3. Create empty snapshot in DB
    4. Enqueue background job
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

        # 🔒 Email verification guard
        if not getattr(current_user, "verified", False):
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
        target_lpa = (request.form.get("target_lpa") or "").strip()
        timeline_months_raw = (request.form.get("timeline_months") or "").strip()
        hours_raw = (request.form.get("hours_per_day") or "").strip()
        company_prefs = (request.form.get("company_prefs") or "").strip()
        extra_context = (request.form.get("extra_context") or "").strip()

        # Optional startup-specific fields
        startup_budget_range = (
            request.form.get("startup_budget_range")
            or request.form.get("startup_budget")
            or ""
        ).strip()

        try:
            timeline_months = int(timeline_months_raw or 6)
        except Exception:
            timeline_months = 6

        try:
            hours_per_day = int(hours_raw or 2)
        except Exception:
            hours_per_day = 2

        # Profile + resume context for AI
        profile = _profile_json(current_user.id)
        skills_json = _skills_json_from_user()
        resume_text = _latest_resume_text(current_user.id)

        # Shared AI inputs
        ai_inputs = {
            "target_role": target_role,
            "target_salary_lpa": target_lpa,
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
            current_app.logger.exception("Dream Planner: failed to create snapshot: %s", e)
            db.session.rollback()
            
            # Refund credits since we can't proceed
            try:
                from modules.credits.engine import refund
                refund(current_user, "dream_planner", currency="gold", run_id=run_id, commit=True)
            except Exception:
                pass
            
            flash(
                "We couldn't start your Dream Plan right now. Your credits were refunded. Please try again.",
                "danger",
            )
            return redirect(url_for("dream.index", path_type=path_type))

        # -----------------------------------
        # ✅ STEP 3: Enqueue background job
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
            current_app.logger.exception("Dream Planner: failed to enqueue job: %s", e)
            
            # Mark snapshot as failed
            try:
                snapshot.plan_json = json.dumps({
                    "_status": "failed",
                    "_error": "Failed to enqueue background job"
                })
                db.session.commit()
            except Exception:
                db.session.rollback()
            
            # Refund credits
            try:
                from modules.credits.engine import refund
                refund(current_user, "dream_planner", currency="gold", run_id=run_id, commit=True)
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
# modules/dream/routes.py - PART 2 (append to part 1)

@dream_bp.route("/processing/<int:snapshot_id>", methods=["GET"], endpoint="processing")
@login_required
def processing(snapshot_id):
    """
    Processing page - shows animated loader while worker generates plan.
    Polls /api/status/<snapshot_id> endpoint.
    Redirects to /result/<snapshot_id> when complete.
    """
    snapshot = DreamPlanSnapshot.query.filter_by(
        id=snapshot_id, user_id=current_user.id
    ).first()
    
    if not snapshot:
        flash("Dream Plan not found.", "warning")
        return redirect(url_for("dream.index"))
    
    # Check if already completed
    try:
        plan_data = json.loads(snapshot.plan_json or "{}")
        status = plan_data.get("_status", "queued")
        
        if status == "completed":
            # Already done, redirect to result
            return redirect(url_for("dream.result", snapshot_id=snapshot_id))
        
        if status == "failed":
            flash(
                "Your Dream Plan generation failed. Your credits were refunded. Please try again.",
                "danger"
            )
            return redirect(url_for("dream.index", path_type=snapshot.path_type))
    except Exception:
        pass
    
    # Show processing page
    return render_template(
        "dream/processing.html",
        snapshot_id=snapshot_id,
        path_type=snapshot.path_type,
        status_url=url_for("dream.api_status", snapshot_id=snapshot_id, _external=False),
        result_url=url_for("dream.result", snapshot_id=snapshot_id, _external=False),
    )


@dream_bp.route("/api/status/<int:snapshot_id>", methods=["GET"], endpoint="api_status")
@login_required
def api_status(snapshot_id):
    """
    API endpoint for polling job status.
    Returns JSON: {"status": "queued|processing|completed|failed", ...}
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
            response["error"] = plan_data.get("error", "Unknown error")
        
        return jsonify(response)
    
    except Exception as e:
        current_app.logger.exception("Dream status API error: %s", e)
        return jsonify({
            "status": "error",
            "error": "Failed to check status"
        }), 500


@dream_bp.route("/result/<int:snapshot_id>", methods=["GET"], endpoint="result")
@login_required
def result(snapshot_id):
    """
    Show completed Dream Plan.
    This renders the same result.html template as the sync version.
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
                "danger"
            )
            return redirect(url_for("dream.index", path_type=snapshot.path_type))
        
        if status != "completed":
            # Still processing, redirect back to processing page
            return redirect(url_for("dream.processing", snapshot_id=snapshot_id))
        
        # Extract path_type from plan or snapshot
        path_type = plan_view.get("mode") or plan_view.get("input", {}).get("path_type") or snapshot.path_type
        
        is_pro_user = _current_is_pro_user()
        profile_snapshot = load_profile_snapshot(current_user)
        
        return render_template(
            "dream/result.html",
            plan=plan_view,
            path_type=path_type,
            is_pro_user=is_pro_user,
            profile_snapshot=profile_snapshot,
            snapshot_id=snapshot_id,
        )
    
    except Exception as e:
        current_app.logger.exception("Dream result render error: %s", e)
        flash("Could not load your Dream Plan. Please try again.", "error")
        return redirect(url_for("dream.index"))


# -----------------------------------
# Project Selection (unchanged)
# -----------------------------------

@dream_bp.route("/projects/select", methods=["POST"], endpoint="select_projects")
@login_required
def select_projects():
    """
    Handle project selection from the Dream Plan UI.
    (Same as sync version - no changes needed)
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

    # Get selected project indices from form
    selected_indices_raw = request.form.getlist("selected_projects")
    selected_indices = []
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
                selected_projects.append({
                    "title": proj.get("title", "Project"),
                    "description": proj.get("description", ""),
                    "index": idx,
                })
            elif isinstance(proj, str):
                selected_projects.append({
                    "title": proj,
                    "description": "",
                    "index": idx,
                })

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
# History / Plans List (unchanged)
# -----------------------------------

@dream_bp.route("/plans", methods=["GET"], endpoint="plans")
@login_required
def plans():
    """
    Show user's Dream Plan history.
    (Same as sync version - no changes needed)
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
            plans_data.append({
                "snapshot": snap,
                "status": status,
            })
        except Exception:
            plans_data.append({
                "snapshot": snap,
                "status": "error",
            })

    return render_template(
        "dream/plans.html",
        plans=plans_data,
        is_pro_user=_current_is_pro_user(),
    )