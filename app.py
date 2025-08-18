# app.py

import os
from datetime import datetime
from flask import Flask, render_template, request, send_from_directory, url_for
from flask_login import current_user
from dotenv import load_dotenv

from models import db, User, University
from limits import init_limits

# Blueprints
from modules.auth.routes import auth_bp, login_manager  # provides login_manager
from modules.billing.routes import billing_bp
# from modules.resume.routes import resume_bp  # (removed) resume lives under Settings (Pro-only)
from modules.portfolio.routes import portfolio_bp
from modules.internships.routes import internships_bp
from modules.referral.routes import referral_bp
from modules.jobpack.routes import jobpack_bp
from modules.skillmapper.routes import skillmapper_bp
from modules.settings.routes import settings_bp

load_dotenv()


# ---------------------------------------------------------------------
# Template helpers (available inside Jinja as simple functions)
# ---------------------------------------------------------------------
def free_coins():
    if getattr(current_user, "is_authenticated", False):
        return getattr(current_user, "coins_free", 0) or 0
    return 0


def pro_coins():
    if getattr(current_user, "is_authenticated", False):
        return getattr(current_user, "coins_pro", 0) or 0
    return 0


def is_pro():
    if getattr(current_user, "is_authenticated", False):
        status = (getattr(current_user, "subscription_status", "free") or "free").lower()
        return bool(getattr(current_user, "is_pro", False) or status == "pro")
    return False


def register_template_globals(app: Flask):
    app.jinja_env.globals.update(
        free_coins=free_coins,
        pro_coins=pro_coins,
        is_pro=is_pro,
    )


# ---------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------
def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # ----- Config
    secret = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET_KEY") or "dev-secret-key"
    app.config["SECRET_KEY"] = secret

    db_url = os.getenv("DATABASE_URL") or os.getenv("DEV_DATABASE_URI") or "sqlite:///career_ai.db"
    # Alembic/SQLAlchemy expect "postgresql://" not "postgres://"
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB uploads

    # Optional: dev nicety
    if os.getenv("FLASK_ENV") != "production":
        app.config["TEMPLATES_AUTO_RELOAD"] = True

    # Optional: prod security + proxy headers (Render/CDN)
    if os.getenv("FLASK_ENV") == "production" or os.getenv("ENV") == "production":
        app.config.update(
            SESSION_COOKIE_SECURE=True,
            SESSION_COOKIE_SAMESITE="Lax",
            REMEMBER_COOKIE_SECURE=True,
        )
        try:
            from werkzeug.middleware.proxy_fix import ProxyFix
            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
        except Exception:
            pass

    # ----- Extensions
    db.init_app(app)
    login_manager.init_app(app)
    init_limits(app)

    # ----- Blueprints
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(billing_bp, url_prefix="/billing")  # ensure consistent path
    # app.register_blueprint(resume_bp, url_prefix="/resume")   # (removed)
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(internships_bp, url_prefix="/internships")
    app.register_blueprint(referral_bp, url_prefix="/referral")
    app.register_blueprint(jobpack_bp, url_prefix="/jobpack")
    app.register_blueprint(skillmapper_bp, url_prefix="/skillmapper")
    app.register_blueprint(settings_bp, url_prefix="/settings")

    # ----- Make helpers available in templates
    register_template_globals(app)

    # ----- Routes
    @app.route("/", endpoint="landing")
    def landing():
        return render_template("landing.html")

    @app.route("/dashboard", endpoint="dashboard")
    def dashboard():
        return render_template("dashboard.html")

    # Favicon (prevents noisy 404s when you don't have a file yet)
    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(
            app.static_folder, "favicon.ico", mimetype="image/vnd.microsoft.icon"
        )

    # ----- Context data available to all templates
    @app.context_processor
    def inject_globals():
        tenant_name = None
        try:
            host = (request.host or "").split(":")[0]
            uni = University.query.filter(
                (University.domain == host) | (University.tenant_slug == host)
            ).first()
            tenant_name = uni.name if uni else None
        except Exception:
            tenant_name = None

        # Safe url_for to avoid crashes if an endpoint is missing
        def safe_url(endpoint, **kwargs):
            try:
                return url_for(endpoint, **kwargs)
            except Exception:
                return "#"

        # Centralized feature links used by base.html (e.g., feature_paths.resume)
        feature_paths = {
            "home":        safe_url("landing"),
            "dashboard":   safe_url("dashboard"),
            # Resume upload/profile is under Settings for Pro; fall back to settings.index
            "resume":      safe_url("settings.resume") if safe_url("settings.resume") != "#" else safe_url("settings.index"),
            "portfolio":   safe_url("portfolio.index"),
            "internships": safe_url("internships.index"),
            "referral":    safe_url("referral.index"),
            "jobpack":     safe_url("jobpack.index"),
            "skillmapper": safe_url("skillmapper.index"),
            "settings":    safe_url("settings.index"),
            "billing":     safe_url("billing.index"),
            "login":       safe_url("auth.login"),
            "logout":      safe_url("auth.logout"),
            "signup":      safe_url("auth.signup"),
        }

        return dict(
            now=datetime.utcnow(),
            tenant_name=tenant_name,
            user_free=free_coins(),
            user_pro=pro_coins(),
            subscription_status=(
                getattr(current_user, "subscription_status", "free")
                if getattr(current_user, "is_authenticated", False)
                else "free"
            ),
            feature_paths=feature_paths,
        )

    # ----- Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def srv_error(e):
        return render_template("errors/500.html"), 500

    # ----- Create tables only for local SQLite/dev; use Alembic in prod
    with app.app_context():
        is_sqlite = str(app.config["SQLALCHEMY_DATABASE_URI"]).startswith("sqlite")
        is_prod = os.getenv("FLASK_ENV") == "production" or os.getenv("ENV") == "production"
        if is_sqlite and not is_prod:
            db.create_all()

    return app


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------
app = create_app()
