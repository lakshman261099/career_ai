# modules/jobpack/helpers.py
import os, json, re, textwrap
from typing import Dict, Any, Tuple
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

def _clean(s:str, max_chars:int=24000) -> str:
    s = s.strip()
    return s[:max_chars]

def _truncate_input(jd:str, resume:str, jd_limit:int=12000, resume_limit:int=9000)->Tuple[str,str]:
    return _clean(jd, jd_limit), _clean(resume, resume_limit)

def _sys_fast():
    return (
        "You are CareerBoost Job Pack (FAST). "
        "Given a role, a pasted job description, and an optional resume snippet, return a concise JSON with: "
        "`meta` (analysis_mode, role), `scores` (ats_format, skills, experience, impact, domain_fit each score 0-100 and 1-2 bullet notes), "
        "`missing_skills` (top 3-5), `tailored_resume_bullets` (3 bullets, STAR-style), "
        "`interview_questions` (3-4 with short sample_answer), and `overall_verdict` (status: 'Strong'|'Borderline'|'Reach', summary, recommendations: 3). "
        "No preamble. No markdown. JSON only."
    )

def _sys_deep():
    return (
        "You are CareerBoost Job Pack (DEEP), an expert ATS evaluator, recruiter, and career coach. "
        "You must produce a rich, **strict JSON** object ONLY (no markdown) with these keys: "
        "`meta` {analysis_mode:'deep', role, seniority}, "
        "`scores` {ats_format:{score,notes[2-3]}, skills:{score,notes[2-3]}, experience:{score,notes[2-3]}, impact:{score,notes[2-3]}, domain_fit:{score,notes[2-3]}}, "
        "`missing_skills` [5-8 items], "
        "`tailored_resume_bullets` [5-7 STAR bullets], "
        "`cover_letter` (7-10 sentence tight paragraph), "
        "`interview_questions` [6-8 {question,type:'behavioral'|'technical'|'open',sample_answer}], "
        "`follow_up_actions` [3-5], "
        "`overall_verdict` {status:'Strong'|'Borderline'|'Reach', summary, recommendations[3-5]}. "
        "Rules: never fabricate employment; base strictly on provided JD and resume; use crisp language; keep JSON valid."
    )

def _user_prompt(role:str, jd_text:str, resume_text:str, deep:bool)->str:
    want = "deep" if deep else "fast"
    return textwrap.dedent(f"""
    Role: {role}

    Job Description (text):
    {jd_text}

    Resume (text; may be partial):
    {resume_text}

    Produce the {want} JSON object exactly as specified in the system instructions.
    """)

def fast_jobpack_llm(role:str, jd_text:str, resume_text:str, user_id:int|None=None, spend:Dict[str,int]|None=None) -> Dict[str,Any]:
    # MOCK path
    if current_app.config.get("MOCK", True) or _client() is None:
        data = {
            "meta": {"analysis_mode":"fast","role": role},
            "scores": {
                "ats_format":{"score": 86,"notes":["Clear headings","Simple layout"]},
                "skills":{"score": 78,"notes":["Good core stack","Add 1-2 domain tools"]},
                "experience":{"score": 75,"notes":["Relevant academic projects","1 internship would help"]},
                "impact":{"score": 77,"notes":["Quantify results more","Tie to outcomes"]},
                "domain_fit":{"score": 80,"notes":["Understands basics","Learn product metrics"]},
            },
            "missing_skills": ["SQL","A/B testing"],
            "tailored_resume_bullets":[
                "Built a dashboard that reduced manual QA time by 22%.",
                "Automated weekly reporting with Python; saved 6 hrs/week.",
                "Collaborated with 3 peers to ship a data mini-project (STAR).",
            ],
            "interview_questions":[
                {"question":"Walk me through a project with ambiguity.","sample_answer":"Context, goal, your approach, outcome, lesson.","type":"behavioral"},
                {"question":"How would you design a metric pipeline?","sample_answer":"Clarify SLA, data sources, schema, quality, monitoring.","type":"technical"},
                {"question":"What gaps do you see in our product?","sample_answer":"Hypothesize issues, propose quick experiments.","type":"product"},
            ],
            "overall_verdict":{"status":"Borderline","summary":"Competitive with clear growth areas.","recommendations":["Quantify 2 bullets","Close 2 skill gaps","Add 1 project"]},
        }
        return data

    # Real call
    jd_text, resume_text = _truncate_input(jd_text, resume_text, 8000, 6000)
    model = current_app.config.get("OPENAI_MODEL_FAST","gpt-4o-mini")
    messages = [
        {"role":"system","content": _sys_fast()},
        {"role":"user","content": _user_prompt(role, jd_text, resume_text, deep=False)}
    ]
    cli = _client()
    resp = cli.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.4,
        max_tokens=3800
    )
    content = resp.choices[0].message.content.strip()
    try:
        data = json.loads(content)
    except Exception:
        data = {"error":"LLM returned non-JSON", "raw":content}
    # log usage
    if user_id:
        _log_usage(user_id, "jobpack", "fast", model, {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens
        }, spend)
    return data

def deep_jobpack_llm(role:str, jd_text:str, resume_text:str, user_id:int|None=None, spend:Dict[str,int]|None=None) -> Dict[str,Any]:
    if current_app.config.get("MOCK", True) or _client() is None:
        data = {
            "meta": {"analysis_mode":"deep","role": role,"seniority":"Intern/Entry"},
            "scores": {
                "ats_format":{"score": 88,"notes":["Keyword-rich, simple layout","Standard section headers"]},
                "skills":{"score": 82,"notes":["Solid Python/SQL","Add dbt or Airflow basics"]},
                "experience":{"score": 78,"notes":["Good academic projects","Ownership could be clearer"]},
                "impact":{"score": 81,"notes":["Quantify results","Tie to user/business outcomes"]},
                "domain_fit":{"score": 84,"notes":["Understands domain basics","Add 1–2 metrics examples"]},
            },
            "missing_skills": ["Experiment design","Data modeling","Versioned analytics"],
            "tailored_resume_bullets":[
                "Led a 3-person team to deliver X, improving Y by 24% using Z (STAR).",
                "Automated A with B, cutting cycle time by 31% and saving 10 hrs/week.",
                "Designed C experiment; analyzed with Python; insights drove decision D.",
                "Improved pipeline reliability from 92→99% by adding validation checks.",
                "Built KPI dashboard used by 5 stakeholders weekly; reduced ad hoc requests."
            ],
            "cover_letter":"I’m excited about the role. My experience aligns with your needs—including building dashboards, improving reliability, and collaborating across teams. I learn fast, quantify outcomes, and would love to contribute.",
            "interview_questions":[
                {"question":"Tell me about a time you influenced without authority.","type":"behavioral","sample_answer":"Stakeholders, alignment, experiment, impact, lesson."},
                {"question":"Design a data pipeline for metric X.","type":"technical","sample_answer":"SLA, schema, lineage, quality, orchestration, monitoring."},
                {"question":"Debug a drop in metric Y.","type":"technical","sample_answer":"Hypotheses, logs, segment checks, experiments, rollback."},
                {"question":"Favorite project?","type":"open","sample_answer":"Context, constraints, role, obstacles, measurable impact."},
                {"question":"How do you prioritize?","type":"behavioral","sample_answer":"Value vs effort, stakeholders, quick wins, risk."},
                {"question":"What would you build in first 30 days?","type":"open","sample_answer":"Listen, map KPIs, fix papercuts, ship one visible win."},
            ],
            "follow_up_actions":["Connect with a team member","Ship a mini-project","Apply via company portal","Refine resume bullets"],
            "overall_verdict":{"status":"Strong","summary":"Good fit with skill gaps that are fixable quickly.","recommendations":["Add 1 measurable project","Close experiment design gap","Show ownership in bullets"]},
        }
        return data

    jd_text, resume_text = _truncate_input(jd_text, resume_text, 12000, 9000)
    model = current_app.config.get("OPENAI_MODEL_DEEP","gpt-4o")
    messages = [
        {"role":"system","content": _sys_deep()},
        {"role":"user","content": _user_prompt(role, jd_text, resume_text, deep=True)}
    ]
    cli = _client()
    resp = cli.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.35,
        max_tokens=14000  # adjust to cost budget later
    )
    content = resp.choices[0].message.content.strip()
    try:
        data = json.loads(content)
    except Exception:
        data = {"error":"LLM returned non-JSON", "raw":content}
    if user_id:
        _log_usage(user_id, "jobpack", "deep", model, {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens
        }, spend)
    return data
