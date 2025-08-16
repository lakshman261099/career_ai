# modules/portfolio/routes.py
import os, re, unicodedata, json
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import db, PortfolioPage, ProjectDetail, ResumeAsset, Subscription
from limits import enforce_free_feature, is_pro_user
from openai import OpenAI

portfolio_bp = Blueprint("portfolio", __name__)

def _slugify(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii","ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]","", value).strip().lower()
    return re.sub(r"[-\s]+","-", value)[:60] or "portfolio"

def _openai_client():
    key = os.getenv("OPENAI_API_KEY","")
    return OpenAI(api_key=key) if key else None

def _mock_projects(n=3):
    samples = [
        {
            "title": "Product Analytics Dashboard",
            "summary": "Build a metrics dashboard tracking activation, retention and feature adoption.",
            "why": "Demonstrates SQL, visualization, and product sense.",
            "how": ["Define north-star & guardrail metrics", "Model events", "Dashboards with filters"],
            "tips": ["Start with a single table", "Add cohort view", "Ship a GIF demo"]
        },
        {
            "title": "Experiments Playbook",
            "summary": "Design & analyze 2 A/B tests (real or simulated).",
            "why": "Shows experimental design & inference.",
            "how": ["Pick a feature", "Define hypothesis", "Analyze uplift & CI"],
            "tips": ["Mind power & MDE", "Use CUPED if needed"]
        },
        {
            "title": "Data Pipelines & ETL",
            "summary": "Ingest APIs, clean data, and publish a dataset for analytics.",
            "why": "Demonstrates pipelines & quality checks.",
            "how": ["Ingest 2 APIs", "Normalize schema", "Publish dbt models"],
            "tips": ["Automate checks", "Document assumptions"]
        },
    ]
    return samples[:n]

def _ai_projects_from_resume(resume_text: str, count: int = 3):
    if current_app.config.get("MOCK", True):
        return _mock_projects(count)
    client = _openai_client()
    if not client:
        return _mock_projects(count)
    prompt = f"""From the resume below, propose {count} portfolio project ideas.
Each: title, summary, why it matters, 3-5 how steps, 2-3 tips.
Resume:
{resume_text[:4000]}"""
    resp = client.chat.completions.create(
        model=current_app.config.get("OPENAI_MODEL","gpt-4o-mini"),
        messages=[{"role":"user","content":prompt}],
        temperature=0.4,
    )
    text = resp.choices[0].message.content.strip()
    # naive parse fallback
    ideas = _mock_projects(count)
    try:
        # try to parse bullet sections if present
        parts = [p.strip("- ").strip() for p in text.split("\n\n") if p.strip()]
        out = []
        for p in parts[:count]:
            out.append({
                "title": p.split("\n")[0][:80],
                "summary": "Summary based on resume.",
                "why": "Highlights strengths from resume.",
                "how": ["Outline", "Build", "Polish"],
                "tips": ["Keep scope tight", "Show demo"]
            })
        if len(out)>=1: return out[:count]
        return ideas
    except Exception:
        return ideas

def _ai_project_detail(title: str, resume_text: str):
    if current_app.config.get("MOCK", True):
        return f"""
        <h2 class="text-2xl font-bold mb-2">{title}</h2>
        <p class="text-slate-600 mb-3">A guided project designed to showcase your skills to recruiters.</p>
        <h3 class="font-semibold">Why this project</h3>
        <p>It maps to core role expectations and gives you measurable results to discuss.</p>
        <h3 class="font-semibold mt-3">How to build it</h3>
        <ol class="list-decimal pl-5 space-y-1">
          <li>Plan scope & success metric</li>
          <li>Build v1 with a minimal dataset</li>
          <li>Instrument, iterate, polish</li>
          <li>Record a 60-sec demo video</li>
        </ol>
        <h3 class="font-semibold mt-3">Tips</h3>
        <ul class="list-disc pl-5">
          <li>Keep it shippable under a week</li>
          <li>Show before/after impact</li>
        </ul>
        """
    client = _openai_client()
    if not client:
        return _ai_project_detail(title, resume_text="")  # fallback to mock
    prompt = f"""Create a project deep-dive page for "{title}" based on this resume:
{resume_text[:4000]}
Sections: Why this matters, How to build (step list), Tips, Deliverables, What you'll learn.
Return clean HTML only."""
    resp = client.chat.completions.create(
        model=current_app.config.get("OPENAI_MODEL","gpt-4o-mini"),
        messages=[{"role":"user","content":prompt}],
        temperature=0.4,
    )
    return resp.choices[0].message.content

def _links_json_from_form(form):
    links = []
    for label in ["GitHub","LinkedIn","Website"]:
        url = (form.get(label.lower()) or "").strip()
        if url:
            links.append({"label":label, "url":url})
    return json.dumps(links)

@portfolio_bp.get("/")
@login_required
def index():
    page = PortfolioPage.query.filter_by(user_id=current_user.id).first()
    # Free users: no publishing; show “idea generator”
    if not is_pro_user(current_user):
        return render_template("portfolio_edit.html", page=page, is_free=True, suggestions=None)
    # Pro: can edit full sections and publish
    return render_template("portfolio_edit.html", page=page, is_free=False, suggestions=None)

# FREE: one idea only (consumes 'portfolio' free run)
@portfolio_bp.post("/idea")
@login_required
@enforce_free_feature("portfolio")
def free_idea():
    # get resume from latest ResumeAsset if any, else from textarea
    resume_text = (request.form.get("resume_text") or "").strip()
    if not resume_text:
        ra = ResumeAsset.query.filter_by(user_id=current_user.id).order_by(ResumeAsset.created_at.desc()).first()
        if ra:
            resume_text = ra.content_text or ""
    ideas = _ai_projects_from_resume(resume_text, count=1)
    flash("Here’s a project idea you can build. Upgrade to Pro to publish a portfolio and get 3 curated options.", "success")
    page = None
    return render_template("portfolio_edit.html", page=page, is_free=True, suggestions=ideas)

# PRO: scan → 3 options, allow include/skip
@portfolio_bp.post("/scan")
@login_required
def scan():
    if not is_pro_user(current_user):
        flash("Portfolio publishing is Pro only.", "error")
        return redirect(url_for("pricing"))
    resume_text = (request.form.get("resume_text") or "").strip()
    # Pull from saved resume if available and not provided
    if not resume_text:
        ra = ResumeAsset.query.filter_by(user_id=current_user.id).order_by(ResumeAsset.created_at.desc()).first()
        if ra:
            resume_text = ra.content_text or ""
    ideas = _ai_projects_from_resume(resume_text, count=3)
    page = PortfolioPage.query.filter_by(user_id=current_user.id).first()
    return render_template("portfolio_edit.html", page=page, is_free=False, suggestions=ideas)

# PRO: save & publish (with or without selected project)
@portfolio_bp.post("/save")
@login_required
def save():
    if not is_pro_user(current_user):
        flash("Portfolio publishing is Pro only. Upgrade to Pro.", "error")
        return redirect(url_for("pricing"))

    title = (request.form.get("title") or "My Portfolio").strip()
    about = (request.form.get("about_html") or "").strip()
    skills = (request.form.get("skills_csv") or "").strip()
    exp = (request.form.get("experience_html") or "").strip()
    edu = (request.form.get("education_html") or "").strip()
    links_json = _links_json_from_form(request.form)
    selected_title = (request.form.get("include_project_title") or "").strip()
    resume_text = (request.form.get("resume_text") or "").strip()

    page = PortfolioPage.query.filter_by(user_id=current_user.id).first()
    if not page:
        slug = _slugify(title) or f"user-{current_user.id}"
        page = PortfolioPage(user_id=current_user.id, title=title, slug=slug,
                             about_html=about, skills_csv=skills,
                             experience_html=exp, education_html=edu,
                             links_json=links_json)
    else:
        page.title = title; page.about_html = about; page.skills_csv = skills
        page.experience_html = exp; page.education_html = edu; page.links_json = links_json
        if not page.slug: page.slug = _slugify(title) or f"user-{current_user.id}"
    db.session.add(page); db.session.commit()

    # If a project was chosen, generate a ProjectDetail and link it
    if selected_title:
        html = _ai_project_detail(selected_title, resume_text)
        pslug = _slugify(f"{page.slug}-{selected_title}")
        proj = ProjectDetail(user_id=current_user.id, portfolio_id=page.id, title=selected_title, slug=pslug, html=html)
        db.session.add(proj); db.session.commit()

    flash("Portfolio published!", "success")
    return redirect(url_for("portfolio.index"))

@portfolio_bp.get("/view/<slug>")
def view(slug):
    page = PortfolioPage.query.filter_by(slug=slug).first_or_404()
    projs = ProjectDetail.query.filter_by(portfolio_id=page.id).order_by(ProjectDetail.created_at.desc()).all()
    links = []
    try:
       links = json.loads(page.links_json or "[]")
    except Exception:
       links = []
    return render_template("portfolio_public.html", page=page, projects=projs, links=links, hybrid=True)

@portfolio_bp.get("/project/<slug>")
def project(slug):
    proj = ProjectDetail.query.filter_by(slug=slug).first_or_404()
    return render_template("project_detail.html", project=proj)
