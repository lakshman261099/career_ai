# app.py
import os, datetime as dt
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify
from flask_login import LoginManager, login_required, current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import text

from models import db, User, Subscription
from modules.auth.routes import auth_bp
from modules.billing.routes import billing_bp
from modules.jobpack.routes import jobpack_bp
from modules.internships.routes import internships_bp
from modules.portfolio.routes import portfolio_bp
from modules.referral.routes import referral_bp
from modules.resume.routes import resume_bp
from modules.agent.routes import agent_bp
from modules.settings.routes import settings_bp

load_dotenv()

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")

    # Normalize Render/Heroku database URLs to psycopg (v3) driver
    raw_url = os.getenv("DATABASE_URL", "sqlite:///career_ai.db")
    if raw_url.startswith("postgres://"):
        fixed_url = raw_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif raw_url.startswith("postgresql://") and "+psycopg" not in raw_url and "+psycopg2" not in raw_url:
        fixed_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    else:
        fixed_url = raw_url
    app.config["SQLALCHEMY_DATABASE_URI"] = fixed_url

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB uploads

    # Feature flags
    app.config["MOCK"] = os.getenv("MOCK", "1") == "1"
    app.config["OPENAI_MODEL"] = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Free-tier (per-feature handled in limits.py)
    app.config["FREE_RUNS_PER_DAY"] = 1
    app.config["FREE_AGENT_ENABLED"] = False  # Agent is Pro-only now
    app.config["FREE_FAST_PERSONALIZED"] = os.getenv("FREE_FAST_PERSONALIZED", "0") == "1"
    app.config["GLOBAL_FREE_BUDGET_INR"] = int(os.getenv("GLOBAL_FREE_BUDGET_INR", "50000"))
    app.config["SURGE_MODE"] = os.getenv("SURGE_MODE", "1") == "1"
    app.config["CACHE_TTL_JD_FAST_SEC"] = int(os.getenv("CACHE_TTL_JD_FAST_SEC", "172800"))
    app.config["CACHE_TTL_INTERNSHIP_FAST_SEC"] = int(os.getenv("CACHE_TTL_INTERNSHIP_FAST_SEC", "3600"))

    # Public search (Referrals contacts-only)
    app.config["PUBLIC_SEARCH_PROVIDER"] = os.getenv("PUBLIC_SEARCH_PROVIDER", "brave")
    app.config["PUBLIC_SEARCH_KEY"] = os.getenv("PUBLIC_SEARCH_KEY", "")
    app.config["REFERRAL_MAX_CONTACTS"] = int(os.getenv("REFERRAL_MAX_CONTACTS", "25"))
    app.config["REFERRAL_CACHE_TTL_SEC"] = int(os.getenv("REFERRAL_CACHE_TTL_SEC", "172800"))
    app.config["REFERRAL_CONTACT_COOLDOWN_DAYS"] = int(os.getenv("REFERRAL_CONTACT_COOLDOWN_DAYS", "14"))

    # Stripe
    app.config["STRIPE_PORTAL_RETURN_URL"] = os.getenv("STRIPE_PORTAL_RETURN_URL", "/dashboard")

    # Auth
    login_mgr = LoginManager()
    login_mgr.login_view = "auth.login"
    login_mgr.init_app(app)

    @login_mgr.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    db.init_app(app)
    with app.app_context():
        db.create_all()
        run_phase3_migrations(app)  # ← ensure Phase-3 columns exist

    @app.context_processor
    def inject_flags():
        def is_pro():
            if not current_user or not current_user.is_authenticated:
                return False
            if current_user.plan and str(current_user.plan).lower().startswith("pro"):
                return True
            sub = (Subscription.query.filter_by(user_id=current_user.id, status="active")
                   .order_by(Subscription.current_period_end.desc()).first())
            return bool(sub)
        return dict(is_pro=is_pro, MOCK=app.config["MOCK"], SURGE_MODE=app.config["SURGE_MODE"], year=dt.datetime.utcnow().year)

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, ts=dt.datetime.utcnow().isoformat())

    @app.get("/")
    def home():
        return render_template("landing.html")

    @app.get("/pricing")
    def pricing():
        return render_template("pricing.html")

    @app.get("/privacy")
    def privacy():
        return render_template("privacy.html")

    @app.get("/dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html")

    # Blueprints
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(jobpack_bp, url_prefix="/jobpack")
    app.register_blueprint(internships_bp, url_prefix="/internships")
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(referral_bp, url_prefix="/referral")
    app.register_blueprint(resume_bp, url_prefix="/resume")
    app.register_blueprint(agent_bp, url_prefix="/agent")
    app.register_blueprint(settings_bp, url_prefix="/settings")

    return app

def run_phase3_migrations(app):
    """
    Safe, idempotent DDL to align DB with Phase-3 models.
    Works on Postgres; on SQLite it will no-op for IF NOT EXISTS cases.
    """
    try:
        ddl_list = [
            # PortfolioPage columns
            "ALTER TABLE portfolio_page ADD COLUMN IF NOT EXISTS about_html TEXT",
            "ALTER TABLE portfolio_page ADD COLUMN IF NOT EXISTS skills_csv TEXT",
            "ALTER TABLE portfolio_page ADD COLUMN IF NOT EXISTS experience_html TEXT",
            "ALTER TABLE portfolio_page ADD COLUMN IF NOT EXISTS education_html TEXT",
            "ALTER TABLE portfolio_page ADD COLUMN IF NOT EXISTS links_json TEXT",
            # ProjectDetail columns & index
            "ALTER TABLE project_detail ADD COLUMN IF NOT EXISTS html TEXT",
            "ALTER TABLE project_detail ADD COLUMN IF NOT EXISTS slug VARCHAR(255)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_project_detail_slug ON project_detail (slug)",
            # FreeFeatureUsage safety columns (if table exists but lacks fields)
            "ALTER TABLE free_feature_usage ADD COLUMN IF NOT EXISTS feature VARCHAR(50)",
            "ALTER TABLE free_feature_usage ADD COLUMN IF NOT EXISTS user_id INTEGER",
            "ALTER TABLE free_feature_usage ADD COLUMN IF NOT EXISTS ip VARCHAR(64)",
            "ALTER TABLE free_feature_usage ADD COLUMN IF NOT EXISTS day DATE",
            "ALTER TABLE free_feature_usage ADD COLUMN IF NOT EXISTS count INTEGER",
            "ALTER TABLE free_feature_usage ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
        ]
        for ddl in ddl_list:
            try:
                db.session.execute(text(ddl))
                db.session.commit()
            except Exception:
                db.session.rollback()
    except Exception as e:
        app.logger.exception("Phase-3 migrations failed (continuing): %s", e)
        db.session.rollback()

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
