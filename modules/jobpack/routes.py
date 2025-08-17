from flask import Blueprint, render_template, request, current_app, flash
from flask_login import login_required, current_user
from models import db, JobPackReport, UsageLedger
from limits import enforce_free_feature, spend_coins
import datetime as dt

jobpack_bp = Blueprint("jobpack", __name__, template_folder="../../templates")

@jobpack_bp.route("/", methods=["GET","POST"])
@login_required
@enforce_free_feature("jobpack")
def run_jobpack():
    if request.method=="POST":
        role = request.form.get("role","")
        mode = request.form.get("mode","fast")
        ok,msg,spend = spend_coins(current_user,"jobpack",mode)
        if not ok:
            flash(msg,"error")
            return render_template("jobpack/run.html")
        # MOCK output
        if current_app.config["MOCK"]:
            result={"ats_score":80,"suggested_skills":["Python","Flask"],"interview_q":["Tell me about Flask?"],"links":["https://flask.palletsprojects.com/"]}
            r=JobPackReport(user_id=current_user.id, role=role, mode=mode, verdict="ok", payload_json=str(result))
            db.session.add(r); db.session.commit()
            return render_template("jobpack/result.html", result=result)
        # TODO: OpenAI call
    return render_template("jobpack/run.html")
