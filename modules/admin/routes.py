# modules/admin/routes.py
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict
import json
import io
import csv

from flask import (
    Blueprint,
    render_template,
    request,
    flash,
    redirect,
    url_for,
    g,
    make_response,
)
from flask_login import current_user, login_required
from sqlalchemy import func

from models import (
    User,
    University,
    CreditTransaction,
    UniversityDeal,
    VoucherCampaign,
    VoucherRedemption,
    SkillMapSnapshot,
    JobPackReport,
    InternshipRecord,
    AdminActionLog,
    db,
)
from modules.credits import engine as credits_engine

admin_bp = Blueprint("admin", __name__, template_folder="../../templates/admin")

# ---------------------------------------------------------------------
# Helpers: admin / tenant checks
# ---------------------------------------------------------------------


def _is_ultra_admin() -> bool:
    """
    Ultra admin check:

    - If ULTRA_ADMIN_EMAILS env var is set (comma-separated), any user whose email
      is in that list is treated as ULTRA ADMIN.
    - Otherwise (or in addition), use:
        * role == "ultra_admin" OR is_ultra_admin bool field/property.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    # Env override (highest authority)
    emails = os.getenv("ULTRA_ADMIN_EMAILS", "")
    if emails:
        allowed = {e.strip().lower() for e in emails.split(",") if e.strip()}
        if (current_user.email or "").lower() in allowed:
            return True

    # Role-based ultra admin
    role = (getattr(current_user, "role", "") or "").lower()
    if role == "ultra_admin":
        return True

    return bool(getattr(current_user, "is_ultra_admin", False))


def _is_super_admin() -> bool:
    """
    Super admin check (NOT including ultra admins):

    - If ADMIN_EMAILS env var is set (comma-separated), any user whose email
      is in that list is treated as SUPER ADMIN.
    - Otherwise (or in addition), use:
        * role == "super_admin" OR is_super_admin bool field/property.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    # Env override
    emails = os.getenv("ADMIN_EMAILS", "")
    if emails:
        allowed = {e.strip().lower() for e in emails.split(",") if e.strip()}
        if (current_user.email or "").lower() in allowed:
            return True

    # Role-based super admin
    role = (getattr(current_user, "role", "") or "").lower()
    if role == "super_admin":
        return True

    return bool(getattr(current_user, "is_super_admin", False))


def _is_global_admin() -> bool:
    """
    Global admins = super_admin OR ultra_admin (or their env overrides).
    Used for platform-wide actions and cross-tenant access.
    """
    return _is_ultra_admin() or _is_super_admin()


def _is_admin_user() -> bool:
    """
    General admin check:

    - Global admins (super_admin + ultra_admin) are always admins.
    - University admins (role == "university_admin") are admins,
      but tenant-scoped.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    if _is_global_admin():
        return True

    role = (getattr(current_user, "role", "") or "").lower()
    if role == "university_admin":
        return True

    return bool(getattr(current_user, "is_university_admin", False))


def _check_tenant_scope_for_user(target: User | None) -> bool:
    """
    Ensure admin is operating within their tenant scope.

    - global_admin (super/ultra) → allowed everywhere.
    - university_admin → only allowed when:
        * current_tenant matches their university_id, AND
        * target user (if provided) also belongs to the same university.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    # Global admins can operate across tenants
    if _is_global_admin():
        return True

    # For non-global admins, enforce tenant scoping
    tenant = getattr(g, "current_tenant", None)
    if tenant is None:
        return False

    # Admin must belong to the current tenant
    if current_user.university_id != tenant.id:
        return False

    # If we have a specific target user, they must belong to same tenant
    if target is not None and target.university_id != tenant.id:
        return False

    return True


# ---------------------------------------------------------------------
# Helpers: Admin action logging
# ---------------------------------------------------------------------
def _log_admin_action(
    action_type: str,
    *,
    target_user: User | None = None,
    university: University | None = None,
    meta: Dict[str, Any] | None = None,
) -> None:
    """
    Centralized helper to append an AdminActionLog row.

    - performed_by_user_id: current_user.id (if authenticated)
    - target_user_id: optional, the user being modified/credited
    - university_id: explicit, or inferred from target_user, or g.current_tenant
    """
    if not getattr(current_user, "is_authenticated", False):
        return

    uni_id = None
    if university is not None:
        uni_id = university.id
    elif target_user is not None:
        uni_id = getattr(target_user, "university_id", None)
    else:
        tenant = getattr(g, "current_tenant", None)
        if tenant is not None:
            uni_id = tenant.id

    log = AdminActionLog(
        performed_by_user_id=current_user.id,
        target_user_id=target_user.id if target_user else None,
        university_id=uni_id,
        action_type=action_type,
        meta_json=meta or {},
    )
    db.session.add(log)


# ---------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------
@admin_bp.route("/", methods=["GET"], endpoint="dashboard")
@login_required
def dashboard():
    if not _is_admin_user():
        flash("You are not allowed to access the admin panel.", "danger")
        return redirect(url_for("dashboard"))

    tenant = getattr(g, "current_tenant", None)

    # Base queries
    user_q = User.query
    uni_q = University.query
    tx_q = CreditTransaction.query

    # Tenant scoping for non-global admins
    if not _is_global_admin():
        if tenant is not None:
            user_q = user_q.filter(User.university_id == tenant.id)
            tx_q = tx_q.filter(CreditTransaction.university_id == tenant.id)
            uni_q = uni_q.filter(University.id == tenant.id)
        else:
            # No tenant resolved -> show nothing for safety
            user_q = user_q.filter(User.id == -1)
            uni_q = uni_q.filter(University.id == -1)
            tx_q = tx_q.filter(CreditTransaction.id == -1)

    total_users = user_q.count()
    total_universities = uni_q.count()
    total_transactions = tx_q.count()

    # Simple credit stats
    debit_sum = (
        tx_q.filter(CreditTransaction.tx_type == "debit")
        .with_entities(func.coalesce(func.sum(CreditTransaction.amount), 0))
        .scalar()
        or 0
    )
    credit_sum = (
        tx_q.filter(CreditTransaction.tx_type.in_(["credit", "refund"]))
        .with_entities(func.coalesce(func.sum(CreditTransaction.amount), 0))
        .scalar()
        or 0
    )

    recent_txs = (
        tx_q.order_by(CreditTransaction.created_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "admin/dashboard.html",
        tenant=tenant,
        total_users=total_users,
        total_universities=total_universities,
        total_transactions=total_transactions,
        debit_sum=debit_sum,
        credit_sum=credit_sum,
    )


# ---------------------------------------------------------------------
# Admin · Users (search + edit)
# ---------------------------------------------------------------------
@admin_bp.route("/users", methods=["GET", "POST"], endpoint="users")
@login_required
def users():
    if not _is_admin_user():
        flash("You are not allowed to access admin users.", "danger")
        return redirect(url_for("dashboard"))

    actor_is_ultra = _is_ultra_admin()
    actor_is_global = _is_global_admin()

    # -------- POST: update a user (role / university) ----------
    if request.method == "POST":
        user_id_raw = request.form.get("user_id")
        new_role = (request.form.get("role") or "").strip()
        uni_id_raw = request.form.get("university_id") or ""

        try:
            user_id = int(user_id_raw)
        except (TypeError, ValueError):
            flash("Invalid user id.", "danger")
            return redirect(url_for("admin.users"))

        target = User.query.get(user_id)
        if not target:
            flash("User not found.", "danger")
            return redirect(url_for("admin.users"))

        # Tenant safety
        if not _check_tenant_scope_for_user(target):
            flash("You cannot modify users outside your tenant.", "danger")
            return redirect(url_for("admin.users"))

        target_role = (getattr(target, "role", "") or "").lower()
        old_role = target_role
        old_university_id = target.university_id

        # ----- Role rules -----
        allowed_roles = ["student", "university_admin", "super_admin"]
        if actor_is_ultra:
            allowed_roles.append("ultra_admin")

        if new_role not in allowed_roles:
            flash("Invalid role.", "danger")
            return redirect(url_for("admin.users"))

        # University admins cannot modify global admins at all
        if not actor_is_global and target_role in ("super_admin", "ultra_admin"):
            flash("You cannot modify a global admin account.", "danger")
            return redirect(url_for("admin.users"))

        # Super admins cannot touch ultra admins
        if not actor_is_ultra and target_role == "ultra_admin":
            flash("Only ultra admins can modify ultra admin accounts.", "danger")
            return redirect(url_for("admin.users"))

        # Only ultra admins can grant or revoke SUPER ADMIN role
        if (target_role == "super_admin" or new_role == "super_admin") and not actor_is_ultra:
            flash("Only ultra admins can assign or remove the super_admin role.", "danger")
            return redirect(url_for("admin.users"))

        # Only ultra admins can grant ULTRA ADMIN
        if new_role == "ultra_admin" and not actor_is_ultra:
            flash("Only ultra admins can assign the ultra_admin role.", "danger")
            return redirect(url_for("admin.users"))

        # Apply role
        target.role = new_role
        new_role_l = (new_role or "").lower()

        # ----- University assignment rules -----
        if actor_is_global:
            # Global admin can assign any university, or clear it
            if uni_id_raw.strip():
                try:
                    uni_id = int(uni_id_raw)
                except ValueError:
                    flash("Invalid university id.", "danger")
                    db.session.rollback()
                    return redirect(url_for("admin.users"))

                uni = University.query.get(uni_id)
                if not uni:
                    flash("University not found.", "danger")
                    db.session.rollback()
                    return redirect(url_for("admin.users"))

                target.university_id = uni.id
            else:
                target.university_id = None
        else:
            # University admins: force user into their own tenant (if any)
            tenant = getattr(g, "current_tenant", None)
            if tenant is not None:
                target.university_id = tenant.id

        # ----- Logging: role/university changes -----
        try:
            changes: Dict[str, Any] = {}

            # Role change
            if old_role != new_role_l:
                changes["role"] = {
                    "before": old_role,
                    "after": new_role_l,
                }

            # University change
            if old_university_id != target.university_id:
                changes["university_id"] = {
                    "before": old_university_id,
                    "after": target.university_id,
                }

            if changes:
                meta: Dict[str, Any] = {
                    "changes": changes,
                    "admin_email": current_user.email,
                    "target_email": target.email,
                }

                # Explicitly mark super/ultra admin promotions/demotions in meta
                if "role" in changes:
                    before = (changes["role"]["before"] or "").lower()
                    after = (changes["role"]["after"] or "").lower()

                    if "super_admin" in (before, after):
                        meta["super_admin_change"] = {"before": before, "after": after}

                    if "ultra_admin" in (before, after):
                        meta["ultra_admin_change"] = {"before": before, "after": after}

                _log_admin_action(
                    "user_update",
                    target_user=target,
                    meta=meta,
                )

            db.session.commit()
            flash("User updated.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Failed to update user: {e}", "danger")

        return redirect(url_for("admin.users"))

    # -------- GET: list/search users ----------
    tenant = getattr(g, "current_tenant", None)

    q = (request.args.get("q") or "").strip()
    role_filter = (request.args.get("role") or "").strip()

    user_q = User.query

    # Tenant scoping for non-global admins
    if not actor_is_global:
        if tenant is not None:
            user_q = user_q.filter(User.university_id == tenant.id)
        else:
            user_q = user_q.filter(User.id == -1)  # show none

    if q:
        like = f"%{q}%"
        user_q = user_q.filter(
            db.or_(
                User.email.ilike(like),
                User.name.ilike(like),
            )
        )

    if role_filter:
        user_q = user_q.filter(User.role == role_filter)

    users_list = user_q.order_by(User.created_at.desc()).limit(100).all()

    # Universities for dropdowns
    if actor_is_global:
        universities = University.query.order_by(University.name.asc()).all()
    else:
        # Tenant-scoped – only show own university in dropdown
        if tenant is not None:
            universities = [tenant]
        else:
            universities = []

    return render_template(
        "admin/users.html",
        users=users_list,
        universities=universities,
        q=q,
        role_filter=role_filter,
        is_super_admin=_is_super_admin(),
        is_ultra_admin=actor_is_ultra,
    )


# ---------------------------------------------------------------------
# Admin · User Pro / Verification actions
# ---------------------------------------------------------------------
@admin_bp.route("/users/<int:user_id>/grant_pro", methods=["POST"], endpoint="user_grant_pro")
@login_required
def user_grant_pro(user_id: int):
    if not _is_admin_user() or not _is_global_admin():
        flash("Only global admins can grant Pro.", "danger")
        return redirect(url_for("admin.users"))

    target = User.query.get_or_404(user_id)

    before_status = (target.subscription_status or "free").lower()
    before_balances = credits_engine.get_balances(target)

    try:
        # Mark as Pro
        target.subscription_status = "pro"
        if not target.pro_since:
            target.pro_since = datetime.utcnow()
        target.pro_cancel_at = None

        # Ensure at least Pro starting balances
        credits_engine.apply_starting_balances(target)

        after_balances = credits_engine.get_balances(target)

        _log_admin_action(
            "pro_grant",
            target_user=target,
            meta={
                "before_status": before_status,
                "after_status": "pro",
                "before_balances": before_balances,
                "after_balances": after_balances,
                "admin_email": current_user.email,
                "target_email": target.email,
                "notes": "Admin granted Pro status",
            },
        )

        db.session.commit()
        flash(f"Granted Pro to {target.email}.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Failed to grant Pro: {e}", "danger")

    return redirect(url_for("admin.users", q=target.email))


@admin_bp.route("/users/<int:user_id>/revoke_pro", methods=["POST"], endpoint="user_revoke_pro")
@login_required
def user_revoke_pro(user_id: int):
    if not _is_admin_user() or not _is_global_admin():
        flash("Only global admins can revoke Pro.", "danger")
        return redirect(url_for("admin.users"))

    target = User.query.get_or_404(user_id)

    before_status = (target.subscription_status or "free").lower()
    before_balances = credits_engine.get_balances(target)

    try:
        target.subscription_status = "canceled"
        target.pro_cancel_at = datetime.utcnow()

        after_balances = credits_engine.get_balances(target)

        _log_admin_action(
            "pro_revoke",
            target_user=target,
            meta={
                "before_status": before_status,
                "after_status": "canceled",
                "before_balances": before_balances,
                "after_balances": after_balances,
                "admin_email": current_user.email,
                "target_email": target.email,
                "notes": "Admin revoked Pro status",
            },
        )

        db.session.commit()
        flash(f"Revoked Pro from {target.email}.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Failed to revoke Pro: {e}", "danger")

    return redirect(url_for("admin.users", q=target.email))


@admin_bp.route("/users/<int:user_id>/verify", methods=["POST"], endpoint="user_verify")
@login_required
def user_verify(user_id: int):
    if not _is_admin_user() or not _is_global_admin():
        flash("Only global admins can verify accounts.", "danger")
        return redirect(url_for("admin.users"))

    target = User.query.get_or_404(user_id)

    before_verified = bool(target.verified)

    try:
        target.verified = True

        _log_admin_action(
            "verify_user",
            target_user=target,
            meta={
                "before_verified": before_verified,
                "after_verified": True,
                "admin_email": current_user.email,
                "target_email": target.email,
                "notes": "Admin marked user as verified",
            },
        )

        db.session.commit()
        flash(f"Marked {target.email} as verified.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Failed to verify user: {e}", "danger")

    return redirect(url_for("admin.users", q=target.email))


@admin_bp.route("/users/<int:user_id>/unverify", methods=["POST"], endpoint="user_unverify")
@login_required
def user_unverify(user_id: int):
    if not _is_admin_user() or not _is_global_admin():
        flash("Only global admins can unverify accounts.", "danger")
        return redirect(url_for("admin.users"))

    target = User.query.get_or_404(user_id)

    before_verified = bool(target.verified)

    try:
        target.verified = False

        _log_admin_action(
            "unverify_user",
            target_user=target,
            meta={
                "before_verified": before_verified,
                "after_verified": False,
                "admin_email": current_user.email,
                "target_email": target.email,
                "notes": "Admin marked user as unverified",
            },
        )

        db.session.commit()
        flash(f"Marked {target.email} as unverified.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Failed to unverify user: {e}", "danger")

    return redirect(url_for("admin.users", q=target.email))


# ---------------------------------------------------------------------
# Credits top-up
# ---------------------------------------------------------------------
@admin_bp.route("/credits", methods=["GET", "POST"], endpoint="credits")
@login_required
def credits():
    if not _is_admin_user():
        flash("You are not allowed to access the admin credits panel.", "danger")
        return redirect(url_for("dashboard"))

    target: User | None = None
    recent_txs = []

    # POST: add credits
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        amount = int(request.form.get("amount") or "0")
        currency = (request.form.get("currency") or "silver").lower()
        reason = (request.form.get("reason") or "admin_adjust").strip() or "admin_adjust"

        if not email or amount <= 0:
            flash("Please enter a valid email and positive amount.", "warning")
            return redirect(url_for("admin.credits"))

        target = User.query.filter_by(email=email).first()
        if not target:
            flash(f"No user found with email: {email}", "danger")
            return redirect(url_for("admin.credits"))

        # Tenant safety: non-global admins can only modify users in their own tenant
        if not _check_tenant_scope_for_user(target):
            flash(
                "You cannot modify credits for users outside your university.",
                "danger",
            )
            return redirect(url_for("dashboard"))

        try:
            # Use commit=False so we can atomically commit both credit + log
            if currency == "gold":
                credits_engine.add_pro(
                    target,
                    amount,
                    feature=reason,
                    run_id=None,
                    commit=False,
                )
            else:
                credits_engine.add_free(
                    target,
                    amount,
                    feature=reason,
                    run_id=None,
                    commit=False,
                )

            # Admin action log
            meta = {
                "admin_email": current_user.email,
                "target_email": target.email,
                "amount": amount,
                "currency": currency,
                "reason": reason,
            }
            _log_admin_action(
                "credit_adjust",
                target_user=target,
                meta=meta,
            )

            db.session.commit()
            flash(
                f"Added {amount} {'Gold ⭐' if currency == 'gold' else 'Silver 🪙'} to {email}.",
                "success",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Failed to add credits: {e}", "danger")

        return redirect(url_for("admin.credits") + f"?email={email}")

    # GET: view user + recent txs
    email = (request.args.get("email") or "").strip().lower()
    if email:
        target = User.query.filter_by(email=email).first()
        if target:
            # Tenant safety: non-global admins should not see other tenants' users
            if not _check_tenant_scope_for_user(target):
                flash(
                    "You cannot view credit history for users outside your university.",
                    "danger",
                )
                return redirect(url_for("dashboard"))

            recent_txs = (
                CreditTransaction.query.filter_by(user_id=target.id)
                .order_by(CreditTransaction.created_at.desc())
                .limit(20)
                .all()
            )

    return render_template(
        "admin/credits.html",
        target=target,
        recent_txs=recent_txs,
    )


# ---------------------------------------------------------------------
# Universities management (global admins only)
# ---------------------------------------------------------------------
@admin_bp.route("/universities", methods=["GET", "POST"], endpoint="universities")
@login_required
def universities():
    if not _is_admin_user() or not _is_global_admin():
        flash("Only global admins can manage universities.", "danger")
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        domain = (request.form.get("domain") or "").strip().lower() or None
        tenant_slug = (request.form.get("tenant_slug") or "").strip().lower() or None

        if not name:
            flash("University name is required.", "warning")
            return redirect(url_for("admin.universities"))

        # Very basic uniqueness check
        if domain and University.query.filter_by(domain=domain).first():
            flash("That domain is already in use.", "danger")
            return redirect(url_for("admin.universities"))

        if tenant_slug and University.query.filter_by(tenant_slug=tenant_slug).first():
            flash("That tenant slug is already in use.", "danger")
            return redirect(url_for("admin.universities"))

        uni = University(name=name, domain=domain, tenant_slug=tenant_slug)
        db.session.add(uni)

        try:
            # Log university creation
            meta = {
                "name": name,
                "domain": domain,
                "tenant_slug": tenant_slug,
                "admin_email": current_user.email,
            }
            _log_admin_action(
                "university_create",
                university=uni,
                meta=meta,
            )

            db.session.commit()
            flash("University created.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Failed to create university: {e}", "danger")

        return redirect(url_for("admin.universities"))

    # GET: list all universities
    universities_list = University.query.order_by(University.created_at.desc()).all()
    return render_template(
        "admin/universities.html",
        universities=universities_list,
    )


# ---------------------------------------------------------------------
# University Deals (global admins only)
# ---------------------------------------------------------------------
@admin_bp.route("/deals", methods=["GET", "POST"], endpoint="deals")
@login_required
def deals():
    if not _is_admin_user() or not _is_global_admin():
        flash("Only global admins can manage deals.", "danger")
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        uni_id_raw = request.form.get("university_id")
        name = (request.form.get("name") or "").strip()
        seats_total_raw = request.form.get("seats_total")
        silver_total_raw = request.form.get("silver_credits_total")
        gold_total_raw = request.form.get("gold_credits_total")
        price_cents_raw = request.form.get("price_cents")
        currency_code = (request.form.get("currency_code") or "INR").upper()
        status = (request.form.get("status") or "active").strip() or "active"
        notes = (request.form.get("notes") or "").strip()

        start_date_raw = request.form.get("start_date") or ""
        end_date_raw = request.form.get("end_date") or ""

        if not uni_id_raw or not name:
            flash("University and deal name are required.", "warning")
            return redirect(url_for("admin.deals"))

        try:
            university_id = int(uni_id_raw)
        except ValueError:
            flash("Invalid university id.", "danger")
            return redirect(url_for("admin.deals"))

        uni = University.query.get(university_id)
        if not uni:
            flash("University not found.", "danger")
            return redirect(url_for("admin.deals"))

        # Optional ints
        def _to_int(val: str | None) -> int | None:
            val = (val or "").strip()
            if not val:
                return None
            try:
                return int(val)
            except ValueError:
                return None

        seats_total = _to_int(seats_total_raw)
        silver_total = _to_int(silver_total_raw)
        gold_total = _to_int(gold_total_raw)
        price_cents = _to_int(price_cents_raw)

        def _parse_date(s: str | None):
            s = (s or "").strip()
            if not s:
                return None
            try:
                return datetime.strptime(s, "%Y-%m-%d").date()
            except ValueError:
                return None

        start_date = _parse_date(start_date_raw)
        end_date = _parse_date(end_date_raw)

        deal = UniversityDeal(
            university_id=university_id,
            name=name,
            seats_total=seats_total,
            seats_used=0,
            silver_credits_total=silver_total,
            silver_credits_used=0,
            gold_credits_total=gold_total,
            gold_credits_used=0,
            price_cents=price_cents,
            currency_code=currency_code,
            status=status,
            start_date=start_date,
            end_date=end_date,
            notes=notes,
            created_by_user_id=current_user.id,
        )
        db.session.add(deal)

        try:
            meta = {
                "university_id": university_id,
                "university_name": uni.name,
                "name": name,
                "seats_total": seats_total,
                "silver_credits_total": silver_total,
                "gold_credits_total": gold_total,
                "price_cents": price_cents,
                "currency_code": currency_code,
                "status": status,
                "admin_email": current_user.email,
            }
            _log_admin_action(
                "deal_create",
                university=uni,
                meta=meta,
            )

            db.session.commit()
            flash("Deal created.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Failed to create deal: {e}", "danger")

        return redirect(url_for("admin.deals"))

    # GET: list all deals
    deals_list = UniversityDeal.query.order_by(UniversityDeal.created_at.desc()).all()
    universities_list = University.query.order_by(University.name.asc()).all()

    return render_template(
        "admin/deals.html",
        deals=deals_list,
        universities=universities_list,
    )


# ---------------------------------------------------------------------
# Voucher Campaigns (global admins only)
# ---------------------------------------------------------------------
@admin_bp.route("/vouchers", methods=["GET", "POST"], endpoint="vouchers")
@login_required
def vouchers():
    if not _is_admin_user() or not _is_global_admin():
        flash("Only global admins can manage vouchers.", "danger")
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        code = (request.form.get("code") or "").strip().upper()
        description = (request.form.get("description") or "").strip()
        discount_raw = request.form.get("discount_percent")
        bonus_silver_raw = request.form.get("bonus_silver")
        bonus_gold_raw = request.form.get("bonus_gold")
        uni_id_raw = request.form.get("university_id")
        max_uses_raw = request.form.get("max_uses")
        expires_raw = request.form.get("expires_at")

        if not code:
            flash("Voucher code is required.", "warning")
            return redirect(url_for("admin.vouchers"))

        # Optional int helpers
        def _to_int_default(val: str | None, default: int = 0) -> int:
            val = (val or "").strip()
            if not val:
                return default
            try:
                return int(val)
            except ValueError:
                return default

        def _to_int_nullable(val: str | None) -> int | None:
            val = (val or "").strip()
            if not val:
                return None
            try:
                return int(val)
            except ValueError:
                return None

        discount_percent = _to_int_nullable(discount_raw)
        bonus_silver = _to_int_default(bonus_silver_raw, 0)
        bonus_gold = _to_int_default(bonus_gold_raw, 0)
        max_uses = _to_int_nullable(max_uses_raw)

        university_id = None
        uni = None
        if uni_id_raw:
            try:
                university_id = int(uni_id_raw)
            except ValueError:
                university_id = None

            if university_id:
                uni = University.query.get(university_id)

        expires_at = None
        expires_raw = (expires_raw or "").strip()
        if expires_raw:
            try:
                # HTML date input gives YYYY-MM-DD
                d = datetime.strptime(expires_raw, "%Y-%m-%d").date()
                expires_at = datetime(d.year, d.month, d.day, 23, 59, 59)
            except ValueError:
                expires_at = None

        # Ensure code uniqueness
        existing = (
            VoucherCampaign.query.filter(
                func.lower(VoucherCampaign.code) == code.lower()
            ).first()
        )
        if existing:
            flash("That voucher code already exists.", "danger")
            return redirect(url_for("admin.vouchers"))

        campaign = VoucherCampaign(
            code=code,
            description=description,
            discount_percent=discount_percent,
            bonus_silver=bonus_silver,
            bonus_gold=bonus_gold,
            university_id=university_id,
            max_uses=max_uses,
            used_count=0,
            expires_at=expires_at,
            is_active=True,
            created_by_user_id=current_user.id,
        )
        db.session.add(campaign)

        try:
            meta = {
                "code": code,
                "description": description,
                "discount_percent": discount_percent,
                "bonus_silver": bonus_silver,
                "bonus_gold": bonus_gold,
                "max_uses": max_uses,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "university_id": university_id,
                "university_name": uni.name if uni else None,
                "admin_email": current_user.email,
            }
            _log_admin_action(
                "voucher_create",
                university=uni,
                meta=meta,
            )

            db.session.commit()
            flash("Voucher campaign created.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Failed to create voucher: {e}", "danger")

        return redirect(url_for("admin.vouchers"))

    vouchers_list = VoucherCampaign.query.order_by(VoucherCampaign.created_at.desc()).all()
    universities_list = University.query.order_by(University.name.asc()).all()

    return render_template(
        "admin/vouchers.html",
        vouchers=vouchers_list,
        universities=universities_list,
    )


# ---------------------------------------------------------------------
# Voucher redemptions detail (global admins only)
# ---------------------------------------------------------------------
@admin_bp.route(
    "/vouchers/<int:campaign_id>",
    methods=["GET"],
    endpoint="voucher_detail",
)
@login_required
def voucher_detail(campaign_id: int):
    if not _is_admin_user() or not _is_global_admin():
        flash("Only global admins can view voucher redemptions.", "danger")
        return redirect(url_for("admin.dashboard"))

    campaign = VoucherCampaign.query.get_or_404(campaign_id)
    redemptions = (
        VoucherRedemption.query.filter_by(campaign_id=campaign.id)
        .order_by(VoucherRedemption.redeemed_at.desc())
        .all()
    )

    return render_template(
        "admin/voucher_detail.html",
        campaign=campaign,
        redemptions=redemptions,
    )


# ---------------------------------------------------------------------
# University Analytics (university admins ONLY)
# ---------------------------------------------------------------------
@admin_bp.route("/analytics", methods=["GET"], endpoint="analytics")
@login_required
def analytics():
    """
    Tenant-scoped analytics for UNIVERSITY ADMINS.
    - Only role=='university_admin' for their own tenant.
    - Global admins (super/ultra) are explicitly blocked from student-level analytics.
    """
    if not _is_admin_user():
        flash("You are not allowed to access analytics.", "danger")
        return redirect(url_for("dashboard"))

    role = (getattr(current_user, "role", "") or "").lower()

    if role != "university_admin":
        flash("Only university admins can view student analytics.", "danger")
        return redirect(url_for("admin.dashboard"))

    if _is_global_admin():
        # Safety: even if role mis-set, we block global admins from student analytics
        flash("Global admins cannot view student-level analytics.", "danger")
        return redirect(url_for("admin.dashboard"))

    tenant = getattr(g, "current_tenant", None)
    if tenant is None or current_user.university_id != tenant.id:
        flash("Analytics are only available when using your university tenant domain.", "danger")
        return redirect(url_for("admin.dashboard"))

    # ------------------------------------------------------------------
    # Basic per-university counts
    # ------------------------------------------------------------------
    user_q = User.query.filter(User.university_id == tenant.id)

    total_users = user_q.count()
    total_students = user_q.filter(User.role == "student").count()
    total_university_admins = user_q.filter(User.role == "university_admin").count()

    tx_q = CreditTransaction.query.filter(CreditTransaction.university_id == tenant.id)

    total_debits = (
        tx_q.filter(CreditTransaction.tx_type == "debit")
        .with_entities(func.coalesce(func.sum(CreditTransaction.amount), 0))
        .scalar()
        or 0
    )
    total_credits = (
        tx_q.filter(CreditTransaction.tx_type.in_(["credit", "refund"]))
        .with_entities(func.coalesce(func.sum(CreditTransaction.amount), 0))
        .scalar()
        or 0
    )

    # Admin-adjust transactions (likely coming from /admin/credits).
    admin_adjust_txs = (
        tx_q.filter(CreditTransaction.feature.ilike("admin%"))
        .order_by(CreditTransaction.created_at.desc())
        .limit(50)
        .all()
    )

    # ------------------------------------------------------------------
    # Skills (SkillMapSnapshot) – top skills for this university
    # ------------------------------------------------------------------
    skill_snapshots = (
        SkillMapSnapshot.query
        .join(User, SkillMapSnapshot.user_id == User.id)
        .filter(User.university_id == tenant.id)
        .order_by(SkillMapSnapshot.created_at.desc())
        .limit(300)
        .all()
    )

    skill_counts: dict[str, int] = {}

    for snap in skill_snapshots:
        if not snap.skills_json:
            continue
        try:
            payload = json.loads(snap.skills_json)
        except Exception:
            continue

        # Flexible: handle list or dict structures
        skills_list = []
        if isinstance(payload, dict):
            # e.g. {"skills": [...]}
            if isinstance(payload.get("skills"), list):
                skills_list = payload.get("skills") or []
        elif isinstance(payload, list):
            skills_list = payload

        for item in skills_list:
            name = None
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("name") or item.get("skill")

            if not name:
                continue

            name_norm = name.strip()
            if not name_norm:
                continue
            skill_counts[name_norm] = skill_counts.get(name_norm, 0) + 1

    skills_top = [
        {"name": name, "count": count}
        for name, count in sorted(
            skill_counts.items(), key=lambda kv: kv[1], reverse=True
        )[:12]
    ]

    # ------------------------------------------------------------------
    # Target roles (JobPackReport.job_title) – popularity
    # ------------------------------------------------------------------
    role_rows = (
        db.session.query(JobPackReport.job_title, func.count(JobPackReport.id))
        .join(User, JobPackReport.user_id == User.id)
        .filter(User.university_id == tenant.id)
        .filter(JobPackReport.job_title.isnot(None))
        .group_by(JobPackReport.job_title)
        .order_by(func.count(JobPackReport.id).desc())
        .limit(10)
        .all()
    )

    roles_top = [
        {"name": (row[0] or "").strip(), "count": int(row[1] or 0)}
        for row in role_rows
        if (row[0] or "").strip()
    ]

    # ------------------------------------------------------------------
    # Internship search roles (InternshipRecord.role)
    # ------------------------------------------------------------------
    internship_rows = (
        db.session.query(InternshipRecord.role, func.count(InternshipRecord.id))
        .join(User, InternshipRecord.user_id == User.id)
        .filter(User.university_id == tenant.id)
        .filter(InternshipRecord.role.isnot(None))
        .group_by(InternshipRecord.role)
        .order_by(func.count(InternshipRecord.id).desc())
        .limit(10)
        .all()
    )

    internship_roles = [
        {"name": (row[0] or "").strip(), "count": int(row[1] or 0)}
        for row in internship_rows
        if (row[0] or "").strip()
    ]

    # ------------------------------------------------------------------
    # Credit usage over time (daily silver/gold debits)
    # ------------------------------------------------------------------
    silver_daily_rows = (
        tx_q.filter(
            CreditTransaction.tx_type == "debit",
            CreditTransaction.currency == "silver",
        )
        .with_entities(
            func.date(CreditTransaction.created_at).label("day"),
            func.coalesce(func.sum(CreditTransaction.amount), 0),
        )
        .group_by("day")
        .order_by("day")
        .all()
    )

    gold_daily_rows = (
        tx_q.filter(
            CreditTransaction.tx_type == "debit",
            CreditTransaction.currency == "gold",
        )
        .with_entities(
            func.date(CreditTransaction.created_at).label("day"),
            func.coalesce(func.sum(CreditTransaction.amount), 0),
        )
        .group_by("day")
        .order_by("day")
        .all()
    )

    silver_map = {str(day): int(total or 0) for day, total in silver_daily_rows}
    gold_map = {str(day): int(total or 0) for day, total in gold_daily_rows}

    all_days = sorted(set(silver_map.keys()) | set(gold_map.keys()))
    daily_credits = {
        "labels": all_days,
        "silver": [silver_map.get(d, 0) for d in all_days],
        "gold": [gold_map.get(d, 0) for d in all_days],
    }

    # ------------------------------------------------------------------
    # Tool usage (by feature) – total debit amount
    # ------------------------------------------------------------------
    tool_rows = (
        tx_q.filter(CreditTransaction.tx_type == "debit")
        .with_entities(
            CreditTransaction.feature,
            func.coalesce(func.sum(CreditTransaction.amount), 0),
        )
        .group_by(CreditTransaction.feature)
        .order_by(func.coalesce(func.sum(CreditTransaction.amount), 0).desc())
        .limit(10)
        .all()
    )

    tool_debits = [
        {"feature": (row[0] or "unknown"), "amount": int(row[1] or 0)}
        for row in tool_rows
    ]

    # ------------------------------------------------------------------
    # Human-readable insights for the analytics page
    # ------------------------------------------------------------------
    insights: Dict[str, str] = {}

    # Headline
    if total_students > 0:
        insights["headline"] = (
            f"{total_students} students are active on CareerAI for {tenant.name if tenant else 'this university'}."
        )
    elif total_users > 0:
        insights["headline"] = (
            f"{total_users} users are registered for {tenant.name if tenant else 'this university'}, "
            "but student usage has not started yet."
        )
    else:
        insights["headline"] = (
            "No active students yet. Once students start using Skill Mapper and Job Packs, "
            "you'll see live analytics here."
        )

    # Skill summary
    if skills_top:
        top_skill = skills_top[0]
        insights["skill_summary"] = (
            f"Most frequently mapped skill is “{top_skill['name']}”, "
            f"appearing in about {top_skill['count']} recent Skill Mapper runs."
        )
    else:
        insights["skill_summary"] = (
            "No Skill Mapper data yet. Encourage students to run Skill Mapper so you can see "
            "which skills are trending in your university."
        )

    # Role summary (JobPack)
    if roles_top:
        top_role = roles_top[0]
        insights["role_summary"] = (
            f"Students are most frequently exploring the role “{top_role['name']}” using Job Packs."
        )
    elif internship_roles:
        top_intern = internship_roles[0]
        insights["role_summary"] = (
            f"Internship interest is highest for “{top_intern['name']}” roles."
        )
    else:
        insights["role_summary"] = (
            "No Job Pack or internship data yet. Once students paste job and internship descriptions, "
            "this section highlights their most popular target roles."
        )

    # Tool summary
    if tool_debits:
        top_tool = tool_debits[0]
        feature_name = top_tool["feature"] or "Unknown feature"
        insights["tool_summary"] = (
            f"The highest credit usage so far is for “{feature_name}”, "
            f"indicating strong engagement with that tool."
        )
    else:
        insights["tool_summary"] = (
            "No credit spend recorded yet. As students start using tools like Skill Mapper, Job Packs "
            "and Internship Finder, you'll see which ones they rely on most."
        )

    # Credits summary
    if all_days:
        total_silver_spent = sum(silver_map.values())
        total_gold_spent = sum(gold_map.values())
        days_count = len(all_days)
        avg_silver = round(total_silver_spent / days_count, 1)
        avg_gold = round(total_gold_spent / days_count, 1)

        parts = []
        parts.append(
            f"On average, students spend about {avg_silver} Silver 🪙 credits per active day"
            + (" and" if avg_gold > 0 else ".")
        )
        if avg_gold > 0:
            parts.append(f" {avg_gold} Gold ⭐ credits per active day.")
        insights["credits_summary"] = "".join(parts)
    else:
        insights["credits_summary"] = (
            "No historical credit usage yet. After a few days of activity, you'll see average Silver 🪙 "
            "and Gold ⭐ usage trends here."
        )

    analytics_insights = insights

    return render_template(
        "admin/analytics.html",
        tenant=tenant,
        total_users=total_users,
        total_students=total_students,
        total_university_admins=total_university_admins,
        total_debits=total_debits,
        total_credits=total_credits,
        admin_adjust_txs=admin_adjust_txs,
        # datasets for charts
        skills_top=skills_top,
        roles_top=roles_top,
        internship_roles=internship_roles,
        daily_credits=daily_credits,
        tool_debits=tool_debits,
        analytics_insights=analytics_insights,
    )


# ---------------------------------------------------------------------
# Analytics CSV export (university admins ONLY)
# ---------------------------------------------------------------------
@admin_bp.route("/analytics/export", methods=["GET"], endpoint="analytics_export")
@login_required
def analytics_export():
    """
    Export aggregated analytics as CSV for the current university.
    """
    if not _is_admin_user():
        flash("You are not allowed to export analytics.", "danger")
        return redirect(url_for("dashboard"))

    role = (getattr(current_user, "role", "") or "").lower()
    if role != "university_admin" or _is_global_admin():
        flash("Only tenant-scoped university admins can export analytics.", "danger")
        return redirect(url_for("admin.dashboard"))

    tenant = getattr(g, "current_tenant", None)
    if tenant is None or current_user.university_id != tenant.id:
        flash("Analytics export is only available within your university tenant.", "danger")
        return redirect(url_for("admin.dashboard"))

    # Re-use the same aggregates as in analytics(), but only the portions
    # that are most useful in CSV form.
    user_q = User.query.filter(User.university_id == tenant.id)
    tx_q = CreditTransaction.query.filter(CreditTransaction.university_id == tenant.id)

    total_users = user_q.count()
    total_students = user_q.filter(User.role == "student").count()
    total_university_admins = user_q.filter(User.role == "university_admin").count()

    total_debits = (
        tx_q.filter(CreditTransaction.tx_type == "debit")
        .with_entities(func.coalesce(func.sum(CreditTransaction.amount), 0))
        .scalar()
        or 0
    )
    total_credits = (
        tx_q.filter(CreditTransaction.tx_type.in_(["credit", "refund"]))
        .with_entities(func.coalesce(func.sum(CreditTransaction.amount), 0))
        .scalar()
        or 0
    )

    # Skills
    skill_snapshots = (
        SkillMapSnapshot.query
        .join(User, SkillMapSnapshot.user_id == User.id)
        .filter(User.university_id == tenant.id)
        .order_by(SkillMapSnapshot.created_at.desc())
        .limit(300)
        .all()
    )
    skill_counts: dict[str, int] = {}
    for snap in skill_snapshots:
        if not snap.skills_json:
            continue
        try:
            payload = json.loads(snap.skills_json)
        except Exception:
            continue
        skills_list = []
        if isinstance(payload, dict):
            if isinstance(payload.get("skills"), list):
                skills_list = payload.get("skills") or []
        elif isinstance(payload, list):
            skills_list = payload
        for item in skills_list:
            name = None
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("name") or item.get("skill")
            if not name:
                continue
            name_norm = name.strip()
            if not name_norm:
                continue
            skill_counts[name_norm] = skill_counts.get(name_norm, 0) + 1

    # Roles from JobPack
    role_rows = (
        db.session.query(JobPackReport.job_title, func.count(JobPackReport.id))
        .join(User, JobPackReport.user_id == User.id)
        .filter(User.university_id == tenant.id)
        .filter(JobPackReport.job_title.isnot(None))
        .group_by(JobPackReport.job_title)
        .order_by(func.count(JobPackReport.id).desc())
        .limit(50)
        .all()
    )

    # Internship roles
    internship_rows = (
        db.session.query(InternshipRecord.role, func.count(InternshipRecord.id))
        .join(User, InternshipRecord.user_id == User.id)
        .filter(User.university_id == tenant.id)
        .filter(InternshipRecord.role.isnot(None))
        .group_by(InternshipRecord.role)
        .order_by(func.count(InternshipRecord.id).desc())
        .limit(50)
        .all()
    )

    # Tool debits
    tool_rows = (
        tx_q.filter(CreditTransaction.tx_type == "debit")
        .with_entities(
            CreditTransaction.feature,
            func.coalesce(func.sum(CreditTransaction.amount), 0),
        )
        .group_by(CreditTransaction.feature)
        .order_by(func.coalesce(func.sum(CreditTransaction.amount), 0).desc())
        .limit(50)
        .all()
    )

    # Daily credits
    silver_daily_rows = (
        tx_q.filter(
            CreditTransaction.tx_type == "debit",
            CreditTransaction.currency == "silver",
        )
        .with_entities(
            func.date(CreditTransaction.created_at).label("day"),
            func.coalesce(func.sum(CreditTransaction.amount), 0),
        )
        .group_by("day")
        .order_by("day")
        .all()
    )
    gold_daily_rows = (
        tx_q.filter(
            CreditTransaction.tx_type == "debit",
            CreditTransaction.currency == "gold",
        )
        .with_entities(
            func.date(CreditTransaction.created_at).label("day"),
            func.coalesce(func.sum(CreditTransaction.amount), 0),
        )
        .group_by("day")
        .order_by("day")
        .all()
    )
    silver_map = {str(day): int(total or 0) for day, total in silver_daily_rows}
    gold_map = {str(day): int(total or 0) for day, total in gold_daily_rows}
    all_days = sorted(set(silver_map.keys()) | set(gold_map.keys()))

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["section", "metric", "value_1", "value_2"])

    # Summary
    writer.writerow(["summary", "total_users", total_users, ""])
    writer.writerow(["summary", "total_students", total_students, ""])
    writer.writerow(["summary", "total_university_admins", total_university_admins, ""])
    writer.writerow(["summary", "total_debits", total_debits, ""])
    writer.writerow(["summary", "total_credits", total_credits, ""])

    # Skills
    for name, count in sorted(skill_counts.items(), key=lambda kv: kv[1], reverse=True):
        writer.writerow(["skill", name, count, ""])

    # JobPack roles
    for title, n in role_rows:
        title_clean = (title or "").strip()
        if not title_clean:
            continue
        writer.writerow(["jobpack_role", title_clean, int(n or 0), ""])

    # Internship roles
    for role_name, n in internship_rows:
        role_clean = (role_name or "").strip()
        if not role_clean:
            continue
        writer.writerow(["internship_role", role_clean, int(n or 0), ""])

    # Tools
    for feature, amt in tool_rows:
        writer.writerow(["tool", feature or "unknown", int(amt or 0), ""])

    # Daily credits
    for day in all_days:
        writer.writerow([
            "daily_credits",
            day,
            silver_map.get(day, 0),
            gold_map.get(day, 0),
        ])

    csv_data = output.getvalue()
    output.close()

    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f"attachment; filename=careerai_analytics_{tenant.id}.csv"
    return resp


# ---------------------------------------------------------------------
# Ultra Admin Audit (global actions overview)
# ---------------------------------------------------------------------
@admin_bp.route("/audit", methods=["GET"], endpoint="audit")
@login_required
def audit():
    """
    Ultra admin-only audit page.

    Shows:
    - Super admins list
    - Vouchers created (with creator)
    - Deals created (with creator)
    - Recent admin-related credit transactions (any tenant)
    - AdminActionLog entries with basic filters
    """
    if not _is_ultra_admin():
        flash("Only ultra admins can view the audit log.", "danger")
        return redirect(url_for("admin.dashboard"))

    # Quick overviews
    super_admins = (
        User.query.filter(User.role == "super_admin")
        .order_by(User.created_at.asc())
        .all()
    )

    vouchers = (
        VoucherCampaign.query
        .order_by(VoucherCampaign.created_at.desc())
        .limit(100)
        .all()
    )

    deals = (
        UniversityDeal.query
        .order_by(UniversityDeal.created_at.desc())
        .limit(100)
        .all()
    )

    admin_credits = (
        CreditTransaction.query
        .filter(CreditTransaction.feature.ilike("admin%"))
        .order_by(CreditTransaction.created_at.desc())
        .limit(100)
        .all()
    )

    # ------------------------------------------------------------------
    # AdminActionLog – filterable list
    # ------------------------------------------------------------------
    action_type_filter = (request.args.get("type") or "").strip()
    admin_email_filter = (request.args.get("admin_email") or "").strip().lower()
    target_email_filter = (request.args.get("target_email") or "").strip().lower()

    # Distinct action types for dropdown
    action_type_rows = (
        db.session.query(AdminActionLog.action_type)
        .distinct()
        .order_by(AdminActionLog.action_type.asc())
        .all()
    )
    action_types = [row[0] for row in action_type_rows if row[0]]

    # Get recent logs (limit N, then filter in Python for simplicity)
    raw_logs = (
        AdminActionLog.query
        .order_by(AdminActionLog.created_at.desc())
        .limit(250)
        .all()
    )

    filtered_logs = []
    for log in raw_logs:
        # Filter by action type
        if action_type_filter and log.action_type != action_type_filter:
            continue

        # Filter by admin email (performed_by)
        if admin_email_filter:
            if not log.performed_by or not log.performed_by.email:
                continue
            if admin_email_filter not in (log.performed_by.email or "").lower():
                continue

        # Filter by target email
        if target_email_filter:
            if not log.target_user or not log.target_user.email:
                continue
            if target_email_filter not in (log.target_user.email or "").lower():
                continue

        filtered_logs.append(log)

    return render_template(
        "admin/audit.html",
        super_admins=super_admins,
        vouchers=vouchers,
        deals=deals,
        admin_credits=admin_credits,
        admin_logs=filtered_logs,
        action_types=action_types,
        action_type_filter=action_type_filter,
        admin_email_filter=admin_email_filter,
        target_email_filter=target_email_filter,
    )


# ---------------------------------------------------------------------
# Ultra Admin Audit CSV export
# ---------------------------------------------------------------------
@admin_bp.route("/audit/export", methods=["GET"], endpoint="audit_export")
@login_required
def audit_export():
    """
    Export AdminActionLog entries as CSV (ultra admin only).
    """
    if not _is_ultra_admin():
        flash("Only ultra admins can export the audit log.", "danger")
        return redirect(url_for("admin.dashboard"))

    action_type_filter = (request.args.get("type") or "").strip()
    admin_email_filter = (request.args.get("admin_email") or "").strip().lower()
    target_email_filter = (request.args.get("target_email") or "").strip().lower()

    q = AdminActionLog.query.order_by(AdminActionLog.created_at.desc())

    # Optional filter by action_type via SQL
    if action_type_filter:
        q = q.filter(AdminActionLog.action_type == action_type_filter)

    logs = q.limit(1000).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "timestamp",
            "action_type",
            "admin_email",
            "target_email",
            "university_id",
            "university_name",
            "meta_json",
        ]
    )

    for log in logs:
        # Filter by admin email
        if admin_email_filter:
            admin_email_val = (log.performed_by.email if log.performed_by and log.performed_by.email else "").lower()
            if admin_email_filter not in admin_email_val:
                continue
        admin_email = log.performed_by.email if log.performed_by else ""

        # Filter by target email
        if target_email_filter:
            target_email_val = (log.target_user.email if log.target_user and log.target_user.email else "").lower()
            if target_email_filter not in target_email_val:
                continue
        target_email = log.target_user.email if log.target_user else ""

        uni_id = log.university.id if log.university else ""
        uni_name = log.university.name if log.university else ""

        meta_str = ""
        if log.meta_json:
            try:
                meta_str = json.dumps(log.meta_json, ensure_ascii=False)
            except Exception:
                meta_str = str(log.meta_json)

        writer.writerow(
            [
                log.created_at.isoformat() if log.created_at else "",
                log.action_type,
                admin_email,
                target_email,
                uni_id,
                uni_name,
                meta_str,
            ]
        )

    csv_data = output.getvalue()
    output.close()

    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=careerai_admin_audit.csv"
    return resp
