# modules/portfolio/routes.py

import traceback
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user
from models import db, PortfolioPage
from limits import authorize_and_consume, can_use_pro, consume_pro

portfolio_bp = Blueprint("portfolio", __name__, template_folder="../../templates/portfolio")


@portfolio_bp.route("/", endpoint="index")
@login_required
def index():
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


@portfolio_bp.route("/new", methods=["GET", "POST"], endpoint="new")
@login_required
def new():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip() or "Untitled"
        content_md = request.form.get("content_md") or ""
        try:
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

        except Exception as e:
            current_app.logger.exception("Portfolio new error: %s", e)
            try:
                db.session.rollback()
            except Exception:
                pass
            flash("Couldn’t create the draft. Please try again.", "danger")

    return render_template("portfolio/new.html")


@portfolio_bp.route("/<int:page_id>/edit", methods=["GET", "POST"], endpoint="edit")
@login_required
def edit(page_id):
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
            # Editing a draft counts the same as creating (throttle abuse)
            if not authorize_and_consume(current_user, "portfolio"):
                flash("Daily limit reached. Try again tomorrow or go Pro.", "warning")
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

    return render_template("portfolio/edit.html", page=page)


@portfolio_bp.route("/<int:page_id>/publish", methods=["POST"], endpoint="publish")
@login_required
def publish(page_id):
    try:
        page = PortfolioPage.query.filter_by(id=page_id, user_id=current_user.id).first_or_404()

        # Publish is Pro-only per KB
        if not can_use_pro(current_user, "portfolio"):
            flash("Publishing is a Pro feature. Upgrade to publish.", "warning")
            return redirect(url_for("billing.index"))

        # Only consume after successful state change
        page.is_public = True
        db.session.commit()
        consume_pro(current_user, "portfolio")

        flash("Portfolio page published! You can share the link now.", "success")
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
    try:
        page = PortfolioPage.query.filter_by(id=page_id, user_id=current_user.id).first_or_404()
        page.is_public = False
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


@portfolio_bp.route("/view/<int:page_id>", methods=["GET"], endpoint="view")
def view(page_id):
    """
    Public read-only view. Only works if is_public=True.
    No auth required. Does not reveal private drafts.
    """
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
