# modules/settings/routes.py
from flask import Blueprint, render_template
from flask_login import login_required, current_user
from models import Subscription

settings_bp = Blueprint("settings", __name__)

@settings_bp.get("/")
@login_required
def page():
    sub = Subscription.query.filter_by(user_id=current_user.id).order_by(Subscription.current_period_end.desc()).first()
    return render_template("settings.html", sub=sub)
