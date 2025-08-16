# modules/auth/routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User

auth_bp = Blueprint("auth", __name__)

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
    login_user(user)
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
    login_user(user)
    return redirect(url_for("dashboard"))

@auth_bp.get("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out", "success")
    return redirect(url_for("auth.login"))
