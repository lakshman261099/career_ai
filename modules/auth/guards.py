# modules/auth/guards.py

from functools import wraps
from flask import flash, redirect, request, url_for
from flask_login import current_user


def require_verified_email(view_func):
    """
    Ensures the user is logged in AND has a verified email
    (via OTP or Google).

    Usage:
        @bp.route("/run", methods=["POST"])
        @login_required
        @require_verified_email
        def run_tool():
            ...
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to continue.", "error")
            return redirect(url_for("auth.login", next=request.path))

        if not getattr(current_user, "verified", False):
            flash("Please verify your email to use AI tools.", "error")
            # This will now auto-send a code & jump to /otp/verify
            return redirect(url_for("auth.otp_request"))

        return view_func(*args, **kwargs)

    return wrapper
