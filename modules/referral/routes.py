from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, OutreachContact, Subscription
from .helpers import generate_messages

referral_bp = Blueprint("referral", __name__)

def _is_pro(uid) -> bool:
    sub = Subscription.query.filter_by(user_id=uid).first()
    return bool(sub and sub.status == "active")

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
        role = (request.form.get("role") or "").strip()
        company = (request.form.get("company") or "").strip()
        source = (request.form.get("source") or "").strip() or "manual"
        if not name or not email:
            flash("Name and email required.", "error")
            return redirect(url_for("referral.add"))
        c = OutreachContact(user_id=current_user.id, name=name, email=email, role=role, company=company, source=source)
        db.session.add(c); db.session.commit()
        flash("Contact added.", "success")
        return redirect(url_for("referral.index"))
    return render_template("referral_form.html")

@referral_bp.route("/generate/<int:contact_id>", methods=["GET"])
@login_required
def generate(contact_id):
    contact = OutreachContact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    deep = request.args.get("mode","fast").lower() == "deep"
    if deep and not _is_pro(current_user.id):
        flash("Deep outreach is Pro only.", "error")
        return redirect(url_for("pricing"))
    # sample candidate profile; wire to user settings later if desired
    candidate = {"role": "Data Analyst Intern", "highlights": "SQL, Python, 2 dashboards, campus club lead"}
    msgs = generate_messages(
        {"name": contact.name, "role": contact.role, "company": contact.company or "your company", "email": contact.email, "source": contact.source or "manual"},
        candidate_profile=candidate,
        deep=deep
    )
    return render_template("referral_messages.html", contact=contact, messages=msgs, mode="deep" if deep else "fast")

@referral_bp.route("/delete/<int:contact_id>", methods=["POST"])
@login_required
def delete(contact_id):
    contact = OutreachContact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    db.session.delete(contact); db.session.commit()
    flash("Contact deleted.", "info")
    return redirect(url_for("referral.index"))
