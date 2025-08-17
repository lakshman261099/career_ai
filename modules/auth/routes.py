# modules/auth/routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User

auth_bp = Blueprint("auth", __name__, template_folder="../../templates")

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if not user:
            flash("No account found for that email.", "danger")
            return render_template("auth/login.html")
        if hasattr(user, "is_verified") and not user.is_verified:
            flash("Please verify your email before logging in.", "warning")
            return render_template("auth/login.html")
        if check_password_hash(user.password_hash, password):
            login_user(user, remember=True, fresh=True)
            return redirect(url_for("post_login"))
        flash("Invalid email or password.", "danger")
    return render_template("auth/login.html")

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not name or not email or not password:
            flash("Fill all fields.", "warning")
            return render_template("auth/register.html")
        if User.query.filter_by(email=email).first():
            flash("Email is already registered.", "danger")
            return render_template("auth/register.html")
        user = User(name=name, email=email,
                    password_hash=generate_password_hash(password))
        # default: not verified until your flow marks it true
        if hasattr(user, "is_verified"):
            user.is_verified = True  # set True if you don’t have email OTP yet
        db.session.add(user)
        db.session.commit()
        flash("Account created. You can log in now.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html")

@auth_bp.route("/verify", methods=["GET", "POST"])
def verify():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        code = (request.form.get("code") or "").strip()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash("Account not found.", "danger")
            return render_template("auth/verify.html")
        if hasattr(user, "is_verified"):
            user.is_verified = True
            db.session.commit()
        flash("Verified. You can log in now.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/verify.html")

@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("home"))
