# modules/portfolio/routes.py

import json
import os
import uuid
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import case, inspect, text

from models import PortfolioPage, Project, UserProfile, db
from modules.common.ai import generate_project_suggestions  # AI entrypoint

portfolio_bp = Blueprint(
    "portfolio", __name__, template_folder="../../templates/portfolio"
)

CAREER_AI_VERSION = os.getenv("CAREER_AI_VERSION", "2025-Q4")
MAX_FIELD = int(os.getenv("PORTFOLIO_MAX_FIELD", "500"))
MAX_TEXT = int(os.getenv("PORTFOLIO_MAX_TEXT", "2000"))


# ---------------------------
# Helpers
# ---------------------------
def _get_profile_safe():
    """Return the current user's UserProfile row or None. Never raise."""
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
        "email": (links or {}).get("email", ""),
        "website": (links or {}).get("website", ""),
        "linkedin": (links or {}).get("linkedin", ""),
        "github": (links or {}).get("github", ""),
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
        s = val.strip()
        s = s[:MAX_TEXT]
        parts_nl = [p.strip() for p in s.split("\n") if p.strip()]
        if len(parts_nl) > 1:
            return parts_nl
        parts_comma = [p.strip() for p in s.split(",") if p.strip()]
        return parts_comma
    return [str(val)]


def _coerce_links(val):
    out = []
    if not val:
        return out
    if isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                label = (item.get("label") or "Link").strip()[:MAX_FIELD]
                url = (item.get("url") or "").strip()[:MAX_FIELD]
                if url:
                    out.append({"label": label, "url": url})
            else:
                s = str(item).strip()[:MAX_FIELD]
                if s:
                    out.append({"label": "Link", "url": s})
        return out
    if isinstance(val, str):
        s = val.strip()[:MAX_FIELD]
        if s:
            out.append({"label": "Link", "url": s})
    return out


def _render_full_portfolio_md(
    prof: UserProfile, projects: list, chosen: dict | None = None
):
    """Build the Markdown that will be stored in PortfolioPage.content_md."""
    student = {
        "name": (prof.full_name or getattr(current_user, "name", "") or "Your Name")[
            :MAX_FIELD
        ],
        "headline": (prof.headline or "")[:MAX_TEXT],
        "summary": (prof.summary or "")[:MAX_TEXT],
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
    if contact.get("email"):
        lines.append(f"- Email: {contact['email']}")
    if contact.get("website"):
        lines.append(f"- Website: {contact['website']}")
    if contact.get("linkedin"):
        lines.append(f"- LinkedIn: {contact['linkedin']}")
    if contact.get("github"):
        lines.append(f"- GitHub: {contact['github']}")
    for k, v in (contact.get("custom") or {}).items():
        lines.append(f"- {k.title()}: {v}")
    lines.append("")

    if skills:
        lines.append("## Skills")
        formatted = []
        for s in skills:
            if isinstance(s, dict) and s.get("name"):
                lvl = s.get("level")
                nm = str(s["name"])[:MAX_FIELD]
                formatted.append(f"{nm} ({int(lvl)}/5)" if lvl else nm)
            elif isinstance(s, str):
                formatted.append(s[:MAX_FIELD])
        if formatted:
            lines.append(", ".join(formatted))
            lines.append("")

    # Optional: include chosen AI suggestion as "Highlight Project"
    if chosen:
        lines.append("## Highlight Project")
        title = (chosen.get("title") or "Selected Project").strip()[:120]
        why = (chosen.get("why") or "").strip()[:500]
        lines.append(f"### {title}")
        if why:
            lines.append(f"*Why this matters:* {why}")
        stack = [s for s in (chosen.get("stack") or []) if str(s).strip()][:8]
        if stack:
            lines.append(f"*Stack:* {', '.join(stack)}")
        what = [w for w in (chosen.get("what") or []) if str(w).strip()][:6]
        if what:
            lines.append("")
            lines.append("**What you’ll build:**")
            for w in what:
                lines.append(f"- {w}")
        rbs = [r for r in (chosen.get("resume_bullets") or []) if str(r).strip()][:4]
        if rbs:
            lines.append("")
            lines.append("**Resume bullets you can claim:**")
            for r in rbs:
                lines.append(f"- {r}")
        diff = (chosen.get("differentiation") or "").strip()[:400]
        if diff:
            lines.append("")
            lines.append(f"*How it stands out:* {diff}")
        lines.append("")

    if projects:
        lines.append("## Projects")
        for p in projects:
            title = (p.title or "")[:160]
            lines.append(f"### {title}")
            if p.short_desc:
                lines.append(p.short_desc[:MAX_TEXT])
            meta_bits = []
            if p.role:
                meta_bits.append(str(p.role)[:80])
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
                lines.append(f"*Stack:* {', '.join(stack[:12])}")
            for b in bullets[:10]:
                lines.append(f"- {b[:200]}")
            for l in links[:6]:
                if l.get("url"):
                    lbl = (l.get("label") or "Link")[:40]
                    url = l["url"][:300]
                    lines.append(f"- [{lbl}]({url})")
            lines.append("")

    if experience:
        lines.append("## Experience")
        for ex in experience:
            role = (ex.get("role") or "").strip()[:120]
            comp = (ex.get("company") or "").strip()[:120]
            st = (ex.get("start") or "").strip()[:40]
            en = (ex.get("end") or "Present").strip()[:40]
            header = " · ".join([p for p in [comp, f"{st} – {en}"] if p])
            if role:
                lines.append(f"### {role}")
            if header:
                lines.append(header)
            for b in (ex.get("bullets") or [])[:8]:
                lines.append(f"- {str(b)[:220]}")
            lines.append("")

    if education:
        lines.append("## Education")
        for ed in education:
            deg = (ed.get("degree") or "")[:160]
            sch = (ed.get("school") or "")[:160]
            yr = str(ed.get("year") or "")
            comp = " — ".join([p for p in [deg, sch] if p])
            comp = f"{comp} ({yr})" if yr else comp
            if comp.strip():
                lines.append(f"- {comp}")
        lines.append("")

    if certs:
        lines.append("## Certifications")
        for c in certs:
            if isinstance(c, dict):
                nm = (c.get("name") or "")[:160]
                yr = c.get("year")
                lines.append(f"- {nm} ({yr})" if yr else f"- {nm}")
            else:
                lines.append(f"- {str(c)[:160]}")
        lines.append("")

    return "\n".join(lines).strip()


def _sqlite_safe_projects_query():
    nulls_last = case((Project.start_date.is_(None), 1), else_=0)
    return Project.query.filter_by(user_id=current_user.id).order_by(
        nulls_last, Project.start_date.desc(), Project.id.desc()
    )


def _preflight_portfolio_schema():
    """Verify required tables/columns exist before we try to insert."""
    try:
        insp = inspect(db.engine)
        tables = set(insp.get_table_names())
        if "portfolio_page" not in tables:
            return "Schema error: table 'portfolio_page' is missing. Run migrations."
        # project table optional for publish
        pp_cols = {c["name"] for c in insp.get_columns("portfolio_page")}
        required = {
            "id",
            "user_id",
            "title",
            "content_md",
            "is_public",
            "created_at",
            "meta_json",
        }
        still_needed = required - pp_cols
        if still_needed:
            return f"Schema error: 'portfolio_page' missing columns: {', '.join(sorted(still_needed))}. Run migrations."
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return None
    except Exception as e:
        current_app.logger.exception("Preflight schema check failed: %s", e)
        return "Schema preflight failed. See server logs."


def _md_to_html(md_text: str) -> str:
    text = md_text or ""
    html = text
    try:
        import markdown

        html = markdown.markdown(
            text, extensions=["extra", "sane_lists", "smarty", "tables", "toc"]
        )
    except Exception:
        html = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
    try:
        import bleach

        allowed = set(bleach.sanitizer.ALLOWED_TAGS) | {
            "p",
            "pre",
            "code",
            "h1",
            "h2",
            "h3",
            "h4",
            "ul",
            "ol",
            "li",
            "hr",
            "br",
            "blockquote",
            "strong",
            "em",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "a",
        }
        html = bleach.clean(
            html,
            tags=allowed,
            attributes={"a": ["href", "title", "rel", "target"]},
            strip=True,
        )
        html = html.replace("<a ", '<a target="_blank" rel="noopener nofollow" ')
    except Exception:
        pass
    return html


# ---------------------------
# Routes
# ---------------------------


@portfolio_bp.route("/", endpoint="index")
@login_required
def index():
    try:
        pages = (
            PortfolioPage.query.filter_by(user_id=current_user.id)
            .order_by(PortfolioPage.created_at.desc())
            .all()
        )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        pages = []
        flash("Couldn’t load your portfolio pages. Please refresh.", "warning")
    return render_template(
        "portfolio/index.html", pages=pages, updated_tag=CAREER_AI_VERSION
    )


@portfolio_bp.route("/wizard", methods=["GET", "POST"], endpoint="wizard")
@login_required
def wizard():
    """Shows suggestions only. No publishing here."""
    prof = _get_profile_safe()
    ctx = {
        "target_role": "",
        "industry": "",
        "experience_level": "",
        "mode": "free",  # "free" or "pro"
        "suggestions": [],
        "suggestions_json": "[]",
        "prof": prof,  # template reads this (not current_user.profile)
        "updated_tag": CAREER_AI_VERSION,
    }

    if request.method == "GET":
        try:
            return render_template("portfolio/wizard.html", **ctx)
        except Exception as e:
            err_id = uuid.uuid4().hex[:8]
            current_app.logger.exception("Wizard GET failed [%s]: %s", err_id, e)
            flash(f"Something went wrong loading the wizard (wz-{err_id}).", "error")
            return redirect(url_for("portfolio.index"))

    # POST: suggest only
    action = (request.form.get("action") or "").strip().lower()
    if action != "suggest":
        # ignore other actions here
        return render_template("portfolio/wizard.html", **ctx)

    ctx["target_role"] = (request.form.get("target_role") or "").strip()[:MAX_FIELD]
    ctx["industry"] = (request.form.get("industry") or "").strip()[:MAX_FIELD]
    ctx["experience_level"] = (request.form.get("experience_level") or "").strip()[
        :MAX_FIELD
    ]
    ctx["mode"] = (request.form.get("mode") or "free").strip()

    try:
        if not ctx["target_role"] or not ctx["industry"]:
            flash("Please enter both Target Role and Industry.", "warning")
            return render_template("portfolio/wizard.html", **ctx)

        pro_mode = ctx["mode"] == "pro"
        if pro_mode and (
            getattr(current_user, "subscription_status", "free").lower() != "pro"
        ):
            flash(
                "Pro suggestions require a Pro plan. Switch to Free mode or upgrade.",
                "warning",
            )
            return render_template("portfolio/wizard.html", **ctx)

        # ---- NEW: pass Profile Portal details into the AI for higher-quality ideas
        skills_list = (prof.skills if prof else []) or []
        profile_payload = None
        if prof:
            profile_payload = {
                "full_name": prof.full_name,
                "headline": prof.headline,
                "summary": prof.summary,
                "links": prof.links,
                "skills": prof.skills,
                "education": prof.education,
                "experience": prof.experience,
                "certifications": prof.certifications,
            }

        ideas, used_live = generate_project_suggestions(
            ctx["target_role"],
            ctx["industry"],
            ctx["experience_level"],
            skills_list,
            pro_mode,
            return_source=True,  # returns (ideas, used_live)
            profile_json=profile_payload,  # leverage Profile Portal for tailoring
        )
        # ---- END NEW

        ctx["suggestions"] = ideas
        ctx["suggestions_json"] = json.dumps(ideas, ensure_ascii=False)

        if used_live:
            flash(
                ("Pro" if pro_mode else "Free") + " AI suggestions generated.",
                "success",
            )
        else:
            flash("We had trouble parsing AI output. Please try again.", "warning")

        return render_template("portfolio/wizard.html", **ctx)
    except Exception as e:
        err_id = uuid.uuid4().hex[:8]
        current_app.logger.exception("Suggest failed [%s]: %s", err_id, e)
        flash(f"Something went wrong generating suggestions (sg-{err_id}).", "error")
        return render_template("portfolio/wizard.html", **ctx)


@portfolio_bp.route("/publish", methods=["GET", "POST"], endpoint="publish")
@login_required
def publish():
    """Separate publishing endpoint: builds page from Profile Portal + existing Projects (no AI insert)."""
    # Pro required to publish
    is_pro_user = (
        getattr(current_user, "subscription_status", "free") or "free"
    ).lower() == "pro"
    if not is_pro_user:
        flash("Publishing is a Pro feature. Please upgrade to continue.", "warning")
        return redirect(url_for("billing.index"))

    prof = _get_profile_safe()
    schema_msg = _preflight_portfolio_schema()
    if schema_msg:
        flash(schema_msg, "error")
        return redirect(url_for("portfolio.index"))

    if not prof:
        flash("Your Profile Portal is empty. Please add your details first.", "warning")
        return redirect(url_for("settings.profile"))

    missing = []
    if not (prof.full_name or getattr(current_user, "name", "")):
        missing.append("full name")
    if not (prof.headline or "").strip():
        missing.append("headline")
    if missing:
        flash(
            f"Please complete your Profile Portal before publishing: {', '.join(missing)}.",
            "warning",
        )
        return redirect(url_for("settings.profile"))

    # Existing projects are optional
    try:
        projects = _sqlite_safe_projects_query().all()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.exception("Query projects failed during publish")
        projects = []

    # Build markdown (no chosen AI highlight here)
    try:
        page_md = _render_full_portfolio_md(prof, projects, chosen=None)
    except Exception as re:
        err_id = uuid.uuid4().hex[:8]
        current_app.logger.exception(
            "Render portfolio markdown failed [%s]: %s", err_id, re
        )
        flash(
            f"Publish failed (render-{err_id}). Please check your Profile Portal.",
            "error",
        )
        return redirect(url_for("portfolio.index"))

    # Insert page with metadata for quality tracking
    try:
        page = PortfolioPage(
            user_id=current_user.id,
            title=f"{prof.full_name or current_user.name} — Portfolio",
            content_md=page_md,
            is_public=True,
            created_at=datetime.utcnow(),
            meta_json={
                "generated_at": datetime.utcnow().isoformat(),
                "profile_fields": {
                    "headline": bool(prof.headline),
                    "skills": len(prof.skills or []),
                    "projects": len(projects or []),
                    "education": len(prof.education or []),
                    "experience": len(prof.experience or []),
                },
                "career_ai_version": CAREER_AI_VERSION,
            },
        )
        db.session.add(page)
        db.session.flush()
        db.session.commit()
        flash(
            "Portfolio page published! Share your link from the list below.", "success"
        )
        return redirect(url_for("portfolio.index"))
    except Exception as e:
        err_id = uuid.uuid4().hex[:8]
        try:
            db.session.rollback()
        except Exception:
            pass
        db_msg = getattr(getattr(e, "orig", None), "args", None)
        db_msg = db_msg[0] if (isinstance(db_msg, (list, tuple)) and db_msg) else str(e)
        current_app.logger.exception("Publish failed [%s]: %s", err_id, e)
        flash(f"Publish failed (db-{err_id}). Details: {db_msg}", "error")
        return redirect(url_for("portfolio.index"))


@portfolio_bp.route("/preview", methods=["GET"], endpoint="preview")
@login_required
def preview():
    """Render a live preview (not saved) of what Publish would build."""
    prof = _get_profile_safe()
    if not prof:
        flash("Your Profile Portal is empty. Please add your details first.", "warning")
        return redirect(url_for("settings.profile"))

    try:
        projects = _sqlite_safe_projects_query().all()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        projects = []

    try:
        md_text = _render_full_portfolio_md(prof, projects, chosen=None)
        html = _md_to_html(md_text)
    except Exception as e:
        current_app.logger.exception("Preview render failed: %s", e)
        flash("Could not render preview. Please check your Profile Portal.", "error")
        return redirect(url_for("portfolio.index"))

    return render_template(
        "public_view.html",
        page=None,
        page_html=html,
        updated_tag=CAREER_AI_VERSION,
    )


@portfolio_bp.route("/view/<int:page_id>", methods=["GET"], endpoint="view")
def view(page_id):
    """Public view for published pages (minimal public chrome, Markdown rendered)."""
    page = PortfolioPage.query.get_or_404(page_id)
    if not page.is_public:
        abort(404)
    try:
        rendered = _md_to_html(page.content_md)
        return render_template(
            "public_view.html",
            page=page,
            page_html=rendered,
            updated_tag=CAREER_AI_VERSION,
        )
    except Exception as e:
        current_app.logger.exception("Public portfolio render failed: %s", e)
        rendered = _md_to_html(page.content_md)
        return render_template(
            "view.html", page=page, page_html=rendered, updated_tag=CAREER_AI_VERSION
        )
