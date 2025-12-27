# modules/admin/routes.py
from __future__ import annotations

import os
import json
import io
import csv
import zipfile
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import Any, Dict

from flask import (
    Blueprint,
    render_template,
    request,
    flash,
    redirect,
    url_for,
    g,
    make_response,
    send_file,
)
from flask_login import current_user, login_required
from sqlalchemy import func, and_

from models import (
    User,
    University,
    UniversityWallet,
    CreditTransaction,
    UniversityDeal,
    VoucherCampaign,
    VoucherRedemption,
    SkillMapSnapshot,
    JobPackReport,
    InternshipRecord,
    AdminActionLog,
    Project,
    PortfolioPage,
    LearningLog,
    UserProfile,
    DailyCoachTask,
    db,
)

from modules.credits import engine as credits_engine

admin_bp = Blueprint("admin", __name__, template_folder="../../templates/admin")

try:
    from models import ResumeAsset
except Exception:
    ResumeAsset = None


# ---------------------------------------------------------------------
# Helpers: boolean/callable-safe flags
# ---------------------------------------------------------------------
def _bool_attr(obj, name: str, default: bool = False) -> bool:
    """
    Safe attribute accessor where an attribute may be:
      - bool field/property
      - method returning bool
    """
    try:
        val = getattr(obj, name, default)
    except Exception:
        return default

    try:
        if callable(val):
            return bool(val())
        return bool(val)
    except Exception:
        return default


def _role_lower() -> str:
    try:
        return (getattr(current_user, "role", "") or "").strip().lower()
    except Exception:
        return ""


# ---------------------------------------------------------------------
# Helpers: admin / tenant checks
# ---------------------------------------------------------------------
def _is_ultra_admin() -> bool:
    """
    Ultra admin check:

    - If ULTRA_ADMIN_EMAILS env var is set (comma-separated), any user whose email
      is in that list is treated as ULTRA ADMIN.
    - Otherwise (or in addition), use:
        * role == "ultra_admin" OR is_ultra_admin bool field/property/method.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    emails = os.getenv("ULTRA_ADMIN_EMAILS", "")
    if emails:
        allowed = {e.strip().lower() for e in emails.split(",") if e.strip()}
        if (current_user.email or "").lower() in allowed:
            return True

    role = _role_lower()
    if role == "ultra_admin":
        return True

    return _bool_attr(current_user, "is_ultra_admin", False)


def _is_super_admin() -> bool:
    """
    Super admin check (NOT including ultra admins):

    - If ADMIN_EMAILS env var is set (comma-separated), any user whose email
      is in that list is treated as SUPER ADMIN.
    - Otherwise (or in addition), use:
        * role == "super_admin" OR is_super_admin bool field/property/method.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    emails = os.getenv("ADMIN_EMAILS", "")
    if emails:
        allowed = {e.strip().lower() for e in emails.split(",") if e.strip()}
        if (current_user.email or "").lower() in allowed:
            return True

    role = _role_lower()
    if role == "super_admin":
        return True

    return _bool_attr(current_user, "is_super_admin", False)


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

    role = _role_lower()
    if role == "university_admin":
        return True

    return _bool_attr(current_user, "is_university_admin", False)


def _effective_tenant_for_admin() -> University | None:
    """
    IMPORTANT FIX:
    - If g.current_tenant is missing (localhost / main domain), university_admins
      should STILL be able to see their own university data (scoped by their
      assigned university_id).
    - Global admins can operate without tenant.
    """
    tenant = getattr(g, "current_tenant", None)
    if tenant is not None:
        return tenant

    role = _role_lower()
    uni_id = getattr(current_user, "university_id", None)

    is_uni_admin = (role == "university_admin") or _bool_attr(current_user, "is_university_admin", False)
    if is_uni_admin and uni_id:
        try:
            return University.query.get(int(uni_id))
        except Exception:
            return None

    return None


def _check_tenant_scope_for_user(target: User | None) -> bool:
    """
    Ensure admin is operating within their tenant scope.

    - global_admin (super/ultra) → allowed everywhere.
    - university_admin → only allowed when:
        * effective tenant matches their university_id, AND
        * target user (if provided) also belongs to the same university.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    if _is_global_admin():
        return True

    tenant = _effective_tenant_for_admin()
    if tenant is None:
        return False

    if getattr(current_user, "university_id", None) != tenant.id:
        return False

    if target is not None and getattr(target, "university_id", None) != tenant.id:
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
    """
    if not getattr(current_user, "is_authenticated", False):
        return

    uni_id = None
    if university is not None:
        uni_id = university.id
    elif target_user is not None:
        uni_id = getattr(target_user, "university_id", None)
    else:
        tenant = _effective_tenant_for_admin()
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
# Analytics helpers
# ---------------------------------------------------------------------
def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def _parse_yyyy_mm_dd(s: str | None):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None


def _score_tier(score: int) -> str:
    if score >= 80:
        return "Top Tier (80+)"
    if score >= 60:
        return "Job Ready (60–79)"
    if score >= 40:
        return "Building (40–59)"
    return "Getting Started (0–39)"


def _norm_skill_name(name: str | None) -> str | None:
    if not name:
        return None
    s = str(name).strip()
    if not s:
        return None
    return " ".join(s.split())


def _norm_skill_key(name: str | None) -> str | None:
    nm = _norm_skill_name(name)
    if not nm:
        return None
    return nm.lower()


def _extract_skills_from_any(obj) -> list[str]:
    out: list[str] = []

    def add(v):
        if v is None:
            return
        if isinstance(v, str):
            nm = _norm_skill_name(v)
            if nm:
                out.append(nm)
        elif isinstance(v, dict):
            nm = _norm_skill_name(v.get("name") or v.get("skill") or v.get("title"))
            if nm:
                out.append(nm)
            for k in ("skills", "top_skills", "core_skills", "missing_skills", "skill_gaps"):
                if k in v:
                    add(v.get(k))
        elif isinstance(v, list):
            for it in v:
                add(it)

    add(obj)
    return out


def _extract_skills_from_skillmap_payload(payload):
    skills: list[str] = []

    if isinstance(payload, list):
        return _extract_skills_from_any(payload)

    if not isinstance(payload, dict):
        return skills

    if isinstance(payload.get("skills"), list):
        skills.extend(_extract_skills_from_any(payload.get("skills")))

    for key in ("roles", "top_roles", "role_roadmap", "role_cards", "primary_roles"):
        if isinstance(payload.get(key), list):
            for role in payload.get(key) or []:
                if not isinstance(role, dict):
                    continue
                for rk in (
                    "skills",
                    "top_skills",
                    "core_skills",
                    "missing_skills",
                    "skill_gaps",
                    "skills_to_learn",
                    "recommended_skills",
                    "must_have_skills",
                    "nice_to_have_skills",
                ):
                    if rk in role:
                        skills.extend(_extract_skills_from_any(role.get(rk)))
                for nested_key in ("gap_analysis", "analysis", "roadmap", "plan", "requirements"):
                    nv = role.get(nested_key)
                    if nv:
                        skills.extend(_extract_skills_from_any(nv))

    if isinstance(payload.get("learning_paths"), list):
        for lp in payload.get("learning_paths") or []:
            skills.extend(_extract_skills_from_any(lp))

    if isinstance(payload.get("next_steps"), list):
        skills.extend(_extract_skills_from_any(payload.get("next_steps")))

    return skills

# ---------------------------------------------------------------------
# Analytics helpers: filter parsing (shared by /analytics + /analytics/export)
# ---------------------------------------------------------------------
def _analytics_params_from_request():
    q = (request.args.get("q") or "").strip()

    only_verified = (request.args.get("verified") or "").strip().lower() in ("1", "true", "yes", "on")
    only_pro = (request.args.get("pro") or "").strip().lower() in ("1", "true", "yes", "on")

    min_ready = _safe_int(request.args.get("min_ready"), 0)
    max_ready = _safe_int(request.args.get("max_ready"), 100)

    start_dt = _parse_yyyy_mm_dd(request.args.get("start"))
    end_dt = _parse_yyyy_mm_dd(request.args.get("end"))
    if end_dt:
        end_dt = end_dt.replace(hour=23, minute=59, second=59)

    return q, only_verified, only_pro, min_ready, max_ready, start_dt, end_dt


def _apply_user_filters(user_q, q: str, only_verified: bool, only_pro: bool, min_ready: int, max_ready: int):
    if q:
        like = f"%{q}%"
        user_q = user_q.filter(db.or_(User.email.ilike(like), User.name.ilike(like)))

    if only_verified:
        user_q = user_q.filter(User.verified.is_(True))

    if only_pro:
        user_q = user_q.filter(func.lower(User.subscription_status) == "pro")

    user_q = user_q.filter(User.ready_score >= min_ready, User.ready_score <= max_ready)
    return user_q


def _apply_date_filter(qry, col, start_dt, end_dt):
    if start_dt:
        qry = qry.filter(col >= start_dt)
    if end_dt:
        qry = qry.filter(col <= end_dt)
    return qry

# -----------------------------
# Dean-friendly extraction helpers (single source)
# -----------------------------
MISSING_SKILL_KEYS = {
    "missing_skills",
    "skill_gaps",
    "skills_to_learn",
    "must_have_skills",
    "missing",
}


def _collect_skill_names(v) -> list[str]:
    out: list[str] = []

    def add(x):
        if x is None:
            return
        if isinstance(x, str):
            s = _norm_skill_name(x)
            if s:
                out.append(s)
        elif isinstance(x, dict):
            nm = _norm_skill_name(x.get("name") or x.get("skill") or x.get("title"))
            if nm:
                out.append(nm)
            for _, vv in x.items():
                add(vv)
        elif isinstance(x, list):
            for it in x:
                add(it)

    add(v)
    return out


def _extract_missing_skills(payload) -> list[str]:
    skills: list[str] = []

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in MISSING_SKILL_KEYS:
                    skills.extend(_collect_skill_names(v))
                walk(v)
        elif isinstance(obj, list):
            for it in obj:
                walk(it)

    walk(payload)
    return skills


def _extract_resume_issues(jobpack_payload: dict) -> dict[str, list[str]]:
    ra = (jobpack_payload or {}).get("resume_ats") or {}
    blockers = ra.get("blockers") or []
    warnings = ra.get("warnings") or []
    kc = ra.get("keyword_coverage") or {}
    missing_kw = kc.get("missing_keywords") or []

    def norm_list(xs):
        out = []
        for x in xs or []:
            if isinstance(x, str) and x.strip():
                out.append(" ".join(x.split()))
        return out

    return {
        "blockers": norm_list(blockers),
        "warnings": norm_list(warnings),
        "missing_keywords": norm_list(missing_kw),
    }


# ---------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------
@admin_bp.route("/", methods=["GET"], endpoint="dashboard")
@login_required
def dashboard():
    if not _is_admin_user():
        flash("You are not allowed to access the admin panel.", "danger")
        return redirect(url_for("dashboard"))

    # ✅ University admins should not land on “credits/admin ops” style dashboard
    role = _role_lower()
    is_uni_admin = (role == "university_admin") or _bool_attr(current_user, "is_university_admin", False)
    if is_uni_admin and not _is_global_admin():
        return redirect(url_for("admin.strategy"))

    tenant = _effective_tenant_for_admin()

    user_q = User.query
    uni_q = University.query
    tx_q = CreditTransaction.query

    if not _is_global_admin():
        if tenant is not None:
            user_q = user_q.filter(User.university_id == tenant.id)
            tx_q = tx_q.join(User, CreditTransaction.user_id == User.id).filter(User.university_id == tenant.id)
            uni_q = uni_q.filter(University.id == tenant.id)
        else:
            user_q = user_q.filter(User.id == -1)
            uni_q = uni_q.filter(University.id == -1)
            tx_q = tx_q.filter(CreditTransaction.id == -1)

    total_users = user_q.count()
    total_universities = uni_q.count()
    total_transactions = tx_q.count()

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

    recent_txs = tx_q.order_by(CreditTransaction.created_at.desc()).limit(10).all()

    return render_template(
        "admin/dashboard.html",
        tenant=tenant,
        total_users=total_users,
        total_universities=total_universities,
        total_transactions=total_transactions,
        debit_sum=debit_sum,
        credit_sum=credit_sum,
        recent_txs=recent_txs,
    )


# ---------------------------------------------------------------------
# Admin · Users (search + edit + university admin student management)
# ---------------------------------------------------------------------
@admin_bp.route("/users", methods=["GET", "POST"], endpoint="users")
@login_required
def users():
    if not _is_admin_user():
        flash("You are not allowed to access admin users.", "danger")
        return redirect(url_for("dashboard"))

    import csv
    import io
    import secrets

    actor_is_ultra = _is_ultra_admin()
    actor_is_global = _is_global_admin()
    tenant = _effective_tenant_for_admin()

    role = _role_lower()
    is_uni_admin = ((role == "university_admin") or _bool_attr(current_user, "is_university_admin", False)) and not actor_is_global

    if request.method == "POST":
        action = (request.form.get("action") or "update_user").strip()

        if action == "add_student":
            if not is_uni_admin or tenant is None:
                flash("You are not allowed to add students here.", "danger")
                return redirect(url_for("admin.users"))

            email = (request.form.get("email") or "").strip().lower()
            name = (request.form.get("name") or "").strip()
            department = (request.form.get("department") or "").strip()

            if not email or "@" not in email:
                flash("Please enter a valid student email.", "danger")
                return redirect(url_for("admin.users"))

            existing = User.query.filter(func.lower(User.email) == email.lower()).first()
            if existing:
                if getattr(existing, "university_id", None) != tenant.id:
                    flash("That email already exists in another university / scope.", "danger")
                    return redirect(url_for("admin.users"))

                if name:
                    existing.name = name
                if department and hasattr(User, "department"):
                    existing.department = department

                db.session.commit()
                flash("Student already existed — updated details.", "success")
                return redirect(url_for("admin.users"))

            temp_pw = secrets.token_urlsafe(10)

            u = User(
                email=email,
                name=name or email.split("@")[0],
                role="student",
                university_id=tenant.id,
                verified=False,
            )
            if department and hasattr(User, "department"):
                u.department = department

            u.set_password(temp_pw)
            db.session.add(u)

            try:
                # ✅ ensure u.id exists for logging
                db.session.flush()

                _log_admin_action(
                    "student_create",
                    target_user=u,
                    meta={"admin_email": current_user.email, "tenant_id": tenant.id, "email": email},
                )
                db.session.commit()
                flash(f"Student added. Temporary password: {temp_pw}", "success")
            except Exception as e:
                db.session.rollback()
                flash(f"Failed to add student: {e}", "danger")

            return redirect(url_for("admin.users"))

        if action == "bulk_import":
            if not is_uni_admin or tenant is None:
                flash("You are not allowed to import students here.", "danger")
                return redirect(url_for("admin.users"))

            f = request.files.get("csv_file")
            if not f:
                flash("Please upload a CSV file.", "danger")
                return redirect(url_for("admin.users"))

            raw = f.read()
            try:
                text = raw.decode("utf-8-sig", errors="ignore")
            except Exception:
                text = raw.decode("utf-8", errors="ignore")

            stream = io.StringIO(text)
            reader = csv.DictReader(stream)

            created = 0
            updated = 0
            skipped = 0
            creds = []

            max_rows = 500
            for i, row in enumerate(reader):
                if i >= max_rows:
                    break

                email = (row.get("email") or "").strip().lower()
                name = (row.get("name") or "").strip()
                department = (row.get("department") or "").strip()

                if not email or "@" not in email:
                    skipped += 1
                    continue

                existing = User.query.filter(func.lower(User.email) == email.lower()).first()
                if existing:
                    if getattr(existing, "university_id", None) != tenant.id:
                        skipped += 1
                        continue

                    if name:
                        existing.name = name
                    if department and hasattr(User, "department"):
                        existing.department = department

                    updated += 1
                    continue

                temp_pw = secrets.token_urlsafe(10)
                u = User(
                    email=email,
                    name=name or email.split("@")[0],
                    role="student",
                    university_id=tenant.id,
                    verified=False,
                )
                if department and hasattr(User, "department"):
                    u.department = department

                u.set_password(temp_pw)
                db.session.add(u)
                created += 1
                creds.append((email, temp_pw))

            try:
                _log_admin_action(
                    "student_bulk_import",
                    meta={
                        "admin_email": current_user.email,
                        "tenant_id": tenant.id,
                        "created": created,
                        "updated": updated,
                        "skipped": skipped,
                        "max_rows": max_rows,
                    },
                )
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                flash(f"Import failed: {e}", "danger")
                return redirect(url_for("admin.users"))

            if creds:
                out = io.StringIO()
                w = csv.writer(out)
                w.writerow(["email", "temp_password"])
                for em, pw in creds:
                    w.writerow([em, pw])

                data = out.getvalue().encode("utf-8")
                bio = io.BytesIO(data)
                bio.seek(0)

                filename = f"careerai_student_credentials_{tenant.id}.csv"
                return send_file(
                    bio,
                    mimetype="text/csv",
                    as_attachment=True,
                    download_name=filename,
                )

            flash(f"Import complete. Updated: {updated}, skipped: {skipped}. No new accounts created.", "success")
            return redirect(url_for("admin.users"))

        if action == "update_user":
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

            if not _check_tenant_scope_for_user(target):
                flash("You cannot modify users outside your tenant.", "danger")
                return redirect(url_for("admin.users"))

            target_role = (getattr(target, "role", "") or "").lower()
            old_role = target_role
            old_university_id = target.university_id

            allowed_roles = ["student", "university_admin", "super_admin"]
            if actor_is_ultra:
                allowed_roles.append("ultra_admin")

            if new_role not in allowed_roles:
                flash("Invalid role.", "danger")
                return redirect(url_for("admin.users"))

            if not actor_is_global and target_role in ("super_admin", "ultra_admin"):
                flash("You cannot modify a global admin account.", "danger")
                return redirect(url_for("admin.users"))

            if not actor_is_ultra and target_role == "ultra_admin":
                flash("Only ultra admins can modify ultra admin accounts.", "danger")
                return redirect(url_for("admin.users"))

            if (target_role == "super_admin" or new_role == "super_admin") and not actor_is_ultra:
                flash("Only ultra admins can assign or remove the super_admin role.", "danger")
                return redirect(url_for("admin.users"))

            if new_role == "ultra_admin" and not actor_is_ultra:
                flash("Only ultra admins can assign the ultra_admin role.", "danger")
                return redirect(url_for("admin.users"))

            target.role = new_role
            new_role_l = (new_role or "").lower()

            if actor_is_global:
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
                if tenant is not None:
                    target.university_id = tenant.id

            try:
                changes = {}

                if old_role != new_role_l:
                    changes["role"] = {"before": old_role, "after": new_role_l}
                if old_university_id != target.university_id:
                    changes["university_id"] = {"before": old_university_id, "after": target.university_id}

                if changes:
                    meta = {
                        "changes": changes,
                        "admin_email": current_user.email,
                        "target_email": target.email,
                    }
                    _log_admin_action("user_update", target_user=target, meta=meta)

                db.session.commit()
                flash("User updated.", "success")
            except Exception as e:
                db.session.rollback()
                flash(f"Failed to update user: {e}", "danger")

            return redirect(url_for("admin.users"))

        flash("Unknown action.", "danger")
        return redirect(url_for("admin.users"))

    q = (request.args.get("q") or "").strip()
    role_filter = (request.args.get("role") or "").strip()
    selected_department = (request.args.get("department") or "").strip()

    user_q = User.query

    if not actor_is_global:
        if tenant is not None:
            user_q = user_q.filter(User.university_id == tenant.id)
        else:
            user_q = user_q.filter(User.id == -1)

    if is_uni_admin:
        user_q = user_q.filter(User.role == "student")
        role_filter = "student"
    elif role_filter:
        user_q = user_q.filter(User.role == role_filter)

    departments = []
    if tenant is not None and hasattr(User, "department"):
        dept_q = (
            db.session.query(User.department)
            .filter(User.university_id == tenant.id, User.role == "student", User.department.isnot(None))
            .distinct()
            .order_by(User.department.asc())
            .all()
        )
        departments = [r[0] for r in dept_q if r and r[0]]

        if selected_department:
            user_q = user_q.filter(func.lower(User.department) == selected_department.lower())

    if q:
        like = f"%{q}%"
        user_q = user_q.filter(db.or_(User.email.ilike(like), User.name.ilike(like)))

    users_list = user_q.order_by(User.created_at.desc()).limit(100).all()

    if actor_is_global:
        universities = University.query.order_by(University.name.asc()).all()
    else:
        universities = [tenant] if tenant is not None else []

    return render_template(
        "admin/users.html",
        users=users_list,
        universities=universities,
        q=q,
        role_filter=role_filter,
        tenant=tenant,
        is_super_admin=_is_super_admin(),
        is_ultra_admin=actor_is_ultra,
        is_global_admin=actor_is_global,
        is_university_admin=is_uni_admin,
        departments=departments,
        selected_department=selected_department,
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
        target.subscription_status = "pro"
        if not target.pro_since:
            target.pro_since = datetime.utcnow()
        target.pro_cancel_at = None

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
# Credits top-up (GLOBAL ADMINS ONLY)
# ---------------------------------------------------------------------
@admin_bp.route("/credits", methods=["GET", "POST"], endpoint="credits")
@login_required
def credits():
    if not _is_admin_user() or not _is_global_admin():
        flash("Only global admins can access the credits panel.", "danger")
        return redirect(url_for("admin.dashboard"))

    target: User | None = None
    recent_txs: list[CreditTransaction] = []

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

        try:
            if currency == "gold":
                credits_engine.add_pro(target, amount, feature=reason, run_id=None, commit=False)
            else:
                credits_engine.add_free(target, amount, feature=reason, run_id=None, commit=False)

            _log_admin_action(
                "credit_adjust",
                target_user=target,
                meta={
                    "admin_email": current_user.email,
                    "target_email": target.email,
                    "amount": amount,
                    "currency": currency,
                    "reason": reason,
                },
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

    email = (request.args.get("email") or "").strip().lower()
    if email:
        target = User.query.filter_by(email=email).first()
        if target:
            recent_txs = (
                CreditTransaction.query.filter_by(user_id=target.id)
                .order_by(CreditTransaction.created_at.desc())
                .limit(20)
                .all()
            )

    return render_template("admin/credits.html", target=target, recent_txs=recent_txs)


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

        if domain and University.query.filter_by(domain=domain).first():
            flash("That domain is already in use.", "danger")
            return redirect(url_for("admin.universities"))

        if tenant_slug and University.query.filter_by(tenant_slug=tenant_slug).first():
            flash("That tenant slug is already in use.", "danger")
            return redirect(url_for("admin.universities"))

        uni = University(name=name, domain=domain, tenant_slug=tenant_slug)
        db.session.add(uni)

        try:
            _log_admin_action(
                "university_create",
                university=uni,
                meta={
                    "name": name,
                    "domain": domain,
                    "tenant_slug": tenant_slug,
                    "admin_email": current_user.email,
                },
            )
            db.session.commit()
            flash("University created.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Failed to create university: {e}", "danger")

        return redirect(url_for("admin.universities"))

    universities_list = University.query.order_by(University.created_at.desc()).all()
    return render_template("admin/universities.html", universities=universities_list)


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
            _log_admin_action(
                "deal_create",
                university=uni,
                meta={
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
                },
            )
            db.session.commit()
            flash("Deal created.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Failed to create deal: {e}", "danger")

        return redirect(url_for("admin.deals"))

    deals_list = UniversityDeal.query.order_by(UniversityDeal.created_at.desc()).all()
    universities_list = University.query.order_by(University.name.asc()).all()
    return render_template("admin/deals.html", deals=deals_list, universities=universities_list)


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
                d = datetime.strptime(expires_raw, "%Y-%m-%d").date()
                expires_at = datetime(d.year, d.month, d.day, 23, 59, 59)
            except ValueError:
                expires_at = None

        existing = VoucherCampaign.query.filter(func.lower(VoucherCampaign.code) == code.lower()).first()
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
            _log_admin_action(
                "voucher_create",
                university=uni,
                meta={
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
                },
            )
            db.session.commit()
            flash("Voucher campaign created.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Failed to create voucher: {e}", "danger")

        return redirect(url_for("admin.vouchers"))

    vouchers_list = VoucherCampaign.query.order_by(VoucherCampaign.created_at.desc()).all()
    universities_list = University.query.order_by(University.name.asc()).all()
    return render_template("admin/vouchers.html", vouchers=vouchers_list, universities=universities_list)


@admin_bp.route("/vouchers/<int:campaign_id>", methods=["GET"], endpoint="voucher_detail")
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
    return render_template("admin/voucher_detail.html", campaign=campaign, redemptions=redemptions)


# ---------------------------------------------------------------------
# University Analytics (university admins ONLY) — Student Analytics ONLY
# ---------------------------------------------------------------------
@admin_bp.route("/analytics", methods=["GET"], endpoint="analytics")
@login_required
def analytics():
    if not _is_admin_user():
        flash("You are not allowed to access analytics.", "danger")
        return redirect(url_for("dashboard"))

    role = _role_lower()
    is_uni_admin = (role == "university_admin") or _bool_attr(current_user, "is_university_admin", False)

    if not is_uni_admin:
        flash("Only university admins can view student analytics.", "danger")
        return redirect(url_for("admin.dashboard"))

    if _is_global_admin():
        flash("Global admins cannot view student-level analytics.", "danger")
        return redirect(url_for("admin.dashboard"))

    tenant = _effective_tenant_for_admin()
    if tenant is None or getattr(current_user, "university_id", None) != tenant.id:
        flash("Analytics are only available for your university account.", "danger")
        return redirect(url_for("admin.dashboard"))

    # ✅ SAME parsing logic used by /analytics/export
    q, only_verified, only_pro, min_ready, max_ready, start_dt, end_dt = _analytics_params_from_request()

    # ✅ SAME user filters used by /analytics/export
    user_q = User.query.filter(User.university_id == tenant.id, User.role == "student")
    user_q = _apply_user_filters(user_q, q, only_verified, only_pro, min_ready, max_ready)

    students = user_q.all()
    student_ids = [u.id for u in students]

    total_students = len(students)
    total_verified_students = sum(1 for u in students if bool(u.verified))
    total_pro_students = sum(1 for u in students if (u.subscription_status or "").lower() == "pro")

    tier_counts = {
        "Top Tier (80+)": 0,
        "Job Ready (60–79)": 0,
        "Building (40–59)": 0,
        "Getting Started (0–39)": 0,
    }
    scores: list[int] = []
    for u in students:
        s = int(u.ready_score or 0)
        scores.append(s)
        tier_counts[_score_tier(s)] += 1

    bucket_labels = []
    bucket_counts = []
    for start in range(0, 100, 10):
        end = start + 9
        if start == 90:
            end = 100
        label = f"{start}-{end}"
        bucket_labels.append(label)
        bucket_counts.append(sum(1 for s in scores if start <= s <= end))

    readiness_chart = {
        "labels": bucket_labels,
        "counts": bucket_counts,
        "tiers": [{"tier": k, "count": v} for k, v in tier_counts.items()],
        "avg": round((sum(scores) / len(scores)), 1) if scores else 0,
        "median": sorted(scores)[len(scores) // 2] if scores else 0,
    }

    streaks = [int(u.current_streak or 0) for u in students]
    longest = [int(u.longest_streak or 0) for u in students]
    streak_chart = {
        "avg_current": round((sum(streaks) / len(streaks)), 1) if streaks else 0,
        "avg_longest": round((sum(longest) / len(longest)), 1) if longest else 0,
        "top_current": (sorted(streaks, reverse=True)[0] if streaks else 0),
        "top_longest": (sorted(longest, reverse=True)[0] if longest else 0),
    }

    # ✅ Use the SHARED _apply_date_filter(qry, col, start_dt, end_dt)
    sm_q = SkillMapSnapshot.query.join(User, SkillMapSnapshot.user_id == User.id).filter(
        User.university_id == tenant.id,
        User.role == "student",
    )
    sm_q = _apply_date_filter(sm_q, SkillMapSnapshot.created_at, start_dt, end_dt)
    total_skillmapper_runs = sm_q.count()

    jp_q = JobPackReport.query.join(User, JobPackReport.user_id == User.id).filter(
        User.university_id == tenant.id,
        User.role == "student",
    )
    jp_q = _apply_date_filter(jp_q, JobPackReport.created_at, start_dt, end_dt)
    total_jobpack_runs = jp_q.count()

    ir_q = InternshipRecord.query.join(User, InternshipRecord.user_id == User.id).filter(
        User.university_id == tenant.id,
        User.role == "student",
    )
    ir_q = _apply_date_filter(ir_q, InternshipRecord.created_at, start_dt, end_dt)
    total_internship_runs = ir_q.count()

    ll_q = LearningLog.query.join(User, LearningLog.user_id == User.id).filter(
        User.university_id == tenant.id,
        User.role == "student",
    )
    ll_q = _apply_date_filter(ll_q, LearningLog.created_at, start_dt, end_dt)
    total_learning_logs = ll_q.count()

    pr_q = Project.query.join(User, Project.user_id == User.id).filter(
        User.university_id == tenant.id,
        User.role == "student",
    )
    pr_q = _apply_date_filter(pr_q, Project.created_at, start_dt, end_dt)
    total_projects = pr_q.count()

    pp_q = PortfolioPage.query.join(User, PortfolioPage.user_id == User.id).filter(
        User.university_id == tenant.id,
        User.role == "student",
    )
    pp_q = _apply_date_filter(pp_q, PortfolioPage.created_at, start_dt, end_dt)
    total_portfolio_pages = pp_q.count()
    total_public_portfolios = pp_q.filter(PortfolioPage.is_public.is_(True)).count()

    weekly_done_total = sum(int(u.weekly_milestones_completed or 0) for u in students)
    weekly_done_avg = round((weekly_done_total / total_students), 2) if total_students else 0

    engagement_summary = {
        "skillmapper_runs": total_skillmapper_runs,
        "jobpack_runs": total_jobpack_runs,
        "internship_runs": total_internship_runs,
        "learning_logs": total_learning_logs,
        "projects": total_projects,
        "portfolio_pages": total_portfolio_pages,
        "public_portfolios": total_public_portfolios,
        "weekly_milestones_total": weekly_done_total,
        "weekly_milestones_avg": weekly_done_avg,
    }

    skill_counts: dict[str, int] = {}
    skill_snapshots = sm_q.order_by(SkillMapSnapshot.created_at.desc()).limit(600).all()
    for snap in skill_snapshots:
        if not snap.skills_json:
            continue
        try:
            payload = json.loads(snap.skills_json)
        except Exception:
            continue

        for name in _extract_skills_from_skillmap_payload(payload):
            nm = _norm_skill_name(name)
            if not nm:
                continue
            skill_counts[nm] = skill_counts.get(nm, 0) + 1

    skills_top = [
        {"name": name, "count": count}
        for name, count in sorted(skill_counts.items(), key=lambda kv: kv[1], reverse=True)[:12]
    ]

    role_rows = (
        db.session.query(JobPackReport.job_title, func.count(JobPackReport.id))
        .join(User, JobPackReport.user_id == User.id)
        .filter(User.university_id == tenant.id, User.role == "student")
        .filter(JobPackReport.job_title.isnot(None))
    )
    if start_dt:
        role_rows = role_rows.filter(JobPackReport.created_at >= start_dt)
    if end_dt:
        role_rows = role_rows.filter(JobPackReport.created_at <= end_dt)
    role_rows = (
        role_rows.group_by(JobPackReport.job_title)
        .order_by(func.count(JobPackReport.id).desc())
        .limit(10)
        .all()
    )
    roles_top = [{"name": (t or "").strip(), "count": int(n or 0)} for (t, n) in role_rows if (t or "").strip()]

    internship_rows = (
        db.session.query(InternshipRecord.role, func.count(InternshipRecord.id))
        .join(User, InternshipRecord.user_id == User.id)
        .filter(User.university_id == tenant.id, User.role == "student")
        .filter(InternshipRecord.role.isnot(None))
    )
    if start_dt:
        internship_rows = internship_rows.filter(InternshipRecord.created_at >= start_dt)
    if end_dt:
        internship_rows = internship_rows.filter(InternshipRecord.created_at <= end_dt)
    internship_rows = (
        internship_rows.group_by(InternshipRecord.role)
        .order_by(func.count(InternshipRecord.id).desc())
        .limit(10)
        .all()
    )
    internship_roles = [{"name": (t or "").strip(), "count": int(n or 0)} for (t, n) in internship_rows if (t or "").strip()]

    proj_counts = dict(
        db.session.query(Project.user_id, func.count(Project.id))
        .join(User, Project.user_id == User.id)
        .filter(User.university_id == tenant.id, User.role == "student")
        .group_by(Project.user_id)
        .all()
    )
    pub_port_counts = dict(
        db.session.query(PortfolioPage.user_id, func.count(PortfolioPage.id))
        .join(User, PortfolioPage.user_id == User.id)
        .filter(User.university_id == tenant.id, User.role == "student", PortfolioPage.is_public.is_(True))
        .group_by(PortfolioPage.user_id)
        .all()
    )
    log_counts = dict(
        db.session.query(LearningLog.user_id, func.count(LearningLog.id))
        .join(User, LearningLog.user_id == User.id)
        .filter(User.university_id == tenant.id, User.role == "student")
        .group_by(LearningLog.user_id)
        .all()
    )

    top_students = sorted(
        students,
        key=lambda u: (int(u.ready_score or 0), int(u.current_streak or 0)),
        reverse=True,
    )[:50]

    student_rows = []
    for u in top_students:
        rs = int(u.ready_score or 0)
        student_rows.append(
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "ready_score": rs,
                "tier": _score_tier(rs),
                "verified": bool(u.verified),
                "pro": (u.subscription_status or "").lower() == "pro",
                "current_streak": int(u.current_streak or 0),
                "longest_streak": int(u.longest_streak or 0),
                "weekly_milestones_completed": int(u.weekly_milestones_completed or 0),
                "projects": int(proj_counts.get(u.id, 0) or 0),
                "public_portfolio_pages": int(pub_port_counts.get(u.id, 0) or 0),
                "learning_logs": int(log_counts.get(u.id, 0) or 0),
                "created_at": u.created_at,
            }
        )

    resume_missing_users: dict[str, set[int]] = defaultdict(set)
    resume_missing_mentions: dict[str, int] = defaultdict(int)
    resume_missing_display: dict[str, str] = {}

    blocker_users: dict[str, set[int]] = defaultdict(set)
    blocker_display: dict[str, str] = {}
    warning_users: dict[str, set[int]] = defaultdict(set)
    warning_display: dict[str, str] = {}

    students_with_jobpack: set[int] = set()
    students_with_resume_missing: set[int] = set()
    students_with_blockers: set[int] = set()

    if student_ids:
        jp_scan = JobPackReport.query.filter(JobPackReport.user_id.in_(student_ids))
        jp_scan = _apply_date_filter(jp_scan, JobPackReport.created_at, start_dt, end_dt)
        jp_scan = jp_scan.order_by(JobPackReport.created_at.desc()).limit(2000).all()

        seen_users: set[int] = set()
        for r in jp_scan:
            if r.user_id in seen_users:
                continue
            seen_users.add(r.user_id)
            students_with_jobpack.add(r.user_id)

            try:
                payload = json.loads(r.analysis or "{}")
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            issues = _extract_resume_issues(payload)

            missing_kw = issues.get("missing_keywords") or []
            if missing_kw:
                students_with_resume_missing.add(r.user_id)

            for kw in missing_kw:
                k = _norm_skill_key(kw)
                if not k:
                    continue
                if k not in resume_missing_display:
                    resume_missing_display[k] = _norm_skill_name(kw) or kw
                resume_missing_mentions[k] += 1
                resume_missing_users[k].add(r.user_id)

            blockers = issues.get("blockers") or []
            if blockers:
                students_with_blockers.add(r.user_id)
            for b in blockers:
                k = _norm_skill_key(b)
                if not k:
                    continue
                if k not in blocker_display:
                    blocker_display[k] = _norm_skill_name(b) or b
                blocker_users[k].add(r.user_id)

            warnings = issues.get("warnings") or []
            for w in warnings:
                k = _norm_skill_key(w)
                if not k:
                    continue
                if k not in warning_display:
                    warning_display[k] = _norm_skill_name(w) or w
                warning_users[k].add(r.user_id)

    resume_denom = max(1, len(students_with_jobpack) or 1)
    resume_missing_skills_top = []
    for k, users_set in sorted(resume_missing_users.items(), key=lambda kv: len(kv[1]), reverse=True)[:12]:
        resume_missing_skills_top.append(
            {
                "name": resume_missing_display.get(k, k),
                "students": len(users_set),
                "percent": round((len(users_set) / resume_denom) * 100, 1),
                "mentions": int(resume_missing_mentions.get(k, 0) or 0),
            }
        )

    resume_blockers_top = []
    for k, users_set in sorted(blocker_users.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]:
        resume_blockers_top.append(
            {
                "text": blocker_display.get(k, k),
                "students": len(users_set),
                "percent": round((len(users_set) / resume_denom) * 100, 1),
            }
        )

    resume_warnings_top = []
    for k, users_set in sorted(warning_users.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]:
        resume_warnings_top.append(
            {
                "text": warning_display.get(k, k),
                "students": len(users_set),
                "percent": round((len(users_set) / resume_denom) * 100, 1),
            }
        )

    roadmap_gap_users: dict[str, set[int]] = defaultdict(set)
    roadmap_gap_display: dict[str, str] = {}
    students_with_skillmap: set[int] = set()

    if student_ids:
        sm_scan = SkillMapSnapshot.query.filter(SkillMapSnapshot.user_id.in_(student_ids))
        sm_scan = _apply_date_filter(sm_scan, SkillMapSnapshot.created_at, start_dt, end_dt)
        sm_scan = sm_scan.order_by(SkillMapSnapshot.created_at.desc()).limit(2000).all()

        seen_users_sm: set[int] = set()
        for snap in sm_scan:
            if snap.user_id in seen_users_sm:
                continue
            seen_users_sm.add(snap.user_id)
            students_with_skillmap.add(snap.user_id)

            if not snap.skills_json:
                continue
            try:
                payload = json.loads(snap.skills_json)
            except Exception:
                payload = None
            if payload is None:
                continue

            missing = set(_extract_missing_skills(payload))
            for sk in missing:
                k = _norm_skill_key(sk)
                if not k:
                    continue
                if k not in roadmap_gap_display:
                    roadmap_gap_display[k] = _norm_skill_name(sk) or sk
                roadmap_gap_users[k].add(snap.user_id)

    roadmap_denom = max(1, len(students_with_skillmap) or 1)
    roadmap_missing_skills_top = []
    for k, users_set in sorted(roadmap_gap_users.items(), key=lambda kv: len(kv[1]), reverse=True)[:12]:
        roadmap_missing_skills_top.append(
            {
                "name": roadmap_gap_display.get(k, k),
                "students": len(users_set),
                "percent": round((len(users_set) / roadmap_denom) * 100, 1),
            }
        )

    no_projects = sum(1 for u in students if int(proj_counts.get(u.id, 0) or 0) == 0)
    no_devlogs = sum(1 for u in students if int(log_counts.get(u.id, 0) or 0) == 0)
    no_public_portfolio = sum(1 for u in students if int(pub_port_counts.get(u.id, 0) or 0) == 0)
    inactive = sum(1 for u in students if int(u.current_streak or 0) <= 0)
    low_ready = sum(1 for u in students if int(u.ready_score or 0) < 40)

    problems_summary = [
        {"label": "No projects yet", "count": no_projects, "percent": round((no_projects / max(1, total_students)) * 100, 1)},
        {"label": "No DevLogs yet", "count": no_devlogs, "percent": round((no_devlogs / max(1, total_students)) * 100, 1)},
        {"label": "No public portfolio yet", "count": no_public_portfolio, "percent": round((no_public_portfolio / max(1, total_students)) * 100, 1)},
        {"label": "Inactive (streak = 0)", "count": inactive, "percent": round((inactive / max(1, total_students)) * 100, 1)},
        {"label": "Low readiness (below 40)", "count": low_ready, "percent": round((low_ready / max(1, total_students)) * 100, 1)},
    ]

    insights: Dict[str, str] = {}
    if total_students > 0:
        insights["headline"] = f"{total_students} students in {tenant.name} • Verified: {total_verified_students} • Pro: {total_pro_students}."
    else:
        insights["headline"] = "No students found for the selected filters."

    insights["readiness_summary"] = (
        f"Readiness: avg {readiness_chart['avg']}, median {readiness_chart['median']}. "
        f"Tiers — Top: {tier_counts['Top Tier (80+)']}, Job Ready: {tier_counts['Job Ready (60–79)']}, "
        f"Building: {tier_counts['Building (40–59)']}, Getting Started: {tier_counts['Getting Started (0–39)']}."
        if total_students > 0
        else "Readiness: no data."
    )

    insights["consistency_summary"] = (
        f"Consistency: avg streak {streak_chart['avg_current']} days (top {streak_chart['top_current']})."
        if total_students > 0
        else "Consistency: no data."
    )

    insights["proof_summary"] = (
        f"Proof-of-work: DevLogs {engagement_summary['learning_logs']}, Projects {engagement_summary['projects']}, "
        f"Public portfolios {engagement_summary['public_portfolios']} (in selected date window)."
    )

    if resume_missing_skills_top:
        insights["skill_summary"] = (
            f"Most missed resume skill: “{resume_missing_skills_top[0]['name']}” "
            f"({resume_missing_skills_top[0]['students']} students)."
        )
    elif roadmap_missing_skills_top:
        insights["skill_summary"] = (
            f"Most common learning gap: “{roadmap_missing_skills_top[0]['name']}” "
            f"({roadmap_missing_skills_top[0]['students']} students)."
        )
    else:
        insights["skill_summary"] = "No strong skill gaps detected yet (needs more Job Pack / Skill Mapper usage)."

    insights["tool_summary"] = (
        f"Tool adoption (runs): Job Pack {total_jobpack_runs}, Skill Mapper {total_skillmapper_runs}, Internship {total_internship_runs}."
    )

    analytics_insights = insights

    tool_usage_runs = [
        {"tool": "Skill Mapper", "runs": total_skillmapper_runs},
        {"tool": "Job Pack", "runs": total_jobpack_runs},
        {"tool": "Internship Analyzer", "runs": total_internship_runs},
    ]

    return render_template(
        "admin/analytics.html",
        tenant=tenant,
        q=q,
        only_verified=only_verified,
        only_pro=only_pro,
        min_ready=min_ready,
        max_ready=max_ready,
        start=(start_dt.strftime("%Y-%m-%d") if start_dt else ""),
        end=(end_dt.strftime("%Y-%m-%d") if end_dt else ""),
        total_users=User.query.filter(User.university_id == tenant.id).count(),
        total_students=total_students,
        total_university_admins=User.query.filter(User.university_id == tenant.id, User.role == "university_admin").count(),
        readiness_chart=readiness_chart,
        streak_chart=streak_chart,
        engagement_summary=engagement_summary,
        skills_top=skills_top,
        roles_top=roles_top,
        internship_roles=internship_roles,
        student_rows=student_rows,
        analytics_insights=analytics_insights,
        problems_summary=problems_summary,
        resume_missing_skills_top=resume_missing_skills_top,
        roadmap_missing_skills_top=roadmap_missing_skills_top,
        resume_blockers_top=resume_blockers_top,
        resume_warnings_top=resume_warnings_top,
        resume_samples={"students_with_jobpack": len(students_with_jobpack)},
        tool_usage_runs=tool_usage_runs,
    )



# ---------------------------------------------------------------------
# University Analytics Export (university admins ONLY)
# - IMPORTANT: must respect the same filters/date range as /analytics
# ---------------------------------------------------------------------
@admin_bp.route("/analytics/export", methods=["GET"], endpoint="analytics_export")
@login_required
def analytics_export():
    if not _is_admin_user():
        flash("You are not allowed to export analytics.", "danger")
        return redirect(url_for("dashboard"))

    role = _role_lower()
    is_uni_admin = (role == "university_admin") or _bool_attr(current_user, "is_university_admin", False)

    if not is_uni_admin or _is_global_admin():
        flash("Only tenant-scoped university admins can export analytics.", "danger")
        return redirect(url_for("admin.dashboard"))

    tenant = _effective_tenant_for_admin()
    if tenant is None or getattr(current_user, "university_id", None) != tenant.id:
        flash("Analytics export is only available for your university account.", "danger")
        return redirect(url_for("admin.dashboard"))

    # ✅ SAME filters as analytics page
    q, only_verified, only_pro, min_ready, max_ready, start_dt, end_dt = _analytics_params_from_request()

    base_students_q = User.query.filter(User.university_id == tenant.id, User.role == "student")
    base_students_q = _apply_user_filters(base_students_q, q, only_verified, only_pro, min_ready, max_ready)

    # Cap export to protect server (adjust if you want)
    export_limit = 5000
    students = (
        base_students_q
        .order_by(User.ready_score.desc(), User.current_streak.desc(), User.created_at.desc())
        .limit(export_limit)
        .all()
    )
    student_ids = [u.id for u in students]

    # Per-user proof-of-work counts (ALL-TIME to match your UI table semantics)
    proj_counts = {}
    pub_port_counts = {}
    log_counts = {}

    if student_ids:
        proj_counts = dict(
            db.session.query(Project.user_id, func.count(Project.id))
            .filter(Project.user_id.in_(student_ids))
            .group_by(Project.user_id)
            .all()
        )

        pub_port_counts = dict(
            db.session.query(PortfolioPage.user_id, func.count(PortfolioPage.id))
            .filter(PortfolioPage.user_id.in_(student_ids), PortfolioPage.is_public.is_(True))
            .group_by(PortfolioPage.user_id)
            .all()
        )

        log_counts = dict(
            db.session.query(LearningLog.user_id, func.count(LearningLog.id))
            .filter(LearningLog.user_id.in_(student_ids))
            .group_by(LearningLog.user_id)
            .all()
        )

    # Tool runs (DATE-RANGED if start/end are provided)
    sm_q = SkillMapSnapshot.query.join(User, SkillMapSnapshot.user_id == User.id).filter(
        User.university_id == tenant.id, User.role == "student"
    )
    jp_q = JobPackReport.query.join(User, JobPackReport.user_id == User.id).filter(
        User.university_id == tenant.id, User.role == "student"
    )
    ir_q = InternshipRecord.query.join(User, InternshipRecord.user_id == User.id).filter(
        User.university_id == tenant.id, User.role == "student"
    )

    sm_q = _apply_date_filter(sm_q, SkillMapSnapshot.created_at, start_dt, end_dt)
    jp_q = _apply_date_filter(jp_q, JobPackReport.created_at, start_dt, end_dt)
    ir_q = _apply_date_filter(ir_q, InternshipRecord.created_at, start_dt, end_dt)

    total_skillmapper_runs = sm_q.count()
    total_jobpack_runs = jp_q.count()
    total_internship_runs = ir_q.count()

    # Resume missing skills summary (DATE-RANGED, latest report per student sampled from most recent)
    resume_missing_users: dict[str, set[int]] = defaultdict(set)
    resume_missing_display: dict[str, str] = {}

    if student_ids:
        jp_scan = JobPackReport.query.filter(JobPackReport.user_id.in_(student_ids))
        jp_scan = _apply_date_filter(jp_scan, JobPackReport.created_at, start_dt, end_dt)
        jp_scan = jp_scan.order_by(JobPackReport.created_at.desc()).limit(3000).all()

        seen_users: set[int] = set()
        for r in jp_scan:
            if r.user_id in seen_users:
                continue
            seen_users.add(r.user_id)

            try:
                payload = json.loads(r.analysis or "{}")
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            issues = _extract_resume_issues(payload)
            for kw in (issues.get("missing_keywords") or []):
                k = _norm_skill_key(kw)
                if not k:
                    continue
                if k not in resume_missing_display:
                    resume_missing_display[k] = _norm_skill_name(kw) or kw
                resume_missing_users[k].add(r.user_id)

    # ---------------- CSV output ----------------
    out = io.StringIO()
    w = csv.writer(out)

    # Filter metadata
    w.writerow(["section", "key", "value"])
    w.writerow(["meta", "university", tenant.name])
    w.writerow(["meta", "q", q])
    w.writerow(["meta", "verified_only", int(only_verified)])
    w.writerow(["meta", "pro_only", int(only_pro)])
    w.writerow(["meta", "min_ready", min_ready])
    w.writerow(["meta", "max_ready", max_ready])
    w.writerow(["meta", "start", start_dt.strftime("%Y-%m-%d") if start_dt else ""])
    w.writerow(["meta", "end", end_dt.strftime("%Y-%m-%d") if end_dt else ""])
    w.writerow(["meta", "export_limit", export_limit])
    w.writerow([])

    # Summary
    w.writerow(["section", "metric", "value"])
    w.writerow(["summary", "students_in_export", len(students)])
    w.writerow(["summary", "skillmapper_runs_in_window", total_skillmapper_runs])
    w.writerow(["summary", "jobpack_runs_in_window", total_jobpack_runs])
    w.writerow(["summary", "internship_runs_in_window", total_internship_runs])
    w.writerow([])

    # Missing resume skills
    w.writerow(["section", "missing_resume_skill", "students_affected"])
    for k, users_set in sorted(resume_missing_users.items(), key=lambda kv: len(kv[1]), reverse=True):
        w.writerow(["missing_resume_skill", resume_missing_display.get(k, k), len(users_set)])
    w.writerow([])

    # Student table (main export)
    w.writerow([
        "name", "email", "ready_score", "tier", "verified", "pro",
        "current_streak", "longest_streak", "weekly_milestones_completed",
        "projects_all_time", "devlogs_all_time", "public_portfolio_pages_all_time",
        "joined"
    ])

    for u in students:
        rs = int(u.ready_score or 0)
        w.writerow([
            u.name,
            u.email,
            rs,
            _score_tier(rs),
            int(bool(u.verified)),
            int((u.subscription_status or "").lower() == "pro"),
            int(u.current_streak or 0),
            int(u.longest_streak or 0),
            int(u.weekly_milestones_completed or 0),
            int(proj_counts.get(u.id, 0) or 0),
            int(log_counts.get(u.id, 0) or 0),
            int(pub_port_counts.get(u.id, 0) or 0),
            u.created_at.strftime("%Y-%m-%d") if u.created_at else "",
        ])

    csv_data = out.getvalue()
    out.close()

    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f"attachment; filename=careerai_student_analytics_{tenant.id}.csv"
    return resp



# ---------------------------------------------------------------------
# Ultra Admin Audit (global actions overview)
# ---------------------------------------------------------------------
@admin_bp.route("/audit", methods=["GET"], endpoint="audit")
@login_required
def audit():
    if not _is_ultra_admin():
        flash("Only ultra admins can view the audit log.", "danger")
        return redirect(url_for("admin.dashboard"))

    super_admins = User.query.filter(User.role == "super_admin").order_by(User.created_at.asc()).all()
    vouchers = VoucherCampaign.query.order_by(VoucherCampaign.created_at.desc()).limit(100).all()
    deals = UniversityDeal.query.order_by(UniversityDeal.created_at.desc()).limit(100).all()
    admin_credits = (
        CreditTransaction.query.filter(CreditTransaction.feature.ilike("admin%"))
        .order_by(CreditTransaction.created_at.desc())
        .limit(100)
        .all()
    )

    action_type_filter = (request.args.get("type") or "").strip()
    admin_email_filter = (request.args.get("admin_email") or "").strip().lower()
    target_email_filter = (request.args.get("target_email") or "").strip().lower()

    action_type_rows = db.session.query(AdminActionLog.action_type).distinct().order_by(AdminActionLog.action_type.asc()).all()
    action_types = [row[0] for row in action_type_rows if row[0]]

    raw_logs = AdminActionLog.query.order_by(AdminActionLog.created_at.desc()).limit(250).all()

    filtered_logs = []
    for log in raw_logs:
        if action_type_filter and log.action_type != action_type_filter:
            continue

        if admin_email_filter:
            if not log.performed_by or not log.performed_by.email:
                continue
            if admin_email_filter not in (log.performed_by.email or "").lower():
                continue

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


@admin_bp.route("/audit/export", methods=["GET"], endpoint="audit_export")
@login_required
def audit_export():
    if not _is_ultra_admin():
        flash("Only ultra admins can export the audit log.", "danger")
        return redirect(url_for("admin.dashboard"))

    action_type_filter = (request.args.get("type") or "").strip()
    admin_email_filter = (request.args.get("admin_email") or "").strip().lower()
    target_email_filter = (request.args.get("target_email") or "").strip().lower()

    q = AdminActionLog.query.order_by(AdminActionLog.created_at.desc())
    if action_type_filter:
        q = q.filter(AdminActionLog.action_type == action_type_filter)

    logs = q.limit(1000).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["timestamp", "action_type", "admin_email", "target_email", "university_id", "university_name", "meta_json"])

    for log in logs:
        if admin_email_filter:
            admin_email_val = (log.performed_by.email if log.performed_by and log.performed_by.email else "").lower()
            if admin_email_filter not in admin_email_val:
                continue
        admin_email = log.performed_by.email if log.performed_by else ""

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


# ---------------------------------------------------------------------
# University Wallets Management
# ---------------------------------------------------------------------
@admin_bp.route("/university-wallets", methods=["GET"], endpoint="university_wallets")
@login_required
def university_wallets():
    """
    View university credit wallets.

    Global admins see all universities.
    University admins see only their own (even if g.current_tenant is missing).
    """
    if not _is_admin_user():
        flash("You are not allowed to access university wallets.", "danger")
        return redirect(url_for("dashboard"))

    tenant = _effective_tenant_for_admin()
    is_global = _is_global_admin()

    query = db.session.query(University, UniversityWallet).outerjoin(
        UniversityWallet, University.id == UniversityWallet.university_id
    )

    if not is_global:
        if tenant:
            query = query.filter(University.id == tenant.id)
        else:
            query = query.filter(University.id == -1)

    results = query.all()

    wallets_data = []
    for uni, wallet in results:
        is_renewable = False
        if wallet and getattr(wallet, "renewal_date", None):
            try:
                is_renewable = date.today() >= wallet.renewal_date
            except Exception:
                is_renewable = False

        wallets_data.append(
            {
                "university": uni,
                "wallet": wallet,
                "has_wallet": wallet is not None,
                "silver_balance": int(getattr(wallet, "silver_balance", 0) or 0) if wallet else 0,
                "gold_balance": int(getattr(wallet, "gold_balance", 0) or 0) if wallet else 0,
                "silver_cap": getattr(wallet, "silver_annual_cap", None) if wallet else None,
                "gold_cap": getattr(wallet, "gold_annual_cap", None) if wallet else None,
                "renewal_date": getattr(wallet, "renewal_date", None) if wallet else None,
                "is_renewable": is_renewable,
            }
        )

    return render_template("admin/university_wallets.html", wallets=wallets_data, is_global=is_global, tenant=tenant)


@admin_bp.route("/university-wallets/<int:uni_id>/top-up", methods=["POST"], endpoint="university_wallet_topup")
@login_required
def university_wallet_topup(uni_id: int):
    if not _is_global_admin():
        flash("Only global admins can top up university wallets.", "danger")
        return redirect(url_for("admin.university_wallets"))

    uni = University.query.get_or_404(uni_id)
    wallet = UniversityWallet.query.filter_by(university_id=uni.id).first()

    if not wallet:
        wallet = UniversityWallet(
            university_id=uni.id,
            silver_balance=0,
            gold_balance=0,
            silver_annual_cap=10000,
            gold_annual_cap=5000,
            renewal_date=date.today() + timedelta(days=365),
        )
        db.session.add(wallet)
        db.session.flush()

    try:
        amount = int(request.form.get("amount") or "0")
        currency = (request.form.get("currency") or "silver").lower()
        reason = (request.form.get("reason") or "admin_topup").strip()

        if amount <= 0:
            flash("Please enter a positive amount.", "warning")
            return redirect(url_for("admin.university_wallets"))

        before_balance = wallet.silver_balance if currency == "silver" else wallet.gold_balance

        if currency == "silver":
            wallet.silver_balance = int(wallet.silver_balance or 0) + amount
            after_balance = wallet.silver_balance
        else:
            wallet.gold_balance = int(wallet.gold_balance or 0) + amount
            after_balance = wallet.gold_balance

        _log_admin_action(
            "university_wallet_topup",
            university=uni,
            meta={
                "admin_email": current_user.email,
                "university_name": uni.name,
                "amount": amount,
                "currency": currency,
                "reason": reason,
                "before_balance": before_balance,
                "after_balance": after_balance,
            },
        )

        db.session.commit()
        flash(
            f"Added {amount} {'Gold ⭐' if currency == 'gold' else 'Silver 🪙'} to {uni.name}'s wallet.",
            "success",
        )

    except Exception as e:
        db.session.rollback()
        flash(f"Failed to top up wallet: {e}", "danger")

    return redirect(url_for("admin.university_wallets"))


@admin_bp.route("/university-wallets/<int:uni_id>/set-cap", methods=["POST"], endpoint="university_wallet_set_cap")
@login_required
def university_wallet_set_cap(uni_id: int):
    if not _is_global_admin():
        flash("Only global admins can set wallet caps.", "danger")
        return redirect(url_for("admin.university_wallets"))

    uni = University.query.get_or_404(uni_id)
    wallet = UniversityWallet.query.filter_by(university_id=uni.id).first()

    if not wallet:
        wallet = UniversityWallet(university_id=uni.id, renewal_date=date.today() + timedelta(days=365))
        db.session.add(wallet)
        db.session.flush()

    try:
        silver_cap = request.form.get("silver_cap")
        gold_cap = request.form.get("gold_cap")

        before_caps = {"silver": wallet.silver_annual_cap, "gold": wallet.gold_annual_cap}

        if silver_cap:
            wallet.silver_annual_cap = int(silver_cap)
        if gold_cap:
            wallet.gold_annual_cap = int(gold_cap)

        after_caps = {"silver": wallet.silver_annual_cap, "gold": wallet.gold_annual_cap}

        _log_admin_action(
            "university_wallet_set_cap",
            university=uni,
            meta={
                "admin_email": current_user.email,
                "university_name": uni.name,
                "before_caps": before_caps,
                "after_caps": after_caps,
            },
        )

        db.session.commit()
        flash(f"Updated annual caps for {uni.name}.", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"Failed to update caps: {e}", "danger")

    return redirect(url_for("admin.university_wallets"))


@admin_bp.route("/university-wallets/<int:uni_id>/renew", methods=["POST"], endpoint="university_wallet_renew")
@login_required
def university_wallet_renew(uni_id: int):
    if not _is_global_admin():
        flash("Only global admins can renew wallets.", "danger")
        return redirect(url_for("admin.university_wallets"))

    uni = University.query.get_or_404(uni_id)
    wallet = UniversityWallet.query.filter_by(university_id=uni.id).first()

    if not wallet:
        flash("Wallet not found.", "danger")
        return redirect(url_for("admin.university_wallets"))

    try:
        before_balances = {"silver": wallet.silver_balance, "gold": wallet.gold_balance}

        wallet.silver_balance = int(wallet.silver_annual_cap or 0)
        wallet.gold_balance = int(wallet.gold_annual_cap or 0)

        wallet.renewal_date = date.today() + timedelta(days=365)
        if hasattr(wallet, "last_renewed_at"):
            try:
                wallet.last_renewed_at = datetime.utcnow()
            except Exception:
                pass

        after_balances = {"silver": wallet.silver_balance, "gold": wallet.gold_balance}

        _log_admin_action(
            "university_wallet_renew",
            university=uni,
            meta={
                "admin_email": current_user.email,
                "university_name": uni.name,
                "before_balances": before_balances,
                "after_balances": after_balances,
            },
        )

        db.session.commit()
        flash(f"Renewed wallet for {uni.name}.", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"Failed to renew wallet: {e}", "danger")

    return redirect(url_for("admin.university_wallets"))


@admin_bp.route("/university-wallets/<int:uni_id>/stats", methods=["GET"], endpoint="university_wallet_stats")
@login_required
def university_wallet_stats(uni_id: int):
    if not _is_admin_user():
        flash("You are not allowed to view university stats.", "danger")
        return redirect(url_for("dashboard"))

    uni = University.query.get_or_404(uni_id)

    if not _is_global_admin():
        tenant = _effective_tenant_for_admin()
        if not tenant or uni.id != tenant.id:
            flash("You can only view your own university's stats.", "danger")
            return redirect(url_for("admin.dashboard"))

    wallet = UniversityWallet.query.filter_by(university_id=uni.id).first()
    total_students = User.query.filter_by(university_id=uni.id, role="student").count()

    flash(
        f"{uni.name}: students={total_students}, wallet_silver={getattr(wallet,'silver_balance',0) if wallet else 0}, "
        f"wallet_gold={getattr(wallet,'gold_balance',0) if wallet else 0}.",
        "info",
    )
    return redirect(url_for("admin.university_wallets"))


# ---------------------------------------------------------------------
# Dean Strategy Dashboard (university admins)
# ---------------------------------------------------------------------
@admin_bp.route("/university/strategy", methods=["GET"], endpoint="strategy")
@login_required
def strategy():
    if not _is_admin_user():
        flash("You are not allowed to access this dashboard.", "danger")
        return redirect(url_for("dashboard"))

    role = _role_lower()
    is_uni_admin = (role == "university_admin") or _bool_attr(current_user, "is_university_admin", False)

    if not is_uni_admin or _is_global_admin():
        flash("This view is for university admins only.", "danger")
        return redirect(url_for("admin.dashboard"))

    tenant = _effective_tenant_for_admin()
    if tenant is None or getattr(current_user, "university_id", None) != tenant.id:
        flash("This dashboard is only available for your university.", "danger")
        return redirect(url_for("admin.dashboard"))

    dept = (request.args.get("department") or "").strip()
    min_ready = _safe_int(request.args.get("min_ready"), 0)

    start_dt = _parse_yyyy_mm_dd(request.args.get("start"))
    end_dt = _parse_yyyy_mm_dd(request.args.get("end"))
    if end_dt:
        end_dt = end_dt.replace(hour=23, minute=59, second=59)

    student_q = User.query.filter(User.university_id == tenant.id, User.role == "student")

    if min_ready > 0:
        student_q = student_q.filter(User.ready_score >= min_ready)

    if dept and hasattr(User, "department"):
        student_q = student_q.filter(func.lower(User.department) == dept.lower())

    students = student_q.all()
    student_ids = [u.id for u in students]
    total_students = len(students)

    profile_done = 0
    try:
        if student_ids:
            prof_ids = set(
                r[0]
                for r in db.session.query(UserProfile.user_id)
                .filter(UserProfile.user_id.in_(student_ids))
                .all()
            )
            profile_done = sum(1 for u in students if u.id in prof_ids)
        else:
            profile_done = 0
    except Exception:
        profile_done = 0

    streak_7 = sum(1 for u in students if int(getattr(u, "current_streak", 0) or 0) >= 7)
    job_ready = sum(1 for u in students if int(getattr(u, "ready_score", 0) or 0) >= 80)

    proj_counts: dict[int, int] = {}
    log_counts: dict[int, int] = {}
    pub_port_counts: dict[int, int] = {}

    if student_ids:
        proj_counts = dict(
            db.session.query(Project.user_id, func.count(Project.id))
            .filter(Project.user_id.in_(student_ids))
            .group_by(Project.user_id)
            .all()
        )
        log_counts = dict(
            db.session.query(LearningLog.user_id, func.count(LearningLog.id))
            .filter(LearningLog.user_id.in_(student_ids))
            .group_by(LearningLog.user_id)
            .all()
        )
        pub_port_counts = dict(
            db.session.query(PortfolioPage.user_id, func.count(PortfolioPage.id))
            .filter(PortfolioPage.user_id.in_(student_ids), PortfolioPage.is_public.is_(True))
            .group_by(PortfolioPage.user_id)
            .all()
        )

    has_project = sum(1 for u in students if int(proj_counts.get(u.id, 0) or 0) > 0)
    has_devlog = sum(1 for u in students if int(log_counts.get(u.id, 0) or 0) > 0)
    has_public_portfolio = sum(1 for u in students if int(pub_port_counts.get(u.id, 0) or 0) > 0)

    funnel = [
        {"label": "Total students", "count": total_students},
        {"label": "Profile completed", "count": profile_done},
        {"label": "7-day consistency", "count": streak_7},
        {"label": "1+ project", "count": has_project},
        {"label": "Hiring-ready (80+)", "count": job_ready},
    ]

    sm_q = SkillMapSnapshot.query.join(User, SkillMapSnapshot.user_id == User.id).filter(
        User.university_id == tenant.id,
        User.role == "student",
    )

    if student_ids:
        sm_q = sm_q.filter(SkillMapSnapshot.user_id.in_(student_ids))
    else:
        sm_q = sm_q.filter(SkillMapSnapshot.id == -1)

    if start_dt:
        sm_q = sm_q.filter(SkillMapSnapshot.created_at >= start_dt)
    if end_dt:
        sm_q = sm_q.filter(SkillMapSnapshot.created_at <= end_dt)

    gap_students_count: dict[str, int] = defaultdict(int)
    gap_mentions: dict[str, int] = defaultdict(int)
    students_with_snapshots: set[int] = set()

    snaps = sm_q.order_by(SkillMapSnapshot.created_at.desc()).limit(2000).all()
    seen_users: set[int] = set()

    for snap in snaps:
        if snap.user_id in seen_users:
            continue
        seen_users.add(snap.user_id)

        if not snap.skills_json:
            continue
        try:
            payload = json.loads(snap.skills_json)
        except Exception:
            continue

        students_with_snapshots.add(snap.user_id)

        missing = set(_extract_missing_skills(payload))
        for skill in missing:
            gap_mentions[skill] += 1
            gap_students_count[skill] += 1

    denom = max(1, len(students_with_snapshots) or total_students or 1)
    top_gaps = []
    for skill, cnt in sorted(gap_students_count.items(), key=lambda kv: kv[1], reverse=True)[:15]:
        top_gaps.append(
            {
                "skill": skill,
                "students": int(cnt),
                "percent": round((int(cnt) / denom) * 100, 1),
                "mentions": int(gap_mentions.get(skill, 0) or 0),
            }
        )

    jp_q = JobPackReport.query.join(User, JobPackReport.user_id == User.id).filter(
        User.university_id == tenant.id,
        User.role == "student",
    )

    if student_ids:
        jp_q = jp_q.filter(JobPackReport.user_id.in_(student_ids))
    else:
        jp_q = jp_q.filter(JobPackReport.id == -1)

    if start_dt:
        jp_q = jp_q.filter(JobPackReport.created_at >= start_dt)
    if end_dt:
        jp_q = jp_q.filter(JobPackReport.created_at <= end_dt)

    blocker_counts: dict[str, int] = defaultdict(int)
    warning_counts: dict[str, int] = defaultdict(int)
    missing_kw_counts: dict[str, int] = defaultdict(int)

    reports = jp_q.order_by(JobPackReport.created_at.desc()).limit(2000).all()
    seen_users_jp: set[int] = set()

    for r in reports:
        if r.user_id in seen_users_jp:
            continue
        seen_users_jp.add(r.user_id)

        try:
            payload = json.loads(r.analysis or "{}")
        except Exception:
            payload = {}

        issues = _extract_resume_issues(payload if isinstance(payload, dict) else {})

        for b in set(issues["blockers"]):
            blocker_counts[b] += 1
        for w in set(issues["warnings"]):
            warning_counts[w] += 1
        for k in set(issues["missing_keywords"]):
            missing_kw_counts[k] += 1

    top_blockers = [{"text": t, "count": int(c)} for t, c in sorted(blocker_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]]
    top_warnings = [{"text": t, "count": int(c)} for t, c in sorted(warning_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]]
    top_missing_keywords = [{"text": t, "count": int(c)} for t, c in sorted(missing_kw_counts.items(), key=lambda kv: kv[1], reverse=True)[:12]]

    watchlist = []
    for u in sorted(students, key=lambda x: int(getattr(x, "ready_score", 0) or 0)):
        rs = int(getattr(u, "ready_score", 0) or 0)
        streak = int(getattr(u, "current_streak", 0) or 0)
        p = int(proj_counts.get(u.id, 0) or 0)
        d = int(log_counts.get(u.id, 0) or 0)

        tags = []
        if rs < 40:
            tags.append("Low readiness")
        if streak <= 0:
            tags.append("Inactive")
        if p == 0:
            tags.append("No projects")
        if d == 0:
            tags.append("No DevLogs")

        if rs < 40 or (rs >= 60 and p == 0 and d == 0) or (streak == 0 and rs < 60):
            watchlist.append(
                {
                    "id": u.id,
                    "name": u.name,
                    "email": u.email,
                    "department": getattr(u, "department", None) if hasattr(u, "department") else None,
                    "ready_score": rs,
                    "current_streak": streak,
                    "projects": p,
                    "devlogs": d,
                    "tags": tags[:4],
                }
            )

        if len(watchlist) >= 60:
            break

    departments = []
    if hasattr(User, "department"):
        dept_rows = (
            db.session.query(User.department)
            .filter(User.university_id == tenant.id, User.role == "student", User.department.isnot(None))
            .distinct()
            .order_by(User.department.asc())
            .all()
        )
        departments = [r[0] for r in dept_rows if r and r[0]]

    return render_template(
        "admin/strategy.html",
        tenant=tenant,
        departments=departments,
        selected_department=dept,
        min_ready=min_ready,
        start=(start_dt.strftime("%Y-%m-%d") if start_dt else ""),
        end=(end_dt.strftime("%Y-%m-%d") if end_dt else ""),
        funnel=funnel,
        top_gaps=top_gaps,
        top_blockers=top_blockers,
        top_warnings=top_warnings,
        top_missing_keywords=top_missing_keywords,
        watchlist=watchlist,
        summary={
            "total_students": total_students,
            "profile_done": profile_done,
            "has_project": has_project,
            "has_devlog": has_devlog,
            "has_public_portfolio": has_public_portfolio,
            "job_ready": job_ready,
        },
    )
