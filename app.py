import os, logging, sys
from datetime import datetime
from flask import Flask, render_template, request, send_from_directory, url_for
from flask_login import current_user
from dotenv import load_dotenv
from logtail import LogtailHandler
from models import db, University
from limits import init_limits

# Blueprints
from modules.auth.routes import auth_bp, login_manager  # provides login_manager
from modules.billing.routes import billing_bp
from modules.portfolio.routes import portfolio_bp
from modules.internships.routes import internships_bp
from modules.referral.routes import referral_bp
from modules.jobpack.routes import jobpack_bp
from modules.skillmapper.routes import skillmapper_bp
from modules.settings.routes import settings_bp

# Alembic (for auto-migrate on startup)
from alembic import command
from alembic.config import Config

load_dotenv()


# ---------------------------------------------------------------------
# Jinja helpers (available inside templates)
# ---------------------------------------------------------------------
def free_coins():
    if getattr(current_user, "is_authenticated", False):
        try:
            return getattr(current_user, "coins_free", 0) or 0
        except Exception:
            try: db.session.rollback()
            except Exception: pass
            return 0
    return 0

def pro_coins():
    if getattr(current_user, "is_authenticated", False):
        try:
            return getattr(current_user, "coins_pro", 0) or 0
        except Exception:
            try: db.session.rollback()
            except Exception: pass
            return 0
    return 0

def is_pro():
    if getattr(current_user, "is_authenticated", False):
        try:
            status = (getattr(current_user, "subscription_status", "free") or "free").lower()
            return bool(getattr(current_user, "is_pro", False) or status == "pro")
        except Exception:
            try: db.session.rollback()
            except Exception: pass
            return False
    return False

def register_template_globals(app: Flask):
    app.jinja_env.globals.update(
        free_coins=free_coins,
        pro_coins=pro_coins,
        is_pro=is_pro,
    )


# ---------------------------------------------------------------------
# Auto-migration on startup (Render-friendly; no shell required)
# ---------------------------------------------------------------------
def run_auto_migrations(app: Flask) -> None:
    from sqlalchemy import create_engine, text, inspect

    if os.getenv("AUTO_MIGRATE", "1") != "1":
        return

    db_url = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if db_url.startswith("sqlite"):
        app.logger.info("AUTO_MIGRATE skipped (SQLite dev).")
        return

    cfg = Config(os.path.join(app.root_path, "alembic.ini"))
    url = db_url.replace("postgres://", "postgresql://", 1) if db_url.startswith("postgres://") else db_url
    cfg.set_main_option("sqlalchemy.url", url)

    def _upgrade():
        with app.app_context():
            command.upgrade(cfg, "head")

    # 1) Straight upgrade
    try:
        _upgrade()
        app.logger.info("Alembic migrations applied (upgrade head).")
        return
    except Exception as e1:
        msg1 = (str(e1) or "")
        app.logger.error(f"Alembic upgrade failed (1st try): {msg1}")

    # 2) Stale revision pointer
    if "Can't locate revision" in msg1 or "No such revision" in msg1:
        try:
            app.logger.warning("Dropping alembic_version to clear stale revision pointer...")
            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            with app.app_context():
                command.stamp(cfg, "base")
                app.logger.warning("Alembic stamped to base.")
                command.upgrade(cfg, "head")
                app.logger.info("Alembic migrations applied after stamp base.")
                return
        except Exception as e2:
            app.logger.error(f"Alembic upgrade failed after stamp base: {e2}")

    # 3) Tables already exist → stamp head, autogenerate diff, upgrade
    if "already exists" in msg1 or "DuplicateTable" in msg1 or "relation" in msg1:
        try:
            engine = create_engine(url)
            insp = inspect(engine)
            existing = set(insp.get_table_names(schema="public"))
            core_seen = {"university", "user"} & existing
            if core_seen:
                with app.app_context():
                    command.stamp(cfg, "head")
                    from datetime import datetime
                    autogen_msg = f"autosync_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                    command.revision(cfg, message=autogen_msg, autogenerate=True)
                    app.logger.warning("Alembic created an autogenerate 'autosync' migration (diff-only).")
                    command.upgrade(cfg, "head")
                    app.logger.info("Alembic migrations applied after autosync.")
                    return
        except Exception as e3:
            app.logger.error(f"Alembic autosync path failed: {e3}")

    # 4) Fallback: autogenerate then upgrade
    try:
        with app.app_context():
            from datetime import datetime
            autogen_msg = f"autosync_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            command.revision(cfg, message=autogen_msg, autogenerate=True)
            _upgrade()
            app.logger.info("Alembic migrations applied after autosync fallback.")
            return
    except Exception as e4:
        app.logger.error(f"Alembic autosync fallback failed: {e4}")


# ---------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------
def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    
    handlers = [logging.StreamHandler(sys.stdout)]
    token = os.getenv("LOGTAIL_TOKEN")
    if token:
        handlers.append(LogtailHandler(source_token=token))
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    app.logger.handlers = handlers
    app.logger.setLevel(logging.INFO)

    # ----- Config
    secret = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET_KEY") or "dev-secret-key"
    app.config["SECRET_KEY"] = secret

    db_url = os.getenv("DATABASE_URL") or os.getenv("DEV_DATABASE_URI") or "sqlite:///career_ai.db"
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB uploads

    if os.getenv("FLASK_ENV") != "production":
        app.config["TEMPLATES_AUTO_RELOAD"] = True

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
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(internships_bp, url_prefix="/internships")
    app.register_blueprint(referral_bp, url_prefix="/referral")
    app.register_blueprint(jobpack_bp, url_prefix="/jobpack")
    app.register_blueprint(skillmapper_bp, url_prefix="/skillmapper")
    app.register_blueprint(settings_bp, url_prefix="/settings")

    # ----- Helpers in templates
    register_template_globals(app)

    # ----- Routes
    @app.route("/", endpoint="landing")
    def landing():
        return render_template("landing.html")

    @app.route("/dashboard", endpoint="dashboard")
    def dashboard():
        return render_template("dashboard.html")

    # Favicon
    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(
            app.static_folder, "favicon.ico", mimetype="image/vnd.microsoft.icon"
        )

    # ----- Context for all templates
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

        def safe_url(endpoint, **kwargs):
            try:
                return url_for(endpoint, **kwargs)
            except Exception:
                return "#"

        resume_url = safe_url("settings.resume")
        if resume_url == "#":
            resume_url = safe_url("settings.index")

        feature_paths = {
            "home":        safe_url("landing"),
            "dashboard":   safe_url("dashboard"),
            "profile":     safe_url("settings.profile"),  # Profile Portal
            "resume":      resume_url,                    # Resume Hub (Pro action)
            "portfolio":   safe_url("portfolio.index"),
            "internships": safe_url("internships.index"),
            "referral":    safe_url("referral.index"),
            "jobpack":     safe_url("jobpack.index"),
            "skillmapper": safe_url("skillmapper.index"),
            "settings":    safe_url("settings.index"),
            "billing":     safe_url("billing.index"),
            "login":       safe_url("auth.login"),
            "logout":      safe_url("auth.logout"),
            "signup":      safe_url("auth.register"),
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
        try:
            # IMPORTANT: logger.exception already records the active exception;
            # don't pass the exception object as exc_info (it expects True or a tuple)
            app.logger.exception("Unhandled 500 error")
            db.session.rollback()
        except Exception:
            pass
        return render_template("errors/500.html"), 500

    @app.teardown_request
    def _teardown_request(exc):
        if exc:
            try: db.session.rollback()
            except Exception: pass

    # ----- Local SQLite only: create tables for quick start
    with app.app_context():
        is_sqlite = str(app.config["SQLALCHEMY_DATABASE_URI"]).startswith("sqlite")
        is_prod = os.getenv("FLASK_ENV") == "production" or os.getenv("ENV") == "production"
        if is_sqlite and not is_prod:
            db.create_all()

    # ----- Auto-run Alembic migrations on startup (Render/Postgres)
    run_auto_migrations(app)

    return app


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------
app = create_app()
