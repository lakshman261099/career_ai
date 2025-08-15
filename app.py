# app.py (root)
import os
import datetime as dt
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify
from flask_login import LoginManager, login_required, current_user
from werkzeug.middleware.proxy_fix import ProxyFix

# Models & DB
from models import db, User, Subscription

# Blueprints
from modules.jobpack.routes import jobpack_bp
from modules.internships.routes import internships_bp
from modules.portfolio.routes import portfolio_bp
from modules.referral.routes import referral_bp
from modules.resume.routes import resume_bp
from modules.billing.routes import billing_bp
from modules.agent.routes import agent_bp

load_dotenv()


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")

    # ---- Database URL normalization (prefer psycopg v3 driver) ----
    raw_url = os.getenv("DATABASE_URL", "sqlite:///career_ai.db")
    if raw_url.startswith("postgres://"):
        fixed_url = raw_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif raw_url.startswith("postgresql://") and "+psycopg" not in raw_url and "+psycopg2" not in raw_url:
        fixed_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    else:
        fixed_url = raw_url
    app.config["SQLALCHEMY_DATABASE_URI"] = fixed_url
    # ---------------------------------------------------------------

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB uploads

    # Feature/env flags
    app.config["MOCK"] = os.getenv("MOCK", "1") == "1"
    app.config["OPENAI_MODEL"] = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Free-tier & caching env
    app.config["FREE_RUNS_PER_DAY"] = int(os.getenv("FREE_RUNS_PER_DAY", "2"))
    app.config["FREE_AGENT_ENABLED"] = os.getenv("FREE_AGENT_ENABLED", "0") == "1"
    app.config["FREE_FAST_PERSONALIZED"] = os.getenv("FREE_FAST_PERSONALIZED", "0") == "1"
    app.config["GLOBAL_FREE_BUDGET_INR"] = int(os.getenv("GLOBAL_FREE_BUDGET_INR", "50000"))
    app.config["SURGE_MODE"] = os.getenv("SURGE_MODE", "1") == "1"
    app.config["CACHE_TTL_JD_FAST_SEC"] = int(os.getenv("CACHE_TTL_JD_FAST_SEC", "172800"))
    app.config["CACHE_TTL_INTERNSHIP_FAST_SEC"] = int(os.getenv("CACHE_TTL_INTERNSHIP_FAST_SEC", "3600"))

    # Referral
    app.config["PUBLIC_SEARCH_PROVIDER"] = os.getenv("PUBLIC_SEARCH_PROVIDER", "brave")
    app.config["PUBLIC_SEARCH_KEY"] = os.getenv("PUBLIC_SEARCH_KEY", "")
    app.config["REFERRAL_MAX_CONTACTS"] = int(os.getenv("REFERRAL_MAX_CONTACTS", "25"))
    app.config["REFERRAL_CACHE_TTL_SEC"] = int(os.getenv("REFERRAL_CACHE_TTL_SEC", "172800"))
    app.config["REFERRAL_CONTACT_COOLDOWN_DAYS"] = int(os.getenv("REFERRAL_CONTACT_COOLDOWN_DAYS", "14"))

    # Stripe
    app.config["STRIPE_PORTAL_RETURN_URL"] = os.getenv("STRIPE_PORTAL_RETURN_URL", "/dashboard")

    # ----- Auth -----
    login_mgr = LoginManager()
    login_mgr.login_view = "auth.login"  # adjust if your auth blueprint differs
    login_mgr.init_app(app)

    @login_mgr.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ----- DB -----
    db.init_app(app)
    with app.app_context():
        db.create_all()

    # ----- Template helpers -----
    @app.context_processor
    def inject_flags():
        def is_pro():
            if not current_user or not current_user.is_authenticated:
                return False
            if current_user.plan and str(current_user.plan).lower().startswith("pro"):
                return True
            sub = (
                Subscription.query.filter_by(user_id=current_user.id, status="active")
                .order_by(Subscription.current_period_end.desc())
                .first()
            )
            return bool(sub)

        return dict(is_pro=is_pro, MOCK=app.config["MOCK"], SURGE_MODE=app.config["SURGE_MODE"])

    # ----- Health -----
    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, ts=dt.datetime.utcnow().isoformat())

    # ----- Basic pages -----
    @app.get("/")
    def home():
        return render_template("landing.html")

    @app.get("/dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html")

    # ----- Blueprints -----
    app.register_blueprint(jobpack_bp, url_prefix="/jobpack")
    app.register_blueprint(internships_bp, url_prefix="/internships")
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(referral_bp, url_prefix="/referral")
    app.register_blueprint(resume_bp, url_prefix="/resume")
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(agent_bp, url_prefix="/agent")

    return app


# Expose WSGI callable for gunicorn ("app:app")
app = create_app()

if __name__ == "__main__":
    # Local dev runner
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
