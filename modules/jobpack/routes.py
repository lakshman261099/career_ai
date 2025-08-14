import os, json
from flask import Blueprint, request, render_template, redirect, url_for, flash, send_file, abort
from flask_login import login_required, current_user
from models import db, JobPackReport, Subscription
from .helpers import fetch_url_text, fast_jobpack_llm, deep_jobpack_llm, build_pdf_bytes

jobpack_bp = Blueprint("jobpack", __name__, template_folder="../../templates")

def is_pro():
    subs = Subscription.query.filter_by(user_id=current_user.id).first()
    return subs and subs.status == "active"

@jobpack_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    role = request.form.get("role","").strip() or "Candidate"
    jd = request.form.get("jd","").strip()
    resume = request.form.get("resume","").strip()
    mode = request.form.get("mode","fast")
    if mode == "deep" and not is_pro():
        flash("Deep mode is Pro only. Upgrade to continue.", "error")
        return redirect(url_for("pricing"))

    # If JD looks like a URL (not LinkedIn), try fetch
    if jd.startswith("http"):
        if "linkedin.com" in jd.lower():
            flash("LinkedIn URLs are not supported. Paste JD text.", "error")
            return redirect(url_for("dashboard"))
        fetched = fetch_url_text(jd)
        jd_text = fetched if fetched else jd
    else:
        jd_text = jd

    pack = deep_jobpack_llm(role, jd_text, resume) if mode=="deep" else fast_jobpack_llm(role, jd_text, resume)

    verdict = pack.get("overall_verdict",{}).get("status","")
    # Save only if Pro (per data policy); free can opt‑in by toggling plan -> not saved
    save_allowed = is_pro()
    report_id = None
    if save_allowed:
        r = JobPackReport(user_id=current_user.id, role=role, mode=mode, verdict=verdict, payload_json=json.dumps(pack))
        db.session.add(r); db.session.commit()
        report_id = r.id

    return render_template("jobpack_view.html", pack=pack, report_id=report_id)

@jobpack_bp.route("/view/<int:report_id>")
@login_required
def view(report_id):
    r = JobPackReport.query.filter_by(id=report_id, user_id=current_user.id).first_or_404()
    pack = json.loads(r.payload_json or "{}")
    return render_template("jobpack_view.html", pack=pack, report_id=r.id)

@jobpack_bp.route("/export_pdf/<int:report_id>")
@login_required
def export_pdf(report_id):
    r = JobPackReport.query.filter_by(id=report_id, user_id=current_user.id).first_or_404()
    if r.mode == "deep" and not is_pro():
        abort(403)
    pack = json.loads(r.payload_json or "{}")
    pdf = build_pdf_bytes(pack)
    return send_file(
        io.BytesIO(pdf),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"jobpack_{report_id}.pdf"
    )
