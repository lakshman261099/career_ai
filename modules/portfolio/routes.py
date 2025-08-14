from flask import Blueprint, request, redirect, url_for, render_template_string, abort
from flask_login import login_required, current_user
from models import db, PortfolioPage
from .helpers import slugify, build_page_html

portfolio_bp = Blueprint("portfolio", __name__)

@portfolio_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    title = request.form.get("title","My Portfolio")
    role = request.form.get("role","Student")
    slug = slugify(title)
    html = build_page_html(title, role)
    page = PortfolioPage(user_id=current_user.id, title=title, slug=slug, html=html)
    db.session.add(page); db.session.commit()
    return redirect(url_for("portfolio.view_public", slug=slug))

@portfolio_bp.route("/view/<slug>")
def view_public(slug):
    page = PortfolioPage.query.filter_by(slug=slug).first_or_404()
    return render_template_string(page.html)
