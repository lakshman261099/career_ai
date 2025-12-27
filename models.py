from datetime import date, datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Date, ForeignKey, JSON
from sqlalchemy.orm import relationship

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

    # NEW: One-to-one with UniversityWallet
    wallet = db.relationship(
        "UniversityWallet",
        backref="university",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Admin / B2B relationships
    deals = db.relationship(
        "UniversityDeal",
        backref="university",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    voucher_campaigns = db.relationship(
        "VoucherCampaign",
        backref="university",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self):
        return f"<University {self.id} {self.name}>"


# ---------------------------------------------------------------------
# NEW: University Wallet (One per University)
# ---------------------------------------------------------------------
class UniversityWallet(db.Model):
    """
    Centralized credit wallet for each university.
    All students under a university consume from this shared pool.

    Features:
    - Annual credit cap (resets yearly)
    - Separate Silver (free features) and Gold (pro features) balances
    - Tracks renewal date for automatic yearly top-ups
    """
    __tablename__ = "university_wallet"

    id = db.Column(db.Integer, primary_key=True)

    # One-to-one with University
    university_id = db.Column(
        db.Integer,
        db.ForeignKey("university.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Current balances
    silver_balance = db.Column(db.Integer, default=0, nullable=False)
    gold_balance = db.Column(db.Integer, default=0, nullable=False)

    # Annual caps (for yearly renewal model)
    silver_annual_cap = db.Column(db.Integer, nullable=True)
    gold_annual_cap = db.Column(db.Integer, nullable=True)

    # Renewal tracking
    renewal_date = db.Column(db.Date, nullable=True)
    last_renewed_at = db.Column(db.DateTime, nullable=True)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    def __repr__(self):
        return f"<UniversityWallet uni={self.university_id} silver={self.silver_balance} gold={self.gold_balance}>"

    @property
    def is_renewable(self) -> bool:
        """Check if wallet is due for renewal"""
        if not self.renewal_date:
            return False
        return date.today() >= self.renewal_date

    def renew_if_due(self) -> bool:
        """
        Renew credits if renewal_date has passed.
        Returns True if renewal happened.
        """
        if not self.is_renewable:
            return False

        # Reset to annual caps
        if self.silver_annual_cap is not None:
            self.silver_balance = self.silver_annual_cap
        if self.gold_annual_cap is not None:
            self.gold_balance = self.gold_annual_cap

        # Update renewal tracking
        from dateutil.relativedelta import relativedelta
        self.last_renewed_at = datetime.utcnow()
        self.renewal_date = date.today() + relativedelta(years=1)

        return True


# ---------------------------------------------------------------------
# NEW: Credit Transaction (Audit Trail)
# ---------------------------------------------------------------------
class CreditTransaction(db.Model):
    """
    Immutable ledger for all credit movements.
    Tracks every debit, refund, top-up, and bonus.

    Supports both:
    - Personal wallets (user.coins_free / user.coins_pro)
    - University wallets (UniversityWallet)
    """
    __tablename__ = "credit_transaction"

    id = db.Column(db.Integer, primary_key=True)

    # Who triggered this transaction
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Optional: university context (if university-managed user)
    university_id = db.Column(
        db.Integer,
        db.ForeignKey("university.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Transaction details
    feature = db.Column(db.String(64), nullable=False, index=True)
    currency = db.Column(db.String(16), nullable=False)  # "silver" or "gold"
    amount = db.Column(db.Integer, nullable=False)  # Always positive

    # Type of transaction
    tx_type = db.Column(
        db.String(32), nullable=False, index=True
    )  # "debit", "credit", "refund", "bonus", "renewal"

    # Wallet type (for routing logic)
    wallet_type = db.Column(
        db.String(32), nullable=False, default="personal"
    )  # "personal" or "university"

    # Balance tracking (snapshot at time of transaction)
    before_balance = db.Column(db.Integer, nullable=False, default=0)
    after_balance = db.Column(db.Integer, nullable=False, default=0)

    # Context
    run_id = db.Column(db.String(128), nullable=True, index=True)
    status = db.Column(
        db.String(32), default="completed", nullable=False
    )  # "completed", "pending", "failed"

    # Flexible metadata (JSON)
    meta_json = db.Column(db.JSON, nullable=True, default=dict)

    # Timestamp
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    user = db.relationship(
        "User",
        backref=db.backref("credit_transactions", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return (
            f"<CreditTransaction {self.id} user={self.user_id} "
            f"{self.tx_type} {self.currency}={self.amount}>"
        )


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)

    # ✅ NEW: Department (for university admin filters + bulk add)
    department = db.Column(db.String(120), nullable=True, index=True)

    # Auth
    password_hash = db.Column(db.String(255), nullable=False)

    # ✅ Keep legacy field
    verified = db.Column(db.Boolean, default=False, nullable=False)

    # ✅ NEW: email_verified (used by coach/routes.py helper, backward compatible)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)

    # ✅ NEW: timezone (used by coach/routes.py _app_day)
    timezone = db.Column(db.String(64), nullable=True)

    # Role / permissions
    # "student" | "university_admin" | "super_admin" | "ultra_admin"
    role = db.Column(db.String(32), default="student", nullable=False)

    # Billing / subscription
    subscription_status = db.Column(
        db.String(32), default="free", nullable=False
    )  # "free" | "pro" | "canceled" | etc.
    stripe_customer_id = db.Column(db.String(120), index=True, nullable=True)
    stripe_subscription_id = db.Column(db.String(120), index=True, nullable=True)
    pro_since = db.Column(db.DateTime, nullable=True)
    pro_cancel_at = db.Column(db.DateTime, nullable=True)

    # Credits (Personal wallet for B2C users)
    coins_free = db.Column(db.Integer, default=10, nullable=False)  # Silver 🪙
    coins_pro = db.Column(db.Integer, default=0, nullable=False)  # Gold ⭐

    # ✅ NEW: Streak tracking
    current_streak = db.Column(db.Integer, default=0, nullable=False)
    longest_streak = db.Column(db.Integer, default=0, nullable=False)
    last_daily_task_date = db.Column(db.Date, nullable=True)

    # ✅ NEW: Streak freeze system (1 per week)
    streak_freezes_remaining = db.Column(db.Integer, default=1, nullable=False)
    last_freeze_reset_date = db.Column(db.Date, nullable=True)

    # ✅ NEW: Ready Score (for university admin dashboard)
    ready_score = db.Column(db.Integer, default=0, nullable=False)

    # ✅ NEW: Weekly milestones completed
    weekly_milestones_completed = db.Column(db.Integer, default=0, nullable=False)

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

    @property
    def is_ultra_admin(self) -> bool:
        """Ultra admin: highest level; can do everything super admins can, plus extra."""
        return (self.role or "student") == "ultra_admin"

    @property
    def is_super_admin(self) -> bool:
        """
        Super admin: global admin.

        NOTE: ultra_admins are treated as super admins for all existing checks,
        so anywhere the code uses `is_super_admin`, ultra admins are also allowed.
        """
        return (self.role or "student") in ("super_admin", "ultra_admin")

    @property
    def is_university_admin(self) -> bool:
        return (self.role or "student") == "university_admin"

    @property
    def is_admin(self) -> bool:
        # Includes ultra_admin via is_super_admin
        return self.is_super_admin or self.is_university_admin

    @property
    def is_university_managed(self) -> bool:
        """
        NEW: Check if this user belongs to a university.
        If True, credits will be deducted from UniversityWallet instead of personal wallet.
        """
        return self.university_id is not None

    def __repr__(self):
        return f"<User {self.id} {self.email}>"


# ---------------------------------------------------------------------
# OTP Requests (for email verification & login)
# ---------------------------------------------------------------------
class OTPRequest(db.Model):
    __tablename__ = "otp_request"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    code = db.Column(db.String(6), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, nullable=False, default=False)
    ip_address = db.Column(db.String(64), nullable=True)

    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at


# ---------------------------------------------------------------------
# Hiring-Manager style editable Profile (1–1 with User)
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
    links = db.Column(
        db.JSON, default=dict
    )  # {"linkedin": "...", "github": "...", ...}
    skills = db.Column(
        db.JSON, default=list
    )  # e.g. [{"name":"Python","level":3}, ...] or ["Python", ...]
    education = db.Column(db.JSON, default=list)  # [{school, degree, year}, ...]
    experience = db.Column(
        db.JSON, default=list
    )  # [{company, role, start, end, bullets:[...]}]
    certifications = db.Column(
        db.JSON, default=list
    )  # ["AWS CCP", ...] or [{"name":"AWS CCP","year":"2024"}]

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref("profile", uselist=False, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<UserProfile {self.id} u={self.user_id}>"


# ---------------------------------------------------------------------
# Projects (stored in Profile Portal; used by Portfolio Publish)
# ---------------------------------------------------------------------
class Project(db.Model):
    __tablename__ = "project"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Core project fields
    title = db.Column(db.String(200), nullable=False)
    short_desc = db.Column(db.String(500), nullable=True)
    bullets = db.Column(db.JSON, default=list)  # ["Did X", "Improved Y by Z%"]
    tech_stack = db.Column(db.JSON, default=list)  # ["Flask","Postgres","Docker"]
    role = db.Column(db.String(120), nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    links = db.Column(db.JSON, default=list)  # [{"label":"GitHub","url":"..."}]

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user = db.relationship(
        "User", backref=db.backref("projects", lazy=True, cascade="all, delete-orphan")
    )

    def __repr__(self):
        return f"<Project {self.id} u={self.user_id} {self.title[:30]}>"


# ---------------------------------------------------------------------
# Free usage counters (rate/credit governance for Free tier)
# ---------------------------------------------------------------------
class FreeUsage(db.Model):
    __tablename__ = "free_usage"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    feature = db.Column(db.String(64), index=True, nullable=False)
    day = db.Column(db.Date, default=date.today, index=True, nullable=False)
    count = db.Column(db.Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "feature", "day", name="uq_free_usage_user_feature_day"
        ),
        Index("ix_free_usage_user_feature_day", "user_id", "feature", "day"),
    )

    user = db.relationship(
        "User",
        backref=db.backref("free_usage", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<FreeUsage u={self.user_id} {self.feature} {self.day} x{self.count}>"


# ---------------------------------------------------------------------
# Portfolio Builder
# ---------------------------------------------------------------------
class PortfolioPage(db.Model):
    __tablename__ = "portfolio_page"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title = db.Column(db.String(200), nullable=False)
    content_md = db.Column(db.Text, nullable=True)
    is_public = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # NEW: metadata for locking, tier, suggestion_count, timestamps, etc.
    meta_json = db.Column(db.JSON, default=dict)

    user = db.relationship(
        "User",
        backref=db.backref("portfolio_pages", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<PortfolioPage {self.id} u={self.user_id} '{self.title}'>"


# ---------------------------------------------------------------------
# Portfolio Wizard runs (history of project ideas)
# ---------------------------------------------------------------------
class PortfolioIdeaRun(db.Model):
    __tablename__ = "portfolio_idea_run"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # "free" or "pro"
    mode = db.Column(db.String(16), nullable=False)

    # Inputs to the wizard
    target_role = db.Column(db.String(200), nullable=True)
    industry = db.Column(db.String(200), nullable=True)
    experience_level = db.Column(db.String(64), nullable=True)

    # Pro-only extras (Safe to keep empty for free)
    focus_area = db.Column(db.JSON, default=list)  # e.g. ["Backend","Cloud"]
    time_budget = db.Column(db.String(16), nullable=True)
    preferred_stack = db.Column(db.JSON, default=list)  # ["Python","Flask","Postgres"]

    # Suggestions JSON (same structure that comes back from generate_project_suggestions)
    suggestions_json = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref(
            "portfolio_idea_runs", lazy=True, cascade="all, delete-orphan"
        ),
    )

    def __repr__(self):
        return f"<PortfolioIdeaRun {self.id} u={self.user_id} mode={self.mode}>"


# ---------------------------------------------------------------------
# Job Pack Reports
# ---------------------------------------------------------------------
class JobPackReport(db.Model):
    __tablename__ = "jobpack_report"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    job_title = db.Column(db.String(200), nullable=True)
    company = db.Column(db.String(200), nullable=True)
    jd_text = db.Column(db.Text, nullable=True)
    analysis = db.Column(db.Text, nullable=True)  # JSON as text (SQLite friendly)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref("jobpack_reports", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<JobPackReport {self.id} u={self.user_id} {self.job_title}>"


# ---------------------------------------------------------------------
# Internship Finder (paste-only results)
# ---------------------------------------------------------------------
class InternshipRecord(db.Model):
    __tablename__ = "internship_record"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role = db.Column(db.String(120), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    results_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref(
            "internship_records", lazy=True, cascade="all, delete-orphan"
        ),
    )

    def __repr__(self):
        return f"<InternshipRecord {self.id} u={self.user_id} {self.role or ''}>"


# ---------------------------------------------------------------------
# Referral Trainer (Free-only)
# ---------------------------------------------------------------------
class OutreachContact(db.Model):
    __tablename__ = "outreach_contact"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name = db.Column(db.String(120), nullable=True)
    role = db.Column(db.String(120), nullable=True)
    company = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    source = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref(
            "outreach_contacts", lazy=True, cascade="all, delete-orphan"
        ),
    )

    def __repr__(self):
        return (
            f"<OutreachContact {self.id} u={self.user_id} "
            f"{self.email or self.name or ''}>"
        )


# ---------------------------------------------------------------------
# Skill Mapper (stores generated skill maps/snapshots)
# ---------------------------------------------------------------------
class SkillMapSnapshot(db.Model):
    __tablename__ = "skillmap_snapshot"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_title = db.Column(
        db.String(200), nullable=True
    )  # e.g., "Backend Engineer @ X"
    input_text = db.Column(db.Text, nullable=True)  # pasted JD or text
    skills_json = db.Column(db.Text, nullable=True)  # JSON as text
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref(
            "skillmap_snapshots", lazy=True, cascade="all, delete-orphan"
        ),
    )

    def __repr__(self):
        return f"<SkillMapSnapshot {self.id} u={self.user_id}>"


# ---------------------------------------------------------------------
# AI Agent (Coming Soon) — keep table but do not expose feature
# ---------------------------------------------------------------------
class AgentJob(db.Model):
    __tablename__ = "agent_job"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    job_url = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(64), default="queued", nullable=False)
    notes = db.Column(db.Text, nullable=True)

    user = db.relationship(
        "User",
        backref=db.backref("agent_jobs", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<AgentJob {self.id} u={self.user_id} {self.status}>"


# ---------------------------------------------------------------------
# Resume assets (PDF text extracted, used by Profile Portal + features)
# ---------------------------------------------------------------------
class ResumeAsset(db.Model):
    __tablename__ = "resume_asset"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    filename = db.Column(db.String(255), nullable=True)
    text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref("resume_assets", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<ResumeAsset {self.id} u={self.user_id} {self.filename or ''}>"


# ---------------------------------------------------------------------
# Dream Plan Snapshots (for Weekly / Daily Coach)
# ---------------------------------------------------------------------
class DreamPlanSnapshot(db.Model):
    __tablename__ = "dream_plan_snapshot"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # "job" or "startup"
    path_type = db.Column(db.String(20), nullable=False, index=True)

    # Convenience label for UI, e.g. "Dream Job: Data Analyst, 8–12 LPA"
    plan_title = db.Column(db.String(255), nullable=True)

    # JSON-encoded Dream Plan dictionary (usually the plan_view from dream/routes.py)
    plan_json = db.Column(db.Text, nullable=False)

    # Copy of meta.inputs_digest (if available) for tying to credit logs / coach sessions
    inputs_digest = db.Column(db.String(128), nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref(
            "dream_plan_snapshots", lazy=True, cascade="all, delete-orphan"
        ),
    )

    def __repr__(self):
        return (
            f"<DreamPlanSnapshot {self.id} u={self.user_id} "
            f"{self.path_type} created={self.created_at}>"
        )


# ---------------------------------------------------------------------
# Daily Action Coach — sessions & tasks (upgraded for P3 project system)
# ---------------------------------------------------------------------
class DailyCoachSession(db.Model):
    __tablename__ = "daily_coach_session"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # ✅ NEW: Monthly cycle tracking
    month_cycle_id = db.Column(
        db.String(50),
        nullable=True,
        index=True,
        comment="Format: 'user_{user_id}_path_{path_type}_month_{YYYY_MM}'"
    )

    # ✅ NEW: LPA target for this session
    target_lpa = db.Column(
        db.String(20),
        nullable=True,
        comment="Target salary from Dream Planner (12, 24, 48 LPA)"
    )

    # ✅ NEW: Weekly completion tracking
    daily_tasks_completed = db.Column(db.Integer, default=0, nullable=False)
    weekly_task_completed = db.Column(db.Boolean, default=False, nullable=False)

    # ✅ NEW: Progress percentage for UI
    progress_percent = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        comment="0-100, calculated from daily + weekly completion"
    )

    # "job" or "startup"
    path_type = db.Column(db.String(20), index=True, nullable=False, default="job")

    # Digest of Dream Planner used for this week
    plan_digest = db.Column(db.String(128), nullable=True, index=True)

    plan_title = db.Column(db.String(255), nullable=True)

    session_date = db.Column(db.Date, nullable=False, index=True)

    day_index = db.Column(
        db.Integer,
        nullable=True,
        comment="Week index (Weekly Coach) or Day index (daily mode)",
    )

    ai_note = db.Column(db.Text, nullable=True)
    reflection = db.Column(db.Text, nullable=True)

    is_closed = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user = db.relationship(
        "User",
        backref=db.backref(
            "daily_coach_sessions", lazy=True, cascade="all, delete-orphan"
        ),
    )

    # ✅ Upgrade-only: constraints/indexes (safe)
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "month_cycle_id",
            "day_index",
            name="uq_daily_coach_session_user_month_week",
        ),
        Index(
            "ix_daily_coach_session_user_month",
            "user_id",
            "month_cycle_id",
        ),
        Index(
            "ix_daily_coach_session_user_path_date",
            "user_id",
            "path_type",
            "session_date",
        ),
    )

    def __repr__(self):
        return f"<DailyCoachSession {self.id} u={self.user_id} {self.path_type} {self.session_date}>"


class DailyCoachTask(db.Model):
    __tablename__ = "daily_coach_task"

    id = db.Column(db.Integer, primary_key=True)

    session_id = db.Column(
        db.Integer,
        db.ForeignKey("daily_coach_session.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Core task fields
    title = db.Column(db.String(255), nullable=False)
    detail = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(64), nullable=True)
    sort_order = db.Column(db.Integer, nullable=True)
    is_done = db.Column(db.Boolean, nullable=False, default=False, index=True)

    # NEW → Project system integration
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("dream_plan_projects.id", ondelete="SET NULL"),
        nullable=True,
    )

    milestone_id = db.Column(
        db.Integer,
        db.ForeignKey("project_milestones.id", ondelete="SET NULL"),
        nullable=True,
    )

    subtask_id = db.Column(
        db.Integer,
        db.ForeignKey("project_subtasks.id", ondelete="SET NULL"),
        nullable=True,
        comment="Direct link to a specific project subtask",
    )

    milestone_title = db.Column(db.String(255), nullable=True)
    milestone_step = db.Column(db.String(255), nullable=True)

    # NEW → AI metadata
    estimated_minutes = db.Column(db.Integer, nullable=True)
    difficulty = db.Column(db.Integer, nullable=True)  # 1–5 scale
    tags = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # ✅ NEW: Dual-track fields
    task_type = db.Column(
        db.String(20),
        default='daily',
        nullable=False,
        index=True,
        comment="'daily' (maintenance) or 'weekly' (momentum)"
    )

    week_number = db.Column(
        db.Integer,
        nullable=True,
        comment="1-4 for the current month cycle"
    )

    day_number = db.Column(
        db.Integer,
        nullable=True,
        comment="1-28 for daily tasks, NULL for weekly tasks"
    )

    # ✅ NEW: Time estimation (for UI/UX)
    estimated_time_minutes = db.Column(
        db.Integer,
        default=10,
        nullable=False,
        comment="5-15 for daily, 180-300 for weekly"
    )

    # ✅ NEW: LPA alignment
    target_lpa_level = db.Column(
        db.String(20),
        nullable=True,
        comment="'12', '24', '48' - aligns task difficulty with target salary"
    )

    # ✅ NEW: Completion timestamp (for streak calculation)
    completed_at = db.Column(db.DateTime, nullable=True)

    # ✅ NEW: Badge/milestone for weekly tasks
    milestone_badge = db.Column(
        db.String(100),
        nullable=True,
        comment="Badge name for completing weekly task (e.g. 'Backend Basics')"
    )
    
    # ✅ NEW: Task category for Weekly Tasks
    task_category = Column(
        String(20),
        nullable=True,
        comment="'Learn', 'Build', 'Document' for weekly tasks"
    )
    
    # ✅ NEW: Tips field (expert advice for execution)
    tips = Column(
        Text,
        nullable=True,
        comment="Expert tips for completing this task (e.g., 'Use Redux Toolkit, not vanilla Redux')"
    )
    
    # ✅ NEW: Skill tags that will be learned from this task
    skill_tags = Column(
        JSON,
        nullable=True,
        comment="Array of skills this task teaches (for Profile sync)"
    )
    
    # ✅ NEW: Flag for profile sync eligibility
    sync_to_profile = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="If True, suggest adding skill_tags to profile when completed"
    )

    session = db.relationship(
        "DailyCoachSession",
        backref=db.backref(
            "tasks",
            lazy=True,
            cascade="all, delete-orphan",
            order_by="DailyCoachTask.sort_order",
        ),
    )

    # ✅ Upgrade-only: indexes to match your coach/routes queries
    __table_args__ = (
        Index("ix_daily_coach_task_session_type", "session_id", "task_type"),
        Index("ix_daily_coach_task_session_type_day", "session_id", "task_type", "day_number"),
        Index("ix_daily_coach_task_session_week", "session_id", "week_number"),
    )

    def __repr__(self):
        return f"<DailyCoachTask {self.id} s={self.session_id} done={self.is_done}>"


# ---------------------------------------------------------------------
# NEW: P3 Project System (Normalised, used by Dream Plan + Weekly Coach)
# ---------------------------------------------------------------------
class ProjectTemplate(db.Model):
    __tablename__ = "project_templates"

    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(120), nullable=False)  # e.g., "Game Developer"
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)

    difficulty = db.Column(db.String(50), nullable=True)  # Easy / Medium / Hard
    tags = db.Column(db.JSON, nullable=True)  # ["unity", "c#", "3d"]

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Normalized milestone structure
    milestones = db.relationship(
        "ProjectMilestone",
        backref="template",
        cascade="all, delete-orphan",
        order_by="ProjectMilestone.order",
    )


class ProjectMilestone(db.Model):
    __tablename__ = "project_milestones"

    id = db.Column(db.Integer, primary_key=True)

    project_template_id = db.Column(
        db.Integer,
        db.ForeignKey("project_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)

    order = db.Column(db.Integer, nullable=False)

    estimated_weeks = db.Column(
        db.Integer,
        nullable=True,
        comment="Optional rough estimate used for Week-mapping",
    )

    subtasks = db.relationship(
        "ProjectSubtask",
        backref="milestone",
        cascade="all, delete-orphan",
        order_by="ProjectSubtask.order",
    )


class ProjectSubtask(db.Model):
    __tablename__ = "project_subtasks"

    id = db.Column(db.Integer, primary_key=True)

    milestone_id = db.Column(
        db.Integer,
        db.ForeignKey("project_milestones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)

    difficulty = db.Column(db.Integer, nullable=True)  # 1–5
    minutes = db.Column(db.Integer, nullable=True)  # Estimated effort

    tags = db.Column(db.JSON, nullable=True)  # ["api", "sql"]

    order = db.Column(db.Integer, nullable=False)

    def __repr__(self):
        return f"<ProjectSubtask {self.id} m={self.milestone_id}>"


# ---------------------------------------------------------------------
# DreamPlanProject (user-selected project snapshot)
# ---------------------------------------------------------------------
class DreamPlanProject(db.Model):
    __tablename__ = "dream_plan_projects"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    path_type = db.Column(db.String(50), default="job")

    project_template_id = db.Column(
        db.Integer,
        db.ForeignKey("project_templates.id", ondelete="SET NULL"),
        nullable=True,
    )

    custom_title = db.Column(db.String(255), nullable=True)

    # Week window allocation from Dream Plan
    week_start = db.Column(db.Integer, nullable=True)
    week_end = db.Column(db.Integer, nullable=True)

    # Snapshot of milestone → subtask structure at selection time
    milestones = db.Column(db.JSON, nullable=True)

    project_template = db.relationship("ProjectTemplate")

    def __repr__(self):
        return f"<DreamPlanProject {self.id} u={self.user_id}>"


# ---------------------------------------------------------------------
# Links Weekly Coach session → selected project → milestone output
# ---------------------------------------------------------------------
class SessionProjectLink(db.Model):
    __tablename__ = "session_project_links"

    id = db.Column(db.Integer, primary_key=True)

    session_id = db.Column(
        db.Integer,
        db.ForeignKey("daily_coach_session.id", ondelete="CASCADE"),
        nullable=False,
    )

    dream_plan_project_id = db.Column(
        db.Integer,
        db.ForeignKey("dream_plan_projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    week_index = db.Column(
        db.Integer, nullable=False, comment="Coach week index (1–24)"
    )

    milestone_id = db.Column(
        db.Integer,
        db.ForeignKey("project_milestones.id", ondelete="SET NULL"),
        nullable=True,
    )

    milestone_title = db.Column(db.String(255), nullable=True)
    milestone_detail = db.Column(db.Text, nullable=True)

    is_completed = db.Column(db.Boolean, default=False)

    dream_plan_project = db.relationship("DreamPlanProject")

    def __repr__(self):
        return f"<SessionProjectLink s={self.session_id} p={self.dream_plan_project_id}>"


# ---------------------------------------------------------------------
# University Deals (B2B contracts / packs)
# ---------------------------------------------------------------------
class UniversityDeal(db.Model):
    """
    Represents a commercial agreement with a university.
    This is mostly for your internal admin panel:
    - how many Pro seats / credits were sold
    - what pricing / notes apply
    - when the deal is active
    """

    __tablename__ = "university_deal"

    id = db.Column(db.Integer, primary_key=True)

    university_id = db.Column(
        db.Integer,
        db.ForeignKey("university.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Short internal label, e.g. "Veltech 2025 Cohort"
    name = db.Column(db.String(200), nullable=False)

    # Seat-based logic: "we sold X Pro seats to this university"
    seats_total = db.Column(db.Integer, nullable=True)
    seats_used = db.Column(db.Integer, default=0, nullable=False)

    # Optional bulk credit packs (total across all students under this deal)
    silver_credits_total = db.Column(db.Integer, nullable=True)
    silver_credits_used = db.Column(db.Integer, default=0, nullable=False)
    gold_credits_total = db.Column(db.Integer, nullable=True)
    gold_credits_used = db.Column(db.Integer, default=0, nullable=False)

    # Commercial context (amounts are optional; billing is handled by Stripe)
    # Store price as integer cents to avoid floats, or keep null if not tracked.
    price_cents = db.Column(db.Integer, nullable=True)
    currency_code = db.Column(db.String(8), nullable=True, default="INR")

    # Optional: link to a Stripe product/price id if you want later.
    stripe_price_id = db.Column(db.String(128), nullable=True)

    # Deal lifetime
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    status = db.Column(
        db.String(32), default="active", nullable=False
    )  # "draft" | "active" | "paused" | "closed"

    # Who created this deal (super admin)
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    notes = db.Column(
        db.Text, nullable=True
    )  # free-form: "500 seats, 2-year contract, special pricing", etc.

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship(
        "User",
        backref=db.backref("created_deals", lazy=True),
        foreign_keys=[created_by_user_id],
    )

    def __repr__(self):
        return f"<UniversityDeal {self.id} uni={self.university_id} {self.name}>"


# ---------------------------------------------------------------------
# Vouchers / Promo Campaigns
# ---------------------------------------------------------------------
class VoucherCampaign(db.Model):
    """
    High-level definition of a voucher / promo code.
    - code: what students type (e.g. 'VELTECH50', 'LAKSHMAN20')
    - discount_percent: optional % off Pro pricing
    - bonus credits: optional Silver / Gold bonus on redemption
    - can be scoped to a single university or global
    """

    __tablename__ = "voucher_campaign"

    id = db.Column(db.Integer, primary_key=True)

    code = db.Column(db.String(64), nullable=False, unique=True, index=True)
    description = db.Column(db.String(255), nullable=True)

    # Optional discount off Pro subscription (0–100)
    discount_percent = db.Column(db.Integer, nullable=True)

    # Bonus credits granted when redeemed when redeemed (once per user)
    bonus_silver = db.Column(db.Integer, default=0, nullable=False)
    bonus_gold = db.Column(db.Integer, default=0, nullable=False)

    # Scope: optional university
    university_id = db.Column(
        db.Integer,
        db.ForeignKey("university.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # Lifetime & usage limits
    max_uses = db.Column(db.Integer, nullable=True)  # None = unlimited
    used_count = db.Column(db.Integer, default=0, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Who created this campaign
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship(
        "User",
        backref=db.backref("created_voucher_campaigns", lazy=True),
        foreign_keys=[created_by_user_id],
    )

    def __repr__(self):
        return f"<VoucherCampaign {self.id} code={self.code}>"


# ---------------------------------------------------------------------
# Voucher Redemptions (who used which voucher)
# ---------------------------------------------------------------------
class VoucherRedemption(db.Model):
    __tablename__ = "voucher_redemption"

    id = db.Column(db.Integer, primary_key=True)

    campaign_id = db.Column(
        db.Integer,
        db.ForeignKey("voucher_campaign.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    redeemed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Optional context: "applied on checkout", "manual admin grant", etc.
    context = db.Column(db.String(120), nullable=True)

    __table_args__ = (
        # Each user can redeem a given campaign only once
        UniqueConstraint(
            "campaign_id",
            "user_id",
            name="uq_voucher_campaign_user_single",
        ),
    )

    campaign = db.relationship(
        "VoucherCampaign",
        backref=db.backref("redemptions", lazy=True, cascade="all, delete-orphan"),
    )
    user = db.relationship(
        "User",
        backref=db.backref(
            "voucher_redemptions", lazy=True, cascade="all, delete-orphan"
        ),
    )

    def __repr__(self):
        return (
            f"<VoucherRedemption {self.id} campaign={self.campaign_id} "
            f"user={self.user_id}>"
        )


# ---------------------------------------------------------------------
# Admin Action Log (Phase 6 — Ultra Admin Audit)
# ---------------------------------------------------------------------
class AdminActionLog(db.Model):
    """
    Records every privileged admin action:
    - credit adjustments
    - role changes
    - voucher creation
    - deal creation
    - university edits
    - super-admin promotions/demotions
    """

    __tablename__ = "admin_action_log"

    id = db.Column(db.Integer, primary_key=True)

    # Who performed the action (super or ultra admin)
    performed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # Who the action targeted (student/admin/user)
    target_user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # Optional tenant scope (None = global)
    university_id = db.Column(
        db.Integer,
        db.ForeignKey("university.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # e.g. "credit_add", "credit_remove", "role_change",
    #      "voucher_create", "deal_create", "university_edit"
    action_type = db.Column(db.String(64), nullable=False)

    # Additional JSON context describing the action:
    # {"before": {...}, "after": {...}, "notes": "..."}
    meta_json = db.Column(db.JSON, nullable=True, default=dict)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    performed_by = db.relationship(
        "User",
        foreign_keys=[performed_by_user_id],
        backref=db.backref("admin_actions_performed", lazy=True),
    )

    target_user = db.relationship(
        "User",
        foreign_keys=[target_user_id],
        backref=db.backref("admin_actions_received", lazy=True),
    )

    university = db.relationship("University")

    def __repr__(self):
        return (
            f"<AdminActionLog {self.id} type={self.action_type} "
            f"by={self.performed_by_user_id} target={self.target_user_id}>"
        )


# ============================================
# NEW TABLE: LearningLog (DevLog)
# ============================================

class LearningLog(db.Model):
    """
    DevLog entries - Proof of Work when students complete Weekly Tasks.
    
    Phase 4 requirement: When user marks Weekly Task as done,
    they fill out "What I learned/built" → saved here.
    """
    __tablename__ = "learning_log"
    
    id = Column(Integer, primary_key=True)
    
    # Link to task
    task_id = Column(
        Integer,
        ForeignKey("daily_coach_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Link to user
    user_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Link to session (week)
    session_id = Column(
        Integer,
        ForeignKey("daily_coach_session.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    
    # DevLog content
    what_i_learned = Column(Text, nullable=True, comment="What did you learn?")
    what_i_built = Column(Text, nullable=True, comment="What did you build?")
    challenges_faced = Column(Text, nullable=True, comment="What challenges did you face?")
    next_steps = Column(Text, nullable=True, comment="What's next?")
    
    # Proof attachments (URLs or file paths)
    github_link = Column(String(500), nullable=True)
    demo_link = Column(String(500), nullable=True)
    screenshots = Column(JSON, nullable=True, comment="Array of screenshot URLs")
    
    # Metadata
    time_spent_minutes = Column(Integer, nullable=True, comment="How long did it take?")
    difficulty_rating = Column(Integer, nullable=True, comment="1-5 scale")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    
    # Relationships
    task = relationship(
        "DailyCoachTask",
        backref=db.backref("learning_logs", lazy=True, cascade="all, delete-orphan"),
    )
    
    user = relationship(
        "User",
        backref=db.backref("learning_logs", lazy=True, cascade="all, delete-orphan"),
    )
    
    session = relationship(
        "DailyCoachSession",
        backref=db.backref("learning_logs", lazy=True, cascade="all, delete-orphan"),
    )
    
    def __repr__(self):
        return f"<LearningLog {self.id} task={self.task_id} user={self.user_id}>"



# ============================================
# NEW TABLE: ProfileSkillSuggestion (Phase 5)
# ============================================

class ProfileSkillSuggestion(db.Model):
    """
    Phase 5: Track skill suggestions from completed projects.
    When user finishes a project, suggest adding new skills to profile.
    """
    __tablename__ = "profile_skill_suggestion"
    
    id = Column(Integer, primary_key=True)
    
    user_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Source context
    source_type = Column(
        String(50),
        nullable=False,
        comment="'coach_task', 'dream_project', 'learning_log'"
    )
    source_id = Column(Integer, nullable=True)  # ID of source object
    
    # Skill details
    skill_name = Column(String(100), nullable=False)
    skill_category = Column(String(50), nullable=True)  # "Backend", "Frontend", etc.
    proficiency_level = Column(String(20), nullable=True)  # "Beginner", "Intermediate", "Advanced"
    
    # Suggestion status
    status = Column(
        String(20),
        default='pending',
        nullable=False,
        comment="'pending', 'accepted', 'rejected', 'already_has'"
    )
    
    # Context
    context_note = Column(
        Text,
        nullable=True,
        comment="Why this skill is suggested (e.g., 'From completing E-commerce project')"
    )
    
    # Timestamps
    suggested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    responded_at = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship(
        "User",
        backref=db.backref("skill_suggestions", lazy=True, cascade="all, delete-orphan"),
    )
    
    def __repr__(self):
        return f"<ProfileSkillSuggestion {self.id} {self.skill_name} status={self.status}>"


# ============================================
# MIGRATION SQL
# ============================================

MIGRATION_SQL = """
-- Run these in Flask shell or create a migration file

-- 1. Create learning_log table
CREATE TABLE IF NOT EXISTS learning_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    session_id INTEGER,
    what_i_learned TEXT,
    what_i_built TEXT,
    challenges_faced TEXT,
    next_steps TEXT,
    github_link VARCHAR(500),
    demo_link VARCHAR(500),
    screenshots JSON,
    time_spent_minutes INTEGER,
    difficulty_rating INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES daily_coach_task(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES daily_coach_session(id) ON DELETE CASCADE
);

CREATE INDEX idx_learning_log_task ON learning_log(task_id);
CREATE INDEX idx_learning_log_user ON learning_log(user_id);
CREATE INDEX idx_learning_log_session ON learning_log(session_id);

-- 2. Add columns to daily_coach_task
ALTER TABLE daily_coach_task ADD COLUMN task_category VARCHAR(20);
ALTER TABLE daily_coach_task ADD COLUMN tips TEXT;
ALTER TABLE daily_coach_task ADD COLUMN skill_tags JSON;
ALTER TABLE daily_coach_task ADD COLUMN sync_to_profile BOOLEAN DEFAULT FALSE NOT NULL;

-- 3. Create profile_skill_suggestion table
CREATE TABLE IF NOT EXISTS profile_skill_suggestion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    source_type VARCHAR(50) NOT NULL,
    source_id INTEGER,
    skill_name VARCHAR(100) NOT NULL,
    skill_category VARCHAR(50),
    proficiency_level VARCHAR(20),
    status VARCHAR(20) DEFAULT 'pending' NOT NULL,
    context_note TEXT,
    suggested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    responded_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
);

-- 4. Create coach_saved_plan table
CREATE TABLE IF NOT EXISTS coach_saved_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    path_type VARCHAR(20) NOT NULL DEFAULT 'job',
    dream_snapshot_id INTEGER,
    title VARCHAR(255),
    plan_json TEXT NOT NULL DEFAULT '{}',
    locked_at TIMESTAMP,
    is_deleted BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE,
    FOREIGN KEY (dream_snapshot_id) REFERENCES dream_plan_snapshot(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_coach_saved_plan_user_active
ON coach_saved_plan(user_id, is_deleted);

CREATE INDEX IF NOT EXISTS ix_coach_saved_plan_user_path_active
ON coach_saved_plan(user_id, path_type, is_deleted);

CREATE UNIQUE INDEX IF NOT EXISTS uq_coach_saved_plan_user_snapshot
ON coach_saved_plan(user_id, dream_snapshot_id);

CREATE INDEX idx_skill_suggestion_user ON profile_skill_suggestion(user_id);
CREATE INDEX idx_skill_suggestion_status ON profile_skill_suggestion(status);
"""


# ============================================
# HELPER: Apply migration in Flask shell
# ============================================

def apply_sync_upgrade_migration():
    """
    Run this in Flask shell to add new tables and columns:
    
    >>> from models import db, apply_sync_upgrade_migration
    >>> apply_sync_upgrade_migration()
    """
    from sqlalchemy import text
    
    migrations = MIGRATION_SQL.strip().split(';')
    
    for sql in migrations:
        sql = sql.strip()
        if not sql:
            continue
        try:
            db.session.execute(text(sql))
            print(f"✓ {sql[:80]}...")
        except Exception as e:
            print(f"✗ {sql[:80]}... ERROR: {e}")
    
    db.session.commit()
    print("\n✅ Sync upgrade migration complete!")


# ============================================
# HELPER: Check migration status
# ============================================

def check_sync_upgrade_status():
    """
    Check which tables/columns exist.
    
    >>> from models import check_sync_upgrade_status
    >>> check_sync_upgrade_status()
    """
    from sqlalchemy import inspect
    
    inspector = inspect(db.engine)
    
    # Check tables
    tables = inspector.get_table_names()
    
    print("=== Sync Upgrade Migration Status ===\n")
    
    # LearningLog table
    if 'learning_log' in tables:
        print("✅ learning_log table exists")
    else:
        print("❌ learning_log table MISSING")
    
    # ProfileSkillSuggestion table
    if 'profile_skill_suggestion' in tables:
        print("✅ profile_skill_suggestion table exists")
    else:
        print("❌ profile_skill_suggestion table MISSING")
    
    # DailyCoachTask columns
    if 'daily_coach_task' in tables:
        task_cols = {col['name'] for col in inspector.get_columns('daily_coach_task')}
        new_cols = {'task_category', 'tips', 'skill_tags', 'sync_to_profile'}
        missing = new_cols - task_cols
        
        if missing:
            print(f"❌ daily_coach_task missing: {', '.join(missing)}")
        else:
            print("✅ daily_coach_task has all new columns")
    
    print("\n" + "="*40)
    
    
def cleanup_legacy_soft_deleted_coach_plans():
    from models import db, CoachSavedPlan, DreamPlanSnapshot
    import json
    from datetime import datetime

    rows = CoachSavedPlan.query.filter_by(is_deleted=True).all()
    stamped = 0
    deleted = 0

    for p in rows:
        snap_id = getattr(p, "dream_snapshot_id", None)
        if snap_id:
            snap = DreamPlanSnapshot.query.get(snap_id)
            if snap:
                try:
                    pj = json.loads(snap.plan_json or "{}")
                    if not pj.get("_coach_deleted_at"):
                        pj["_coach_deleted_at"] = datetime.utcnow().isoformat()
                        snap.plan_json = json.dumps(pj, ensure_ascii=False)
                        stamped += 1
                except Exception:
                    pass

        db.session.delete(p)
        deleted += 1

    db.session.commit()
    return {"soft_deleted_rows_removed": deleted, "snapshots_stamped": stamped}

    
# ---------------------------------------------------------------------
# NEW: Coach Saved Plans (Dream → Coach selectable library)
# ---------------------------------------------------------------------
class CoachSavedPlan(db.Model):
    __tablename__ = "coach_saved_plan"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    path_type = db.Column(db.String(20), nullable=False, index=True, default="job")

    dream_snapshot_id = db.Column(
        db.Integer,
        db.ForeignKey("dream_plan_snapshot.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title = db.Column(db.String(255), nullable=True)
    plan_json = db.Column(db.Text, nullable=False, default="{}")
    locked_at = db.Column(db.DateTime, nullable=True)

    # Legacy only (you hard-delete now)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref("coach_saved_plans", lazy=True, cascade="all, delete-orphan"),
    )

    dream_snapshot = db.relationship(
        "DreamPlanSnapshot",
        backref=db.backref("coach_saved_plans", lazy=True),
        foreign_keys=[dream_snapshot_id],
    )

    __table_args__ = (
        UniqueConstraint("user_id", "dream_snapshot_id", name="uq_coach_saved_plan_user_snapshot"),
        Index("ix_coach_saved_plan_user_path_created", "user_id", "path_type", "created_at"),
    )


    def __repr__(self):
        return f"<CoachSavedPlan {self.id} u={self.user_id} {self.path_type} deleted={self.is_deleted}>"
