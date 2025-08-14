# modules/portfolio/routes.py
import re
from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, PortfolioPage

# ✅ Define the blueprint FIRST and name it exactly "portfolio_bp"
portfolio_bp = Blueprint("portfolio", __name__)

def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-") or "portfolio"

@portfolio_bp.route("", methods=["GET"])
@login_required
def index():
    # Renders the dedicated Portfolio page UI (form lives in template)
    return render_template("portfolio_index.html")

@portfolio_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    title = (request.form.get("title") or "").strip()
    role = (request.form.get("role") or "").strip()
    mode = (request.form.get("mode") or "fast").strip().lower()

    if not title or not role:
        flash("Please provide a title and role.", "error")
        return redirect(url_for("portfolio.index"))

    # Build a simple HTML portfolio page (you can swap for helpers later)
    page_html = f"""
    <section class="max-w-3xl mx-auto p-6 prose prose-invert">
      <h1 class="text-4xl font-extrabold">{title}</h1>
      <p class="opacity-80">Role: {role} · Mode: {mode.title()}</p>
      <hr class="my-6 opacity-20" />
      <h2>Projects</h2>
      <ul>
        <li>Project 1 — problem, approach, outcome (metrics)</li>
        <li>Project 2 — problem, approach, outcome (metrics)</li>
        <li>Project 3 — problem, approach, outcome (metrics)</li>
      </ul>
    </section>
    """.strip()

    slug = slugify(f"{title}-{current_user.id}")
    # Replace/update existing page with same slug for this user
    existing = PortfolioPage.query.filter_by(user_id=current_user.id, slug=slug).first()
    if existing:
        existing.title = title
        existing.html = page_html
        db.session.commit()
        page = existing
    else:
        page = PortfolioPage(user_id=current_user.id, title=title, slug=slug, html=page_html)
        db.session.add(page)
        db.session.commit()

    flash("Portfolio page generated.", "success")
    return redirect(url_for("portfolio.view", slug=page.slug))

# Public view per brief (no login), so students can share links
@portfolio_bp.route("/view/<slug>", methods=["GET"])
def view(slug):
    page = PortfolioPage.query.filter_by(slug=slug).first_or_404()
    return render_template("portfolio_view.html", page=page)
