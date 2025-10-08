# modules/jobpack/routes.py
import io, json
from datetime import datetime
from typing import Any, List

from flask import Blueprint, render_template, request, send_file, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas

from models import db, JobPackReport, ResumeAsset
from limits import authorize_and_consume, can_use_pro, consume_pro
from modules.jobpack.utils_ats import analyze_jobpack


jobpack_bp = Blueprint("jobpack", __name__, template_folder='../../templates/jobpack')


# ---------------------- helpers ----------------------

def _safe_result(raw) -> dict:
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
        "resume_ats": {
            "resume_ats_score": 0,
            "blockers": [],
            "warnings": [],
            "keyword_coverage": {
                "required_keywords": [],
                "present_keywords": [],
                "missing_keywords": []
            },
            "resume_rewrite_actions": []
        },
        # NEW
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


def _profile_to_resume_text(profile) -> str:
    """
    Build a human-readable text resume from structured Profile Portal data.
    Used for Pro deep evaluation if resume not uploaded.
    """
    if not profile:
        return ""

    lines: List[str] = []
    # Header
    if profile.full_name:
        lines.append(profile.full_name)
    if profile.headline:
        lines.append(profile.headline)
    if profile.location:
        lines.append(f"Location: {profile.location}")

    # Skills
    skills = _coerce_skill_names(profile.skills)
    if skills:
        lines.append("Skills: " + ", ".join(skills))

    # Experience
    if profile.experience:
        lines.append("\nExperience:")
        for item in profile.experience[:8]:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "")
            company = item.get("company", "")
            dates = " • ".join(filter(None, [item.get("start", ""), item.get("end", "")]))
            if role or company:
                lines.append(f"- {role} at {company} ({dates})")
            for b in (item.get("bullets") or [])[:5]:
                if isinstance(b, str) and b.strip():
                    lines.append(f"  • {b.strip()}")

    # Projects
    projects = getattr(profile.user, "projects", []) if getattr(profile, "user", None) else []
    if projects:
        lines.append("\nProjects:")
        for p in projects[:5]:
            title = getattr(p, "title", "")
            desc = getattr(p, "short_desc", "")
            stack = ", ".join(p.tech_stack or [])
            lines.append(f"- {title}")
            if desc:
                lines.append(f"  • {desc}")
            if stack:
                lines.append(f"  • Stack: {stack}")

    # Education
    if profile.education:
        lines.append("\nEducation:")
        for e in profile.education[:4]:
            if not isinstance(e, dict):
                continue
            school = e.get("school", "")
            degree = e.get("degree", "")
            year = str(e.get("year", ""))
            lines.append(" - " + " — ".join(filter(None, [school, degree, year])))

    return "\n".join(lines)[:6000]


def _get_latest_profile_resume_text(user) -> str:
    """
    Prefer the most recent ResumeAsset text.
    Fallback: synthesize from Profile Portal.
    """
    try:
        asset = (
            ResumeAsset.query
            .filter(ResumeAsset.user_id == user.id)
            .order_by(ResumeAsset.created_at.desc())
            .first()
        )
        if asset and asset.text:
            return asset.text
    except Exception as e:
        current_app.logger.warning("ResumeAsset lookup failed: %s", e)

    profile = getattr(user, "profile", None)
    return _profile_to_resume_text(profile)


# ---------------------- main route ----------------------

@jobpack_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Job Pack — AI-powered ATS + Resume Evaluator
    Free: standard match analysis.
    Pro: CareerAI Deep Evaluation (uses profile portal).
    """
    mode = (request.form.get("mode") or "basic").lower()
    result = None

    if request.method == "POST":
        jd_text = (request.form.get("jd") or "").strip()
        resume_text = (request.form.get("resume") or "").strip()

        if not jd_text:
            flash("Please paste a job description.", "warning")
            return render_template("jobpack/index.html", result=None, mode=mode)

        try:
            # Credit validation
            if mode == "pro":
                if not can_use_pro(current_user, "jobpack"):
                    flash("Not enough Pro credits to run Deep Evaluation.", "warning")
                    return redirect(url_for("billing.index"))
            else:
                if not authorize_and_consume(current_user, "jobpack"):
                    flash("You’ve used your free Job Pack today. Upgrade to Pro ⭐ to continue.", "warning")
                    return redirect(url_for("billing.index"))

            # For Pro Deep Evaluation, load profile resume automatically if none pasted
            if mode == "pro" and not resume_text:
                resume_text = _get_latest_profile_resume_text(current_user)
                if resume_text:
                    flash("Loaded your Profile Portal data for deep analysis.", "info")

            # Run analysis
            raw = analyze_jobpack(jd_text, resume_text, pro_mode=(mode == "pro"))
            result = _safe_result(raw)

            # consume credit after successful analysis
            if mode == "pro":
                consume_pro(current_user, "jobpack")

            # persist report
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

            return render_template(
                "jobpack/result.html",
                result=result,
                mode=mode,
                is_pro=current_user.is_pro,
            )

        except Exception as e:
            current_app.logger.exception("JobPack Analysis Error: %s", e)
            try:
                db.session.rollback()
            except Exception:
                pass
            flash("An error occurred during analysis. Please try again later.", "danger")

    return render_template("jobpack/index.html", result=None, mode=mode)


# ---------------------- PDF Export ----------------------

@jobpack_bp.route("/export/pdf", methods=["POST"], endpoint="export_pdf")
@login_required
def export_pdf():
    """
    Export Job Pack report to PDF (Pro only).
    """
    if not current_user.is_pro:
        flash("PDF export is a Pro feature.", "warning")
        return redirect(url_for("billing.index"))

    try:
        data = request.form.get("data")
        if not data:
            flash("Missing report data.", "danger")
            return redirect(url_for("jobpack.index"))

        result = json.loads(data)
        safe = _safe_result(result)

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        y = height - 60

        def section(title: str, lines: List[str], small=False):
            nonlocal y
            c.setFont("Helvetica-Bold", 13)
            c.setFillColor(colors.white)
            c.drawString(40, y, title)
            y -= 18
            c.setFont("Helvetica", 9 if small else 10)
            for line in lines:
                if not line:
                    continue
                c.drawString(40, y, line[:110])
                y -= 12 if small else 14
                if y < 60:
                    c.showPage()
                    y = height - 60

        # Header
        c.setFillColorRGB(0.1, 0.12, 0.18)
        c.rect(0, height - 80, width, 80, fill=True, stroke=False)
        c.setFont("Helvetica-Bold", 16)
        c.setFillColor(colors.white)
        c.drawString(40, height - 50, "CareerAI Deep Evaluation Report ⭐")

        section("Role Summary", [safe["summary"], f"Detected Role: {safe['role_detected']}"])
        section("Fit Overview", [f"{f['category']}: {f['match']}% — {f['comment']}" for f in safe["fit_overview"]])
        section("ATS Score", [f"Overall Score: {safe['ats_score']}%"])

        # Resume ATS details
        ra = safe.get("resume_ats", {}) or {}
        kc = ra.get("keyword_coverage", {}) or {}
        section("Resume ATS — Must Fix (Blockers)", [f"• {b}" for b in ra.get("blockers", [])] or ["None"], small=True)
        section("Resume ATS — Should Fix (Warnings)", [f"• {w}" for w in ra.get("warnings", [])] or ["None"], small=True)
        section("Resume ATS — Missing Keywords", [", ".join(kc.get("missing_keywords", [])[:30]) or "None"], small=True)
        section("Resume ATS — Exact Fixes", [f"• {a}" for a in ra.get("resume_rewrite_actions", [])], small=True)

        section("Resume Rewrite Suggestions", [f"• {s}" for s in safe["rewrite_suggestions"]])
        section("Next Steps", [f"• {s}" for s in safe["next_steps"]])
        section("Impact Summary", [safe["impact_summary"]])

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
