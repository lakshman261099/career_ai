# modules/portfolio/routes.py
import re, unicodedata
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, PortfolioPage

portfolio_bp = Blueprint("portfolio", __name__)

def _slugify(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii","ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]","", value).strip().lower()
    return re.sub(r"[-\s]+","-", value)

@portfolio_bp.get("/")
@login_required
def index():
    page = PortfolioPage.query.filter_by(user_id=current_user.id).first()
    return render_template("portfolio_edit.html", page=page)

@portfolio_bp.post("/save")
@login_required
def save():
    title = (request.form.get("title") or "My Portfolio").strip()
    html  = (request.form.get("html") or "<p>Describe your projects here…</p>").strip()
    page = PortfolioPage.query.filter_by(user_id=current_user.id).first()
    if not page:
        slug = _slugify(title) or f"u{current_user.id}"
        page = PortfolioPage(user_id=current_user.id, title=title, slug=slug, html=html)
    else:
        page.title = title; page.html = html
        if not page.slug: page.slug = _slugify(title) or f"u{current_user.id}"
    db.session.add(page); db.session.commit()
    flash("Portfolio saved", "success")
    return redirect(url_for("portfolio.index"))

@portfolio_bp.get("/view/<slug>")
def view(slug):
    page = PortfolioPage.query.filter_by(slug=slug).first_or_404()
    return render_template("portfolio_public.html", page=page)
