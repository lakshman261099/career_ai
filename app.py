import os
from flask import Flask, render_template
from dotenv import load_dotenv

# Load .env locally; Render/production will inject env vars directly
load_dotenv()

def create_app():
    app = Flask(__name__)
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")

    # ----- Blueprints -----
    from modules.jobpack.routes import bp as jobpack_bp
    from modules.internships.routes import bp as internships_bp
    from modules.portfolio.routes import bp as portfolio_bp
    from modules.referral.routes import bp as referral_bp
    from modules.agent.routes import bp as agent_bp

    app.register_blueprint(jobpack_bp, url_prefix="/jobpack")
    app.register_blueprint(internships_bp, url_prefix="/internships")
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(referral_bp, url_prefix="/referral")
    app.register_blueprint(agent_bp, url_prefix="/agent")

    # ----- Pages -----
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/dashboard")
    def dashboard():
        # You can pass active_tab="jobpack" to default open a tab
        return render_template("dashboard.html", active_tab="jobpack")

    # Simple global error page
    @app.errorhandler(Exception)
    def on_error(e):
        return render_template("dashboard.html", error=str(e), active_tab="jobpack"), 500

    return app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
