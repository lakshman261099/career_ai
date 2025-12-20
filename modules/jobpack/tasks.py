# modules/jobpack/tasks.py
"""
RQ background tasks for Job Pack.

Design goals:
- Runs outside request context (worker process), so it must create Flask app context.
- Updates JobPackReport in DB with status + analysis JSON.
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

from models import db, JobPackReport, User  # type: ignore
from modules.jobpack.utils_ats import analyze_jobpack
from modules.credits.engine import add_credits


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


def _mark_report(report: JobPackReport, status: str, payload: Optional[Dict[str, Any]] = None):
    """
    Store status safely inside analysis JSON so UI can show progress,
    without requiring DB migrations.
    """
    base: Dict[str, Any] = {}
    try:
        if report.analysis:
            base = json.loads(report.analysis) if report.analysis.strip() else {}
            if not isinstance(base, dict):
                base = {}
    except Exception:
        base = {}

    base["_status"] = status
    base["_updated_at"] = datetime.utcnow().isoformat()

    if payload:
        # keep payload under reserved key
        base["_meta"] = payload

    report.analysis = _safe_json(base)


# ----------------------------
# Public API
# ----------------------------

def enqueue_jobpack_analysis(
    *,
    user_id: int,
    report_id: int,
    jd_text: str,
    resume_text: str,
    is_pro_run: bool,
    feature_key: str,
    currency: str,  # "silver" | "gold"
    refund_amount: int,
) -> str:
    """
    Enqueue the job pack analysis task.
    Returns the RQ job_id (string).

    IMPORTANT:
    - Enqueue the function object (not a string path) to avoid RQ import parsing issues.
    """
    q = get_queue(DEFAULT_QUEUE_NAME)

    # Import inside the function so both web + worker resolve the same module path
    from modules.jobpack.tasks import process_jobpack_analysis

    job = q.enqueue(
        process_jobpack_analysis,
        kwargs=dict(
            user_id=user_id,
            report_id=report_id,
            jd_text=jd_text,
            resume_text=resume_text,
            is_pro_run=is_pro_run,
            feature_key=feature_key,
            currency=currency,
            refund_amount=refund_amount,
        ),
        job_timeout=int(os.getenv("JOBPACK_JOB_TIMEOUT", "900")),  # 15 min
        result_ttl=int(os.getenv("RQ_RESULT_TTL", "500")),
        failure_ttl=int(os.getenv("RQ_FAILURE_TTL", "3600")),
    )
    return job.id



def process_jobpack_analysis(
    *,
    user_id: int,
    report_id: int,
    jd_text: str,
    resume_text: str,
    is_pro_run: bool,
    feature_key: str,
    currency: str,
    refund_amount: int,
) -> Dict[str, Any]:
    """
    Worker-side execution:
    - mark report processing
    - run AI
    - save report + analysis
    - refund credits on failure
    """
    app = _load_flask_app()
    job = get_current_job()

    with app.app_context():
        report = JobPackReport.query.filter_by(id=report_id, user_id=user_id).first()
        user = User.query.filter_by(id=user_id).first()

        if not report or not user:
            # Nothing to do; do not refund because we can't safely attribute it
            return {"ok": False, "error": "Report or user not found"}

        try:
            _mark_report(report, "processing", payload={"job_id": job.id if job else None})
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            raw = analyze_jobpack(jd_text, resume_text, pro_mode=is_pro_run)

            # Persist final result
            if isinstance(raw, dict):
                # keep original payload but ensure we annotate completion meta
                raw.setdefault("_status", "completed")
                raw.setdefault("_job_id", job.id if job else None)
                raw.setdefault("_completed_at", datetime.utcnow().isoformat())
                report.analysis = _safe_json(raw)

                # basic indexing fields
                if not report.job_title:
                    report.job_title = raw.get("role_detected") or report.job_title
            else:
                report.analysis = _safe_json(
                    {
                        "_status": "completed",
                        "_job_id": job.id if job else None,
                        "_completed_at": datetime.utcnow().isoformat(),
                        "raw": str(raw),
                    }
                )

            db.session.commit()
            return {"ok": True, "report_id": report_id}

        except Exception as e:
            err = f"{e.__class__.__name__}: {e}"
            tb = traceback.format_exc()

            try:
                report.analysis = _safe_json(
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
                if refund_amount and refund_amount > 0 and currency in ("silver", "gold"):
                    add_credits(
                        user,
                        amount=int(refund_amount),
                        currency=currency,  # type: ignore
                        feature=feature_key,
                        tx_type="refund",
                        run_id=str(report_id),
                        commit=True,
                        metadata={"reason": "jobpack_failed", "error": err},
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
        # job.result is returned dict from process_jobpack_analysis
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
