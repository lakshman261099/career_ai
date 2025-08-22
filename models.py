# models.py

from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import UniqueConstraint, Index

db = SQLAlchemy()


# ---------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------
class University(db.Model):
    __tablename__ = "university"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    # e.g., veltech.edu
    domain = db.Column(db.String(120), unique=True, nullable=True, index=True)
    # e.g., veltech.jobpack.ai
    tenant_slug = db.Column(db.String(120), unique=True, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # relationships
    users = db.relationship(
        "User",
        backref="university",
        lazy=True,
        cascade="save-update",
        passive_deletes=True,
    )

    def __repr__(self):
        return f"<University {self.id} {self.name}>"


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)

    # Auth
    password_hash = db.Column(db.String(255), nullable=False)
    verified = db.Column(db.Boolean, default=False, nullable=False)

    # Billing / subscription
    subscription_status = db.Column(db.String(32), default="free", nullable=False)  # "free" | "pro" | "canceled" | etc.
    stripe_customer_id = db.Column(db.String(120), index=True, nullable=True)
    stripe_subscription_id = db.Column(db.String(120), index=True, nullable=True)
    pro_since = db.Column(db.DateTime, nullable=True)
    pro_cancel_at = db.Column(db.DateTime, nullable=True)

    # Credits
    coins_free = db.Column(db.Integer, default=10, nullable=False)  # Silver 🪙
    coins_pro = db.Column(db.Integer, default=0, nullable=False)    # Gold ⭐

    # Tenancy
    university_id = db.Column(
        db.Integer,
        db.ForeignKey("university.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # helpers
    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)

    @property
    def is_pro(self) -> bool:
        return (self.subscription_status or "free").lower() == "pro"

    def __repr__(self):
        return f"<User {self.id} {self.email}>"


# ---------------------------------------------------------------------
# NEW: Hiring‑Manager style editable Profile (1–1 with User)
# ---------------------------------------------------------------------
class UserProfile(db.Model):
    __tablename__ = "user_profile"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        unique=True,
    )

    # Core identity
    full_name = db.Column(db.String(120), nullable=True)
    headline = db.Column(db.String(200), nullable=True)
    summary = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(32), nullable=True)

    # Flexible structured fields
    links = db.Column(db.JSON, default=dict)           # {"linkedin": "...", "github": "...", ...}
    skills = db.Column(db.JSON, default=list)          # ["Python", "SQL", ...]
    education = db.Column(db.JSON, default=list)       # [{school, degree, year}, ...]
    experience = db.Column(db.JSON, default=list)      # [{company, role, start, end, bullets:[...]}]
    certifications = db.Column(db.JSON, default=list)  # ["AWS CCP", ...]

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref("profile", uselist=False, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<UserProfile {self.id} u={self.user_id}>"


# ---------------------------------------------------------------------
# Free usage counters (rate/credit governance for Free tier)
# ---------------------------------------------------------------------
class FreeUsage(db.Model):
    __tablename__ = "free_usage"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    feature = db.Column(db.String(64), index=True, nullable=False)
    day = db.Column(db.Date, default=date.today, index=True, nullable=False)
    count = db.Column(db.Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "feature", "day", name="uq_free_usage_user_feature_day"),
        Index("ix_free_usage_user_feature_day", "user_id", "feature", "day"),
    )

    user = db.relationship("User", backref=db.backref("free_usage", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<FreeUsage u={self.user_id} {self.feature} {self.day} x{self.count}>"


# ---------------------------------------------------------------------
# Portfolio Builder
# ---------------------------------------------------------------------
class PortfolioPage(db.Model):
    __tablename__ = "portfolio_page"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title = db.Column(db.String(200), nullable=False)
    content_md = db.Column(db.Text, nullable=True)
    is_public = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("portfolio_pages", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<PortfolioPage {self.id} u={self.user_id} '{self.title}'>"


# ---------------------------------------------------------------------
# Job Pack Reports
# ---------------------------------------------------------------------
class JobPackReport(db.Model):
    __tablename__ = "jobpack_report"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_title = db.Column(db.String(200), nullable=True)
    company = db.Column(db.String(200), nullable=True)
    jd_text = db.Column(db.Text, nullable=True)
    analysis = db.Column(db.Text, nullable=True)  # JSON as text (SQLite friendly)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("jobpack_reports", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<JobPackReport {self.id} u={self.user_id} {self.job_title}>"


# ---------------------------------------------------------------------
# Internship Finder (paste-only results)
# ---------------------------------------------------------------------
class InternshipRecord(db.Model):
    __tablename__ = "internship_record"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role = db.Column(db.String(120), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    results_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("internship_records", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<InternshipRecord {self.id} u={self.user_id} {self.role or ''}>"


# ---------------------------------------------------------------------
# Referral Trainer (Free-only in KB)
# ---------------------------------------------------------------------
class OutreachContact(db.Model):
    __tablename__ = "outreach_contact"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name = db.Column(db.String(120), nullable=True)
    role = db.Column(db.String(120), nullable=True)
    company = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    source = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("outreach_contacts", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<OutreachContact {self.id} u={self.user_id} {self.email or self.name or ''}>"


# ---------------------------------------------------------------------
# Skill Mapper (stores generated skill maps/snapshots)
# ---------------------------------------------------------------------
class SkillMapSnapshot(db.Model):
    __tablename__ = "skillmap_snapshot"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_title = db.Column(db.String(200), nullable=True)   # e.g., "Backend Engineer @ X"
    input_text = db.Column(db.Text, nullable=True)            # pasted JD or text
    skills_json = db.Column(db.Text, nullable=True)           # JSON as text
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("skillmap_snapshots", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<SkillMapSnapshot {self.id} u={self.user_id}>"


# ---------------------------------------------------------------------
# AI Agent (Coming Soon per KB) — keep table but do not expose feature
# ---------------------------------------------------------------------
class AgentJob(db.Model):
    __tablename__ = "agent_job"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_url = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(64), default="queued", nullable=False)
    notes = db.Column(db.Text, nullable=True)

    user = db.relationship("User", backref=db.backref("agent_jobs", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<AgentJob {self.id} u={self.user_id} {self.status}>"


# ---------------------------------------------------------------------
# Resume (Pro-only profile upload/parsing)
# ---------------------------------------------------------------------
class ResumeAsset(db.Model):
    __tablename__ = "resume_asset"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename = db.Column(db.String(255), nullable=True)
    text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("resume_assets", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<ResumeAsset {self.id} u={self.user_id} {self.filename or ''}>"
