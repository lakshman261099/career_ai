# modules/auth/routes.py

import os
import smtplib
import string
import random
from email.mime.text import MIMEText
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import LoginManager, login_user, logout_user, login_required
from werkzeug.security import gen_salt

from models import db, User

auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")
login_manager = LoginManager()
login_manager.login_view = "auth.login"


@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None


# ---------------------------
# Email (SMTP) helper
# ---------------------------
def _send_email(to_email: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", user or "no-reply@example.com")

    # Dev fallback: log to console if SMTP not configured
    if not host or not user or not password:
        print(f"[DEV EMAIL] To: {to_email}\nSubject: {subject}\n\n{body}")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email

    with smtplib.SMTP(host, port) as smtp:
        try:
            smtp.starttls()
        except Exception:
            pass
        if user and password:
            smtp.login(user, password)
        smtp.sendmail(sender, [to_email], msg.as_string())


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# ---------------------------
# Password-based Register/Login
# ---------------------------
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = _normalize_email(request.form.get("email"))
        pw = request.form.get("password") or ""

        if not (name and email and pw):
            flash("All fields are required.", "error")
            return render_template("auth/register.html")

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return render_template("auth/register.html")

        u = User(name=name, email=email, verified=True)  # mark verified on password signup
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()

        login_user(u)
        flash("Account created. Welcome!", "success")
        return redirect(url_for("dashboard"))
    return render_template("auth/register.html")


# alias /signup -> /register (so links like feature_paths.signup work)
@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    return register()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Simple email+password login; OTP link remains optional."""
    if request.method == "POST":
        email = _normalize_email(request.form.get("email"))
        pw = request.form.get("password") or ""

        u = User.query.filter_by(email=email).first()
        if not u or not u.check_password(pw):
            flash("Invalid credentials.", "error")
            return render_template("auth/login.html")

        login_user(u)
        flash("Logged in.", "success")
        return redirect(url_for("dashboard"))
    return render_template("auth/login.html")


# ---------------------------
# OTP-based Login (optional)
# ---------------------------

OTP_SESSION_KEY = "otp_login"
OTP_TTL_MINUTES = 10


def _generate_otp_code(length=6) -> str:
    return "".join(random.choice(string.digits) for _ in range(length))


@auth_bp.route("/otp/request", methods=["GET", "POST"], endpoint="otp_request")
def otp_request():
    if request.method == "POST":
        email = _normalize_email(request.form.get("email"))
        if not email:
            flash("Please enter your email.", "error")
            return render_template("auth/otp_request.html")

        code = _generate_otp_code(6)
        expires_at = datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES)

        # store in session only (no csrf arg needed)
        session[OTP_SESSION_KEY] = {
            "email": email,
            "code": code,
            "expires_at": expires_at.isoformat(),
        }
        session.modified = True

        _send_email(
            to_email=email,
            subject="Your Login Code",
            body=f"Your OTP is: {code}\n\nThis code expires in {OTP_TTL_MINUTES} minutes.",
        )

        flash("We’ve sent a 6-digit code to your email.", "info")
        return redirect(url_for("auth.otp_verify"))

    return render_template("auth/otp_request.html")


@auth_bp.route("/otp/verify", methods=["GET", "POST"], endpoint="otp_verify")
def otp_verify():
    data = session.get(OTP_SESSION_KEY) or {}
    if not data:
        flash("OTP session not found. Request a new code.", "error")
        return redirect(url_for("auth.otp_request"))

    if request.method == "POST":
        user_code = (request.form.get("code") or "").strip()
        stored_code = data.get("code", "")
        expires_at = datetime.fromisoformat(data.get("expires_at"))

        if datetime.utcnow() > expires_at:
            session.pop(OTP_SESSION_KEY, None)
            flash("Code expired. Please request a new one.", "error")
            return redirect(url_for("auth.otp_request"))

        if not (len(user_code) == 6 and user_code.isdigit()):
            flash("Please enter a 6-digit numeric code.", "error")
            return render_template("auth/otp_verify.html")

        if user_code != stored_code:
            flash("Invalid code. Please try again.", "error")
            return render_template("auth/otp_verify.html")

        email = data.get("email")
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(name=email.split("@")[0].title(), email=email, verified=True)
            user.set_password(gen_salt(24))  # random; user can set later
            db.session.add(user)
        else:
            user.verified = True

        db.session.commit()
        login_user(user)
        session.pop(OTP_SESSION_KEY, None)

        flash("Logged in with OTP. Welcome!", "success")
        return redirect(url_for("dashboard"))

    return render_template("auth/otp_verify.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("auth.login"))
