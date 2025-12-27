import logging
import os
import sys
from datetime import datetime

# Alembic
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from flask import Flask, render_template, request, send_from_directory, url_for, g, redirect
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
            status = (getattr(current_user, "subscription_status", "free") or "free").lower()
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


# -------------------- Admin role helpers (robust + env overrides) --------------------
def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _email_in_env_list(env_key: str, email: str) -> bool:
    raw = os.getenv(env_key, "") or ""
    if not raw:
        return False
    allowed = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return _normalize_email(email) in allowed


def _is_ultra_admin_user(u) -> bool:
    if not getattr(u, "is_authenticated", False):
        return False
    email = _normalize_email(getattr(u, "email", "") or "")
    if _email_in_env_list("ULTRA_ADMIN_EMAILS", email):
        return True
    role = (getattr(u, "role", "") or "").lower()
    if role == "ultra_admin":
        return True
    return bool(getattr(u, "is_ultra_admin", False))


def _is_super_admin_user(u) -> bool:
    if not getattr(u, "is_authenticated", False):
        return False
    email = _normalize_email(getattr(u, "email", "") or "")
    if _email_in_env_list("ADMIN_EMAILS", email):
        return True
    role = (getattr(u, "role", "") or "").lower()
    if role == "super_admin":
        return True
    return bool(getattr(u, "is_super_admin", False))


def _is_global_admin_user(u) -> bool:
    return _is_ultra_admin_user(u) or _is_super_admin_user(u)


def _is_university_admin_user(u) -> bool:
    if not getattr(u, "is_authenticated", False):
        return False
    role = (getattr(u, "role", "") or "").lower()
    if role == "university_admin":
        return True
    return bool(getattr(u, "is_university_admin", False))


def _is_any_admin_user(u) -> bool:
    if not getattr(u, "is_authenticated", False):
        return False
    if _is_global_admin_user(u) or _is_university_admin_user(u):
        return True
    return bool(getattr(u, "is_admin", False))


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
    url = db_url.replace("postgres://", "postgresql://", 1) if db_url.startswith("postgres://") else db_url
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

    if "already exists" in msg1 or "DuplicateTable" in msg1 or "relation" in msg1:
        try:
            engine = create_engine(url)
            insp = inspect(engine)
            try:
                existing = set(insp.get_table_names(schema="public"))
            except Exception:
                existing = set(insp.get_table_names())

            core_seen = {"university", "user"} & existing
            if core_seen:
                with app.app_context():
                    command.stamp(cfg, "head")
                    from datetime import datetime as _dt

                    autogen_msg = f"autosync_{_dt.utcnow().strftime('%Y%m%d%H%M%S')}"
                    command.revision(cfg, message=autogen_msg, autogenerate=True)
                    app.logger.warning("Alembic created an autogenerate 'autosync' migration (diff-only).")
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

        try:
            uni = University.query.filter(
                (University.domain == host) | (University.tenant_slug == host)
            ).first()

            if not uni and host.count(".") >= 2:
                parts = host.split(".")
                # e.g. "careerai.veltech.ai" -> ["careerai","veltech","ai"]
                mid = parts[-2]  # "veltech"
                tld = parts[-1]  # "ai"

                uni = University.query.filter(University.tenant_slug == mid).first()
                if not uni:
                    candidate_domain = f"{mid}.{tld}"
                    uni = University.query.filter(University.domain == candidate_domain).first()

            g.current_tenant = uni
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            g.current_tenant = None

    # -------------------- HARD GUARD: Admins must not see student routes --------------------
    @app.before_request
    def enforce_admin_ui_isolation():
        """
        Server-side isolation:
        - university_admin users should only use /admin/* + /auth/* + /settings/* + static.
          Any other route -> redirect to admin.strategy (Dean Dashboard).
        - super/ultra admins should only use /admin/* + /auth/* + /settings/* + static.
          Any other route -> redirect to admin.dashboard.
        """
        try:
            if not getattr(current_user, "is_authenticated", False):
                return None

            ep = request.endpoint  # can be None on 404
            if not ep:
                return None

            # Always allow these
            if ep == "static" or ep.startswith("static."):
                return None
            if ep.startswith("auth."):
                return None
            if ep.startswith("admin."):
                return None
            if ep.startswith("settings."):
                return None
            if ep in ("favicon", "landing"):
                return None

            # Block everything else for admins
            if _is_any_admin_user(current_user):
                if _is_university_admin_user(current_user) and not _is_global_admin_user(current_user):
                    return redirect(url_for("admin.strategy"))
                return redirect(url_for("admin.dashboard"))

            return None
        except Exception:
            # never block the app if guard fails
            try:
                db.session.rollback()
            except Exception:
                pass
            return None

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

    # -------------------- Role-aware home routing --------------------
    def _admin_home_endpoint():
        """
        Preferred landing page for admins.
        - university_admin → dean dashboard
        - super/ultra_admin → admin dashboard
        """
        role = (getattr(current_user, "role", "") or "").lower()
        if role == "university_admin":
            return "admin.strategy"
        return "admin.dashboard"

    def _safe_url(endpoint, **kwargs):
        try:
            return url_for(endpoint, **kwargs)
        except Exception:
            return "#"

    # Routes
    @app.route("/", endpoint="landing")
    def landing():
        # If logged in, send to correct UI
        if getattr(current_user, "is_authenticated", False):
            if _is_any_admin_user(current_user):
                return redirect(_safe_url(_admin_home_endpoint()))
            return redirect(_safe_url("dashboard"))
        return render_template("landing.html")

    @app.route("/dashboard", endpoint="dashboard")
    @login_required
    def dashboard():
        # Admins should not see student dashboard
        if _is_any_admin_user(current_user):
            return redirect(_safe_url(_admin_home_endpoint()))
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

        # Fallback: if host-based tenant is missing but user is university-scoped, attach their university
        if tenant is None and getattr(current_user, "is_authenticated", False):
            uni_id = getattr(current_user, "university_id", None)
            if uni_id:
                try:
                    tenant = University.query.get(int(uni_id))
                except Exception:
                    try:
                        db.session.rollback()
                    except Exception:
                        pass
                    tenant = None

        tenant_name = tenant.name if tenant else None

        # Decide nav mode
        is_authed = bool(getattr(current_user, "is_authenticated", False))
        is_university_admin = is_authed and _is_university_admin_user(current_user) and not _is_global_admin_user(current_user)
        is_super_admin = is_authed and _is_global_admin_user(current_user)
        is_admin = is_authed and _is_any_admin_user(current_user)

        nav_mode = "admin" if is_admin else "student"
        nav_show_student_features = not is_admin

        # Decide what "home" should be
        if is_authed:
            if is_admin:
                home_url = _safe_url(_admin_home_endpoint())
            else:
                home_url = _safe_url("dashboard")
        else:
            home_url = _safe_url("landing")

        # Single portal for both resume scan & manual edit
        portal_url = _safe_url("settings.profile")

        # Student feature paths (normal)
        student_feature_paths = {
            "home": home_url,
            "dashboard": _safe_url("dashboard"),
            "profile": portal_url,
            "resume": portal_url,
            "portfolio": _safe_url("portfolio.index"),
            "internships": _safe_url("internships.index"),
            "referral": _safe_url("referral.index"),
            "jobpack": _safe_url("jobpack.index"),
            "skillmapper": _safe_url("skillmapper.index"),
            "dream": _safe_url("dream.index"),
            "coach": _safe_url("coach.index"),
            "settings": _safe_url("settings.index"),
            "billing": _safe_url("billing.shop"),
            "login": _safe_url("auth.login"),
            "logout": _safe_url("auth.logout"),
            "signup": _safe_url("auth.register"),
        }

        # Admin feature paths (admin-only)
        admin_feature_paths = {
            "home": home_url,
            "admin_home": _safe_url(_admin_home_endpoint()),
            "admin_dashboard": _safe_url("admin.dashboard"),
            "admin_analytics": _safe_url("admin.analytics"),
            "admin_strategy": _safe_url("admin.strategy"),
            "admin_users": _safe_url("admin.users"),
            "admin_deals": _safe_url("admin.deals"),
            "admin_vouchers": _safe_url("admin.vouchers"),
            "settings": _safe_url("settings.index"),
            "logout": _safe_url("auth.logout"),
        }

        # Keep legacy keys present so templates don’t crash; disable student ones for admin UI
        feature_paths = dict(student_feature_paths)
        if is_admin:
            for k in [
                "dashboard",
                "profile",
                "resume",
                "portfolio",
                "internships",
                "referral",
                "jobpack",
                "skillmapper",
                "dream",
                "coach",
                "billing",
            ]:
                feature_paths[k] = "#"
            feature_paths.update(admin_feature_paths)

        # Admin flag for navbar: robust
        nav_is_admin = bool(is_admin)

        return dict(
            now=datetime.utcnow(),
            tenant=tenant,
            tenant_name=tenant_name,
            user_free=free_coins(),
            user_pro=pro_coins(),
            subscription_status=(
                getattr(current_user, "subscription_status", "free") if is_authed else "free"
            ),
            feature_paths=feature_paths,
            nav_is_admin=nav_is_admin,
            # NEW: role-aware UI flags for templates
            nav_mode=nav_mode,
            nav_show_student_features=nav_show_student_features,
            nav_is_university_admin=is_university_admin,
            nav_is_super_admin=is_super_admin,
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
        is_prod = (os.getenv("FLASK_ENV") == "production" or os.getenv("ENV") == "production")
        if is_sqlite and not is_prod:
            db.create_all()

    run_auto_migrations(app)
    return app


app = create_app()
