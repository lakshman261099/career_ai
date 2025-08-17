import random, datetime as dt
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
from flask_login import login_user, logout_user, current_user, login_required
from models import db, User, UniversityAllowlist
from werkzeug.security import generate_password_hash
from limits import is_pro_user

auth_bp = Blueprint("auth", __name__, template_folder="../../templates")

@auth_bp.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        pw = request.form.get("password","")
        u = User.query.filter_by(email=email).first()
        if not u or not u.check_password(pw):
            flash("Invalid credentials.","error")
            return redirect(url_for("auth.login"))
        login_user(u)
        flash("Logged in.","success")
        return redirect(url_for("dashboard"))
    return render_template("auth/login.html")

@auth_bp.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        pw = request.form.get("password","")
        if User.query.filter_by(email=email).first():
            flash("Email already registered.","error")
            return redirect(url_for("auth.register"))
        # university allowlist?
        uni_id = None
        if getattr(current_app, "g", None) and getattr(current_app.g, "tenant", None):
            uni = current_app.g.tenant
            allowed = UniversityAllowlist.query.filter_by(university_id=uni.id, email=email).first()
            if not allowed:
                flash("This email is not in the university allowlist.","error")
                return redirect(url_for("auth.register"))
            uni_id = uni.id
        u = User(email=email, university_id=uni_id)
        u.set_password(pw)
        db.session.add(u); db.session.commit()
        # send OTP
        otp = str(random.randint(100000,999999))
        u.otp_code = otp
        u.otp_sent_at = dt.datetime.utcnow()
        db.session.commit()
        print(f"[OTP] Send to {email}: {otp}")  # TODO: email integration
        flash("Account created. Please verify via OTP.","info")
        return redirect(url_for("auth.verify"))
    return render_template("auth/register.html")

@auth_bp.route("/verify", methods=["GET","POST"])
def verify():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        otp = request.form.get("otp","").strip()
        u = User.query.filter_by(email=email).first()
        if not u:
            flash("No such user.","error")
            return redirect(url_for("auth.verify"))
        if u.otp_code == otp:
            u.is_verified = True
            # grant silver credits
            u.silver_balance += int(current_app.config["SILVER_ON_VERIFY"])
            db.session.commit()
            flash("Verified! You can login now.","success")
            return redirect(url_for("auth.login"))
        else:
            flash("Wrong OTP.","error")
    return render_template("auth/verify.html")

@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.","info")
    return redirect(url_for("auth.login"))
