# modules/auth/routes.py
import os, hashlib, hmac, random, smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, PasswordReset

auth_bp = Blueprint("auth", __name__)

# ---------- Helpers ----------
def _send_email(to_email: str, subject: str, body: str) -> bool:
    host = os.getenv("SMTP_HOST"); port = os.getenv("SMTP_PORT")
    user = os.getenv("SMTP_USER"); pwd = os.getenv("SMTP_PASS")
    sender = os.getenv("SMTP_FROM", user or "no-reply@careerai.local")
    if not host or not port:
        # Demo fallback: log in server output
        current_app.logger.info(f"[DEMO EMAIL] To={to_email}\nSubject={subject}\n\n{body}")
        flash("Demo: OTP sent (check server logs).", "success")
        return True
    try:
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(host, int(port)) as s:
            if os.getenv("SMTP_TLS","1") == "1": s.starttls()
            if user and pwd: s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception as e:
        current_app.logger.exception("Email send failed")
        flash(f"Email failed: {e}", "error")
        return False

def _hash_code(code: str) -> str:
    secret = os.getenv("FLASK_SECRET_KEY", "change-me")
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()

# ---------- Routes ----------
@auth_bp.get("/login")
def login():
    return render_template("auth_login.html")

@auth_bp.post("/login")
def do_login():
    email = (request.form.get("email") or "").strip().lower()
    pwd = request.form.get("password") or ""
    user = User.query.filter_by(email=email).first()
    if not user or not user.password_hash or not check_password_hash(user.password_hash, pwd):
        flash("Invalid credentials", "error"); return redirect(url_for("auth.login"))
    login_user(user, remember=True)
    return redirect(url_for("dashboard"))

@auth_bp.get("/register")
def register():
    return render_template("auth_register.html")

@auth_bp.post("/register")
def do_register():
    email = (request.form.get("email") or "").strip().lower()
    pwd = request.form.get("password") or ""
    if not email or not pwd:
        flash("Email and password required", "error"); return redirect(url_for("auth.register"))
    if User.query.filter_by(email=email).first():
        flash("Email already registered", "error"); return redirect(url_for("auth.register"))
    user = User(email=email, password_hash=generate_password_hash(pwd))
    db.session.add(user); db.session.commit()
    login_user(user, remember=True)
    return redirect(url_for("dashboard"))

@auth_bp.get("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out", "success")
    return redirect(url_for("auth.login"))

# Forgot password (OTP)
@auth_bp.get("/forgot")
def forgot():
    return render_template("auth_forgot.html")

@auth_bp.post("/forgot")
def forgot_post():
    email = (request.form.get("email") or "").strip().lower()
    user = User.query.filter_by(email=email).first()
    if not user:
        flash("If the email exists, we'll send a code.", "success")
        return redirect(url_for("auth.forgot"))
    code = f"{random.randint(100000, 999999)}"
    pr = PasswordReset(
        user_id=user.id,
        code_hash=_hash_code(code),
        expires_at=datetime.utcnow() + timedelta(minutes=15)
    )
    db.session.add(pr); db.session.commit()
    _send_email(email, "Your CareerAI reset code", f"Use this code to reset your password: {code}\nIt expires in 15 minutes.")
    flash("If the email exists, we sent a 6-digit code.", "success")
    return redirect(url_for("auth.reset", email=email))

@auth_bp.get("/reset")
def reset():
    email = (request.args.get("email") or "").strip().lower()
    return render_template("auth_reset.html", email=email)

@auth_bp.post("/reset")
def reset_post():
    email = (request.form.get("email") or "").strip().lower()
    code = (request.form.get("code") or "").strip()
    newp = request.form.get("password") or ""
    user = User.query.filter_by(email=email).first()
    if not user or not code or not newp:
        flash("Invalid request", "error"); return redirect(url_for("auth.reset", email=email))
    pr = PasswordReset.query.filter_by(user_id=user.id, used_at=None).order_by(PasswordReset.created_at.desc()).first()
    if not pr or pr.expires_at < datetime.utcnow() or pr.code_hash != _hash_code(code):
        flash("Invalid or expired code", "error"); return redirect(url_for("auth.reset", email=email))
    pr.used_at = datetime.utcnow()
    user.password_hash = generate_password_hash(newp)
    db.session.add(user); db.session.add(pr); db.session.commit()
    flash("Password updated. Please sign in.", "success")
    return redirect(url_for("auth.login"))
