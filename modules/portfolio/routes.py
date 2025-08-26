# modules/portfolio/routes.py

from datetime import datetime
import uuid
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
)
from flask_login import login_required, current_user
from sqlalchemy import case, inspect, text

from models import db, PortfolioPage, UserProfile, Project

portfolio_bp = Blueprint("portfolio", __name__, template_folder="../../templates/portfolio")


# ---------------------------
# Helpers
# ---------------------------
def _get_profile_safe():
    try:
        return UserProfile.query.filter_by(user_id=current_user.id).first()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.exception("Failed loading UserProfile")
        return None


def _safe_links_map(links):
    out = {
        "email":    (links or {}).get("email", ""),
        "website":  (links or {}).get("website", ""),
        "linkedin": (links or {}).get("linkedin", ""),
        "github":   (links or {}).get("github", ""),
    }
    custom = {}
    for k, v in (links or {}).items():
        if k in out:
            continue
        k2, v2 = (k or "").strip(), (v or "").strip()
        if k2 and v2:
            custom[k2] = v2
    out["custom"] = custom
    return out


def _coerce_list(val):
    if val is None:
        return []
    if isinstance(val, list):
        return [x for x in val if str(x).strip()]
    if isinstance(val, str):
        parts_nl = [p.strip() for p in val.split("\n") if p.strip()]
        if len(parts_nl) > 1:
            return parts_nl
        parts_comma = [p.strip() for p in val.split(",") if p.strip()]
        return parts_comma
    return [str(val)]


def _coerce_links(val):
    out = []
    if not val:
        return out
    if isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                label = (item.get("label") or "Link").strip()
                url = (item.get("url") or "").strip()
                if url:
                    out.append({"label": label, "url": url})
            else:
                s = str(item).strip()
                if s:
                    out.append({"label": "Link", "url": s})
        return out
    if isinstance(val, str):
        s = val.strip()
        if s:
            out.append({"label": "Link", "url": s})
    return out


def _suggest_projects(role, industry, exp_level, skills_list, is_pro_user):
    role = (role or "").strip()
    industry = (industry or "").strip()
    skills = []
    for s in (skills_list or []):
        if isinstance(s, dict) and (s.get("name") or "").strip():
            skills.append(s["name"].strip())
        elif isinstance(s, str) and s.strip():
            skills.append(s.strip())

    def mk(title, why, outcomes, stack):
        return {
            "title": title,
            "why": why,
            "what": [
                "Define scope and success metrics",
                "Ship MVP in milestones with a changelog",
                "Add tests, telemetry, and docs",
                "Capture before/after impact",
            ],
            "resume_bullets": outcomes,
            "stack": stack,
        }

    base_stack = skills[:6] if skills else ["Python", "SQL", "Git"]
    ideas = [
        mk(
            f"{role or 'Portfolio'} Project in {industry or 'your domain'}",
            f"Directly aligns with {role or 'your target role'} within {industry or 'your chosen industry'}.",
            [
                f"Designed and shipped a {industry or 'domain'}-focused {role or 'project'} aligned to hiring signals",
                "Planned milestones and hit delivery dates",
                "Built clean, testable components with CI",
            ],
            base_stack,
        ),
        mk(
            f"{industry or 'Industry'} KPI & Insights Dashboard",
            "Proves you can convert business questions into measurable metrics.",
            [
                "Implemented data pipeline + dashboard for KPIs",
                "Automated refresh & alerting on thresholds",
                "Drove X% improvement in a key KPI via insights",
            ],
            list({*base_stack, "Pandas", "Matplotlib", "Streamlit"}),
        ),
        mk(
            f"{role or 'Engineer'} Systems Integration Mini-Platform",
            "Highlights systems thinking and integration quality.",
            [
                "Designed modular architecture with clear contracts",
                "Instrumented telemetry; validated under load",
                "Documented trade-offs & rollback strategy",
            ],
            list({*base_stack, "Docker", "FastAPI"}),
        ),
    ]
    return ideas[:3] if is_pro_user else ideas[:1]


def _render_full_portfolio_md(prof: UserProfile, projects: list):
    student = {
        "name": prof.full_name or (getattr(current_user, "name", "") or "Your Name"),
        "headline": prof.headline or "",
        "summary": prof.summary or "",
    }
    contact = _safe_links_map(prof.links or {})
    skills = prof.skills or []
    education = prof.education or []
    experience = prof.experience or []
    certs = prof.certifications or []

    lines = []
    lines.append(f"# {student['name']}")
    if student["headline"]:
        lines.append(f"**{student['headline']}**")
    lines.append("")
    if student["summary"]:
        lines.append(student["summary"])
        lines.append("")

    lines.append("## Contact")
    if contact.get("email"):    lines.append(f"- Email: {contact['email']}")
    if contact.get("website"):  lines.append(f"- Website: {contact['website']}")
    if contact.get("linkedin"): lines.append(f"- LinkedIn: {contact['linkedin']}")
    if contact.get("github"):   lines.append(f"- GitHub: {contact['github']}")
    for k, v in (contact.get("custom") or {}).items():
        lines.append(f"- {k.title()}: {v}")
    lines.append("")

    if skills:
        lines.append("## Skills")
        formatted = []
        for s in skills:
            if isinstance(s, dict) and s.get("name"):
                lvl = s.get("level")
                formatted.append(f"{s['name']} ({int(lvl)}/5)" if lvl else s["name"])
            elif isinstance(s, str):
                formatted.append(s)
        if formatted:
            lines.append(", ".join(formatted))
            lines.append("")

    if projects:
        lines.append("## Projects")
        for p in projects:
            lines.append(f"### {p.title}")
            if p.short_desc:
                lines.append(p.short_desc)
            meta_bits = []
            if p.role: meta_bits.append(p.role)
            if p.start_date or p.end_date:
                st = p.start_date.isoformat() if p.start_date else ""
                en = p.end_date.isoformat() if p.end_date else "Present"
                meta_bits.append(f"{st} – {en}".strip(" –"))
            if meta_bits:
                lines.append("*" + " · ".join(meta_bits) + "*")
            stack = _coerce_list(p.tech_stack)
            bullets = _coerce_list(p.bullets)
            links = _coerce_links(p.links)
            if stack:
                lines.append(f"*Stack:* {', '.join(stack)}")
            for b in bullets:
                lines.append(f"- {b}")
            for l in links:
                if l.get("url"):
                    lines.append(f"- [{l.get('label') or 'Link'}]({l['url']})")
            lines.append("")

    if experience:
        lines.append("## Experience")
        for ex in experience:
            role = (ex.get("role") or "").strip()
            comp = (ex.get("company") or "").strip()
            st   = (ex.get("start") or "").strip()
            en   = (ex.get("end") or "Present").strip()
            header = " · ".join([p for p in [comp, f"{st} – {en}"] if p])
            if role: lines.append(f"### {role}")
            if header: lines.append(header)
            for b in (ex.get("bullets") or []):
                lines.append(f"- {b}")
            lines.append("")

    if education:
        lines.append("## Education")
        for ed in education:
            deg = ed.get("degree") or ""
            sch = ed.get("school") or ""
            yr  = ed.get("year") or ""
            comp = " — ".join([p for p in [deg, sch] if p])
            comp = f"{comp} ({yr})" if yr else comp
            if comp.strip():
                lines.append(f"- {comp}")
        lines.append("")

    if certs:
        lines.append("## Certifications")
        for c in certs:
            if isinstance(c, dict):
                nm = c.get("name") or ""
                yr = c.get("year")
                lines.append(f"- {nm} ({yr})" if yr else f"- {nm}")
            else:
                lines.append(f"- {c}")
        lines.append("")

    return "\n".join(lines).strip()


def _sqlite_safe_projects_query():
    nulls_last = case((Project.start_date.is_(None), 1), else_=0)
    return (Project.query
            .filter_by(user_id=current_user.id)
            .order_by(nulls_last, Project.start_date.desc(), Project.id.desc()))


def _preflight_portfolio_schema():
    """
    Verify required tables/columns exist before we try to insert.
    If something is missing, return a user-readable message.
    """
    try:
        insp = inspect(db.engine)
        tables = set(insp.get_table_names())
        missing = []
        if "portfolio_page" not in tables:
            return "Schema error: table 'portfolio_page' is missing. Run migrations."
        if "project" not in tables:
            return "Schema error: table 'project' is missing. Run migrations."

        # columns check (tolerant to SQLite typing)
        pp_cols = {c["name"] for c in insp.get_columns("portfolio_page")}
        required = {"id", "user_id", "title", "content_md", "is_public", "created_at", "meta_json"}
        still_needed = required - pp_cols
        if still_needed:
            return f"Schema error: 'portfolio_page' missing columns: {', '.join(sorted(still_needed))}. Run migrations."

        # dumb write test (no commit): does INSERT work syntactically?
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return None
    except Exception as e:
        current_app.logger.exception("Preflight schema check failed: %s", e)
        return "Schema preflight failed. See server logs."


# ---------------------------
# Routes
# ---------------------------

@portfolio_bp.route("/", endpoint="index")
@login_required
def index():
    try:
        pages = (PortfolioPage.query
                 .filter_by(user_id=current_user.id)
                 .order_by(PortfolioPage.created_at.desc())
                 .all())
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        pages = []
        flash("Couldn’t load your portfolio pages. Please refresh.", "warning")
    return render_template("portfolio/index.html", pages=pages)


@portfolio_bp.route("/wizard", methods=["GET", "POST"], endpoint="wizard")
@login_required
def wizard():
    prof = _get_profile_safe()

    ctx = {
        "target_role": "",
        "industry": "",
        "experience_level": "",
        "imported": False,
        "suggestions": [],
        "contact": {"email": "", "website": "", "linkedin": "", "github": "", "custom": {}},
        "student": {"name": "", "headline": "", "summary": ""},
    }

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        ctx["target_role"] = (request.form.get("target_role") or "").strip()
        ctx["industry"] = (request.form.get("industry") or "").strip()
        ctx["experience_level"] = (request.form.get("experience_level") or "").strip()

        if action == "import":
            if not prof:
                flash("No Profile found. Pro users can set up their Profile Portal first.", "warning")
                return render_template("portfolio/wizard.html", **ctx)
            links_map = _safe_links_map(prof.links or {})
            ctx["student"] = {
                "name": prof.full_name or (getattr(current_user, "name", "") or ""),
                "headline": prof.headline or "",
                "summary": prof.summary or "",
            }
            ctx["contact"] = links_map
            ctx["imported"] = True
            flash("Imported from your Profile Portal.", "success")
            return render_template("portfolio/wizard.html", **ctx)

        if action == "suggest":
            try:
                if not ctx["target_role"] or not ctx["industry"]:
                    flash("Please enter both Target Role and Industry.", "warning")
                    return render_template("portfolio/wizard.html", **ctx)
                is_pro_user = ((getattr(current_user, "subscription_status", "free") or "free").lower() == "pro")
                skills_list = (prof.skills if prof else []) or []
                ctx["suggestions"] = _suggest_projects(
                    ctx["target_role"], ctx["industry"], ctx["experience_level"], skills_list, is_pro_user
                )
                flash("Here are your tailored project suggestions.", "success")
                return render_template("portfolio/wizard.html", **ctx)
            except Exception as e:
                err_id = uuid.uuid4().hex[:8]
                current_app.logger.exception("Suggest failed [%s]: %s", err_id, e)
                flash(f"Something went wrong generating suggestions (sg-{err_id}). Try again.", "error")
                return render_template("portfolio/wizard.html", **ctx)

        if action == "publish":
            is_pro_user = ((getattr(current_user, "subscription_status", "free") or "free").lower() == "pro")
            if not is_pro_user:
                flash("Publishing is a Pro feature. Please upgrade to continue.", "warning")
                return redirect(url_for("billing.index"))

            # Preflight schema
            schema_msg = _preflight_portfolio_schema()
            if schema_msg:
                flash(schema_msg, "error")
                return render_template("portfolio/wizard.html", **ctx)

            if not prof:
                flash("Your Profile Portal is empty. Please add your details first.", "warning")
                return redirect(url_for("settings.profile"))

            missing = []
            if not (prof.full_name or getattr(current_user, "name", "")):
                missing.append("full name")
            if not (prof.headline or "").strip():
                missing.append("headline")

            try:
                projects = _sqlite_safe_projects_query().all()
            except Exception:
                try: db.session.rollback()
                except Exception: pass
                current_app.logger.exception("Query projects failed during publish")
                flash("Publish failed: couldn’t load your Projects. Please try again.", "error")
                return render_template("portfolio/wizard.html", **ctx)

            if not projects:
                missing.append("at least one Project in Profile Portal")

            if missing:
                flash(f"Please complete your Profile Portal before publishing: {', '.join(missing)}.", "warning")
                return redirect(url_for("settings.profile"))

            try:
                page_md = _render_full_portfolio_md(prof, projects)
            except Exception as re:
                err_id = uuid.uuid4().hex[:8]
                current_app.logger.exception("Render portfolio markdown failed [%s]: %s", err_id, re)
                flash(f"Publish failed (render-{err_id}). Please check your project fields.", "error")
                return render_template("portfolio/wizard.html", **ctx)

            # ---- DB write with visible error message ----
            try:
                page = PortfolioPage(
                    user_id=current_user.id,
                    title=f"{prof.full_name or current_user.name} — Portfolio",
                    content_md=page_md,
                    is_public=True,
                    created_at=datetime.utcnow(),
                )
                db.session.add(page)
                db.session.flush()
                db.session.commit()
                flash("Portfolio page published! Share your link from the list below.", "success")
                return redirect(url_for("portfolio.index"))
            except Exception as e:
                err_id = uuid.uuid4().hex[:8]
                try:
                    db.session.rollback()
                except Exception:
                    pass
                # Extract DB error detail (always show for now so we can fix fast)
                db_msg = getattr(getattr(e, "orig", None), "args", None)
                db_msg = db_msg[0] if (isinstance(db_msg, (list, tuple)) and db_msg) else str(e)
                current_app.logger.exception("Publish failed [%s]: %s", err_id, e)
                flash(f"Publish failed (db-{err_id}). Details: {db_msg}", "error")
                return render_template("portfolio/wizard.html", **ctx)

        return render_template("portfolio/wizard.html", **ctx)

    return render_template("portfolio/wizard.html", **ctx)


@portfolio_bp.route("/view/<int:page_id>", methods=["GET"], endpoint="view")
def view(page_id):
    try:
        page = PortfolioPage.query.get_or_404(page_id)
        if not page.is_public:
            abort(404)
        return render_template("portfolio/view.html", page=page)
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        abort(404)
