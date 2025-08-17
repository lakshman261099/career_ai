# modules/portfolio/helpers.py
import os, json, textwrap
from typing import Dict, Any, Tuple
from flask import current_app
from models import db, UsageLedger, PortfolioPage
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

def _client():
    if current_app.config.get("MOCK", True): return None
    if not os.getenv("OPENAI_API_KEY"): return None
    return OpenAI()

def _log_usage(user_id:int, feature:str, mode:str, model:str, usage:Dict[str,int], spend:Dict[str,int]|None=None):
    try:
        rec = UsageLedger(
            user_id=user_id, feature=feature, mode=mode, model=model,
            prompt_tokens=usage.get("prompt_tokens",0),
            completion_tokens=usage.get("completion_tokens",0),
            total_tokens=usage.get("total_tokens",0),
            silver_spent=(spend or {}).get("silver",0),
            gold_spent=(spend or {}).get("gold",0),
        )
        db.session.add(rec); db.session.commit()
    except Exception:
        db.session.rollback()

def _sys_fast():
    return (
        "You are CareerBoost Portfolio (FAST). Generate succinct HTML sections based on a student's resume/profile text. "
        "Return JSON only with keys: title, about_html, skills (array), experience_html, education_html. "
        "Crisp, recruiter-friendly. No external links unless provided. No markdown."
    )

def _sys_deep():
    return (
        "You are CareerBoost Portfolio (DEEP). You craft a standout single-page portfolio and one optional detailed project. "
        "Return **JSON only** with keys: title, about_html, skills (array), experience_html, education_html, links (array of {label,url}), "
        "project_ideas (array of 3 ideas, each {title, why_this, what_you_build, how_to_execute, tips}), "
        "and if `include_project_title` is provided, also a `project_html` section elaborating that selected idea. "
        "Write clean, minimal HTML. No CSS, no script tags."
    )

def _user_prompt(resume_text:str, title_hint:str, include_project_title:str|None, deep:bool)->str:
    extra = f"\nSelected project to include: {include_project_title}\n" if include_project_title else ""
    mode = "deep" if deep else "fast"
    return textwrap.dedent(f"""
    Mode: {mode}
    Title hint: {title_hint}
    Resume/Profile text:
    {resume_text}
    {extra}
    Produce the JSON as specified by the system. Keep HTML safe and concise.
    """)

def build_portfolio_fast(resume_text:str, title_hint:str, user_id:int|None=None, spend:Dict[str,int]|None=None)->Dict[str,Any]:
    if current_app.config.get("MOCK", True) or _client() is None:
        return {
            "title": title_hint or "Student Portfolio",
            "about_html":"<p>Ambitious student passionate about data and products.</p>",
            "skills":["Python","SQL","Dashboards"],
            "experience_html":"<ul><li>Built a course project that automated reports.</li></ul>",
            "education_html":"<p>B.Tech, 2026</p>",
            "links":[]
        }
    model = current_app.config.get("OPENAI_MODEL_FAST","gpt-4o-mini")
    messages = [
        {"role":"system","content": _sys_fast()},
        {"role":"user","content": _user_prompt(resume_text, title_hint, None, deep=False)}
    ]
    cli=_client()
    resp = cli.chat.completions.create(model=model, messages=messages, temperature=0.4, max_tokens=4500)
    content = resp.choices[0].message.content.strip()
    try:
        data = json.loads(content)
    except Exception:
        data = {"error":"LLM returned non-JSON", "raw":content}
    if user_id:
        _log_usage(user_id, "portfolio", "fast", model, {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens
        }, spend)
    return data

def build_portfolio_deep(resume_text:str, title_hint:str, include_project_title:str|None, user_id:int|None=None, spend:Dict[str,int]|None=None)->Dict[str,Any]:
    if current_app.config.get("MOCK", True) or _client() is None:
        ideas = [
            {"title":"Admissions Insights Dashboard","why_this":"Quantifiable impact and data storytelling.",
             "what_you_build":"A dashboard summarizing student KPIs with filters.",
             "how_to_execute":"Collect sample data; model; build charts; write insights.",
             "tips":"Focus on clarity; add annotations; show before/after."},
            {"title":"A/B Test Analyzer","why_this":"Demonstrates experimental thinking.",
             "what_you_build":"CLI that evaluates test outcomes given CSV.",
             "how_to_execute":"Implement statistical tests and effect size.",
             "tips":"Document assumptions; show edge cases."},
            {"title":"Resume Ranker","why_this":"Search relevance and ranking.",
             "what_you_build":"Keyword-weighted scoring of resumes/JDs.",
             "how_to_execute":"Tokenize, weight skills, compute scores.",
             "tips":"Explain bias risks and mitigations."},
        ]
        proj_html = ""
        if include_project_title:
            for i in ideas:
                if i["title"].lower()==include_project_title.lower():
                    proj_html = f"<h2>{i['title']}</h2><p>{i['why_this']}</p><h3>What you build</h3><p>{i['what_you_build']}</p><h3>How</h3><p>{i['how_to_execute']}</p><h3>Tips</h3><p>{i['tips']}</p>"
        return {
            "title": title_hint or "Student Portfolio",
            "about_html":"<p>Curious builder focused on measurable outcomes.</p>",
            "skills":["Python","SQL","Experimentation","Dashboards"],
            "experience_html":"<ul><li>Project: Automated analytics with 20% time saving.</li></ul>",
            "education_html":"<p>B.Tech, 2026</p>",
            "links":[{"label":"GitHub","url":"https://github.com/"}],
            "project_ideas": ideas,
            "project_html": proj_html
        }

    model = current_app.config.get("OPENAI_MODEL_DEEP","gpt-4o")
    messages = [
        {"role":"system","content": _sys_deep()},
        {"role":"user","content": _user_prompt(resume_text, title_hint, include_project_title, deep=True)}
    ]
    cli=_client()
    resp = cli.chat.completions.create(model=model, messages=messages, temperature=0.35, max_tokens=12000)
    content = resp.choices[0].message.content.strip()
    try:
        data = json.loads(content)
    except Exception:
        data = {"error":"LLM returned non-JSON", "raw":content}
    if user_id:
        _log_usage(user_id, "portfolio", "deep", model, {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens
        }, spend)
    return data
