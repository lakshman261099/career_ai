# wsgi.py
# Production entrypoint for Gunicorn / Render

import os

# If your app module is named "app.py" and exposes create_app():
from app import create_app

# Create the WSGI application object that Gunicorn will serve
app = create_app()

# Optional: allow running this file directly for quick local tests
if __name__ == "__main__":
    # Never use this in production; use gunicorn instead
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
