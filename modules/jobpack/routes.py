# modules/jobpack/routes.py

import io, json
from datetime import datetime
from flask import Blueprint, render_template, request, send_file, flash, redirect, url_for
from flask_login import login_required, current_user
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from helpers import jobpack_analyze
from models import db, JobPackReport
from limits import authorize_and_consume, can_use_pro, consume_pro

jobpack_bp = Blueprint("jobpack", __name__, template_folder='../../templates/jobpack')


@jobpack_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    """
    Paste-only Job Pack analyzer.
    mode=basic  -> free first, fallback to pro (authorize_and_consume)
    mode=pro    -> strictly Pro (consume ⭐ as per FEATURES['jobpack'].pro_cost)
    """
    result = None
    last_report_id = None

    if request.method == "POST":
        jd = (request.form.get("jd") or "").strip()
        resume = (request.form.get("resume") or "").strip()
        mode = (request.form.get("mode") or "basic").lower()

        if not jd:
            flash("Paste a job description.", "error")
            return render_template("jobpack/index.html", result=None, last_report_id=None, mode=mode)

        if mode == "pro":
            # Pro-only deep analysis
            if not can_use_pro(current_user, "jobpack"):
                flash("Not enough Pro credits for deep analysis.", "warning")
                return redirect(url_for("billing.index"))
            consume_pro(current_user, "jobpack")
            result = jobpack_analyze(jd, resume)
        else:
            # Basic analysis: free if available, else Pro if available
            if not authorize_and_consume(current_user, "jobpack"):
                flash("You’ve hit today’s free limit. Upgrade to Pro to continue.", "warning")
                return redirect(url_for("billing.index"))
            result = jobpack_analyze(jd, resume)

        # Persist report for history
        report = JobPackReport(
            user_id=current_user.id,
            job_title=None,
            company=None,
            jd_text=jd,
            analysis=json.dumps(result) if isinstance(result, dict) else str(result),
            created_at=datetime.utcnow(),
        )
        db.session.add(report)
        db.session.commit()
        last_report_id = report.id

    return render_template("jobpack/index.html", result=result, last_report_id=last_report_id)


@jobpack_bp.route("/export/pdf", methods=["POST"], endpoint="export_pdf")
@login_required
def export_pdf():
    """
    Pro-only PDF export (gated, no extra credit consumed).
    """
    if not current_user.is_pro:
        flash("PDF export is a Pro feature.", "warning")
        return redirect(url_for("billing.index"))

    data = request.form.get("data")
    if not data:
        return ("Missing data", 400)

    try:
        result = json.loads(data)
    except Exception:
        return ("Bad JSON", 400)

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 40

    # Header
    p.setFont("Helvetica-Bold", 14)
    p.drawString(40, y, "Job Pack Report")
    y -= 24

    # Fit section
    fit = result.get("fit", {}) if isinstance(result, dict) else {}
    p.setFont("Helvetica", 10)
    p.drawString(40, y, f"Fit Score: {fit.get('score', '-')}")
    y -= 16
    p.drawString(40, y, "Gaps: " + ", ".join(fit.get("gaps", []) or []))
    y -= 16
    p.drawString(40, y, "Keywords: " + ", ".join(fit.get("keywords", []) or []))
    y -= 20

    # Cover Letter
    p.setFont("Helvetica-Bold", 12)
    p.drawString(40, y, "Cover Letter:")
    y -= 16
    p.setFont("Helvetica", 10)
    for line in (result.get("cover", "") or "").split("\n"):
        p.drawString(40, y, (line or "")[:100])
        y -= 14
        if y < 60:
            p.showPage(); y = height - 40
            p.setFont("Helvetica", 10)

    # Q&A
    p.setFont("Helvetica-Bold", 12)
    p.drawString(40, y, "Interview Q&A:")
    y -= 16
    p.setFont("Helvetica", 10)
    for qa in (result.get("qna", []) or []):
        p.drawString(40, y, "Q: " + (qa.get("q", "") or "")[:100])
        y -= 14
        p.drawString(40, y, "A: " + (qa.get("a", "") or "")[:100])
        y -= 18
        if y < 60:
            p.showPage(); y = height - 40
            p.setFont("Helvetica", 10)

    p.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="jobpack_report.pdf", mimetype="application/pdf")
