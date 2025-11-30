import random
import string
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    current_app,
    g,
)
from flask_login import (
    LoginManager,
    login_required,
    login_user,
    logout_user,
    current_user,
)
from werkzeug.security import gen_salt

from models import User, db, OTPRequest
from .email_utils import send_otp_email  # shared email helper
from .oauth import oauth  # Google OAuth client

auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")
login_manager = LoginManager()
login_manager.login_view = "auth.login"


@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# ---------------------------
# Password-based Register/Login
# ---------------------------

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = _normalize_email(request.form.get("email"))
        pw = request.form.get("password") or ""

        if not (name and email and pw):
            flash("All fields are required.", "error")
            return render_template("auth/register.html")

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return render_template("auth/register.html")

        # New accounts start as NOT verified
        u = User(
            name=name,
            email=email,
            verified=False,
        )

        # Attach to current tenant if present
        tenant = getattr(g, "current_tenant", None)
        if tenant is not None:
            u.university_id = tenant.id

        u.set_password(pw)
        db.session.add(u)
        db.session.commit()

        login_user(u)
        flash(
            "Account created. Please verify your email with a login code "
            "before using AI features.",
            "info",
        )
        return redirect(url_for("dashboard"))
    return render_template("auth/register.html")


# alias /signup -> /register
@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    return register()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    Email+password login, with visible options for OTP and Google.
    """
    # Optional: if already logged in, just go to dashboard
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = _normalize_email(request.form.get("email"))
        pw = request.form.get("password") or ""

        u = User.query.filter_by(email=email).first()
        if not u or not u.check_password(pw):
            flash("Invalid credentials.", "error")
            return render_template("auth/login.html")

        login_user(u)
        flash("Logged in.", "success")
        return redirect(url_for("dashboard"))
    return render_template("auth/login.html")


# ---------------------------
# OTP-based Login & Verification
# ---------------------------

OTP_SESSION_KEY = "otp_login"
OTP_TTL_MINUTES = 10


def _generate_otp_code(length=6) -> str:
    return "".join(random.choice(string.digits) for _ in range(length))


def _create_and_send_otp(email: str, ip: str | None = None) -> OTPRequest:
    """
    Helper: create OTPRequest row, send email, and return the OTP object.
    """
    code = _generate_otp_code(6)
    expires_at = datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES)

    otp = OTPRequest(
        email=email,
        code=code,
        expires_at=expires_at,
        ip_address=ip,
    )
    db.session.add(otp)
    db.session.commit()

    # Send email (or log in dev)
    send_otp_email(to_email=email, otp_code=code)
    return otp


@auth_bp.route("/otp/request", methods=["GET", "POST"], endpoint="otp_request")
def otp_request():
    """
    Dual-behavior endpoint:

    - If the user is LOGGED IN:
        Treat this as "Send me a verification code".
        -> auto-send OTP to current_user.email
        -> redirect straight to /otp/verify (no email form).

    - If the user is LOGGED OUT:
        Show the "Login with OTP" page (email form).
        POST: send OTP to the entered email and redirect to /otp/verify.
    """

    # ✅ Already logged in → verification flow (no email form)
    if current_user.is_authenticated:
        email = _normalize_email(getattr(current_user, "email", ""))
        if not email:
            flash("Your account does not have an email address yet.", "error")
            # You can change this redirect if you have a profile settings page
            return redirect(url_for("settings.profile"))

        try:
            otp = _create_and_send_otp(email=email, ip=request.remote_addr)
        except Exception as e:
            current_app.logger.exception(
                "Failed to create/send OTP for logged-in user: %s", e
            )
            flash("Could not send a verification code. Please try again.", "error")
            return redirect(url_for("dashboard"))

        # Store OTP session data
        session[OTP_SESSION_KEY] = {"email": email, "otp_id": otp.id}
        session.modified = True

        flash("We’ve sent a verification code to your email.", "info")
        return redirect(url_for("auth.otp_verify"))

    # ❌ Not logged in → classic "Login with OTP" flow
    if request.method == "POST":
        email = _normalize_email(request.form.get("email"))
        if not email:
            flash("Please enter your email.", "error")
            return render_template("auth/otp_request.html")

        try:
            otp = _create_and_send_otp(email=email, ip=request.remote_addr)
        except Exception as e:
            current_app.logger.exception("Failed to create/send OTP for login: %s", e)
            flash("Could not send a login code. Please try again.", "error")
            return render_template("auth/otp_request.html")

        # Store only minimal info in session (no code)
        session[OTP_SESSION_KEY] = {
            "email": email,
            "otp_id": otp.id,
        }
        session.modified = True

        flash("We’ve sent a 6-digit code to your email.", "info")
        return redirect(url_for("auth.otp_verify"))

    # GET, not logged in → show "Login with OTP" screen
    return render_template("auth/otp_request.html")


@auth_bp.route("/otp/verify", methods=["GET", "POST"], endpoint="otp_verify")
def otp_verify():
    data = session.get(OTP_SESSION_KEY) or {}
    if not data:
        flash("OTP session not found. Request a new code.", "error")
        return redirect(url_for("auth.otp_request"))

    email = data.get("email")
    otp_id = data.get("otp_id")

    if request.method == "POST":
        user_code = (request.form.get("code") or "").strip()

        # Basic validation of format
        if not (len(user_code) == 6 and user_code.isdigit()):
            flash("Please enter a 6-digit numeric code.", "error")
            return render_template("auth/otp_verify.html")

        # Look up OTP in database
        otp = OTPRequest.query.filter_by(id=otp_id, email=email).first()
        if not otp:
            session.pop(OTP_SESSION_KEY, None)
            flash("Code not found. Please request a new one.", "error")
            return redirect(url_for("auth.otp_request"))

        # Check expiry
        if otp.is_expired():
            session.pop(OTP_SESSION_KEY, None)
            flash("Code expired. Please request a new one.", "error")
            return redirect(url_for("auth.otp_request"))

        # Check if already used
        if otp.used:
            session.pop(OTP_SESSION_KEY, None)
            flash("This code was already used. Request a new one.", "error")
            return redirect(url_for("auth.otp_request"))

        # Check code
        if user_code != otp.code:
            flash("Invalid code. Please try again.", "error")
            return render_template("auth/otp_verify.html")

        # Mark OTP as used
        otp.used = True
        db.session.commit()

        # Find or create user
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(
                name=email.split("@")[0].title(),
                email=email,
                verified=True,
            )

            # Attach to current tenant if present
            tenant = getattr(g, "current_tenant", None)
            if tenant is not None:
                user.university_id = tenant.id

            user.set_password(gen_salt(24))  # random; user can set later
            db.session.add(user)
        else:
            user.verified = True
            # (Do not override university_id for existing users here)

        db.session.commit()
        login_user(user)
        session.pop(OTP_SESSION_KEY, None)

        flash("Logged in with OTP. Welcome!", "success")
        return redirect(url_for("dashboard"))

    return render_template("auth/otp_verify.html")


# ---------------------------
# Google OAuth Login
# ---------------------------

@auth_bp.route("/google/login", methods=["GET"], endpoint="google_login")
def google_login():
    """
    Start Google OAuth flow.
    """
    client = oauth.create_client("google")
    if not client:
        flash("Google login is not configured yet.", "error")
        return redirect(url_for("auth.login"))

    redirect_uri = url_for("auth.google_callback", _external=True)
    return client.authorize_redirect(redirect_uri)


@auth_bp.route("/google/callback", methods=["GET"], endpoint="google_callback")
def google_callback():
    """
    Handle Google OAuth callback:
    - Fetch user info
    - Create or update local User
    - Mark as verified
    - Log in
    """
    client = oauth.create_client("google")
    if not client:
        flash("Google login is not configured.", "error")
        return redirect(url_for("auth.login"))

    try:
        token = client.authorize_access_token()
        resp = client.get("userinfo", token=token)
        google_info = resp.json()
    except Exception as e:
        current_app.logger.exception("Google OAuth callback failed: %s", e)
        flash("Could not complete Google login. Please try again.", "error")
        return redirect(url_for("auth.login"))

    email = _normalize_email(google_info.get("email"))
    full_name = (google_info.get("name") or "").strip()

    if not email:
        flash("Google account did not return an email address.", "error")
        return redirect(url_for("auth.login"))

    # Find or create user
    user = User.query.filter_by(email=email).first()
    created = False
    if not user:
        user = User(
            name=full_name or email.split("@")[0].title(),
            email=email,
            verified=True,  # trust Google's email verification
        )

        # Attach to current tenant if present
        tenant = getattr(g, "current_tenant", None)
        if tenant is not None:
            user.university_id = tenant.id

        user.set_password(gen_salt(24))  # random password; they use Google/OTP
        db.session.add(user)
        created = True
    else:
        user.verified = True
        # keep existing user.university_id as-is

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Failed to commit user after Google login: %s", e)
        flash("Could not store your account. Please try again.", "error")
        return redirect(url_for("auth.login"))

    login_user(user)
    if created:
        flash("Account created via Google. Welcome!", "success")
    else:
        flash("Logged in with Google.", "success")

    return redirect(url_for("dashboard"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("auth.login"))
