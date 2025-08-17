from flask import Blueprint, render_template, request, flash, current_app
from flask_login import login_required, current_user
from limits import spend_coins, enforce_free_feature

skillmapper_bp = Blueprint("skillmapper", __name__, template_folder="../../templates")

@skillmapper_bp.route("/", methods=["GET","POST"])
@login_required
@enforce_free_feature("skillmapper")
def skillmapper():
    if request.method=="POST":
        skills=request.form.get("skills","")
        mode="deep"
        ok,msg,spend=spend_coins(current_user,"skillmapper",mode)
        if not ok:
            flash(msg,"error")
            return render_template("skillmapper/run.html")
        # MOCK
        result={"jobs":[{"role":"Python Dev","why":"Matches skills in Python, Flask"},{"role":"Data Analyst","why":"Good with SQL"}],"note":"These are suggestions, not guaranteed"}
        return render_template("skillmapper/result.html", result=result)
    return render_template("skillmapper/run.html")
