import os, json, datetime as dt
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
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
    role = db.Column(db.String(50), default="student")

class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    plan = db.Column(db.String(50), default="pro")
    status = db.Column(db.String(50), default="inactive")  # 'active' when webhook confirms
    current_period_end = db.Column(db.DateTime)

class JobPackReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role = db.Column(db.String(120))
    mode = db.Column(db.String(20))  # 'fast' | 'deep'
    verdict = db.Column(db.String(50))
    payload_json = db.Column(db.Text)  # stored JSON as text
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

class InternshipRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role = db.Column(db.String(120))
    location = db.Column(db.String(120))
    source = db.Column(db.String(120))
    title = db.Column(db.String(255))
    company = db.Column(db.String(255))
    link = db.Column(db.String(500))
    match_score = db.Column(db.Integer)
    missing_skills = db.Column(db.String(500))

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
    name = db.Column(db.String(255))
    role = db.Column(db.String(255))
    company = db.Column(db.String(255))
    email = db.Column(db.String(255))
    source = db.Column(db.String(255))
    notes = db.Column(db.Text)

class AgentJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    preferences_json = db.Column(db.Text)     # form inputs
    results_json = db.Column(db.Text)         # fast packs results
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

# --- Add at the bottom of models.py ---

class ResumeAsset(db.Model):
    __tablename__ = "resume_assets"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    filename = db.Column(db.String(255))
    mime = db.Column(db.String(64))
    content_text = db.Column(db.Text)  # extracted, searchable text
    persisted = db.Column(db.Boolean, default=False)  # Pro users opt-in
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

    def __repr__(self):
        return f"<ResumeAsset {self.id} user={self.user_id} {self.filename}>"
