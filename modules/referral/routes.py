# modules/referral/routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, OutreachContact

# ✅ Define the blueprint FIRST
referral_bp = Blueprint("referral", __name__)

@referral_bp.route("", methods=["GET"])
@login_required
def index():
    contacts = OutreachContact.query.filter_by(user_id=current_user.id).all()
    return render_template("referral_list.html", contacts=contacts)

@referral_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        if not name or not email:
            flash("Name and email are required.", "error")
            return redirect(url_for("referral.add"))
        
        contact = OutreachContact(user_id=current_user.id, name=name, email=email)
        db.session.add(contact)
        db.session.commit()
        flash("Referral contact added successfully.", "success")
        return redirect(url_for("referral.index"))

    return render_template("referral_form.html")

@referral_bp.route("/delete/<int:contact_id>", methods=["POST"])
@login_required
def delete(contact_id):
    contact = OutreachContact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    db.session.delete(contact)
    db.session.commit()
    flash("Referral contact deleted.", "info")
    return redirect(url_for("referral.index"))
