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


# ---------------------------
# Flask-Login user loader
# ---------------------------
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

    if not host or not user or not password:
        # In dev, just log to console to avoid hard failures
        print(f"[DEV EMAIL] To: {to_email}\nSubject: {subject}\n\n{body}")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email

    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(sender, [to_email], msg.as_string())


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# ---------------------------
# Password-based Register/Login (kept for compatibility)
# ---------------------------
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = _normalize_email(request.form.get("email", ""))
        pw = request.form.get("password", "")

        if not (name and email and pw):
            flash("All fields are required.", "error")
            return render_template("auth/register.html")

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return render_template("auth/register.html")

        u = User(name=name, email=email, verified=False)
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()

        login_user(u)
        flash("Welcome! Account created.", "success")
        return redirect(url_for("dashboard"))

    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    Password login (legacy). The page should also link to OTP flow.
    """
    if request.method == "POST":
        email = _normalize_email(request.form.get("email", ""))
        pw = request.form.get("password", "")

        u = User.query.filter_by(email=email).first()
        if not u or not u.check_password(pw):
            flash("Invalid credentials.", "error")
            return render_template("auth/login.html")

        login_user(u)
        flash("Logged in.", "success")
        return redirect(url_for("dashboard"))

    return render_template("auth/login.html")


# ---------------------------
# OTP-based Login (KB default)
# ---------------------------

# Session keys (no DB schema change needed)
OTP_SESSION_KEY = "otp_login"  # will store dict: {email, code_hash?, expires_at, csrf}
OTP_TTL_MINUTES = 10


def _generate_otp_code(length=6) -> str:
    digits = string.digits
    return "".join(random.choice(digits) for _ in range(length))


@auth_bp.route("/otp/request", methods=["GET", "POST"], endpoint="otp_request")
def otp_request():
    """
    Step 1: Ask for email; send OTP.
    """
    if request.method == "POST":
        email = _normalize_email(request.form.get("email", ""))
        if not email:
            flash("Please enter your email.", "error")
            return render_template("auth/otp_request.html")

        code = _generate_otp_code(6)
        expires_at = datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES)
        csrf = gen_salt(16)

        session[OTP_SESSION_KEY] = {
            "email": email,
            "code": code,         # stored plain in session (server-side), not in DB
            "expires_at": expires_at.isoformat(),
            "csrf": csrf,
        }
        session.modified = True

        _send_email(
            to_email=email,
            subject="Your Login Code",
            body=f"Your OTP is: {code}\n\nThis code expires in {OTP_TTL_MINUTES} minutes.\nIf you did not request this, you can ignore this email.",
        )

        flash("We’ve sent a 6-digit code to your email.", "info")
        return redirect(url_for("auth.otp_verify", csrf=csrf))

    return render_template("auth/otp_request.html")


@auth_bp.route("/otp/verify", methods=["GET", "POST"], endpoint="otp_verify")
def otp_verify():
    """
    Step 2: Verify code; create user if not exists; mark verified; log in.
    """
    data = session.get(OTP_SESSION_KEY) or {}
    csrf_q = request.args.get("csrf") or request.form.get("csrf")
    if not data or not csrf_q or data.get("csrf") != csrf_q:
        flash("OTP session expired. Please request a new code.", "error")
        return redirect(url_for("auth.otp_request"))

    if request.method == "POST":
        input_code = (request.form.get("code", "") or "").strip()
        stored_code = data.get("code", "")
        expires_at = datetime.fromisoformat(data.get("expires_at"))

        if datetime.utcnow() > expires_at:
            session.pop(OTP_SESSION_KEY, None)
            flash("Code expired. Please request a new one.", "error")
            return redirect(url_for("auth.otp_request"))

        if input_code != stored_code:
            flash("Invalid code. Please try again.", "error")
            return render_template("auth/otp_verify.html", csrf=csrf_q)

        # Success: get or create user
        email = data.get("email")
        user = User.query.filter_by(email=email).first()
        if not user:
            # Minimal profile; user can complete name later in Settings
            user = User(name=email.split("@")[0].title(), email=email, verified=True)
            # set a random password so password login remains disabled unless they set one
            user.set_password(gen_salt(24))
            db.session.add(user)
        else:
            user.verified = True

        db.session.commit()

        # Log in
        login_user(user)

        # Cleanup
        session.pop(OTP_SESSION_KEY, None)

        flash("Logged in with OTP. Welcome!", "success")
        return redirect(url_for("dashboard"))

    # GET -> show form
    return render_template("auth/otp_verify.html", csrf=csrf_q)


# ---------------------------
# Logout
# ---------------------------
@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("auth.login"))
