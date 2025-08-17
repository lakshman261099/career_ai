from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
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

        # Enforce email verification if your model has it
        if hasattr(user, "is_verified") and not user.is_verified:
            flash("Please verify your email before logging in.", "warning")
            return render_template("auth/login.html")

        if check_password_hash(user.password_hash, password):
            login_user(user, remember=True, force=False, fresh=True)
            return redirect(url_for("post_login"))

        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("home"))


# --- Optional: simple verification flow placeholder ---
# If you already have a verify route, keep your logic and just ensure it renders "auth/verify.html".
@auth_bp.route("/verify", methods=["GET", "POST"])
def verify():
    """
    Example minimal verify page:
    - If GET with ?email=...&code=..., look up and verify.
    - If POST, accept email+code form submission.
    Adjust to your existing token/OTP mechanism.
    """
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        code = (request.form.get("code") or "").strip()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash("Account not found.", "danger")
            return render_template("auth/verify.html")

        # TODO: replace with your real code/token check
        # For now, accept any non-empty code as a demo
        if not code:
            flash("Enter the verification code.", "warning")
            return render_template("auth/verify.html")

        if hasattr(user, "is_verified"):
            user.is_verified = True
            db.session.commit()
        flash("Your email has been verified. You can log in now.", "success")
        return redirect(url_for("auth.login"))

    # GET
    email = (request.args.get("email") or "").strip().lower()
    code = (request.args.get("code") or "").strip()
    if email and code:
        user = User.query.filter_by(email=email).first()
        if user:
            if hasattr(user, "is_verified"):
                user.is_verified = True
                db.session.commit()
            flash("Your email has been verified. You can log in now.", "success")
            return redirect(url_for("auth.login"))

    return render_template("auth/verify.html")
