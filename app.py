import os, re, datetime as dt
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, g, redirect, url_for
from flask_login import LoginManager, current_user, login_required
from werkzeug.middleware.proxy_fix import ProxyFix

from models import db, User, University, UniversityAllowlist, Subscription
# blueprints (added in Part 2)
from modules.auth.routes import auth_bp
from modules.settings.routes import settings_bp
from modules.billing.routes import billing_bp
from modules.resume.routes import resume_bp
from modules.jobpack.routes import jobpack_bp
from modules.internships.routes import internships_bp
from modules.portfolio.routes import portfolio_bp
from modules.referral.routes import referral_bp
from modules.skillmapper.routes import skillmapper_bp

load_dotenv()

def _fix_db_url(raw: str) -> str:
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+psycopg://", 1)
    if raw.startswith("postgresql://") and "+psycopg" not in raw:
        return raw.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")

    # DB
    app.config["SQLALCHEMY_DATABASE_URI"] = _fix_db_url(os.getenv("DATABASE_URL", "sqlite:///career_ai.db"))
    app.config["SQLALCHEMY_TRACKERS_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

    # Files
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

    # App flags
    app.config["MOCK"] = os.getenv("MOCK", "1") == "1"
    app.config["OPENAI_MODEL_FAST"] = os.getenv("OPENAI_MODEL_FAST", "gpt-4o-mini")
    app.config["OPENAI_MODEL_DEEP"] = os.getenv("OPENAI_MODEL_DEEP", "gpt-4o")

    # Credits / limits
    app.config["FREE_RUNS_PER_FEATURE_PER_DAY"] = int(os.getenv("FREE_RUNS_PER_FEATURE_PER_DAY", "1"))
    app.config["CREDITS_ENABLED"] = os.getenv("CREDITS_ENABLED", "1") == "1"
    app.config["SILVER_ON_VERIFY"] = int(os.getenv("SILVER_ON_VERIFY", "500"))
    app.config["GOLD_ON_PRO"] = int(os.getenv("GOLD_ON_PRO", "1500"))
    app.config["SILVER_ON_PRO"] = int(os.getenv("SILVER_ON_PRO", "1000"))
    app.config["GOLD_ON_UNI_PRO"] = int(os.getenv("GOLD_ON_UNI_PRO", "1500"))
    app.config["SILVER_ON_UNI_PRO"] = int(os.getenv("SILVER_ON_UNI_PRO", "500"))

    # Stripe
    app.config["STRIPE_PORTAL_RETURN_URL"] = os.getenv("STRIPE_PORTAL_RETURN_URL", "/settings")

    # Tenant base domain
    app.config["BASE_DOMAIN"] = os.getenv("BASE_DOMAIN", "localhost")

    # Auth
    login_mgr = LoginManager()
    login_mgr.login_view = "auth.login"
    login_mgr.init_app(app)

    @login_mgr.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Init DB
    db.init_app(app)
    #with app.app_context():
     #   db.create_all()

    # Tenant resolver
    @app.before_request
    def resolve_tenant():
        host = request.host.split(":")[0]
        base = app.config["BASE_DOMAIN"]
        # localhost handling
        if host == "localhost" or re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
            g.tenant = None
            return
        if host.endswith(base):
            sub = host[:-len(base)].rstrip(".")
            if sub:
                uni = University.query.filter_by(subdomain=sub, status="active").first()
                g.tenant = uni
            else:
                g.tenant = None
        else:
            g.tenant = None

    # Jinja helpers
    @app.context_processor
    def inject_helpers():
        def is_pro():
            if not current_user or not current_user.is_authenticated:
                return False
            # Pro by subscription or explicit plan
            if (current_user.plan or "").lower().startswith("pro"):
                return True
            sub = Subscription.query.filter_by(user_id=current_user.id, status="active").first()
            return bool(sub)
        def tenant_name():
            return g.tenant.name if getattr(g, "tenant", None) else "CareerBoost"
        def year():
            return dt.datetime.utcnow().year
        return dict(is_pro=is_pro, tenant_name=tenant_name, year=year, MOCK=app.config["MOCK"])

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, ts=dt.datetime.utcnow().isoformat())

    # Public pages
    @app.get("/")
    def home():
        return render_template("landing.html")

    @app.get("/dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html")

    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(resume_bp, url_prefix="/resume")
    app.register_blueprint(jobpack_bp, url_prefix="/jobpack")
    app.register_blueprint(internships_bp, url_prefix="/internships")
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(referral_bp, url_prefix="/referral")
    app.register_blueprint(skillmapper_bp, url_prefix="/skillmapper")

    return app

app = create_app()

if __name__ == "__main__":
    # Dev run
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
