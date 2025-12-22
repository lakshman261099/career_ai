# modules/dream/tasks.py
"""
RQ background tasks for Dream Planner - COMPLETE FIX

✅ Calls generate_sync_plan()
✅ Converts NEW format → OLD format for templates
✅ Builds phases from coach_plan.weeks
✅ Builds resources (mini_projects, tutorials, resume_bullets, linkedin_actions)
✅ Full data for students to use
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

from models import db, DreamPlanSnapshot, User
from modules.common.ai import generate_sync_plan
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
    """Load Flask app for worker context."""
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
    """Store status inside plan_json."""
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
    """Enqueue Dream Plan generation. Returns RQ job_id."""
    q = get_queue(DEFAULT_QUEUE_NAME)

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
        job_timeout=int(os.getenv("DREAM_JOB_TIMEOUT", "900")),
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
    Worker execution:
    1. Call generate_sync_plan()
    2. Convert NEW format → OLD format
    3. Build phases, resources, everything
    4. Save to DB
    """
    app = _load_flask_app()
    job = get_current_job()

    with app.app_context():
        snapshot = DreamPlanSnapshot.query.filter_by(id=snapshot_id, user_id=user_id).first()
        user = User.query.filter_by(id=user_id).first()

        if not snapshot or not user:
            return {"ok": False, "error": "Snapshot or user not found"}

        try:
            _mark_snapshot(snapshot, "processing", payload={"job_id": job.id if job else None})
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            # ===========================================
            # STEP 1: Extract inputs and call AI
            # ===========================================
            
            job_title = ai_inputs.get("target_role", "")
            target_lpa = str(ai_inputs.get("target_salary_lpa") or ai_inputs.get("target_lpa") or "12")
            
            if target_lpa not in ["3", "6", "12", "24", "48"]:
                target_lpa = "12"
            
            timeline = ai_inputs.get("timeline", "3_months")
            if timeline not in ["28_days", "3_months"]:
                timeline_months = ai_inputs.get("timeline_months", 3)
                timeline = "28_days" if timeline_months == 1 else "3_months"
            
            extra_context = ai_inputs.get("extra_context", "")
            
            # Call AI
            plan_raw, used_live_ai = generate_sync_plan(
                job_title=job_title,
                target_lpa=target_lpa,
                timeline=timeline,
                profile_json=profile_json,
                skills_json=skills_json,
                resume_text=resume_text,
                extra_context=extra_context,
                return_source=True,
            )

            if not isinstance(plan_raw, dict):
                plan_raw = {}

            # ===========================================
            # STEP 2: Extract NEW format data
            # ===========================================
            
            meta = plan_raw.get("meta", {}) or {}
            analysis = plan_raw.get("analysis", {}) or {}
            
            probabilities = analysis.get("probabilities", {})
            if not isinstance(probabilities, dict):
                probabilities = {"3": 0, "6": 0, "12": 0, "24": 0}
            
            projected_probabilities = analysis.get("projected_probabilities", {})
            if not isinstance(projected_probabilities, dict):
                projected_probabilities = {"3": 0, "6": 0, "12": 0, "24": 0}
            
            bold_truth = analysis.get("bold_truth", "")
            missing_skills = analysis.get("missing_skills", [])
            if not isinstance(missing_skills, list):
                missing_skills = []
            
            raw_projects = plan_raw.get("projects", [])
            if not isinstance(raw_projects, list):
                raw_projects = []
            
            coach_plan = plan_raw.get("coach_plan", {})
            if not isinstance(coach_plan, dict):
                coach_plan = {}
            
            # ===========================================
            # STEP 3: Convert coach_plan.weeks → phases
            # ===========================================
            
            weeks = coach_plan.get("weeks", [])
            if not isinstance(weeks, list):
                weeks = []
            
            timeline_months = 1 if timeline == "28_days" else 3
            
            # Build 3 phases from weeks
            phases = []
            phase_labels = ["Foundation & Gaps", "Building & Shipping", "Polish & Launch"]
            
            if timeline == "28_days":
                # 4 weeks → Phase 1: W1-2, Phase 2: W3, Phase 3: W4
                phase_splits = [(0, 2), (2, 3), (3, 4)]
            else:
                # 3 months (4 weeks) → Phase 1: W1, Phase 2: W2-3, Phase 3: W4
                phase_splits = [(0, 1), (1, 3), (3, 4)]
            
            for phase_idx, (start_w, end_w) in enumerate(phase_splits):
                phase_weeks = weeks[start_w:end_w] if start_w < len(weeks) else []
                
                phase_items = []
                for week_data in phase_weeks:
                    if not isinstance(week_data, dict):
                        continue
                    
                    # Weekly task
                    weekly_task = week_data.get("weekly_task", {})
                    if isinstance(weekly_task, dict) and weekly_task.get("title"):
                        phase_items.append(f"🎯 {weekly_task.get('title', '')}")
                    
                    # Daily tasks
                    daily_tasks = week_data.get("daily_tasks", [])
                    if isinstance(daily_tasks, list):
                        for task in daily_tasks[:4]:  # Max 4 per week
                            if isinstance(task, dict) and task.get("title"):
                                phase_items.append(f"✓ {task.get('title', '')}")
                
                phases.append({
                    "label": phase_labels[phase_idx] if phase_idx < len(phase_labels) else f"Phase {phase_idx + 1}",
                    "items": phase_items[:12]  # Max 12 items per phase
                })
            
            # ===========================================
            # STEP 4: Build resources from projects
            # ===========================================
            
            mini_projects = []
            tutorials = []
            resume_bullets = []
            linkedin_actions = []
            
            # Convert projects
            for idx, proj in enumerate(raw_projects[:4]):
                if not isinstance(proj, dict):
                    continue
                
                project_obj = {
                    "title": proj.get("title", f"Project {idx + 1}"),
                    "description": proj.get("description", "Build something meaningful that demonstrates your skills."),
                    "tech_stack": proj.get("tech_stack", []) if isinstance(proj.get("tech_stack"), list) else [],
                    "estimated_hours": proj.get("estimated_hours", 20),
                    "difficulty": proj.get("difficulty", "intermediate"),
                    "outcomes": proj.get("outcomes", []) if isinstance(proj.get("outcomes"), list) else [],
                }
                mini_projects.append(project_obj)
                
                # Generate tutorials
                for tech in project_obj["tech_stack"][:2]:
                    if tech:
                        tutorials.append({
                            "label": f"Learn {tech} fundamentals for {project_obj['title']}",
                            "url": None
                        })
                
                # Generate resume bullets
                tech_list = ', '.join(project_obj['tech_stack'][:3]) if project_obj['tech_stack'] else 'modern technologies'
                resume_bullets.append(
                    f"Built {project_obj['title']} using {tech_list}, "
                    f"demonstrating {proj.get('skills_demonstrated', ['problem-solving'])[0] if proj.get('skills_demonstrated') else 'technical proficiency'} "
                    f"and delivering a functional product"
                )
                
                # Generate LinkedIn actions
                linkedin_actions.append(f"Share {project_obj['title']} with code samples and learnings")
            
            # Add skill-based tutorials
            for skill in missing_skills[:4]:
                if skill:
                    tutorials.append({
                        "label": f"Master {skill} through hands-on practice",
                        "url": None
                    })
            
            # Add skill-based bullets
            if missing_skills:
                resume_bullets.append(
                    f"Developed expertise in {missing_skills[0]} through self-directed learning and project application"
                )
            
            # Add standard LinkedIn actions
            linkedin_actions.extend([
                f"Update headline: '{job_title} | {', '.join(missing_skills[:3]) if missing_skills else 'Tech Skills'}'",
                "Write a post about your learning journey and projects completed",
                f"Engage with {job_title} professionals and share insights",
                "Request recommendations from mentors or colleagues"
            ])
            
            # Limit arrays
            tutorials = tutorials[:10]
            resume_bullets = resume_bullets[:8]
            linkedin_actions = linkedin_actions[:6]
            
            # ===========================================
            # STEP 5: Build complete plan_view
            # ===========================================
            
            from modules.dream.routes import _max_projects_for_timeline
            max_projects = _max_projects_for_timeline(timeline_months)
            
            plan_view = {
                # Core
                "mode": path_type,
                "meta": {
                    "path_type": path_type,
                    "generated_at": meta.get("generated_at", datetime.utcnow().isoformat()),
                    "used_live_ai": bool(used_live_ai),
                    "target_lpa": target_lpa,
                    "timeline": timeline,
                    "timeline_months": timeline_months,
                    "version": "sync_v1_complete",
                },
                "input": {
                    "target_role": job_title,
                    "target_lpa": target_lpa,
                    "timeline": timeline,
                    "timeline_months": timeline_months,
                    "extra_context": extra_context,
                    "path_type": path_type,
                    "hours_per_day": ai_inputs.get("hours_per_day", 2),
                    "company_prefs": ai_inputs.get("company_preferences", ""),
                },
                
                # Analysis (NEW format)
                "analysis": {
                    "probabilities": probabilities,
                    "projected_probabilities": projected_probabilities,
                    "bold_truth": bold_truth,
                    "missing_skills": missing_skills,
                },
                
                # Backward compat (direct access)
                "probabilities": probabilities,
                "projected_probabilities": projected_probabilities,
                "bold_truth": bold_truth,
                "missing_skills": missing_skills,
                
                # Projects (for selection)
                "projects": raw_projects,
                
                # Coach plan (raw, for sync)
                "coach_plan": coach_plan,
                
                # ✅ CRITICAL: Phases (for Phase 1, 2, 3 display)
                "phases": phases,
                
                # ✅ CRITICAL: Resources (for all resource sections)
                "resources": {
                    "mini_projects": mini_projects,
                    "tutorials": tutorials,
                    "resume_bullets": resume_bullets,
                    "linkedin_actions": linkedin_actions,
                },
                
                # Direct access to resources
                "mini_projects": mini_projects,
                "tutorials": tutorials,
                "resume_bullets": resume_bullets,
                "linkedin_actions": linkedin_actions,
                
                # Misc
                "max_projects": max_projects,
                
                # Status
                "_status": "completed",
                "_job_id": job.id if job else None,
                "_completed_at": datetime.utcnow().isoformat(),
            }
            
            # Save
            snapshot.plan_json = _safe_json(plan_view)
            snapshot.plan_title = f"{job_title} ({target_lpa}+ LPA, {timeline_months}mo)"[:255]
            
            # Inputs digest
            try:
                import hashlib
                inputs_str = json.dumps(ai_inputs, sort_keys=True)
                snapshot.inputs_digest = hashlib.sha256(inputs_str.encode()).hexdigest()[:16]
            except Exception:
                snapshot.inputs_digest = datetime.utcnow().isoformat()[:16]

            db.session.commit()
            return {"ok": True, "snapshot_id": snapshot_id}

        except Exception as e:
            err = f"{e.__class__.__name__}: {e}"
            tb = traceback.format_exc()

            try:
                snapshot.plan_json = _safe_json({
                    "_status": "failed",
                    "_job_id": job.id if job else None,
                    "_failed_at": datetime.utcnow().isoformat(),
                    "error": err,
                })
                db.session.commit()
            except Exception:
                db.session.rollback()

            # Refund
            try:
                refund(
                    user,
                    feature="dream_planner",
                    currency="gold",
                    amount=None,
                    run_id=run_id,
                    commit=True,
                    metadata={"reason": "dream_plan_failed", "error": err},
                )
            except Exception:
                pass

            return {"ok": False, "error": err, "traceback": tb}


def get_job_status(job_id: str) -> Dict[str, Any]:
    """Status helper for UI polling."""
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


def get_queue_stats() -> Dict[str, int]:
    """Queue stats for monitoring."""
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