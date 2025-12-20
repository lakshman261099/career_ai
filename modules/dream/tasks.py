# modules/dream/tasks.py
"""
RQ background tasks for Dream Planner.

Design goals:
- Runs outside request context (worker process), so it must create Flask app context.
- Updates DreamPlanSnapshot in DB with status + plan JSON.
- Credits are deducted BEFORE enqueue (in routes) and refunded on failure (here).
- Compatible with Redis + RQ (2.x).
"""

from __future__ import annotations

import importlib
import json
import os
import traceback
from datetime import datetime
from typing import Any, Dict, Optional

from redis import Redis
from rq import Queue, get_current_job

from models import db, DreamPlanSnapshot, User  # type: ignore
from modules.common.ai import generate_dream_plan
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
    """
    RQ runs in a separate process, so we need to import the Flask app instance.

    Priority:
    1) FLASK_APP env var (module:attr or module)
    2) try wsgi:app
    3) try app:app
    """
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
# Status JSON helpers
# ----------------------------

def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"


def _mark_snapshot(snapshot: DreamPlanSnapshot, status: str, payload: Optional[Dict[str, Any]] = None):
    """
    Store status safely inside plan_json so UI can show progress,
    without requiring DB migrations.
    """
    base: Dict[str, Any] = {}
    try:
        if snapshot.plan_json:
            base = json.loads(snapshot.plan_json) if snapshot.plan_json.strip() else {}
            if not isinstance(base, dict):
                base = {}
    except Exception:
        base = {}

    base["_status"] = status
    base["_updated_at"] = datetime.utcnow().isoformat()

    if payload:
        # keep payload under reserved key
        base["_meta"] = payload

    snapshot.plan_json = _safe_json(base)


# ----------------------------
# Public API
# ----------------------------

def enqueue_dream_plan_generation(
    *,
    user_id: int,
    snapshot_id: int,
    path_type: str,
    ai_inputs: Dict[str, Any],
    profile_json: Dict[str, Any],
    skills_json: Dict[str, Any],
    resume_text: str,
    run_id: str,
) -> str:
    """
    Enqueue the Dream Plan generation task.
    Returns the RQ job_id (string).

    IMPORTANT:
    - Enqueue the function object (not a string path) to avoid RQ import parsing issues.
    """
    q = get_queue(DEFAULT_QUEUE_NAME)

    # Import inside the function so both web + worker resolve the same module path
    from modules.dream.tasks import process_dream_plan_generation

    job = q.enqueue(
        process_dream_plan_generation,
        kwargs=dict(
            user_id=user_id,
            snapshot_id=snapshot_id,
            path_type=path_type,
            ai_inputs=ai_inputs,
            profile_json=profile_json,
            skills_json=skills_json,
            resume_text=resume_text,
            run_id=run_id,
        ),
        job_timeout=int(os.getenv("DREAM_JOB_TIMEOUT", "900")),  # 15 min
        result_ttl=int(os.getenv("RQ_RESULT_TTL", "500")),
        failure_ttl=int(os.getenv("RQ_FAILURE_TTL", "3600")),
    )
    return job.id


def process_dream_plan_generation(
    *,
    user_id: int,
    snapshot_id: int,
    path_type: str,
    ai_inputs: Dict[str, Any],
    profile_json: Dict[str, Any],
    skills_json: Dict[str, Any],
    resume_text: str,
    run_id: str,
) -> Dict[str, Any]:
    """
    Worker-side execution:
    - mark snapshot processing
    - run AI
    - save snapshot + plan_json
    - refund credits on failure
    """
    app = _load_flask_app()
    job = get_current_job()

    with app.app_context():
        snapshot = DreamPlanSnapshot.query.filter_by(id=snapshot_id, user_id=user_id).first()
        user = User.query.filter_by(id=user_id).first()

        if not snapshot or not user:
            # Nothing to do; do not refund because we can't safely attribute it
            return {"ok": False, "error": "Snapshot or user not found"}

        try:
            _mark_snapshot(snapshot, "processing", payload={"job_id": job.id if job else None})
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            # AI CALL
            plan_raw, used_live_ai = generate_dream_plan(
                mode=path_type,
                inputs=ai_inputs,
                profile_json=profile_json,
                skills_json=skills_json,
                resume_text=resume_text,
                return_source=True,
            )

            if not isinstance(plan_raw, dict):
                plan_raw = {}

            # Import helper functions from routes (same as sync version)
            from modules.dream.routes import (
                _ensure_phases,
                _legacy_plan_core_from_phases,
                _max_projects_for_timeline,
                _split_to_list,
            )

            # Build meta information
            meta_raw = plan_raw.get("meta") or {}
            if not isinstance(meta_raw, dict):
                meta_raw = {}

            base_meta = {
                "path_type": path_type,
                "generated_at": meta_raw.get("generated_at_utc") or datetime.utcnow().isoformat() + "Z",
                "used_live_ai": bool(used_live_ai),
                "inputs_digest": meta_raw.get("inputs_digest"),
                "version": meta_raw.get("version"),
                "career_ai_version": meta_raw.get("version") or meta_raw.get("career_ai_version"),
            }

            summary_text = plan_raw.get("summary") or ""

            # Extract phases + legacy plan_core
            phases = _ensure_phases(plan_raw.get("phases"))
            raw_core = plan_raw.get("plan_core")
            if not isinstance(raw_core, dict) or not raw_core:
                legacy_core = _legacy_plan_core_from_phases(phases)
            else:
                legacy_core = raw_core

            # Compute max projects based on timeline
            timeline_months = ai_inputs.get("timeline_months", 6)
            max_projects = _max_projects_for_timeline(timeline_months)

            # Build plan_view (same structure as sync version)
            if path_type == "job":
                probs = plan_raw.get("probabilities") or {}
                if not isinstance(probs, dict):
                    probs = {}

                resources = plan_raw.get("resources") or {}
                if not isinstance(resources, dict):
                    resources = {}

                # Tutorials
                tutorials_raw = resources.get("tutorials") or []
                tutorials = []
                for t in tutorials_raw:
                    label = str(t).strip()
                    if label:
                        tutorials.append({"label": label, "url": None})

                plan_view = {
                    "mode": path_type,
                    "meta": base_meta,
                    "summary": summary_text,
                    "input": {
                        "path_type": path_type,
                        "target_role": ai_inputs.get("target_role") or "Your ideal job title",
                        "target_lpa": ai_inputs.get("target_salary_lpa") or "12",
                        "timeline_months": timeline_months,
                        "hours_per_day": ai_inputs.get("hours_per_day", 2),
                        "company_prefs": ai_inputs.get("company_preferences", ""),
                        "extra_context": ai_inputs.get("extra_context", ""),
                    },
                    "probabilities": {
                        "lpa_12": probs.get("lpa_12"),
                        "lpa_24": probs.get("lpa_24"),
                        "lpa_48": probs.get("lpa_48"),
                        "notes": summary_text,
                    },
                    "missing_skills": plan_raw.get("missing_skills") or [],
                    "phases": phases,
                    "plan_core": legacy_core,
                    "resources": resources,
                    "tutorials": tutorials,
                    "mini_projects": resources.get("mini_projects") or [],
                    "resume_bullets": resources.get("resume_bullets") or [],
                    "linkedin_actions": resources.get("linkedin_actions") or [],
                    "max_projects": max_projects,
                }

            else:  # startup mode
                sx = plan_raw.get("startup_extras") or {}
                if not isinstance(sx, dict):
                    sx = {}

                resources = plan_raw.get("resources") or {}
                if not isinstance(resources, dict):
                    resources = {}

                plan_view = {
                    "mode": path_type,
                    "meta": base_meta,
                    "summary": summary_text,
                    "input": {
                        "path_type": path_type,
                        "target_role": ai_inputs.get("target_role") or "Founder / Cofounder",
                        "startup_theme": ai_inputs.get("startup_theme") or ai_inputs.get("target_role") or "Not specified",
                        "timeline_months": timeline_months,
                        "hours_per_day": ai_inputs.get("hours_per_day", 2),
                        "company_prefs": ai_inputs.get("company_preferences", ""),
                        "extra_context": ai_inputs.get("extra_context", ""),
                        "startup_budget_range": ai_inputs.get("startup_budget_range", ""),
                    },
                    "startup_extras": sx,
                    "phases": phases,
                    "plan_core": legacy_core,
                    "startup_summary": {
                        "founder_role": sx.get("founder_role_fit") or "",
                        "cofounder_needs": _split_to_list(sx.get("cofounder_gaps")),
                        "positioning": sx.get("positioning") or "",
                    },
                    "mvp_outline": sx.get("mvp_outline") or "",
                    "budget_and_stack": {
                        "budget_estimate": sx.get("budget_notes") or "Use a lean, student-friendly budget for domains, hosting and tools.",
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
                    "resources": resources,
                    "max_projects": max_projects,
                }

            # Mark completion and save final plan
            plan_view["_status"] = "completed"
            plan_view["_job_id"] = job.id if job else None
            plan_view["_completed_at"] = datetime.utcnow().isoformat()

            snapshot.plan_json = _safe_json(plan_view)

            # Update plan_title
            if path_type == "job":
                base_title = plan_view["input"].get("target_role") or "Dream Job"
                snapshot.plan_title = f"Dream Job: {base_title}"[:255]
            else:
                base_title = plan_view["input"].get("target_role") or "Founder path"
                snapshot.plan_title = f"Dream Startup: {base_title}"[:255]

            snapshot.inputs_digest = base_meta.get("inputs_digest")

            db.session.commit()
            return {"ok": True, "snapshot_id": snapshot_id}

        except Exception as e:
            err = f"{e.__class__.__name__}: {e}"
            tb = traceback.format_exc()

            try:
                snapshot.plan_json = _safe_json(
                    {
                        "_status": "failed",
                        "_job_id": job.id if job else None,
                        "_failed_at": datetime.utcnow().isoformat(),
                        "error": err,
                    }
                )
                db.session.commit()
            except Exception:
                db.session.rollback()

            # Refund credits (because we deducted BEFORE enqueue)
            try:
                refund(
                    user,
                    feature="dream_planner",
                    currency="gold",
                    amount=None,  # Will use feature cost from config
                    run_id=run_id,
                    commit=True,
                    metadata={"reason": "dream_plan_failed", "error": err},
                )
            except Exception:
                # don't crash worker due to refund failure
                pass

            return {"ok": False, "error": err, "traceback": tb}


def get_job_status(job_id: str) -> Dict[str, Any]:
    """
    Lightweight status helper. UI polls this.
    """
    r = _redis()
    from rq.job import Job  # imported here to avoid worker import issues

    try:
        job = Job.fetch(job_id, connection=r)
    except Exception:
        return {"status": "not_found"}

    status = job.get_status()  # queued/started/finished/failed
    out: Dict[str, Any] = {"status": status, "job_id": job_id}

    if status == "failed":
        out["error"] = "Job failed. Check server logs."
    if status == "finished":
        # job.result is returned dict from process_dream_plan_generation
        out["result"] = job.result

    return out


def get_queue_stats() -> Dict[str, int]:
    """
    For admin/ops monitoring later.
    """
    q = get_queue(DEFAULT_QUEUE_NAME)
    try:
        return {
            "queued": q.count,
            "started": q.started_job_registry.count,
            "finished": q.finished_job_registry.count,
            "failed": q.failed_job_registry.count,
        }
    except Exception:
        return {"queued": 0, "started": 0, "finished": 0, "failed": 0}