# modules/portfolio/routes.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db, PortfolioPage
from limits import authorize_and_consume, can_use_pro, consume_pro

portfolio_bp = Blueprint("portfolio", __name__, template_folder="../../templates/portfolio")


@portfolio_bp.route("/", endpoint="index")
@login_required
def index():
    pages = (PortfolioPage.query
             .filter_by(user_id=current_user.id)
             .order_by(PortfolioPage.created_at.desc())
             .all())
    return render_template("portfolio/index.html", pages=pages)


@portfolio_bp.route("/new", methods=["GET", "POST"], endpoint="new")
@login_required
def new():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip() or "Untitled"
        content_md = request.form.get("content_md") or ""

        # Create/save outline (Free daily limit or Pro ⭐)
        if not authorize_and_consume(current_user, "portfolio"):
            flash("Limit reached. Upgrade to Pro to continue today.", "warning")
            return redirect(url_for("billing.index"))

        page = PortfolioPage(
            user_id=current_user.id,
            title=title,
            content_md=content_md,
            is_public=False,
        )
        db.session.add(page)
        db.session.commit()

        flash("Draft saved.", "success")
        return redirect(url_for("portfolio.edit", page_id=page.id))

    return render_template("portfolio/new.html")


@portfolio_bp.route("/<int:page_id>/edit", methods=["GET", "POST"], endpoint="edit")
@login_required
def edit(page_id):
    page = PortfolioPage.query.filter_by(id=page_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip() or page.title
        content_md = request.form.get("content_md") or page.content_md

        # Editing a draft counts the same as creating (throttle abuse)
        if not authorize_and_consume(current_user, "portfolio"):
            flash("Daily limit reached. Try again tomorrow or go Pro.", "warning")
            return redirect(url_for("billing.index"))

        page.title = title
        page.content_md = content_md
        db.session.commit()
        flash("Draft updated.", "success")
        return redirect(url_for("portfolio.edit", page_id=page.id))

    return render_template("portfolio/edit.html", page=page)


@portfolio_bp.route("/<int:page_id>/publish", methods=["POST"], endpoint="publish")
@login_required
def publish(page_id):
    page = PortfolioPage.query.filter_by(id=page_id, user_id=current_user.id).first_or_404()

    # Publish is Pro-only per KB
    if not can_use_pro(current_user, "portfolio"):
        flash("Publishing is a Pro feature. Upgrade to publish.", "warning")
        return redirect(url_for("billing.index"))

    consume_pro(current_user, "portfolio")
    page.is_public = True
    db.session.commit()

    flash("Portfolio page published! You can share the link now.", "success")
    return redirect(url_for("portfolio.index"))


@portfolio_bp.route("/<int:page_id>/unpublish", methods=["POST"], endpoint="unpublish")
@login_required
def unpublish(page_id):
    page = PortfolioPage.query.filter_by(id=page_id, user_id=current_user.id).first_or_404()
    page.is_public = False
    db.session.commit()
    flash("Unpublished.", "info")
    return redirect(url_for("portfolio.index"))


# -------- Optional: public view route (no login) --------
@portfolio_bp.route("/view/<int:page_id>", methods=["GET"], endpoint="view")
def view(page_id):
    """
    Public read-only view. Only works if is_public=True.
    No auth required. Does not reveal private drafts.
    """
    page = PortfolioPage.query.get_or_404(page_id)
    if not page.is_public:
        abort(404)
    # Optionally hide author PII; we only show the page contents.
    return render_template("portfolio/view.html", page=page)
