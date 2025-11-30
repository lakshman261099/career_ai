# modules/auth/oauth.py

import os

from authlib.integrations.flask_client import OAuth
from flask import Flask

oauth = OAuth()


def init_oauth(app: Flask):
    """
    Initialize Authlib OAuth client and register Google provider.
    Call this once in app.py after the Flask app is created.
    """
    oauth.init_app(app)

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        app.logger.warning("Google OAuth not configured (missing client id/secret).")
        return

    oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile",
        },
    )
