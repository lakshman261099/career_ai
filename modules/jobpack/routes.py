# modules/jobpack/routes.py
import io
import json
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
)
from flask_login import current_user, login_required
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from limits import authorize_and_consume, can_use_pro, consume_pro
from models import JobPackReport, db
from modules.jobpack.utils_ats import analyze_jobpack
from modules.common.profile_loader import load_profile_snapshot

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


# ---------------------- main route ----------------------


@jobpack_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Job Pack — AI-powered ATS + Resume Evaluator
    Free: standard match analysis (gpt-4o-mini).
    Pro: CareerAI Deep Evaluation (gpt-4o).
    Profile Portal is the main resume source for ALL runs.
    """
    mode = (request.form.get("mode") or "basic").lower()
    is_pro_run = mode == "pro"
    result = None

    profile_snapshot = load_profile_snapshot(current_user)
    profile_resume_text = profile_snapshot.get("resume_text") or ""

    if request.method == "POST":
        jd_text = (request.form.get("jd") or "").strip()

        if not jd_text:
            flash("Please paste a job description.", "warning")
            return render_template(
                "jobpack/index.html",
                result=None,
                mode=mode,
                profile_snapshot=profile_snapshot,
            )

        # Credits
        if is_pro_run:
            if not can_use_pro(current_user, "jobpack"):
                flash("Not enough Pro credits to run Deep Evaluation.", "warning")
                return redirect(url_for("billing.index"))
        else:
            if not authorize_and_consume(current_user, "jobpack"):
                flash(
                    "You’ve used your free Job Pack today. Upgrade to Pro ⭐ to continue.",
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

        # Run AI
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

        # Mark resume_missing for UI if resume_text was empty
        if not resume_text:
            result["resume_missing"] = True

        # Deduct ⭐ for pro runs AFTER successful AI call
        if is_pro_run:
            try:
                consume_pro(current_user, "jobpack")
            except Exception as e:
                current_app.logger.warning(
                    "Pro credit consume failed after analysis: %s", e
                )

        # Persist (best-effort)
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
        except Exception as e:
            current_app.logger.warning("JobPack report save failed: %s", e)
            try:
                db.session.rollback()
            except Exception:
                pass

        # SAFE RENDER: if the template raises, show a small inline debug view
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

    # Infer pro-ness for display: deep reports generally carry a non-standard tier
    tier = (result.get("report_tier") or "").lower()
    is_pro_run = "deep" in tier or "careerai" in tier

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

        # Prefer loading from DB when report_id is provided
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

        # simple text wrapper (characters-based, not perfect but readable)
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

        # Header bar
        c.setFillColorRGB(0.1, 0.12, 0.18)
        c.rect(0, height - 80, width, 80, fill=True, stroke=False)
        c.setFont("Helvetica-Bold", 16)
        c.setFillColor(colors.white)
        c.drawString(40, height - 50, "CareerAI Deep Evaluation Report ⭐")

        # ---- ROLE SUMMARY ----
        section(
            "Role Summary",
            [
                safe["summary"] or "No summary available.",
                f"Detected Role: {safe['role_detected'] or '—'}",
            ],
        )

        # ---- FIT OVERVIEW ----
        fit_lines: List[str] = []
        for f in safe["fit_overview"]:
            if isinstance(f, dict):
                cat = f.get("category", "")
                m = f.get("match", 0)
                comment = f.get("comment", "")
                fit_lines.append(f"{cat}: {m}% — {comment}")
        if fit_lines:
            section("Fit Overview", fit_lines)

        # ---- ATS SCORE + SUBSCORES ----
        subs = safe.get("subscores") or {}
        subs_lines = [
            f"Overall ATS Alignment (model estimate): {safe['ats_score']}%",
            f"Keyword relevance: {subs.get('keyword_relevance', 0)}%",
            f"Quantifiable impact: {subs.get('quantifiable_impact', 0)}%",
            f"Formatting & clarity: {subs.get('formatting_clarity', 0)}%",
            f"Professional tone: {subs.get('professional_tone', 0)}%",
        ]
        section("ATS Score & Subscores (model estimates)", subs_lines)

        # ---- SKILL TABLE (JD vs resume) ----
        skill_lines: List[str] = []
        for row in safe["skill_table"]:
            if isinstance(row, dict):
                skill = row.get("skill", "")
                status = row.get("status", "")
                if skill:
                    skill_lines.append(f"{skill} — {status}")
        if skill_lines:
            section("JD vs Resume Skill Match", skill_lines, small=True)

        # ---- RESUME ATS DETAILS ----
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

        # ---- LEARNING LINKS ----
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

        # ---- INTERVIEW Q&A ----
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
                qa_lines.append("")  # blank line between Qs
            section("Interview Q&A Practice", qa_lines, small=True)

        # ---- PRACTICE PLAN ----
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

        # ---- APPLICATION CHECKLIST ----
        if safe["application_checklist"]:
            section(
                "Application Checklist",
                [f"• {item}" for item in safe["application_checklist"]],
                small=True,
            )

        # ---- RESUME REWRITE SUGGESTIONS ----
        if safe["rewrite_suggestions"]:
            section(
                "Resume Rewrite Suggestions",
                [f"• {s}" for s in safe["rewrite_suggestions"]],
                small=True,
            )

        # ---- NEXT STEPS ----
        if safe["next_steps"]:
            section(
                "Next Steps",
                [f"• {s}" for s in safe["next_steps"]],
            )

        # ---- IMPACT SUMMARY ----
        section(
            "Impact Summary",
            [safe["impact_summary"] or "Evaluation complete."],
        )

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
