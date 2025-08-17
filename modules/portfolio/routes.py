from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from models import db, PortfolioPage
from limits import enforce_free_feature, spend_coins

portfolio_bp = Blueprint("portfolio", __name__, template_folder="../../templates")

@portfolio_bp.route("/", methods=["GET","POST"])
@login_required
@enforce_free_feature("portfolio")
def portfolio():
    page=PortfolioPage.query.filter_by(user_id=current_user.id).first()
    if request.method=="POST":
        title=request.form.get("title","")
        about=request.form.get("about","")
        skills=request.form.get("skills","")
        mode=request.form.get("mode","fast")
        ok,msg,spend=spend_coins(current_user,"portfolio",mode)
        if not ok:
            flash(msg,"error"); return render_template("portfolio/edit.html", page=page)
        if not page:
            page=PortfolioPage(user_id=current_user.id)
            db.session.add(page)
        page.title=title; page.about_html=about; page.skills_csv=skills
        db.session.commit()
        flash("Portfolio saved.","success")
        return redirect(url_for("portfolio.view_portfolio", slug=page.slug))
    return render_template("portfolio/edit.html", page=page)

@portfolio_bp.route("/<slug>")
def view_portfolio(slug):
    page=PortfolioPage.query.filter_by(slug=slug).first_or_404()
    return render_template("portfolio/view.html", page=page)
