# modules/portfolio/routes.py

import traceback
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user
from models import db, PortfolioPage, UserProfile
from limits import authorize_and_consume, can_use_pro, consume_pro

portfolio_bp = Blueprint("portfolio", __name__, template_folder="../../templates/portfolio")

# ---------------------------
# Helpers
# ---------------------------
def _get_profile() -> UserProfile | None:
    try:
        return UserProfile.query.filter_by(user_id=current_user.id).first()
    except Exception:
        current_app.logger.exception("Load profile failed")
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def _skills_list(profile: UserProfile) -> list[str]:
    raw = (profile.skills or []) if profile else []
    out = []
    for item in raw:
        if isinstance(item, dict):
            nm = (item.get("name") or item.get("skill") or "").strip()
            if nm:
                lvl = item.get("level")
                if isinstance(lvl, int):
                    out.append(f"{nm} (Lv{max(1, min(5, lvl))})")
                else:
                    out.append(nm)
        elif isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
    return out[:12]


def _compose_content_from_profile(profile: UserProfile) -> str:
    """Generate a tasteful markdown scaffold from the profile; used for 'Import from Profile'."""
    if not profile:
        return "# Portfolio\n\n(Add your details…)"

    name = profile.full_name or ""
    headline = profile.headline or ""
    summary = profile.summary or ""
    skills_md = ", ".join(_skills_list(profile)) or "—"
    links = profile.links or {}

    lines = []
    lines.append(f"# {name}".strip() or "# Portfolio")
    if headline:
        lines.append(f"**{headline}**")
    lines.append("")
    if summary:
        lines.append(summary)
        lines.append("")
    lines.append("## Skills")
    lines.append(skills_md)
    lines.append("")
    if profile.education:
        lines.append("## Education")
        for ed in profile.education:
            deg = (ed.get("degree") or "").strip()
            sch = (ed.get("school") or "").strip()
            yr = (str(ed.get("year") or "").strip())
            bullet = " · ".join([x for x in [deg, sch, yr] if x])
            if bullet:
                lines.append(f"- {bullet}")
        lines.append("")
    if profile.experience:
        lines.append("## Experience")
        for job in profile.experience:
            role = (job.get("role") or "").strip()
            comp = (job.get("company") or "").strip()
            st = (job.get("start") or "").strip()
            en = (job.get("end") or "Present").strip()
            head = " · ".join([x for x in [role, comp, f"{st}–{en}"] if x])
            if head:
                lines.append(f"- **{head}**")
            bullets = job.get("bullets") or []
            for b in bullets[:5]:
                if b:
                    lines.append(f"  - {b}")
        lines.append("")
    if links:
        lines.append("## Links")
        for k, v in links.items():
            if v:
                lines.append(f"- **{k.title()}**: {v}")
        lines.append("")
    lines.append("## Projects")
    lines.append("- (Add selected project details below…)")

    return "\n".join(lines)


def _project_suggestions(profile: UserProfile | None, tier: str = "free") -> list[dict]:
    """
    Lightweight, deterministic suggestions (no API). Uses profile (if any) to tailor.
    Free -> 1 suggestion, Pro -> 3.
    """
    base_role = (profile.headline or "").lower() if profile else ""
    skills = [s.split(" ")[0] for s in _skills_list(profile)] if profile else []

    def mk(title, why, steps, tech):
        return {
            "title": title,
            "why": why,
            "steps": steps,
            "tech": tech,
        }

    # Fallbacks
    if "data" in base_role:
        seed = [
            mk("Student Hiring Trends Dashboard",
               "Shows you can aggregate datasets and build clean insights.",
               ["Scrape/collect 2–3 open datasets",
                "Clean & join; compute 5 KPIs",
                "Build interactive dashboard (filters, drilldowns)",
                "Write a short findings report"],
               ["Python", "Pandas", "SQLite", "Streamlit"]),
            mk("Resume Keyword Miner",
               "Demonstrates NLP feature extraction + simple ranking.",
               ["Collect 100 job posts in your target role",
                "Extract skills/keywords & frequencies",
                "Build a small tool that scores a resume vs job"],
               ["Python", "spaCy", "scikit-learn"]),
            mk("Cohort Retention Simulator",
               "Shows modeling and experiment thinking.",
               ["Synthesize user cohort data",
                "Model churn scenarios and interventions",
                "Visualize retention curves and ROI"],
               ["Python", "Matplotlib", "Jupyter"])
        ]
    elif "backend" in base_role or "api" in base_role:
        seed = [
            mk("CareerAI Notes API",
               "Proves REST design, auth, and clean data modeling.",
               ["Design schema (users, notes, tags)",
                "Implement JWT auth, rate limit, pagination",
                "Ship OpenAPI spec + Postman collection"],
               ["Python", "Flask/FastAPI", "PostgreSQL", "OpenAPI"]),
            mk("Job Pack Scraper",
               "Demonstrates async I/O and resilient scraping.",
               ["Pick 3 job boards",
                "Write async scraper + dedupe",
                "Export clean JSON and a small dashboard"],
               ["Python", "httpx/asyncio", "SQLite"]),
            mk("Resume PDF Text Service",
               "Shows file processing, queuing, and observability.",
               ["Parse PDFs, return JSON blocks",
                "Queue long jobs; add retries",
                "Add metrics and structured logs"],
               ["Python", "Celery/RQ", "S3", "Grafana"])
        ]
    else:
        seed = [
            mk("Job Search Companion",
               "A polished, real‑world helpful tool.",
               ["Collect target roles & locations",
                "Generate weekly plan + resources",
                "Progress tracker and shareable profile card"],
               ["Python/JS", "Flask", "Tailwind", "SQLite"]),
            mk("Personal Portfolio v2",
               "Refactor to modern design with crisp storytelling.",
               ["Define narrative + hero metrics",
                "Add projects with outcomes",
                "Add blog and contact form"],
               ["HTML", "Tailwind", "Netlify/Vercel"]),
            mk("Internship Application Tracker",
               "Practical CRUD; shows product thinking.",
               ["Schema: applications, status, contacts",
                "Kanban board; export to CSV",
                "Email reminders"],
               ["Flask", "SQLAlchemy", "HTMX/Tailwind"])
        ]

    # Use skills to tweak tech stack ordering
    if skills:
        for s in seed:
            s["tech"] = list(dict.fromkeys(skills + s["tech"]))  # pref skills first

    return seed[:1] if tier == "free" else seed[:3]


# ---------------------------
# Landing: Free vs Pro
# ---------------------------
@portfolio_bp.route("/", methods=["GET", "POST"], endpoint="index")
@login_required
def index():
    is_pro = (current_user.subscription_status or "free").lower() == "pro"

    # FREE: render single-suggestion generator
    if not is_pro:
        if request.method == "POST":
            # consume free credit for suggestion generation
            if not authorize_and_consume(current_user, "portfolio"):
                flash("You’ve reached today’s free limit. Upgrade to Pro to continue.", "warning")
                return redirect(url_for("billing.index"))
            profile = _get_profile()  # may be None (Profile Portal is Pro; this can still be None)
            suggestions = _project_suggestions(profile, tier="free")
            return render_template("portfolio/free.html", suggestions=suggestions)
        # GET free landing
        return render_template("portfolio/free.html", suggestions=None)

    # PRO: list pages + actions
    try:
        pages = (PortfolioPage.query
                 .filter_by(user_id=current_user.id)
                 .order_by(PortfolioPage.created_at.desc())
                 .all())
    except Exception as e:
        current_app.logger.exception("Portfolio index error: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass
        pages = []
        flash("Couldn’t load your portfolio pages. Please refresh.", "warning")
    return render_template("portfolio/index.html", pages=pages)


# ---------------------------
# Create new draft (Pro)
# ---------------------------
@portfolio_bp.route("/new", methods=["GET", "POST"], endpoint="new")
@login_required
def new():
    # gate to Pro
    if (current_user.subscription_status or "free").lower() != "pro":
        flash("Portfolio builder is a Pro feature. Upgrade to get 3 tailored projects and publish.", "warning")
        return redirect(url_for("billing.index"))

    preset = (request.args.get("preset") or "").lower()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip() or "Untitled"
        content_md = request.form.get("content_md") or ""
        try:
            # Creating a draft uses free portfolio cost (🪙) to throttle churn or treat as no‑cost.
            # If you prefer no cost here, comment the next 3 lines.
            if not authorize_and_consume(current_user, "portfolio"):
                flash("Limit reached. Try later or top up credits.", "warning")
                return redirect(url_for("billing.index"))

            page = PortfolioPage(
                user_id=current_user.id,
                title=title,
                content_md=content_md,
                is_public=False,
                meta_json={"tier": "pro", "created_at": datetime.utcnow().isoformat()}
            )
            db.session.add(page)
            db.session.commit()

            flash("Draft created.", "success")
            return redirect(url_for("portfolio.edit", page_id=page.id))

        except Exception as e:
            current_app.logger.exception("Portfolio new error: %s", e)
            try:
                db.session.rollback()
            except Exception:
                pass
            flash("Couldn’t create the draft. Please try again.", "danger")

    # GET — optional preset from profile
    profile = _get_profile()
    prefilled = _compose_content_from_profile(profile) if preset == "profile" else ""
    return render_template("portfolio/new.html", prefilled=prefilled)


# ---------------------------
# Edit draft (Pro)
# ---------------------------
@portfolio_bp.route("/<int:page_id>/edit", methods=["GET", "POST"], endpoint="edit")
@login_required
def edit(page_id):
    # gate to Pro
    if (current_user.subscription_status or "free").lower() != "pro":
        flash("Portfolio builder is a Pro feature. Upgrade to continue.", "warning")
        return redirect(url_for("billing.index"))

    try:
        page = PortfolioPage.query.filter_by(id=page_id, user_id=current_user.id).first_or_404()
    except Exception as e:
        current_app.logger.exception("Portfolio load error: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass
        abort(404)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip() or page.title
        content_md = request.form.get("content_md") or page.content_md
        try:
            # Editing a draft can be free; if you want to throttle, keep this check.
            if not authorize_and_consume(current_user, "portfolio"):
                flash("Daily limit reached. Try again tomorrow or top up.", "warning")
                return redirect(url_for("billing.index"))

            page.title = title
            page.content_md = content_md
            db.session.commit()
            flash("Draft updated.", "success")
            return redirect(url_for("portfolio.edit", page_id=page.id))

        except Exception as e:
            current_app.logger.exception("Portfolio edit error: %s", e)
            try:
                db.session.rollback()
            except Exception:
                pass
            flash("Couldn’t update the draft. Please try again.", "danger")

    # Tailored suggestions (3) to help fill content
    profile = _get_profile()
    suggestions = _project_suggestions(profile, tier="pro")
    return render_template("portfolio/edit.html", page=page, suggestions=suggestions)


# ---------------------------
# Publish (Pro ⭐ credit)
# ---------------------------
@portfolio_bp.route("/<int:page_id>/publish", methods=["POST"], endpoint="publish")
@login_required
def publish(page_id):
    # gate to Pro coins
    if not can_use_pro(current_user, "portfolio"):
        flash("Publishing requires Pro ⭐ credits. Please top up or upgrade.", "warning")
        return redirect(url_for("billing.index"))

    try:
        page = PortfolioPage.query.filter_by(id=page_id, user_id=current_user.id).first_or_404()
        confirm = (request.form.get("confirm_publish") or "").lower() == "yes"
        if not confirm:
            flash("Please confirm the publish checkbox before continuing.", "warning")
            return redirect(url_for("portfolio.edit", page_id=page.id))

        page.is_public = True
        meta = dict(page.meta_json or {})
        meta["published_at"] = datetime.utcnow().isoformat()
        meta["lock"] = True
        page.meta_json = meta
        db.session.commit()

        # consume ⭐ after successful publish
        consume_pro(current_user, "portfolio")

        flash("Portfolio page published! Share your link.", "success")
    except Exception as e:
        current_app.logger.exception("Portfolio publish error: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass
        flash("Couldn’t publish the page. Please try again.", "danger")
    return redirect(url_for("portfolio.index"))


@portfolio_bp.route("/<int:page_id>/unpublish", methods=["POST"], endpoint="unpublish")
@login_required
def unpublish(page_id):
    # Optional: keep unpublish Pro-gated or free. We'll allow unpublish without cost.
    try:
        page = PortfolioPage.query.filter_by(id=page_id, user_id=current_user.id).first_or_404()
        page.is_public = False
        meta = dict(page.meta_json or {})
        meta["unpublished_at"] = datetime.utcnow().isoformat()
        page.meta_json = meta
        db.session.commit()
        flash("Unpublished.", "info")
    except Exception as e:
        current_app.logger.exception("Portfolio unpublish error: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass
        flash("Couldn’t unpublish the page. Please try again.", "danger")
    return redirect(url_for("portfolio.index"))


# ---------------------------
# Public view
# ---------------------------
@portfolio_bp.route("/view/<int:page_id>", methods=["GET"], endpoint="view")
def view(page_id):
    try:
        page = PortfolioPage.query.get_or_404(page_id)
        if not page.is_public:
            abort(404)
        return render_template("portfolio/view.html", page=page)
    except Exception as e:
        current_app.logger.exception("Portfolio public view error: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass
        abort(404)
