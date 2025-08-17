from flask import Blueprint, render_template
from flask_login import login_required, current_user
from models import db, PortfolioPage

portfolio_bp = Blueprint("portfolio", __name__, template_folder="../../templates")

@portfolio_bp.route("/")
@login_required
def index():
    page = PortfolioPage.query.filter_by(user_id=current_user.id).first()

    show_public = False
    public_url = None
    if page and getattr(page, "is_published", False):
        slug = getattr(page, "slug", None)
        if getattr(page, "public_url", None):
            public_url = page.public_url
            show_public = True
        elif slug:
            public_url = f"/portfolio/view/{slug}"
            show_public = True

    return render_template(
        "portfolio/edit.html",   # <<— matches your folder
        page=page,
        show_public=show_public,
        public_url=public_url
    )
