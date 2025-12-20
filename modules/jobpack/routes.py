# modules/jobpack/routes.py
import io
import json
import os
from datetime import datetime
from typing import Any, List

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    render_template_string,
    request,
    send_file,
    url_for,
    jsonify,
)
from flask_login import current_user, login_required
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from models import JobPackReport, db
from modules.jobpack.utils_ats import analyze_jobpack
from modules.common.profile_loader import load_profile_snapshot

# Phase 4: central credits engine
from modules.credits.engine import can_afford, deduct_free, deduct_pro

# RQ tasks
from modules.jobpack.tasks import enqueue_jobpack_analysis, get_job_status

jobpack_bp = Blueprint("jobpack", __name__, template_folder="../../templates/jobpack")


# ---------------------- helpers ----------------------


def _safe_result(raw) -> dict:
    """
    Normalize raw AI output so templates/PDF always see the expected keys.
    Also sets sensible defaults for subscores / resume_ats extensions.
    """
    base = {
        "summary": "",
        "role_detected": "",
        "fit_overview": [],
        "ats_score": 0,
        "skill_table": [],
        "rewrite_suggestions": [],
        "next_steps": [],
        "impact_summary": "",
        "detected_keywords": [],
        "matched_count": 0,
        "missing_count": 0,
        "resume_missing": False,
        "report_tier": "Standard Analysis",
        # New: subscores for more granular ATS breakdown
        "subscores": {
            "keyword_relevance": 0,
            "quantifiable_impact": 0,
            "formatting_clarity": 0,
            "professional_tone": 0,
        },
        "resume_ats": {
            "resume_ats_score": 0,
            "blockers": [],
            "warnings": [],
            "keyword_coverage": {
                "required_keywords": [],
                "present_keywords": [],
                "missing_keywords": [],
            },
            "resume_rewrite_actions": [],
            # New: phrases that can be pasted directly into the resume
            "exact_phrases_to_add": [],
        },
        # Extended sections
        "learning_links": [],
        "interview_qa": [],
        "practice_plan": [],
        "application_checklist": [],
    }
    if isinstance(raw, dict):
        for k in base.keys():
            base[k] = raw.get(k, base[k])
        # preserve status metadata if present
        for meta_k in ("_status", "_job_id", "_completed_at", "_failed_at", "error"):
            if meta_k in raw:
                base[meta_k] = raw.get(meta_k)
    return base


def _coerce_skill_names(skills_any: Any) -> List[str]:
    out: List[str] = []
    if isinstance(skills_any, list):
        for s in skills_any:
            if isinstance(s, dict) and (s.get("name") or "").strip():
                out.append(s["name"].strip())
            elif isinstance(s, str) and s.strip():
                out.append(s.strip())
    return out


def _feature_cost_amount(feature_key: str, currency: str) -> int:
    """
    Read feature cost from app config / credits config.
    We only need this to pass refund_amount to the worker.
    """
    cfg = current_app.config.get("FEATURE_COSTS") or {}
    raw = (cfg.get(feature_key) or {}) if isinstance(cfg, dict) else {}
    if currency == "silver":
        return int(raw.get("silver") or raw.get("coins_free") or 0)
    return int(raw.get("gold") or raw.get("coins_pro") or 0)


def _async_enabled() -> bool:
    """
    Feature flag:
    JOBPACK_ASYNC=1 enables RQ flow.
    Default: enabled in dev (safer under load).
    """
    v = os.getenv("JOBPACK_ASYNC", "1").strip()
    return v not in ("0", "false", "False")


# ---------------------- main route ----------------------


@jobpack_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Job Pack — AI-powered ATS + Resume Evaluator
    Free: standard match analysis (gpt-4o-mini) using Silver 🪙.
    Pro: CareerAI Deep Evaluation (gpt-4o) using Gold ⭐.
    Profile Portal is the main resume source for ALL runs.

    UPGRADE:
    - Async RQ mode (default) to avoid blocking the web server.
    - Credits deducted BEFORE enqueue; refunded by worker on failure.
    """
    mode = (request.form.get("mode") or "basic").lower()
    is_pro_run = mode == "pro"
    result = None

    profile_snapshot = load_profile_snapshot(current_user)
    profile_resume_text = profile_snapshot.get("resume_text") or ""

    if request.method == "POST":
        # 🔒 Email verification guard (only for running AI)
        if not getattr(current_user, "verified", False):
            flash(
                "Please verify your email with a login code before using Job Pack.",
                "warning",
            )
            return redirect(url_for("auth.otp_request"))

        jd_text = (request.form.get("jd") or "").strip()

        if not jd_text:
            flash("Please paste a job description.", "warning")
            return render_template(
                "jobpack/index.html",
                result=None,
                mode=mode,
                profile_snapshot=profile_snapshot,
            )

        # ------------------ Credits: check BEFORE AI/enqueue ------------------
        feature_key = "jobpack_pro" if is_pro_run else "jobpack_free"
        currency = "gold" if is_pro_run else "silver"

        if is_pro_run:
            if not current_user.is_pro:
                flash(
                    "Job Pack Deep Evaluation is available for Pro ⭐ members only.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

            if not can_afford(current_user, feature_key, currency=currency):
                flash(
                    "You don’t have enough Gold ⭐ credits to run Deep Evaluation. "
                    "Upgrade your plan or add more credits in the Coins Shop.",
                    "warning",
                )
                return redirect(url_for("billing.index"))
        else:
            if not can_afford(current_user, feature_key, currency=currency):
                flash(
                    "You don’t have enough Silver 🪙 credits to run Job Pack. "
                    "Upgrade to Pro ⭐ or add more credits in the Coins Shop.",
                    "warning",
                )
                return redirect(url_for("billing.index"))

        # Resume is always sourced from Profile Portal / ResumeAsset
        resume_text = profile_resume_text
        if not resume_text:
            flash(
                "We couldn’t find resume data in your Profile Portal yet. "
                "You’ll still get JD insights, but ATS results will be limited. "
                "Fill your Profile Portal for deeper analysis.",
                "warning",
            )

        # ------------------ ASYNC (RQ) default path ------------------
        if _async_enabled():
            refund_amount = _feature_cost_amount(feature_key, currency)

            try:
                # Create report row FIRST (so we have report_id as run_id)
                report = JobPackReport(
                    user_id=current_user.id,
                    job_title=None,
                    company=None,
                    jd_text=jd_text,
                    analysis=json.dumps({"_status": "queued"}, ensure_ascii=False),
                    created_at=datetime.utcnow(),
                )
                db.session.add(report)
                db.session.flush()  # get report.id without committing

                # Deduct credits BEFORE enqueue (same transaction)
                if is_pro_run:
                    ok = deduct_pro(
                        current_user,
                        feature_key,
                        run_id=str(report.id),
                        commit=False,
                    )
                else:
                    ok = deduct_free(
                        current_user,
                        feature_key,
                        run_id=str(report.id),
                        commit=False,
                    )

                if not ok:
                    db.session.rollback()
                    flash(
                        "We couldn’t reserve credits for this run. Please try again.",
                        "danger",
                    )
                    return redirect(url_for("jobpack.index"))

                db.session.commit()

                # Enqueue worker job
                job_id = enqueue_jobpack_analysis(
                    user_id=current_user.id,
                    report_id=report.id,
                    jd_text=jd_text,
                    resume_text=resume_text or "",
                    is_pro_run=is_pro_run,
                    feature_key=feature_key,
                    currency=currency,
                    refund_amount=int(refund_amount or 0),
                )

                # Store job id in analysis meta (optional, best effort)
                try:
                    report.analysis = json.dumps(
                        {"_status": "queued", "_job_id": job_id}, ensure_ascii=False
                    )
                    db.session.commit()
                except Exception:
                    db.session.rollback()

                # ✅ FIX: because blueprint template_folder already points to templates/jobpack,
                # render "processing.html" (NOT "jobpack/processing.html")
                return render_template(
                    "processing.html",
                    job_id=job_id,
                    report_id=report.id,
                    status_url=url_for("jobpack.api_job_status", job_id=job_id),
                    report_url=url_for("jobpack.report", report_id=report.id),
                )

            except Exception as e:
                current_app.logger.exception("JobPack async enqueue failed: %s", e)
                try:
                    db.session.rollback()
                except Exception:
                    pass
                flash(
                    "We could not start the Job Pack analysis. Please try again.",
                    "danger",
                )
                return redirect(url_for("jobpack.index"))

        # ------------------ SYNC fallback (kept, not removed) ------------------
        try:
            raw = analyze_jobpack(jd_text, resume_text, pro_mode=is_pro_run)
        except Exception as e:
            current_app.logger.exception("JobPack Analysis Error: %s", e)
            flash(
                "An error occurred during analysis. Please try again later.", "danger"
            )
            return render_template(
                "jobpack/index.html",
                result=None,
                mode=mode,
                profile_snapshot=profile_snapshot,
            )

        result = _safe_result(raw)

        if not resume_text:
            result["resume_missing"] = True

        # Persist (best-effort) BEFORE deducting credits, so we can attach run_id if needed
        report_id = None
        try:
            report = JobPackReport(
                user_id=current_user.id,
                job_title=result.get("role_detected"),
                company=None,
                jd_text=jd_text,
                analysis=json.dumps(result, ensure_ascii=False),
                created_at=datetime.utcnow(),
            )
            db.session.add(report)
            db.session.commit()
            report_id = report.id
        except Exception as e:
            current_app.logger.warning("JobPack report save failed: %s", e)
            try:
                db.session.rollback()
            except Exception:
                pass

        # Deduct credits AFTER successful AI call (legacy sync behavior)
        try:
            if is_pro_run:
                if not deduct_pro(current_user, "jobpack_pro", run_id=report_id):
                    current_app.logger.warning(
                        "JobPack: deduct_pro failed after analysis for user %s",
                        current_user.id,
                    )
                    flash(
                        "Deep Evaluation completed, but your Pro credits could not be updated. "
                        "Please contact support if this keeps happening.",
                        "warning",
                    )
            else:
                if not deduct_free(current_user, "jobpack_free", run_id=report_id):
                    current_app.logger.warning(
                        "JobPack: deduct_free failed after analysis for user %s",
                        current_user.id,
                    )
                    flash(
                        "Job Pack analysis completed, but your credits could not be updated. "
                        "Please contact support if this keeps happening.",
                        "warning",
                    )
        except Exception as e:
            current_app.logger.exception("JobPack credit deduction error: %s", e)
            flash(
                "Your analysis completed, but we had trouble updating your credits. "
                "Please contact support if this keeps happening.",
                "warning",
            )

        try:
            return render_template(
                "jobpack/result.html",
                result=result,
                mode=mode,
                is_pro=is_pro_run,
                from_history=False,
            )
        except Exception as e:
            current_app.logger.exception("JobPack result template failed: %s", e)
            pretty = json.dumps(result, ensure_ascii=False, indent=2)[:8000]
            html = f"""
            <div style="max-width:900px;margin:40px auto;font-family:system-ui;">
              <h2>JobPack Result (Fallback Debug View)</h2>
              <p style="color:#b00">Template rendering failed. See server logs for the stack trace.</p>
              <pre style="background:#111;color:#eee;padding:16px;border-radius:8px;white-space:pre-wrap">{pretty}</pre>
              <p><a href="{url_for('jobpack.index')}" style="text-decoration:underline">← Back to Job Pack</a></p>
            </div>
            """
            return render_template_string(html), 200

    # GET
    return render_template(
        "jobpack/index.html",
        result=None,
        mode=mode,
        profile_snapshot=profile_snapshot,
    )


# ---------------------- RQ status endpoint ----------------------


@jobpack_bp.route("/api/status/<job_id>", methods=["GET"], endpoint="api_job_status")
@login_required
def api_job_status(job_id: str):
    """
    Polled by the processing page.
    """
    try:
        return jsonify(get_job_status(job_id)), 200
    except Exception as e:
        current_app.logger.exception("JobPack api_job_status error: %s", e)
        return jsonify({"status": "error"}), 200


# ---------------------- history + single report ----------------------


@jobpack_bp.route("/history", methods=["GET"], endpoint="history")
@login_required
def history():
    """
    List past Job Pack reports for the current user (most recent first).
    """
    from models import JobPackReport  # re-import for clarity if needed

    page = request.args.get("page", 1, type=int)
    per_page = 10
    pagination = (
        JobPackReport.query.filter_by(user_id=current_user.id)
        .order_by(JobPackReport.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    reports = pagination.items
    return render_template(
        "jobpack/history.html",
        reports=reports,
        pagination=pagination,
    )


@jobpack_bp.route("/report/<int:report_id>", methods=["GET"], endpoint="report")
@login_required
def report(report_id: int):
    """
    Reopen a previously saved Job Pack report without re-running AI.
    """
    from models import JobPackReport  # re-import for clarity if needed

    report = (
        JobPackReport.query.filter_by(id=report_id, user_id=current_user.id)
        .first_or_404()
    )
    try:
        raw = json.loads(report.analysis or "{}")
    except Exception:
        raw = {}
    result = _safe_result(raw)

    tier = (result.get("report_tier") or "").lower()
    is_pro_run = "deep" in tier or "careerai" in tier

    # If still processing, show a soft message
    if (result.get("_status") or "").lower() in ("queued", "processing"):
        flash(
            "This report is still processing. Please wait a moment and refresh.",
            "info",
        )

    if (result.get("_status") or "").lower() == "failed":
        flash("This report failed. Credits were refunded. Please try again.", "warning")

    return render_template(
        "jobpack/result.html",
        result=result,
        mode="pro" if is_pro_run else "basic",
        is_pro=is_pro_run,
        from_history=True,
        report=report,
    )


# ---------------------- PDF Export ----------------------


@jobpack_bp.route("/export/pdf", methods=["POST"], endpoint="export_pdf")
@login_required
def export_pdf():
    """
    Export Job Pack report to PDF (Pro only).
    Can accept either:
    - data (raw JSON string from the page), or
    - report_id (to reload from database, e.g. from history).
    """
    if not current_user.is_pro:
        flash("PDF export is a Pro feature.", "warning")
        return redirect(url_for("billing.index"))

    try:
        safe: dict

        report_id = request.form.get("report_id")
        if report_id:
            try:
                rid = int(report_id)
            except ValueError:
                flash("Invalid report id.", "danger")
                return redirect(url_for("jobpack.history"))

            report = (
                JobPackReport.query.filter_by(id=rid, user_id=current_user.id)
                .first()
            )
            if not report:
                flash("Report not found.", "danger")
                return redirect(url_for("jobpack.history"))

            try:
                raw = json.loads(report.analysis or "{}")
            except Exception:
                raw = {}
            safe = _safe_result(raw)
        else:
            data = request.form.get("data")
            if not data:
                flash("Missing report data.", "danger")
                return redirect(url_for("jobpack.index"))
            safe = _safe_result(json.loads(data))

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        y = height - 60

        def _wrap_line(text: str, max_chars: int = 100) -> List[str]:
            text = (text or "").replace("\r", "")
            lines_out: List[str] = []
            for raw_line in (text.split("\n") or [""]):
                line = raw_line.strip()
                if not line:
                    lines_out.append("")
                    continue
                while len(line) > max_chars:
                    cut = line.rfind(" ", 0, max_chars)
                    if cut <= 0:
                        cut = max_chars
                    lines_out.append(line[:cut].strip())
                    line = line[cut:].strip()
                lines_out.append(line)
            return lines_out

        def new_page():
            nonlocal y
            c.showPage()
            y = height - 60

        def section(title: str, lines: List[str], small: bool = False):
            nonlocal y
            if y < 80:
                new_page()
            c.setFont("Helvetica-Bold", 13)
            c.setFillColor(colors.black)
            c.drawString(40, y, title)
            y -= 18
            c.setFont("Helvetica", 9 if small else 10)
            c.setFillColor(colors.black)
            for line in lines:
                for chunk in _wrap_line(line, max_chars=100):
                    if not chunk:
                        y -= 10 if small else 12
                    else:
                        c.drawString(40, y, chunk)
                        y -= 10 if small else 12
                    if y < 60:
                        new_page()
                        c.setFont("Helvetica", 9 if small else 10)

        c.setFillColorRGB(0.1, 0.12, 0.18)
        c.rect(0, height - 80, width, 80, fill=True, stroke=False)
        c.setFont("Helvetica-Bold", 16)
        c.setFillColor(colors.white)
        c.drawString(40, height - 50, "CareerAI Deep Evaluation Report ⭐")

        section(
            "Role Summary",
            [
                safe["summary"] or "No summary available.",
                f"Detected Role: {safe['role_detected'] or '—'}",
            ],
        )

        fit_lines: List[str] = []
        for f in safe["fit_overview"]:
            if isinstance(f, dict):
                cat = f.get("category", "")
                m = f.get("match", 0)
                comment = f.get("comment", "")
                fit_lines.append(f"{cat}: {m}% — {comment}")
        if fit_lines:
            section("Fit Overview", fit_lines)

        subs = safe.get("subscores") or {}
        subs_lines = [
            f"Overall ATS Alignment (model estimate): {safe['ats_score']}%",
            f"Keyword relevance: {subs.get('keyword_relevance', 0)}%",
            f"Quantifiable impact: {subs.get('quantifiable_impact', 0)}%",
            f"Formatting & clarity: {subs.get('formatting_clarity', 0)}%",
            f"Professional tone: {subs.get('professional_tone', 0)}%",
        ]
        section("ATS Score & Subscores (model estimates)", subs_lines)

        skill_lines: List[str] = []
        for row in safe["skill_table"]:
            if isinstance(row, dict):
                skill = row.get("skill", "")
                status = row.get("status", "")
                if skill:
                    skill_lines.append(f"{skill} — {status}")
        if skill_lines:
            section("JD vs Resume Skill Match", skill_lines, small=True)

        ra = safe.get("resume_ats") or {}
        kc = ra.get("keyword_coverage") or {}
        blockers = [f"• {b}" for b in (ra.get("blockers") or [])] or ["None"]
        warnings = [f"• {w}" for w in (ra.get("warnings") or [])] or ["None"]
        missing_kw = kc.get("missing_keywords") or []
        present_kw = kc.get("present_keywords") or []
        required_kw = kc.get("required_keywords") or []
        phrases = ra.get("exact_phrases_to_add") or []

        section(
            "Resume ATS — Score (model estimate)",
            [f"Resume ATS Score: {ra.get('resume_ats_score', 0)}%"],
        )
        section("Resume ATS — Must Fix (Blockers)", blockers, small=True)
        section("Resume ATS — Should Fix (Warnings)", warnings, small=True)

        if required_kw or present_kw or missing_kw:
            kw_lines = []
            if required_kw:
                kw_lines.append("Required keywords: " + ", ".join(required_kw[:40]))
            if present_kw:
                kw_lines.append("Present in resume: " + ", ".join(present_kw[:40]))
            if missing_kw:
                kw_lines.append("Missing from resume: " + ", ".join(missing_kw[:40]))
            section("Resume ATS — Keyword Coverage", kw_lines, small=True)

        if phrases:
            section(
                "Resume ATS — Exact Phrases to Add",
                [f"• {p}" for p in phrases[:40]],
                small=True,
            )

        rra = ra.get("resume_rewrite_actions") or []
        if rra:
            section(
                "Resume ATS — Rewrite Actions",
                [f"• {a}" for a in rra[:40]],
                small=True,
            )

        if safe["learning_links"]:
            ll_lines: List[str] = []
            for link in safe["learning_links"]:
                if isinstance(link, dict):
                    label = link.get("label", "")
                    url = link.get("url", "")
                    why = link.get("why", "")
                    ll_lines.append(f"{label} — {url}")
                    if why:
                        ll_lines.append(f"  • {why}")
            section("Learning Resources", ll_lines, small=True)

        if safe["interview_qa"]:
            qa_lines: List[str] = []
            for qa in safe["interview_qa"]:
                if not isinstance(qa, dict):
                    continue
                q = qa.get("q", "")
                outline = qa.get("a_outline") or []
                why = qa.get("why_it_matters", "")
                follow = qa.get("followup", "")
                if q:
                    qa_lines.append(f"Q: {q}")
                for pt in outline:
                    qa_lines.append(f"  • {pt}")
                if why:
                    qa_lines.append(f"Why it matters: {why}")
                if follow:
                    qa_lines.append(f"Follow-up: {follow}")
                qa_lines.append("")
            section("Interview Q&A Practice", qa_lines, small=True)

        if safe["practice_plan"]:
            pp_lines: List[str] = []
            for blk in safe["practice_plan"]:
                if not isinstance(blk, dict):
                    continue
                period = blk.get("period", "")
                goals = blk.get("goals", "")
                tasks = blk.get("tasks") or []
                output = blk.get("output", "")
                if period:
                    pp_lines.append(f"Period: {period}")
                if goals:
                    pp_lines.append(f"Goals: {goals}")
                for t in tasks:
                    pp_lines.append(f"  • {t}")
                if output:
                    pp_lines.append(f"Output: {output}")
                pp_lines.append("")
            section("Practice Plan", pp_lines, small=True)

        if safe["application_checklist"]:
            section(
                "Application Checklist",
                [f"• {item}" for item in safe["application_checklist"]],
                small=True,
            )

        if safe["rewrite_suggestions"]:
            section(
                "Resume Rewrite Suggestions",
                [f"• {s}" for s in safe["rewrite_suggestions"]],
                small=True,
            )

        if safe["next_steps"]:
            section("Next Steps", [f"• {s}" for s in safe["next_steps"]])

        section("Impact Summary", [safe["impact_summary"] or "Evaluation complete."])

        c.save()
        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name="CareerAI_Deep_Evaluation_Report.pdf",
            mimetype="application/pdf",
        )

    except Exception as e:
        current_app.logger.exception("PDF export failed: %s", e)
        flash("Could not export PDF. Please try again later.", "danger")
        return redirect(url_for("jobpack.index"))
