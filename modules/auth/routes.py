# modules/auth/routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.exc import IntegrityError
from models import db, User

auth_bp = Blueprint("auth", __name__, template_folder="../../templates")

def _set_if_attr(obj, field, value):
    if hasattr(obj, field):
        setattr(obj, field, value)

def _get_attr(obj, *candidates, default=None):
    for c in candidates:
        if hasattr(obj, c):
            return getattr(obj, c)
    return default

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

        # Optional email verification gate
        if hasattr(user, "is_verified") and not user.is_verified:
            flash("Please verify your email before logging in.", "warning")
            return render_template("auth/login.html")

        # Password check (supports password_hash OR password)
        ok = False
        if hasattr(user, "password_hash"):
            ok = check_password_hash(getattr(user, "password_hash"), password)
        elif hasattr(user, "password"):
            try:
                ok = check_password_hash(getattr(user, "password"), password)
            except Exception:
                ok = (getattr(user, "password") == password)

        if ok:
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
            flash("Please fill all fields.", "warning")
            return render_template("auth/register.html", name=name, email=email)

        # Build user with flexible fields to match your model
        user = User()
        _set_if_attr(user, "name", name)
        _set_if_attr(user, "full_name", name)
        _set_if_attr(user, "email", email)

        if hasattr(User, "password_hash"):
            user.password_hash = generate_password_hash(password)
        elif hasattr(User, "password"):
            # Prefer hashed even if column is named 'password'
            user.password = generate_password_hash(password)

        # Sensible defaults for common non-nullable fields
        for fld in ["free_credits", "silver_balance", "credits_free", "free_balance",
                    "gold_balance", "paid_credits", "pro_credits", "credit_balance", "credits"]:
            if hasattr(User, fld) and getattr(user, fld, None) is None:
                setattr(user, fld, 0)

        if hasattr(User, "subscription_status") and getattr(user, "subscription_status", None) is None:
            user.subscription_status = "inactive"

        if hasattr(User, "is_active") and getattr(user, "is_active", None) is None:
            user.is_active = True

        # If you don't have email OTP yet, mark verified to allow login
        if hasattr(User, "is_verified") and getattr(user, "is_verified", None) is None:
            user.is_verified = True

        try:
            db.session.add(user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Email is already registered.", "danger")
            return render_template("auth/register.html", name=name, email=email)
        except Exception as e:
            db.session.rollback()
            flash(f"Could not create account: {e}", "danger")
            return render_template("auth/register.html", name=name, email=email)

        flash("Account created. You can log in now.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")


@auth_bp.route("/verify", methods=["GET", "POST"])
def verify():
    # Minimal placeholder; adapt to your OTP system
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
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
