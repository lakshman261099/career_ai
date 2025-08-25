# modules/portfolio/routes.py

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
)
from flask_login import login_required, current_user
from models import db, PortfolioPage, UserProfile
from datetime import datetime

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


def _suggest_projects(role, industry, exp_level, skills_list, is_pro_user):
    role = (role or "").strip()
    industry = (industry or "").strip()
    exp_level = (exp_level or "").strip()
    skills = [s.get("name") for s in (skills_list or []) if (s.get("name") or "").strip()]

    def mk(title, why, outcomes, stack):
        return {
            "title": title,
            "why": why,
            "what": [
                "Scope the problem and success criteria",
                "Implement MVP with versioned milestones",
                "Write tests and measure impact",
                "Document decisions and trade-offs",
            ],
            "resume_bullets": outcomes,
            "stack": stack,
        }

    base_stack = skills[:6] if skills else ["Python", "SQL", "Git"]
    ideas = []

    ideas.append(mk(
        f"{role or 'Portfolio'} Project for {industry or 'Industry'}",
        f"Demonstrates direct alignment with {role or 'target role'} in the {industry or 'target'} domain.",
        [
            f"Designed and shipped a {industry or 'domain'}-focused {role or 'project'} aligned to hiring signals",
            "Decomposed the scope into milestones; achieved measurable improvements",
            "Wrote clean, testable code with CI and documentation",
        ],
        base_stack
    ))
    ideas.append(mk(
        f"{industry or 'Industry'} Metrics & Insights Dashboard",
        "Shows you can connect business questions to actionable metrics.",
        [
            "Built an analytics pipeline and dashboard to track KPIs",
            "Collaborated on defining metrics; automated refresh and alerts",
            "Drove X% improvement in a key metric via insights",
        ],
        list({*base_stack, "Dash/Streamlit", "Matplotlib"})
    ))
    ideas.append(mk(
        f"{role or 'Engineer'} Systems Integration Mini‑Platform",
        "Highlights architecture thinking and integration skills.",
        [
            "Designed a modular system with clear contracts between services",
            "Instrumented logging/metrics; validated reliability under load",
            "Documented architecture and trade‑offs",
        ],
        list({*base_stack, "Docker", "FastAPI"})
    ))

    return ideas[:3] if is_pro_user else ideas[:1]


def _render_page_md(student, contact, skills, education, experience, chosen):
    lines = []
    lines.append(f"# {student.get('name','Your Name')}")
    if student.get("headline"):
        lines.append(f"**{student['headline']}**")
    lines.append("")
    if student.get("summary"):
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
        s = [f"{s['name']} ({s.get('level',3)}/5)" for s in skills if s.get("name")]
        if s:
            lines.append(", ".join(s))
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

    if experience:
        lines.append("## Experience")
        for ex in experience:
            role = ex.get("role") or ""
            comp = ex.get("company") or ""
            st   = ex.get("start") or ""
            en   = ex.get("end") or "Present"
            header = " · ".join([p for p in [comp, f"{st} – {en}"] if p])
            if role:
                lines.append(f"### {role}")
            if header:
                lines.append(header)
            for b in (ex.get("bullets") or []):
                lines.append(f"- {b}")
            lines.append("")
    lines.append("")

    if chosen:
        lines.append("## Highlight Project")
        lines.append(f"### {chosen.get('title','Selected Project')}")
        if chosen.get("why"):
            lines.append(f"*Why this matters:* {chosen['why']}")
        if chosen.get("stack"):
            lines.append(f"*Stack:* {', '.join(chosen['stack'])}")
        lines.append("")
        lines.append("**What you’ll build:**")
        for w in (chosen.get("what") or []):
            lines.append(f"- {w}")
        lines.append("")
        lines.append("**Resume bullets you can claim:**")
        for r in (chosen.get("resume_bullets") or []):
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines).strip()


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
    ctx = {
        "target_role": "",
        "industry": "",
        "experience_level": "",
        "imported": False,
        "suggestions": [],
        "contact": {"email": "", "website": "", "linkedin": "", "github": "", "custom": {}},
        "student": {"name": "", "headline": "", "summary": ""},
    }

    prof = _get_profile_safe()

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        ctx["target_role"] = (request.form.get("target_role") or "").strip()
        ctx["industry"] = (request.form.get("industry") or "").strip()
        ctx["experience_level"] = (request.form.get("experience_level") or "").strip()

        if action == "import":
            if not prof:
                flash("No Profile found. Pro users can create one in Profile Portal.", "warning")
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

        if action == "publish":
            is_pro_user = ((getattr(current_user, "subscription_status", "free") or "free").lower() == "pro")
            if not is_pro_user:
                flash("Publishing is a Pro feature. Please upgrade to continue.", "warning")
                return redirect(url_for("billing.index"))

            # MUST have a profile for high-quality page
            if not prof or not (prof.full_name or current_user.name):
                flash("Your Profile Portal is incomplete. Please set your name in Profile Portal.", "warning")
                return redirect(url_for("settings.profile"))

            # selection
            selected_index_raw = (request.form.get("selected_index") or "").strip()
            if not selected_index_raw.isdigit():
                flash("Please select a project suggestion.", "warning")
                # Rebuild suggestions for display
                skills_list = (prof.skills if prof else []) or []
                ctx["suggestions"] = _suggest_projects(
                    ctx["target_role"], ctx["industry"], ctx["experience_level"], skills_list, is_pro_user
                )
                return render_template("portfolio/wizard.html", **ctx)
            sel = int(selected_index_raw)

            # Rebuild suggestions (stateless server)
            skills_list = (prof.skills if prof else []) or []
            suggestions = _suggest_projects(
                ctx["target_role"], ctx["industry"], ctx["experience_level"], skills_list, is_pro_user
            )
            if sel < 0 or sel >= len(suggestions):
                flash("Invalid selection. Please choose one of the suggestions.", "warning")
                ctx["suggestions"] = suggestions
                return render_template("portfolio/wizard.html", **ctx)
            chosen = suggestions[sel]

            # Build page
            student = {
                "name": prof.full_name or (getattr(current_user, "name", "") or ""),
                "headline": prof.headline or "",
                "summary": prof.summary or "",
            }
            contact = _safe_links_map(prof.links or {})
            page_md = _render_page_md(
                student=student,
                contact=contact,
                skills=(prof.skills or []),
                education=(prof.education or []),
                experience=(prof.experience or []),
                chosen=chosen
            )

            try:
                page = PortfolioPage(
                    user_id=current_user.id,
                    title=f"{student['name']} — Portfolio",
                    content_md=page_md,
                    is_public=True,
                    created_at=datetime.utcnow(),
                )
                db.session.add(page)
                # flush first to expose DB issues immediately (e.g., schema mismatch)
                db.session.flush()
                db.session.commit()
                flash("Portfolio page published! Share your link from the list below.", "success")
                return redirect(url_for("portfolio.index"))
            except Exception as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                current_app.logger.exception("Failed to publish PortfolioPage: %s", e)
                flash("Could not publish your page. Please try again.", "error")
                return render_template("portfolio/wizard.html", **ctx)

        # Fallback
        return render_template("portfolio/wizard.html", **ctx)

    # GET
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
