import logging
import os
import sys
from datetime import datetime

# Alembic
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from flask import Flask, render_template, request, send_from_directory, url_for, g
from flask_login import current_user, login_required
from logtail import LogtailHandler

from limits import init_limits
from models import University, db

# Blueprints
from modules.auth.routes import auth_bp, login_manager
from modules.auth.oauth import init_oauth  # NEW: Google OAuth init
from modules.billing.routes import billing_bp
from modules.internships.routes import internships_bp
from modules.jobpack.routes import jobpack_bp
from modules.portfolio.routes import portfolio_bp
from modules.referral.routes import referral_bp
from modules.settings.routes import settings_bp
from modules.skillmapper import bp as skillmapper_bp
from modules.admin.routes import admin_bp
from modules.dream.routes import dream_bp
from modules.coach.routes import coach_bp

# 🔹 Central credits config (single source of truth)
from modules.credits.config import FEATURE_COSTS, STARTING_BALANCES, SHOP_PACKAGES

load_dotenv()


# -------------------- Jinja helpers --------------------
def free_coins():
    if getattr(current_user, "is_authenticated", False):
        try:
            return getattr(current_user, "coins_free", 0) or 0
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            return 0
    return 0


def pro_coins():
    if getattr(current_user, "is_authenticated", False):
        try:
            return getattr(current_user, "coins_pro", 0) or 0
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            return 0
    return 0


def is_pro():
    if getattr(current_user, "is_authenticated", False):
        try:
            status = (
                getattr(current_user, "subscription_status", "free") or "free"
            ).lower()
            return bool(getattr(current_user, "is_pro", False) or status == "pro")
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            return False
    return False


def register_template_globals(app: Flask):
    # Keep named for legacy templates; we still inject the values via context_processor below
    app.jinja_env.globals.update(
        free_coins=free_coins,
        pro_coins=pro_coins,
        is_pro=is_pro,
    )


# -------------------- Auto Alembic ---------------------
def run_auto_migrations(app: Flask) -> None:
    from sqlalchemy import create_engine, inspect, text

    if os.getenv("AUTO_MIGRATE", "1") != "1":
        return

    db_url = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if db_url.startswith("sqlite"):
        app.logger.info("AUTO_MIGRATE skipped (SQLite dev).")
        return

    cfg = Config(os.path.join(app.root_path, "alembic.ini"))
    url = (
        db_url.replace("postgres://", "postgresql://", 1)
        if db_url.startswith("postgres://")
        else db_url
    )
    cfg.set_main_option("sqlalchemy.url", url)

    def _upgrade():
        with app.app_context():
            command.upgrade(cfg, "head")

    try:
        _upgrade()
        app.logger.info("Alembic migrations applied (upgrade head).")
        return
    except Exception as e1:
        msg1 = str(e1) or ""
        app.logger.error(f"Alembic upgrade failed (1st try): {msg1}")

    if "Can't locate revision" in msg1 or "No such revision" in msg1:
        try:
            app.logger.warning(
                "Dropping alembic_version to clear stale revision pointer..."
            )
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

    if "already exists" in msg1 or "DuplicateTable" in msg1 or "relation" in msg1:
        try:
            engine = create_engine(url)
            insp = inspect(engine)
            existing = set(insp.get_table_names(schema="public"))
            core_seen = {"university", "user"} & existing
            if core_seen:
                with app.app_context():
                    command.stamp(cfg, "head")
                    from datetime import datetime as _dt

                    autogen_msg = f"autosync_{_dt.utcnow().strftime('%Y%m%d%H%M%S')}"
                    command.revision(cfg, message=autogen_msg, autogenerate=True)
                    app.logger.warning(
                        "Alembic created an autogenerate 'autosync' migration (diff-only)."
                    )
                    command.upgrade(cfg, "head")
                    app.logger.info("Alembic migrations applied after autosync.")
                    return
        except Exception as e3:
            app.logger.error(f"Alembic autosync path failed: {e3}")

    try:
        with app.app_context():
            from datetime import datetime as _dt

            autogen_msg = f"autosync_{_dt.utcnow().strftime('%Y%m%d%H%M%S')}"
            command.revision(cfg, message=autogen_msg, autogenerate=True)
            _upgrade()
            app.logger.info("Alembic migrations applied after autosync fallback.")
            return
    except Exception as e4:
        app.logger.error(f"Alembic autosync fallback failed: {e4}")


# -------------------- App factory ----------------------
def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Logging
    handlers = [logging.StreamHandler(sys.stdout)]
    token = os.getenv("LOGTAIL_TOKEN")
    if token:
        handlers.append(LogtailHandler(source_token=token))
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    app.logger.handlers = handlers
    app.logger.setLevel(logging.INFO)

    # Core config
    secret = (
        os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET_KEY") or "dev-secret-key"
    )
    app.config["SECRET_KEY"] = secret

    db_url = (
        os.getenv("DATABASE_URL")
        or os.getenv("DEV_DATABASE_URI")
        or "sqlite:///career_ai.db"
    )
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

            app.wsgi_app = ProxyFix(
                app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1
            )
        except Exception:
            pass

    # 🔹 Central credits + shop config
    app.config["FEATURE_COSTS"] = FEATURE_COSTS
    app.config["STARTING_BALANCES"] = STARTING_BALANCES
    app.config["SHOP_PACKAGES"] = SHOP_PACKAGES

    # Extensions
    db.init_app(app)
    login_manager.init_app(app)
    init_limits(app)
    init_oauth(app)  # NEW: Google OAuth

    # -------------------- Tenant resolution (IMPORTANT) --------------------
    @app.before_request
    def load_current_tenant():
        """
        Resolve g.current_tenant based on host/subdomain.

        Supports:
        - Exact domain match: university.domain == request.host (e.g. veltech.edu)
        - Exact slug match:   university.tenant_slug == request.host
        - Pattern: careerai.<slug>.<tld>  → match tenant_slug == <slug>
                  or domain == "<slug>.<tld>"
        """
        g.current_tenant = None
        host = (request.host or "").split(":")[0]

        if not host:
            return

        uni = University.query.filter(
            (University.domain == host) | (University.tenant_slug == host)
        ).first()

        if not uni and host.count(".") >= 2:
            parts = host.split(".")
            # e.g. "careerai.veltech.ai" -> ["careerai","veltech","ai"]
            mid = parts[-2]   # "veltech"
            tld = parts[-1]   # "ai"

            # Try tenant_slug == mid (veltech)
            uni = University.query.filter(University.tenant_slug == mid).first()
            if not uni:
                # Try domain == "veltech.ai"
                candidate_domain = f"{mid}.{tld}"
                uni = University.query.filter(University.domain == candidate_domain).first()

        g.current_tenant = uni

    # Blueprints
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(internships_bp, url_prefix="/internships")
    app.register_blueprint(referral_bp, url_prefix="/referral")
    app.register_blueprint(jobpack_bp, url_prefix="/jobpack")
    app.register_blueprint(skillmapper_bp)
    app.register_blueprint(settings_bp, url_prefix="/settings")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(dream_bp, url_prefix="/dream")
    app.register_blueprint(coach_bp, url_prefix="/coach")

    # Expose helper callables (legacy support)
    register_template_globals(app)

    # Routes
    @app.route("/", endpoint="landing")
    def landing():
        return render_template("landing.html")

    @app.route("/dashboard", endpoint="dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html")

    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(
            app.static_folder, "favicon.ico", mimetype="image/vnd.microsoft.icon"
        )

    # -------------------- Context for all templates --------------------
    @app.context_processor
    def inject_globals():
        """
        Safe globals for all templates.
        Uses g.current_tenant set in load_current_tenant().
        """
        tenant = getattr(g, "current_tenant", None)
        tenant_name = tenant.name if tenant else None

        def safe_url(endpoint, **kwargs):
            try:
                return url_for(endpoint, **kwargs)
            except Exception:
                return "#"

        # Decide what "home" should be:
        # - If logged in → dashboard
        # - If logged out → landing
        if getattr(current_user, "is_authenticated", False):
            home_url = safe_url("dashboard")
        else:
            home_url = safe_url("landing")

        # Single portal for both resume scan & manual edit
        portal_url = safe_url("settings.profile")

        feature_paths = {
            "home": home_url,
            "dashboard": safe_url("dashboard"),
            "profile": portal_url,
            "resume": portal_url,  # back-compat
            "portfolio": safe_url("portfolio.index"),
            "internships": safe_url("internships.index"),
            "referral": safe_url("referral.index"),
            "jobpack": safe_url("jobpack.index"),
            "skillmapper": safe_url("skillmapper.index"),
            "dream": safe_url("dream.index"),
            "coach": safe_url("coach.index"),
            "settings": safe_url("settings.index"),
            "billing": safe_url("billing.shop"),
            "login": safe_url("auth.login"),
            "logout": safe_url("auth.logout"),
            "signup": safe_url("auth.register"),
        }

        # Admin flag for navbar: role-based OR ADMIN_EMAILS override
        nav_is_admin = False
        if getattr(current_user, "is_authenticated", False):
            # Role-based
            if getattr(current_user, "is_admin", False) or getattr(
                current_user, "is_super_admin", False
            ):
                nav_is_admin = True
            else:
                # Env override
                emails = os.getenv("ADMIN_EMAILS", "")
                if emails:
                    allowed = {e.strip().lower() for e in emails.split(",") if e.strip()}
                    if (current_user.email or "").lower() in allowed:
                        nav_is_admin = True

        return dict(
            now=datetime.utcnow(),  # used in footer
            tenant=tenant,
            tenant_name=tenant_name,
            user_free=free_coins(),
            user_pro=pro_coins(),
            subscription_status=(
                getattr(current_user, "subscription_status", "free")
                if getattr(current_user, "is_authenticated", False)
                else "free"
            ),
            feature_paths=feature_paths,
            nav_is_admin=nav_is_admin,
        )

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def srv_error(e):
        
        try:
            app.logger.exception("Unhandled 500 error")
            db.session.rollback()
        except Exception:
            pass
        return render_template("errors/500.html"), 500

    @app.teardown_request
    def _teardown_request(exc):
        if exc:
            try:
                db.session.rollback()
            except Exception:
                pass

    # Dev sqlite quickstart
    with app.app_context():
        is_sqlite = str(app.config["SQLALCHEMY_DATABASE_URI"]).startswith("sqlite")
        is_prod = (
            os.getenv("FLASK_ENV") == "production" or os.getenv("ENV") == "production"
        )
        if is_sqlite and not is_prod:
            db.create_all()

    run_auto_migrations(app)
    return app


app = create_app()
