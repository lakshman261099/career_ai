# career_ai/models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import datetime as dt

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255))
    plan = db.Column(db.String(50), default="free")
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

class University(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

class UserUniversity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    university_id = db.Column(db.Integer, db.ForeignKey("university.id"), nullable=False)
    role = db.Column(db.String(100))

class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    plan = db.Column(db.String(50))
    status = db.Column(db.String(50))
    current_period_end = db.Column(db.DateTime)

class JobPackReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role = db.Column(db.String(200))
    mode = db.Column(db.String(50))  # fast|deep
    verdict = db.Column(db.String(50))
    payload_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

class InternshipRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role = db.Column(db.String(200))
    location = db.Column(db.String(200))
    source = db.Column(db.String(100))
    title = db.Column(db.String(255))
    company = db.Column(db.String(255))
    link = db.Column(db.String(500))
    match_score = db.Column(db.Integer)
    missing_skills = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

class PortfolioPage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(255))
    slug = db.Column(db.String(255), unique=True)
    html = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

class OutreachContact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(200))
    role = db.Column(db.String(200))
    company = db.Column(db.String(200))
    email = db.Column(db.String(255), nullable=True)  # contacts-only: keep blank
    source = db.Column(db.String(100))  # brave|serpapi|google_cse|manual
    notes = db.Column(db.Text)
    public_url = db.Column(db.String(600))
    approx_location = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

class AgentJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    preferences_json = db.Column(db.Text)
    results_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

class ResumeAsset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    filename = db.Column(db.String(255))
    mime = db.Column(db.String(100))
    content_text = db.Column(db.Text)  # extracted plain text
    persisted = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

class FreeUsage(db.Model):
    """Tracks per-user free runs per day and soft per-IP."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    ip = db.Column(db.String(64))
    day = db.Column(db.Date, default=lambda: dt.date.today())
    count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)
