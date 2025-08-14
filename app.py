import os, json, re, io, csv, uuid, datetime as dt
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

# Local modules
from models import db, User, Subscription, JobPackReport, InternshipRecord, PortfolioPage, OutreachContact, AgentJob, University, UserUniversity
from modules.jobpack.routes import jobpack_bp
from modules.internships.routes import internships_bp
from modules.portfolio.routes import portfolio_bp
from modules.referral.routes import referral_bp
from modules.agent.routes import agent_bp
from modules.billing.routes import billing_bp

load_dotenv()

def create_app():
    app = Flask(__name__, template_folder='templates')
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")

    # Database: SQLite locally, Postgres on Render
    database_url = os.getenv("DATABASE_URL", "sqlite:///career_ai.db")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://")
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    with app.app_context():
        db.create_all()
    # Allow {{ os.getenv(...) }} in Jinja
    app.jinja_env.globals.update(os=os)

    # Login
    login_manager = LoginManager()
    login_manager.login_view = "auth_login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Blueprints
    app.register_blueprint(jobpack_bp, url_prefix="/jobpack")
    app.register_blueprint(internships_bp, url_prefix="/internships")
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(referral_bp, url_prefix="/referral")
    app.register_blueprint(agent_bp, url_prefix="/agent")
    app.register_blueprint(billing_bp, url_prefix="/billing")

    # ---------- Routes ----------
    @app.route("/")
    def landing():
        return render_template("landing.html")

    @app.route("/pricing")
    def pricing():
        return render_template("pricing.html")

    @app.route("/dashboard")
    @login_required
    def dashboard():
        subs = Subscription.query.filter_by(user_id=current_user.id).first()
        is_pro = subs and subs.status == "active"
        recent_reports = JobPackReport.query.filter_by(user_id=current_user.id).order_by(JobPackReport.created_at.desc()).limit(5).all()
        portfolios = PortfolioPage.query.filter_by(user_id=current_user.id).order_by(PortfolioPage.created_at.desc()).all()
        agent_jobs = AgentJob.query.filter_by(user_id=current_user.id).order_by(AgentJob.created_at.desc()).limit(3).all()
        return render_template("dashboard.html", is_pro=is_pro, recent_reports=recent_reports, portfolios=portfolios, agent_jobs=agent_jobs)

    # -------- Auth ----------
    @app.route("/auth/register", methods=["GET", "POST"])
    def auth_register():
        if request.method == "POST":
            email = request.form.get("email","").strip().lower()
            pw = request.form.get("password","")
            if not email or not pw:
                flash("Email and password required", "error")
                return redirect(url_for("auth_register"))
            if User.query.filter_by(email=email).first():
                flash("Email already registered", "error")
                return redirect(url_for("auth_register"))
            user = User(email=email, password_hash=generate_password_hash(pw), plan="free")
            db.session.add(user); db.session.commit()
            login_user(user)
            flash("Welcome! Account created.", "success")
            return redirect(url_for("dashboard"))
        return render_template("auth_register.html")

    @app.route("/auth/login", methods=["GET", "POST"])
    def auth_login():
        if request.method == "POST":
            email = request.form.get("email","").strip().lower()
            pw = request.form.get("password","")
            user = User.query.filter_by(email=email).first()
            if user and check_password_hash(user.password_hash, pw):
                login_user(user)
                flash("Logged in.", "success"); return redirect(url_for("dashboard"))
            flash("Invalid credentials.", "error")
        return render_template("auth_login.html")

    @app.route("/auth/logout")
    @login_required
    def auth_logout():
        logout_user(); flash("Logged out.", "info")
        return redirect(url_for("landing"))

    @app.route("/library")
    @login_required
    def library():
        reports = JobPackReport.query.filter_by(user_id=current_user.id).order_by(JobPackReport.created_at.desc()).all()
        return render_template("library.html", reports=reports)

    @app.route("/privacy")
    def privacy():
        return "We store the minimum data required. No LinkedIn scraping. Free tier does not persist resumes unless opted-in. Delete my data: email support."

    return app

app = create_app()

# Local dev convenience
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
