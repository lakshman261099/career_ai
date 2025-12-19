# modules/dream/routes.py
from datetime import datetime
import re
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
from sqlalchemy import desc

from models import ResumeAsset, UserProfile, DreamPlanSnapshot, db
from modules.common.profile_loader import load_profile_snapshot
from modules.common.ai import generate_dream_plan
from modules.credits.engine import can_afford, deduct_pro

dream_bp = Blueprint(
    "dream",
    __name__,
    template_folder="../../templates/dream",
)


# -----------------------------
# Helpers
# -----------------------------
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


def _latest_resume_text(user_id: int) -> str:
    """
    Get the latest extracted resume text for richer Dream Planner context.
    """
    r = (
        ResumeAsset.query.filter_by(user_id=user_id)
        .order_by(desc(ResumeAsset.created_at))
        .first()
    )
    return (r.text or "") if r else ""


def _profile_json(user_id: int) -> dict:
    """
    Pack Profile Portal data into a JSON snapshot for AI context.
    """
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
    """
    Optional: structured skills extracted from resume/profile.
    """
    try:
        return getattr(current_user, "skills_json", None) or {}
    except Exception:
        return {}


def _normalize_path_type(raw: str | None) -> str:
    """
    Normalize path_type input:
      - 'startup' → startup planner
      - anything else → job planner
    """
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

    Maps:
      phase 1 → focus_30 / weeks_30
      phase 2 → focus_60 / weeks_60
      phase 3 → focus_90 / weeks_90
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
    """
    Helper to convert either list or semi-structured string into a clean list.
    Used for startup GTM, customers, pricing, risks, etc.
    """
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
# Routes
# -----------------------------
@dream_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Dream Planner (HTML flow)

    - Pro-only feature (requires Pro subscription).
    - Consumes Gold ⭐ credits (feature key: 'dream_planner').
    - Two modes:
        - 'job'     → Dream Job Planner
        - 'startup' → Dream Startup Planner
    """
    is_pro_user = _current_is_pro_user()
    path_type = _normalize_path_type(
        request.form.get("path_type") or request.args.get("path_type")
    )
    profile_snapshot = load_profile_snapshot(current_user)

    # -----------------------------------
    # POST — Generate Dream Plan
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
                    "You don’t have enough Gold ⭐ credits to run Dream Planner.",
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

        # Optional startup-specific fields (non-breaking if form doesn’t have them yet)
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

        # Shared AI inputs (P3 Dream Planner)
        ai_inputs = {
            "target_role": target_role,
            "target_salary_lpa": target_lpa,
            "timeline_months": timeline_months,
            "hours_per_day": hours_per_day,
            "company_preferences": company_prefs,
            "extra_context": extra_context,
        }

        # Startup-specific hints (P3 fields)
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
        # AI CALL
        # -----------------------------------
        try:
            plan_raw, used_live_ai = generate_dream_plan(
                mode=path_type,
                inputs=ai_inputs,
                profile_json=profile,
                skills_json=skills_json,
                resume_text=resume_text,
                return_source=True,
            )
        except Exception:
            current_app.logger.exception("Dream Planner AI call failed.")
            db.session.rollback()
            flash(
                "Dream Planner had an internal error while generating your plan. "
                "Your Gold credits were not deducted. Please try again later.",
                "danger",
            )
            return redirect(url_for("dream.index", path_type=path_type))

        if not isinstance(plan_raw, dict):
            plan_raw = {}

        # -----------------------------------
        # Meta information
        # -----------------------------------
        meta_raw = plan_raw.get("meta") or {}
        if not isinstance(meta_raw, dict):
            meta_raw = {}

        base_meta = {
            "path_type": path_type,
            "generated_at": meta_raw.get("generated_at_utc")
            or datetime.utcnow().isoformat() + "Z",
            "used_live_ai": bool(used_live_ai),
            "inputs_digest": meta_raw.get("inputs_digest"),
            "version": meta_raw.get("version"),
            "career_ai_version": meta_raw.get("version")
            or meta_raw.get("career_ai_version"),
        }

        summary_text = plan_raw.get("summary") or ""

        # -----------------------------------
        # Extract phases (dynamic) + legacy plan_core
        # -----------------------------------
        phases = _ensure_phases(plan_raw.get("phases"))
        raw_core = plan_raw.get("plan_core")
        if not isinstance(raw_core, dict) or not raw_core:
            legacy_core = _legacy_plan_core_from_phases(phases)
        else:
            legacy_core = raw_core

        # Compute max projects based on timeline for UI + logic
        max_projects = _max_projects_for_timeline(timeline_months)

        # -----------------------------------
        # Job Mode View Model
        # -----------------------------------
        if path_type == "job":
            probs = plan_raw.get("probabilities") or {}
            if not isinstance(probs, dict):
                probs = {}

            resources = plan_raw.get("resources") or {}
            if not isinstance(resources, dict):
                resources = {}

            # Tutorials: keep as simple strings for templates
            tutorials_raw = resources.get("tutorials") or []
            tutorials = []
            for t in tutorials_raw:
                label = str(t).strip()
                if label:
                    tutorials.append({"label": label, "url": None})

            plan_view = {
                "mode": path_type,  # ← used in template for path_type detection
                "meta": base_meta,
                "summary": summary_text,
                # 🔑 This "input" block is what the Coach reads later (P3)
                "input": {
                    "path_type": path_type,
                    "target_role": target_role or "Your ideal job title",
                    "target_lpa": target_lpa or "12",
                    "timeline_months": timeline_months,
                    "hours_per_day": hours_per_day,
                    "company_prefs": company_prefs,
                    "extra_context": extra_context,
                },
                "probabilities": {
                    "lpa_12": probs.get("lpa_12"),
                    "lpa_24": probs.get("lpa_24"),
                    "lpa_48": probs.get("lpa_48"),
                    "notes": summary_text,
                },
                "missing_skills": plan_raw.get("missing_skills") or [],
                "phases": phases,          # new flexible phase model
                "plan_core": legacy_core,  # legacy 30/60/90-style for template
                "resources": resources,    # used in result.html as plan.resources
                "tutorials": tutorials,
                "mini_projects": resources.get("mini_projects") or [],
                "resume_bullets": resources.get("resume_bullets") or [],
                "linkedin_actions": resources.get("linkedin_actions") or [],
                "max_projects": max_projects,
            }

        # -----------------------------------
        # Startup Mode View Model
        # -----------------------------------
        else:
            sx = plan_raw.get("startup_extras") or {}
            if not isinstance(sx, dict):
                sx = {}

            # It's fine if resources are empty in startup mode, but we keep it consistent
            resources = plan_raw.get("resources") or {}
            if not isinstance(resources, dict):
                resources = {}

            plan_view = {
                "mode": path_type,        # ← used in template
                "meta": base_meta,
                "summary": summary_text,
                # 🔑 Coach-friendly "input" block (P3, startup)
                "input": {
                    "path_type": path_type,
                    "target_role": target_role or "Founder / Cofounder",
                    "startup_theme": target_role or "Not specified",
                    "timeline_months": timeline_months,
                    "hours_per_day": hours_per_day,
                    "company_prefs": company_prefs,
                    "extra_context": extra_context,
                    "startup_budget_range": startup_budget_range or "",
                },
                # raw startup extras for the premium UI sections
                "startup_extras": sx,
                # phases + legacy plan_core keep everything compatible
                "phases": phases,
                "plan_core": legacy_core,
                # extra structured view (legacy / still useful)
                "startup_summary": {
                    "founder_role": sx.get("founder_role_fit") or "",
                    "cofounder_needs": _split_to_list(sx.get("cofounder_gaps")),
                    "positioning": sx.get("positioning") or "",
                },
                "mvp_outline": sx.get("mvp_outline") or "",
                "budget_and_stack": {
                    "budget_estimate": sx.get("budget_notes")
                    or "Use a lean, student-friendly budget for domains, hosting and tools.",
                    "tech_stack": _split_to_list(sx.get("tech_stack")),
                },
                "go_to_market": {
                    "channels": _split_to_list(sx.get("go_to_market")),
                    "first_customers": _split_to_list(sx.get("first_10_customers")),
                    "pricing": _split_to_list(sx.get("pricing_notes")),
                },
                "risks": {
                    "items": _split_to_list(sx.get("risk_analysis")),
                },
                "resources": resources,  # harmless but consistent
                "max_projects": max_projects,
            }

        # -----------------------------------
        # Persist Dream Plan snapshot for Weekly Coach
        # -----------------------------------
        snapshot_id = None
        try:
            if path_type == "job":
                base_title = plan_view["input"].get("target_role") or "Dream Job"
                plan_title = f"Dream Job: {base_title}"
            else:
                base_title = plan_view["input"].get("target_role") or "Founder path"
                plan_title = f"Dream Startup: {base_title}"

            snapshot = DreamPlanSnapshot(
                user_id=current_user.id,
                path_type=path_type,
                plan_title=str(plan_title)[:255],
                plan_json=json.dumps(plan_view),
                inputs_digest=base_meta.get("inputs_digest"),
            )
            db.session.add(snapshot)
            db.session.commit()
            snapshot_id = snapshot.id
        except Exception:
            current_app.logger.exception(
                "Dream Planner: failed to persist DreamPlanSnapshot."
            )
            db.session.rollback()
            # Do not block user; plan is still rendered and credits will be handled.

        # -----------------------------------
        # Deduct Credits AFTER successful plan
        # -----------------------------------
        try:
            run_id = base_meta.get("inputs_digest")
            if not deduct_pro(current_user, "dream_planner", run_id=run_id):
                current_app.logger.warning(
                    "Dream Planner: deduct_pro failed after plan generation for user %s",
                    current_user.id,
                )
                flash(
                    "Your Dream Plan was generated, but your Pro credits could not be "
                    "updated correctly. Please contact support if this keeps happening.",
                    "warning",
                )
        except Exception:
            current_app.logger.exception("Dream Planner credit deduction error.")
            flash(
                "Your plan was generated, but we had trouble updating your credits. "
                "Please contact support if this keeps happening.",
                "warning",
            )

        return render_template(
            "dream/result.html",
            plan=plan_view,
            path_type=path_type,
            is_pro_user=is_pro_user,
            profile_snapshot=profile_snapshot,
            snapshot_id=snapshot_id,
        )

    # -----------------------------------
    # GET — Render form
    # -----------------------------------
    return render_template(
        "dream/index.html",
        path_type=path_type,
        is_pro_user=is_pro_user,
        profile_snapshot=profile_snapshot,
    )


@dream_bp.route("/projects/select", methods=["POST"], endpoint="select_projects")
@login_required
def select_projects():
    """
    Handle project selection from the Dream Plan UI.

    - Student sees mini_projects in their plan.
    - Based on timeline_months, they can pick up to N projects.
    - We update the plan_json["projects"] of the snapshot with ONLY those
      selected projects (as plain title-based entries).
    - Weekly Coach then uses this list when generating tasks.
    """
    path_type = _normalize_path_type(request.form.get("path_type"))
    snapshot_id_raw = request.form.get("snapshot_id")

    # Basic validation for snapshot id
    try:
        snapshot_id = int(snapshot_id_raw)
    except Exception:
        flash(
            "We couldn’t save your project selection. Please regenerate your Dream Plan and try again.",
            "warning",
        )
        return redirect(url_for("dream.index", path_type=path_type))

    snapshot = DreamPlanSnapshot.query.filter_by(
        id=snapshot_id, user_id=current_user.id
    ).first()
    if not snapshot:
        flash(
            "We couldn’t find that Dream Plan to attach projects to. "
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
    if not isinstance(input_block, dict):
        input_block = {}

    timeline_months = input_block.get("timeline_months") or 6
    try:
        timeline_months = int(timeline_months)
    except Exception:
        timeline_months = 6

    max_projects = _max_projects_for_timeline(timeline_months)

    # Selected indices from the form
    selected_indices_raw = request.form.getlist("project_index")
    selected_indices: list[int] = []
    for v in selected_indices_raw:
        try:
            idx = int(v)
            if idx >= 0:
                selected_indices.append(idx)
        except Exception:
            continue

    # Pull original mini_projects (source of truth for titles)
    mini_projects = plan_json.get("mini_projects")
    if not isinstance(mini_projects, list):
        resources = plan_json.get("resources") or {}
        if isinstance(resources, dict):
            mini_projects = resources.get("mini_projects") or []
        else:
            mini_projects = []

    projects: list[dict] = []

    if selected_indices:
        # Enforce max_projects limit
        selected_indices = selected_indices[:max_projects]

        for new_id, idx in enumerate(selected_indices, start=1):
            if 0 <= idx < len(mini_projects):
                title = str(mini_projects[idx]).strip()
                if not title:
                    continue
                projects.append(
                    {
                        "id": new_id,
                        "title": title[:255],
                        "week_start": None,
                        "week_end": None,
                        "milestones": [],
                    }
                )

    # Save into plan JSON for Coach engine
    plan_json["projects"] = projects

    snapshot.plan_json = json.dumps(plan_json)
    try:
        db.session.add(snapshot)
        db.session.commit()
        flash(
            "Project selection saved. Weekly Coach will now focus on these projects.",
            "success",
        )
    except Exception:
        current_app.logger.exception("Dream Planner: failed to save project selection.")
        db.session.rollback()
        flash(
            "We couldn’t save your project selection. Please try again.",
            "danger",
        )

    # After selection, send them to Weekly Coach for this path
    return redirect(url_for("coach.index", path_type=path_type))
