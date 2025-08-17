from flask import Blueprint, render_template, request, current_app, flash
from flask_login import login_required, current_user
from models import db, InternshipRecord
from limits import enforce_free_feature, spend_coins

internships_bp = Blueprint("internships", __name__, template_folder="../../templates")

@internships_bp.route("/", methods=["GET","POST"])
@login_required
@enforce_free_feature("internships")
def internships():
    if request.method=="POST":
        role=request.form.get("role","")
        mode=request.form.get("mode","fast")
        ok,msg,spend=spend_coins(current_user,"internships",mode)
        if not ok:
            flash(msg,"error")
            return render_template("internships/run.html")
        # MOCK
        result={"benefits":"You will learn teamwork.","skills":["Communication","Python"],"details":"Deep dive..." if mode=="deep" else "Basic info"}
        rec=InternshipRecord(user_id=current_user.id, role=role, source="mock", title="Intern", company="Mock Inc", link="#", match_score=90, missing_skills="JS")
        db.session.add(rec); db.session.commit()
        return render_template("internships/result.html", result=result)
    return render_template("internships/run.html")
