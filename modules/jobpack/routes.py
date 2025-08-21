# modules/jobpack/routes.py

import io, json, traceback
from datetime import datetime
from flask import Blueprint, render_template, request, send_file, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from helpers import jobpack_analyze
from models import db, JobPackReport
from limits import authorize_and_consume, can_use_pro, consume_pro

jobpack_bp = Blueprint("jobpack", __name__, template_folder='../../templates/jobpack')


# ---------- helpers ----------
def _safe_result(raw) -> dict:
    """
    Normalize any analyzer output (None/str/dict/other) into a safe dict
    that templates can always render without key errors.
    """
    base = {
        "fit": {"score": "-", "gaps": [], "keywords": []},
        "cover": "",
        "qna": [],  # list of {"q": "...", "a": "..."}
        "notes": "",
    }

    # If it's a string, treat as notes/summary
    if isinstance(raw, str):
        base["notes"] = raw[:4000]
        return base

    # If it's a dict, pull known fields defensively
    if isinstance(raw, dict):
        fit = raw.get("fit") or {}
        base["fit"]["score"] = fit.get("score", base["fit"]["score"])
        base["fit"]["gaps"] = fit.get("gaps", base["fit"]["gaps"]) or []
        base["fit"]["keywords"] = fit.get("keywords", base["fit"]["keywords"]) or []
        base["cover"] = (raw.get("cover") or "")[:20000]
        qna = raw.get("qna") or []
        # keep only well-formed q/a items
        safe_qna = []
        for item in qna:
            if isinstance(item, dict):
                safe_qna.append({"q": (item.get("q") or "")[:1000], "a": (item.get("a") or "")[:2000]})
        base["qna"] = safe_qna[:20]
        base["notes"] = (raw.get("notes") or raw.get("summary") or "")[:4000]
        return base

    # Fallback for weird types
    base["notes"] = str(raw)[:4000]
    return base


# ---------- routes ----------
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
    mode = (request.form.get("mode") or "basic").lower() if request.method == "POST" else "basic"

    if request.method == "POST":
        jd = (request.form.get("jd") or "").strip()
        resume = (request.form.get("resume") or "").strip()

        if not jd:
            flash("Paste a job description.", "warning")
            return render_template("jobpack/index.html", result=None, last_report_id=None, mode=mode)

        try:
            # 1) Credit gating (only consume AFTER success)
            used_pro_credit = False
            if mode == "pro":
                if not can_use_pro(current_user, "jobpack"):
                    flash("Not enough Pro credits for deep analysis.", "warning")
                    return redirect(url_for("billing.index"))
                # don't consume yet; consume after successful analysis
                use_pro = True
            else:
                # basic: try free; if none, block with upsell (no hidden pro auto-spend)
                if not authorize_and_consume(current_user, "jobpack"):
                    flash("You’ve hit today’s free limit. Upgrade to Pro to continue.", "warning")
                    return redirect(url_for("billing.index"))
                use_pro = False  # already consumed a free unit

            # 2) Run analysis safely
            raw = jobpack_analyze(jd, resume)  # may raise or return odd shapes
            safe = _safe_result(raw)

            # 3) Only now consume pro credit if requested mode=pro and analysis succeeded
            if use_pro:
                consume_pro(current_user, "jobpack")
                used_pro_credit = True

            # 4) Persist report
            report = JobPackReport(
                user_id=current_user.id,
                job_title=None,
                company=None,
                jd_text=jd,
                analysis=json.dumps(safe, ensure_ascii=False),
                created_at=datetime.utcnow(),
            )
            db.session.add(report)
            db.session.commit()
            last_report_id = report.id
            result = safe

        except Exception as e:
            current_app.logger.exception("JobPack analyse error: %s", e)
            try:
                db.session.rollback()  # <-- IMPORTANT
            except Exception:
                pass
            flash("Something went wrong while analysing. Try a different job text or try again later.", "danger")

            result = _safe_result({"notes": "Analysis failed.", "fit": {"score": "-", "gaps": [], "keywords": []}})
            last_report_id = None
            # Log full traceback; never 500 to the user
            

    return render_template("jobpack/index.html", result=result, last_report_id=last_report_id, mode=mode)


@jobpack_bp.route("/export/pdf", methods=["POST"], endpoint="export_pdf")
@login_required
def export_pdf():
    """
    Pro-only PDF export (gated, no extra credit consumed).
    """
    # Be robust: some codebases define is_pro as prop; others as status
    is_pro = getattr(current_user, "is_pro", False) or getattr(current_user, "subscription_status", "free") == "pro"
    if not is_pro:
        flash("PDF export is a Pro feature.", "warning")
        return redirect(url_for("billing.index"))

    data = request.form.get("data")
    if not data:
        return ("Missing data", 400)

    try:
        result = json.loads(data)
    except Exception:
        return ("Bad JSON", 400)

    # Ensure safe shape for PDF generator
    safe = _safe_result(result)

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 40

    # Header
    p.setFont("Helvetica-Bold", 14)
    p.drawString(40, y, "Job Pack Report")
    y -= 24

    # Fit section
    fit = safe.get("fit", {})
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
    for line in (safe.get("cover", "") or "").split("\n"):
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
    for qa in (safe.get("qna", []) or []):
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
