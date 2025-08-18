# app.py

import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from flask_login import LoginManager, current_user
from dotenv import load_dotenv

from models import db, User, University
from limits import init_limits

# blueprints
from modules.auth.routes import auth_bp, login_manager
from modules.billing.routes import billing_bp
from modules.resume.routes import resume_bp
from modules.portfolio.routes import portfolio_bp
from modules.internships.routes import internships_bp
from modules.referral.routes import referral_bp
from modules.jobpack.routes import jobpack_bp
from modules.skillmapper.routes import skillmapper_bp
from modules.settings.routes import settings_bp

load_dotenv()


# ----------------------------------------------------------------------
# Template globals (fixes UndefinedError in base.html)
# ----------------------------------------------------------------------
def free_coins():
    if current_user and current_user.is_authenticated:
        return current_user.coins_free
    return 0

def pro_coins():
    if current_user and current_user.is_authenticated:
        return current_user.coins_pro
    return 0

def register_template_globals(app):
    app.jinja_env.globals.update(
        free_coins=free_coins,
        pro_coins=pro_coins
    )


# ----------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------
def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Config
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.getenv("FLASK_SECRET_KEY", "dev-secret-key"))
    db_url = os.getenv("DATABASE_URL")
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url or os.getenv("DEV_DATABASE_URI", "sqlite:///career_ai.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB uploads

    # Init extensions
    db.init_app(app)
    login_manager.init_app(app)
    init_limits(app)

    # Blueprints
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(billing_bp)
    app.register_blueprint(resume_bp, url_prefix="/resume")
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(internships_bp, url_prefix="/internships")
    app.register_blueprint(referral_bp, url_prefix="/referral")
    app.register_blueprint(jobpack_bp, url_prefix="/jobpack")
    app.register_blueprint(skillmapper_bp, url_prefix="/skillmapper")
    app.register_blueprint(settings_bp, url_prefix="/settings")

    # Template globals
    register_template_globals(app)

    # Routes
    @app.route("/")
    def landing():
        return render_template("landing.html")

    @app.route("/dashboard")
    def dashboard():
        return render_template("dashboard.html")

    # favicon to silence 404
    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(os.path.join(app.static_folder),
                                   'favicon.ico', mimetype='image/vnd.microsoft.icon')

    # Inject globals (tenant info + balances)
    @app.context_processor
    def inject_globals():
        tenant_name = None
        try:
            host = request.host.split(":")[0]
            uni = University.query.filter(
                (University.domain == host) | (University.tenant_slug == host)
            ).first()
            tenant_name = uni.name if uni else None
        except Exception:
            tenant_name = None

        return {
            "now": datetime.utcnow(),
            "tenant_name": tenant_name,
            "user_free": current_user.coins_free if current_user.is_authenticated else 0,
            "user_pro": current_user.coins_pro if current_user.is_authenticated else 0,
            "subscription_status": current_user.subscription_status if current_user.is_authenticated else "free",
        }

    # Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def srv_error(e):
        return render_template("errors/500.html"), 500

    with app.app_context():
        db.create_all()

    return app


# ----------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------
app = create_app()
