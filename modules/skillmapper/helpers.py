# modules/skillmapper/helpers.py
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

def _sys():
    return (
        "You are CareerBoost Skill Mapper. From a student's up-to-30 skills (with optional levels), "
        "output JSON ONLY: `roles` (8-10 items). Each role: {title, why_fit (3 bullets), gaps (2-3), learning_links (2-3 URLs), search_strings (2-3)}. "
        "No preamble, no markdown."
    )

def _user(skills_blob:str)->str:
    return textwrap.dedent(f"""
    Skills (<=30), with optional levels, comma-separated or line-separated:
    {skills_blob}

    Produce the JSON object with `roles` array as specified.
    """)

def map_roles_deep(skills_blob:str, user_id:int|None=None, spend:Dict[str,int]|None=None)->Dict[str,Any]:
    if current_app.config.get("MOCK", True) or _client() is None:
        return {
            "roles":[
                {"title":"Data Analyst","why_fit":["Python/SQL foundation","Comfort with dashboards","Analytical mindset"],"gaps":["A/B testing"],"learning_links":["https://mode.com/sql-tutorial"],"search_strings":["\"Data Analyst\" intern SQL"]},
                {"title":"Product Analyst","why_fit":["Product curiosity","Metrics thinking","Basic stats"],"gaps":["Experiment design"],"learning_links":["https://experiment.guide"],"search_strings":["\"Product Analyst\" internship"]}
            ]
        }
    model = current_app.config.get("OPENAI_MODEL_DEEP","gpt-4o")
    cli=_client()
    resp = cli.chat.completions.create(
        model=model,
        messages=[{"role":"system","content":_sys()}, {"role":"user","content":_user(skills_blob)}],
        temperature=0.35,
        max_tokens=6000
    )
    content = resp.choices[0].message.content.strip()
    try:
        data = json.loads(content)
    except Exception:
        data = {"error":"LLM returned non-JSON","raw":content}
    if user_id:
        _log_usage(user_id, "skillmapper", "deep", model, {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens
        }, spend)
    return data
