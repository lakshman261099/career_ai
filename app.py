import os
from datetime import timedelta
from flask import Flask, render_template, url_for, redirect, request, flash
from flask_login import LoginManager, current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db, User

# Blueprints
from modules.auth.routes import auth_bp
from modules.billing.routes import billing_bp
from modules.jobpack.routes import jobpack_bp
from modules.internships.routes import internships_bp
from modules.portfolio.routes import portfolio_bp
from modules.referral.routes import referral_bp
from modules.resume.routes import resume_bp
from modules.settings.routes import settings_bp
try:
    from modules.agent.routes import agent_bp
    HAVE_AGENT = True
except Exception:
    HAVE_AGENT = False


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    is_prod = os.getenv("FLASK_ENV", "production").lower() == "production"
    app.config.update(
        SESSION_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=is_prod,
        REMEMBER_COOKIE_SECURE=is_prod,
        REMEMBER_COOKIE_DURATION=timedelta(days=30),
    )

    db.init_app(app)

    lm = LoginManager()
    lm.login_view = "auth.login"
    lm.login_message_category = "warning"
    lm.init_app(app)

    @lm.user_loader
    def load_user(user_id):
        try:
            return User.query.get(int(user_id))
        except Exception:
            return None

    # ---------- Jinja helpers ----------
    def _first_attr(obj, names, default=0):
        for n in names:
            if hasattr(obj, n):
                try:
                    return getattr(obj, n) or 0
                except Exception:
                    pass
        return default

    @app.context_processor
    def inject_globals():
        # Coins
        def free_coins():
            if not current_user.is_authenticated:
                return 0
            return int(max(0, _first_attr(current_user,
                ["free_credits", "silver_balance", "credits_free", "free_balance"])))

        def pro_coins():
            if not current_user.is_authenticated:
                return 0
            return int(max(0, _first_attr(current_user,
                ["gold_balance", "paid_credits", "pro_credits", "credit_balance"])))

        def is_pro():
            if not current_user.is_authenticated:
                return False
            if hasattr(current_user, "subscription_status"):
                if str(getattr(current_user, "subscription_status") or "").lower() == "active":
                    return True
            return pro_coins() > 0

        # Fix for your dashboard title
        def tenant_name():
            return os.getenv("TENANT_NAME", "CareerAI")

        # Hard-safe paths so tiles/nav always navigate
        feature_paths = {
            "resume": "/resume/upload",
            "portfolio": "/portfolio/",
            "internships": "/internships/",
            "referrals": "/referrals/",
            "jobpack": "/jobpack/",
            "pricing": "/billing/pricing",
            "settings": "/settings/",
            "login": "/auth/login",
            "register": "/auth/register",
        }

        return dict(
            is_pro=is_pro, free_coins=free_coins, pro_coins=pro_coins,
            tenant_name=tenant_name, feature_paths=feature_paths
        )

    # ---------- Blueprints ----------
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(jobpack_bp, url_prefix="/jobpack")
    app.register_blueprint(internships_bp, url_prefix="/internships")
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(referral_bp, url_prefix="/referrals")
    app.register_blueprint(resume_bp, url_prefix="/resume")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    if HAVE_AGENT:
        app.register_blueprint(agent_bp, url_prefix="/agent")

    # ---------- Pages ----------
    @app.route("/")
    def home(): return render_template("landing.html")

    @app.route("/dashboard")
    def dashboard(): return render_template("dashboard.html")

    @app.route("/healthz")
    def healthz(): return {"ok": True}

    @app.route("/post-login")
    def post_login():
        nxt = request.args.get("next") or url_for("dashboard")
        flash("Welcome back!", "success")
        return redirect(nxt)

    with app.app_context():
        db.create_all()

    return app


# Gunicorn uses wsgi.py; below is local dev only
if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=True)
