import io, json
from flask import Blueprint, render_template, request, send_file, flash
from flask_login import login_required, current_user
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from helpers import jobpack_analyze
from limits import can_use_free, consume_free, can_use_pro, consume_pro

jobpack_bp = Blueprint("jobpack", __name__, template_folder='../../templates/jobpack')

@jobpack_bp.route("/", methods=["GET","POST"])
@login_required
def index():
    result = None
    if request.method == "POST":
        jd = request.form.get("jd","").strip()
        resume = request.form.get("resume","").strip()
        deep = request.form.get("mode") == "pro"
        feature = "jobpack"
        if not jd:
            flash("Paste a job description.", "error")
        else:
            if deep:
                if not can_use_pro(current_user, feature):
                    flash("Not enough Pro coins.", "error")
                else:
                    consume_pro(current_user, feature)
                    result = jobpack_analyze(jd, resume)
            else:
                if not can_use_free(current_user, feature):
                    flash("Daily free limit reached.", "error")
                else:
                    consume_free(current_user, feature)
                    result = jobpack_analyze(jd, resume)
    return render_template("jobpack/index.html", result=result)

@jobpack_bp.route("/export", methods=["POST"])
@login_required
def export_pdf():
    data = request.form.get("data")
    if not data:
        return ("Missing data", 400)
    result = json.loads(data)
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 40
    p.setFont("Helvetica-Bold", 14); p.drawString(40, y, "Job Pack Report"); y -= 24
    p.setFont("Helvetica", 10)
    p.drawString(40, y, f"Fit Score: {result.get('fit',{}).get('score','-')}"); y -= 16
    p.drawString(40, y, "Gaps: " + ", ".join(result.get("fit",{}).get("gaps", []))); y -= 16
    p.drawString(40, y, "Keywords: " + ", ".join(result.get("fit",{}).get("keywords", []))); y -= 20
    p.setFont("Helvetica-Bold", 12); p.drawString(40, y, "Cover Letter:"); y -= 16
    p.setFont("Helvetica", 10)
    for line in result.get("cover","").split("\n"):
        p.drawString(40, y, line[:100]); y -= 14
        if y < 60: p.showPage(); y = height - 40
    p.setFont("Helvetica-Bold", 12); p.drawString(40, y, "Interview Q&A:"); y -= 16
    p.setFont("Helvetica", 10)
    for qa in result.get("qna", []):
        p.drawString(40, y, "Q: " + qa.get("q","")[:100]); y -= 14
        p.drawString(40, y, "A: " + qa.get("a","")[:100]); y -= 18
        if y < 60: p.showPage(); y = height - 40
    p.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="jobpack_report.pdf", mimetype="application/pdf")
