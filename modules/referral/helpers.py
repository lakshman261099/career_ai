# modules/referral/helpers.py
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

def _log_usage(user_id:int, feature:str, mode:str, model:str, usage:Dict[str,int]):
    try:
        rec = UsageLedger(
            user_id=user_id, feature=feature, mode=mode, model=model,
            prompt_tokens=usage.get("prompt_tokens",0),
            completion_tokens=usage.get("completion_tokens",0),
            total_tokens=usage.get("total_tokens",0),
        )
        db.session.add(rec); db.session.commit()
    except Exception:
        db.session.rollback()

def _sys():
    return (
        "You are CareerBoost Referral Trainer. Given a company and role (plus optional person summary), "
        "output JSON ONLY with: titles_to_target (3-6 role titles), why_you (3-5 bullets), "
        "playbook (3 steps), messages {warm, cold, follow_up} each 2-4 sentences. "
        "Be respectful, concise, no emails or scraping suggestions."
    )

def _user(company_role:str, person_hint:str|None)->str:
    return textwrap.dedent(f"""
    Target: {company_role}
    Person hint (optional): {person_hint or ''}
    Produce JSON with titles_to_target, why_you, playbook, and messages {{warm,cold,follow_up}}.
    """)

def coach(company_role:str, person_hint:str|None, user_id:int|None=None)->Dict[str,Any]:
    if current_app.config.get("MOCK", True) or _client() is None:
        return {
            "titles_to_target":["Senior Analyst","Team Lead","Hiring Manager"],
            "why_you":["Your skills match the stack","You quantify outcomes","You learn fast"],
            "playbook":["Connect politely","Share a one-line value","Ask for referral only if invited"],
            "messages":{
                "warm":"Hey! Noticed your team does X. My work in Y could help. Open to a quick chat?",
                "cold":"Hello—admire the team’s work on X. I’ve built Y with measurable impact. Could we briefly chat?",
                "follow_up":"Just following up—happy to share a 1-page summary or small demo if helpful."
            }
        }
    model=current_app.config.get("OPENAI_MODEL_FAST","gpt-4o-mini")
    cli=_client()
    resp=cli.chat.completions.create(
        model=model,
        messages=[{"role":"system","content":_sys()},
                  {"role":"user","content":_user(company_role, person_hint)}],
        temperature=0.4, max_tokens=2500
    )
    content=resp.choices[0].message.content.strip()
    try:
        data=json.loads(content)
    except Exception:
        data={"error":"LLM returned non-JSON","raw":content}
    if user_id:
        _log_usage(user_id,"referral","free",model,{
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens
        })
    return data
