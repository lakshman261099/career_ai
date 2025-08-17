from flask import Blueprint, render_template, request, flash
from flask_login import login_required
from limits import enforce_free_feature

referral_bp = Blueprint("referral", __name__, template_folder="../../templates")

@referral_bp.route("/", methods=["GET","POST"])
@login_required
@enforce_free_feature("referral")
def referral_trainer():
    if request.method=="POST":
        target=request.form.get("target","")
        # MOCK
        result={"script":f"Hi, I am interested in {target}. Could you kindly refer me?","tips":["Keep it short","Be polite"]}
        return render_template("referral/result.html", result=result)
    return render_template("referral/run.html")
