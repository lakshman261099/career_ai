from datetime import date, datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

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

    # Credits
    coins_free = db.Column(db.Integer, default=10, nullable=False)  # Silver 🪙
    coins_pro = db.Column(db.Integer, default=0, nullable=False)  # Gold ⭐

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
# Credit Transactions (Phase 4 — logging & transparency)
# ---------------------------------------------------------------------
class CreditTransaction(db.Model):
    __tablename__ = "credit_transaction"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    university_id = db.Column(
        db.Integer,
        db.ForeignKey("university.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # e.g. "jobpack_free", "skill_mapper_pro", "admin_add", "pro_initial_minimum"
    feature = db.Column(db.String(64), nullable=False)

    # positive integer amount
    amount = db.Column(db.Integer, nullable=False)

    # "silver" | "gold"
    currency = db.Column(db.String(16), nullable=False)

    # "debit" | "credit" | "refund"
    tx_type = db.Column(db.String(16), nullable=False)

    # optional link back to a run/report id (stored as text for flexibility)
    run_id = db.Column(db.String(64), nullable=True)

    before_balance = db.Column(db.Integer, nullable=False)
    after_balance = db.Column(db.Integer, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref(
            "credit_transactions", lazy=True, cascade="all, delete-orphan"
        ),
    )
    university = db.relationship("University")

    def __repr__(self):
        return (
            f"<CreditTransaction {self.id} u={self.user_id} "
            f"{self.currency} {self.tx_type} {self.amount}>"
        )


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
