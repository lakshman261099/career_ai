import io, json, time, hashlib
from flask import Blueprint, request, render_template, redirect, url_for, flash, send_file, abort, current_app
from flask_login import login_required, current_user
from models import db, JobPackReport, Subscription
from .helpers import fetch_url_text, fast_jobpack_llm, deep_jobpack_llm, build_pdf_bytes
from limits import is_pro_user, can_consume_free, consume_free, client_ip, free_budget_blocked

jobpack_bp = Blueprint("jobpack", __name__)

def is_pro() -> bool:
    sub = Subscription.query.filter_by(user_id=current_user.id).first()
    return bool(sub and sub.status == "active")

@jobpack_bp.route("", methods=["GET"])
@login_required
def index():
    return render_template("jobpack_form.html")

@jobpack_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    role = (request.form.get("role") or "").strip() or "Candidate"
    jd = (request.form.get("jd") or "").strip()
    resume = (request.form.get("resume") or "").strip()
    mode = (request.form.get("mode") or "fast").strip().lower()

    if mode == "deep" and not is_pro():
        flash("Deep mode is Pro only. Upgrade to continue.", "error")
        return redirect(url_for("pricing"))

    if jd.startswith("http"):
        if "linkedin.com" in jd.lower():
            flash("LinkedIn URLs are not supported. Paste JD text.", "error")
            return redirect(url_for("dashboard"))
        fetched = fetch_url_text(jd); jd_text = fetched if fetched else jd
    else:
        jd_text = jd
    if not jd_text:
        flash("Please paste a job description.", "error")
        return redirect(url_for("jobpack.index"))

    if mode != "deep" and not is_pro_user(current_user):
        if free_budget_blocked(): current_app.config["MOCK"] = True
        ip = client_ip()
        if not can_consume_free(current_user, ip):
            flash("Free daily limit reached (2/day). Upgrade to Pro for unlimited runs.", "error")
            return redirect(url_for("pricing"))
        consume_free(current_user, ip)

    if mode != "deep":
        key = "JDFAST:" + hashlib.sha256(jd_text.encode()).hexdigest()
        ttl = int(current_app.config.get("CACHE_TTL_JD_FAST_SEC", 172800))
        if not hasattr(current_app, "_jd_cache"): current_app._jd_cache = {}
        cached = current_app._jd_cache.get(key)
        if cached and (time.time() - cached["ts"] < ttl):
            pack = cached["val"]
        else:
            pack = fast_jobpack_llm(role, jd_text, resume)
            current_app._jd_cache[key] = {"ts": time.time(), "val": pack}
    else:
        pack = deep_jobpack_llm(role, jd_text, resume)

    verdict = pack.get("overall_verdict", {}).get("status", "")
    report_id = None
    if is_pro():
        try:
            rec = JobPackReport(user_id=current_user.id, role=role, mode=mode, verdict=verdict, payload_json=json.dumps(pack))
            db.session.add(rec); db.session.commit(); report_id = rec.id
        except Exception:
            db.session.rollback()

    return render_template("jobpack_view.html", pack=pack, report_id=report_id)

@jobpack_bp.route("/view/<int:report_id>")
@login_required
def view(report_id: int):
    rec = JobPackReport.query.filter_by(id=report_id, user_id=current_user.id).first_or_404()
    pack = json.loads(rec.payload_json or "{}")
    return render_template("jobpack_view.html", pack=pack, report_id=rec.id)

@jobpack_bp.route("/export_pdf/<int:report_id>")
@login_required
def export_pdf(report_id: int):
    rec = JobPackReport.query.filter_by(id=report_id, user_id=current_user.id).first_or_404()
    if rec.mode == "deep" and not is_pro():
        abort(403)
    pack = json.loads(rec.payload_json or "{}")
    pdf_bytes = build_pdf_bytes(pack)
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True, download_name=f"jobpack_{report_id}.pdf")
