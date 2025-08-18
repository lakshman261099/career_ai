from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required
from models import db, User

auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")
login_manager = LoginManager()
login_manager.login_view = "auth.login"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Make User compatible with Flask-Login (simple properties)
User.is_authenticated = property(lambda self: True)
User.is_active = property(lambda self: True)
User.is_anonymous = property(lambda self: False)
User.get_id = lambda self: str(self.id)

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        pw = request.form.get("password","")
        if not (name and email and pw):
            flash("All fields are required.", "error")
            return render_template("auth/register.html")
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return render_template("auth/register.html")
        u = User(name=name, email=email)
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()
        login_user(u)
        flash("Welcome! Account created.", "success")
        return redirect(url_for("dashboard"))
    return render_template("auth/register.html")

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        pw = request.form.get("password","")
        u = User.query.filter_by(email=email).first()
        if not u or not u.check_password(pw):
            flash("Invalid credentials.", "error")
            return render_template("auth/login.html")
        login_user(u)
        flash("Logged in.", "success")
        return redirect(url_for("dashboard"))
    return render_template("auth/login.html")

@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("auth.login"))
