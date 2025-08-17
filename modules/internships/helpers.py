# modules/internships/helpers.py
import os, json, textwrap
from typing import Dict, Any
from flask import current_app
from models import db, UsageLedger
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
        "You are CareerBoost Internship Analyzer (FAST). Return JSON only: "
        "{ benefits (3-5 bullets: what student will learn), required_skills (3-6), suggested_prep (2-3 quick links), summary (2-3 sentences) }."
    )

def _sys_deep():
    return (
        "You are CareerBoost Internship Analyzer (DEEP). Return JSON only with: "
        "overview (4-6 sentences), expectations (bullets), growth_path (bullets), "
        "skills_gain (bullets), gaps_vs_resume (3-5), learning_plan (3-5 links with label+url), summary (2-3 sentences). "
        "No markdown. Plain JSON."
    )

def analyze_fast(jd_text:str, resume_text:str, user_id:int|None=None, spend:Dict[str,int]|None=None)->Dict[str,Any]:
    if current_app.config.get("MOCK", True) or _client() is None:
        return {
            "benefits":["Real-world teamwork","Practical Python","Basic dashboards"],
            "required_skills":["Python","SQL","Collaboration"],
            "suggested_prep":["https://mode.com/sql-tutorial","https://pandas.pydata.org/"],
            "summary":"Good entry-level exposure with measurable learning."
        }
    model=current_app.config.get("OPENAI_MODEL_FAST","gpt-4o-mini")
    cli=_client()
    resp=cli.chat.completions.create(
        model=model,
        messages=[{"role":"system","content":_sys_fast()},
                  {"role":"user","content":f"Internship description:\n{jd_text}\n\nResume snippet:\n{resume_text}\nReturn JSON."}],
        temperature=0.4, max_tokens=4000
    )
    content=resp.choices[0].message.content.strip()
    try:
        data=json.loads(content)
    except Exception:
        data={"error":"LLM returned non-JSON","raw":content}
    if user_id:
        _log_usage(user_id,"internships","fast",model,{
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens
        }, spend)
    return data

def analyze_deep(jd_text:str, resume_text:str, user_id:int|None=None, spend:Dict[str,int]|None=None)->Dict[str,Any]:
    if current_app.config.get("MOCK", True) or _client() is None:
        return {
            "overview":"You will work with analytics and product stakeholders to build measurable outcomes.",
            "expectations":["Own small tasks","Communicate clearly","Ship weekly progress"],
            "growth_path":["From intern to junior analyst","Own one metric area","Lead a tiny project"],
            "skills_gain":["SQL fluency","Data storytelling","Experiment basics"],
            "gaps_vs_resume":["Experiment design","Visualization polish"],
            "learning_plan":[{"label":"AB testing intro","url":"https://experiment.guide"}],
            "summary":"Strong fit if you close 1-2 gaps quickly."
        }
    model=current_app.config.get("OPENAI_MODEL_DEEP","gpt-4o")
    cli=_client()
    resp=cli.chat.completions.create(
        model=model,
        messages=[{"role":"system","content":_sys_deep()},
                  {"role":"user","content":f"Internship description:\n{jd_text}\n\nResume snippet:\n{resume_text}\nReturn JSON only."}],
        temperature=0.35, max_tokens=9000
    )
    content=resp.choices[0].message.content.strip()
    try:
        data=json.loads(content)
    except Exception:
        data={"error":"LLM returned non-JSON","raw":content}
    if user_id:
        _log_usage(user_id,"internships","deep",model,{
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens
        }, spend)
    return data
