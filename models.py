from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import datetime as dt
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import func, UniqueConstraint
import secrets, string

db = SQLAlchemy()

def _now():
    return dt.datetime.utcnow()

def gen_slug(n=8):
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))

# --- Tenancy / Access ---

class University(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    subdomain = db.Column(db.String(80), unique=True, nullable=False)  # e.g. andhrauniversity
    logo_url = db.Column(db.String(300))
    theme_color = db.Column(db.String(20), default="#6d28d9")
    status = db.Column(db.String(20), default="active")  # active/inactive
    created_at = db.Column(db.DateTime, default=_now)

class UniversityAllowlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    university_id = db.Column(db.Integer, db.ForeignKey("university.id"), nullable=False)
    email = db.Column(db.String(200), nullable=False, index=True)
    status = db.Column(db.String(20), default="allowed")  # allowed/used/blocked
    notes = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=_now)
    __table_args__ = (UniqueConstraint("university_id", "email", name="uniq_uni_email"),)

# --- Auth / Users ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    university_id = db.Column(db.Integer, db.ForeignKey("university.id"))
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    plan = db.Column(db.String(50), default="free")  # free/pro
    is_verified = db.Column(db.Boolean, default=False)
    otp_code = db.Column(db.String(6))
    otp_sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=_now)

    silver_balance = db.Column(db.Integer, default=0)
    gold_balance = db.Column(db.Integer, default=0)

    def set_password(self, raw): self.password_hash = generate_password_hash(raw)
    def check_password(self, raw): return check_password_hash(self.password_hash, raw)

# --- Billing ---

class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    stripe_customer_id = db.Column(db.String(120))
    stripe_subscription_id = db.Column(db.String(120))
    plan = db.Column(db.String(50), default="pro")
    status = db.Column(db.String(50), default="inactive")  # active, canceled
    current_period_end = db.Column(db.DateTime)

# --- Resume / Assets ---

class ResumeAsset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    filename = db.Column(db.String(200))
    mime = db.Column(db.String(100))
    content_text = db.Column(db.Text)     # extracted text
    persisted = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=_now)

# --- Features data ---

class JobPackReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role = db.Column(db.String(200))
    mode = db.Column(db.String(20))  # fast/deep
    verdict = db.Column(db.String(50))
    payload_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=_now)

class InternshipRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role = db.Column(db.String(200))
    location = db.Column(db.String(200))
    source = db.Column(db.String(120))
    title = db.Column(db.String(200))
    company = db.Column(db.String(200))
    link = db.Column(db.String(400))
    match_score = db.Column(db.Integer)
    missing_skills = db.Column(db.String(400))
    created_at = db.Column(db.DateTime, default=_now)

class PortfolioPage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(200))
    slug = db.Column(db.String(80), unique=True, index=True, default=gen_slug)
    about_html = db.Column(db.Text)
    skills_csv = db.Column(db.Text)
    experience_html = db.Column(db.Text)
    education_html = db.Column(db.Text)
    links_json = db.Column(db.Text)
    project_html = db.Column(db.Text)  # optional detailed project page
    created_at = db.Column(db.DateTime, default=_now)

class OutreachContact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(200))
    role = db.Column(db.String(200))
    company = db.Column(db.String(200))
    email = db.Column(db.String(200))      # kept for future; unused in trainer
    source = db.Column(db.String(100))
    notes = db.Column(db.Text)
    public_url = db.Column(db.String(600))
    approx_location = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=_now)

# --- Usage / Limits ---

class FreeUsage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True)
    feature = db.Column(db.String(50), index=True)
    ymd = db.Column(db.Date, index=True, default=dt.date.today)
    count = db.Column(db.Integer, default=0)
    __table_args__ = (UniqueConstraint("user_id", "feature", "ymd", name="uniq_free_daily"),)

class UsageLedger(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True)
    feature = db.Column(db.String(50))
    mode = db.Column(db.String(20))  # fast/deep/free
    model = db.Column(db.String(80))
    prompt_tokens = db.Column(db.Integer, default=0)
    completion_tokens = db.Column(db.Integer, default=0)
    total_tokens = db.Column(db.Integer, default=0)
    silver_spent = db.Column(db.Integer, default=0)
    gold_spent = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=_now)
