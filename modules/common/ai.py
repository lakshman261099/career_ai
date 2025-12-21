# modules/common/ai.py
import os
import json
import re
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timezone

# -------------------------------------------------------------------
# Config (env-driven)
# -------------------------------------------------------------------
OPENAI_MODEL_FAST = os.getenv("OPENAI_MODEL_FAST", "gpt-4o-mini")
OPENAI_MODEL_DEEP = os.getenv("OPENAI_MODEL_DEEP", "gpt-4o")

CAREER_AI_VERSION = os.getenv("CAREER_AI_VERSION", "2025-Q4")
FRESHNESS_NOTE = (
    "CareerAI model snapshot "
    + CAREER_AI_VERSION
    + " · tuned for Indian early-career tech talent and global remote roles."
)


# -------------------------------------------------------------------
# Simple helpers
# -------------------------------------------------------------------
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _inputs_digest(obj: Any) -> str:
    try:
        import hashlib

        s = json.dumps(obj, sort_keys=True)[:5000]
        return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()
    except Exception:
        return "sha256:na"


def _to_sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if not text.endswith((".", "!", "?")):
        text = text + "."
    return text


# -------------------------------------------------------------------
# Portfolio Builder (free & pro) JSON schemas + prompts
# -------------------------------------------------------------------
PORTFOLIO_FREE_JSON_SCHEMA = r"""
{
  "type": "object",
  "additionalProperties": false,
  "required": ["mode", "ideas", "meta"],
  "properties": {
    "mode": { "type": "string", "enum": ["free"] },
    "ideas": {
      "type": "array",
      "minItems": 1,
      "maxItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["title", "why", "what", "milestones", "resume_bullets", "stack"],
        "properties": {
          "title": { "type": "string", "minLength": 3, "maxLength": 120 },
          "why":   { "type": "string", "minLength": 12, "maxLength": 260 },
          "what":  { "type": "array", "minItems": 4, "maxItems": 6, "items": { "type": "string", "maxLength": 110 } },
          "milestones": { "type": "array", "minItems": 3, "maxItems": 4, "items": { "type": "string", "maxLength": 110 } },
          "resume_bullets": { "type": "array", "minItems": 3, "maxItems": 4, "items": { "type": "string", "maxLength": 160 } },
          "stack": { "type": "array", "minItems": 3, "maxItems": 8, "items": { "type": "string", "maxLength": 32 } }
        }
      }
    },
    "meta": {
      "type": "object",
      "additionalProperties": false,
      "required": ["generated_at_utc", "inputs_digest"],
      "properties": {
        "generated_at_utc": { "type": "string" },
        "inputs_digest":    { "type": "string" },
        "profile_used":     { "type": "boolean" }
      }
    }
  }
}
"""

PORTFOLIO_PRO_JSON_SCHEMA = r"""
{
  "type": "object",
  "additionalProperties": false,
  "required": ["mode", "ideas", "meta"],
  "properties": {
    "mode": { "type": "string", "enum": ["pro"] },
    "ideas": {
      "type": "array",
      "minItems": 3,
      "maxItems": 3,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["title", "why", "what", "milestones", "rubric", "risks", "stretch_goals", "resume_bullets", "stack", "mentor_note"],
        "properties": {
          "title": { "type": "string", "minLength": 3, "maxLength": 120 },
          "why":   { "type": "string", "minLength": 12, "maxLength": 300 },
          "what":  { "type": "array", "minItems": 6, "maxItems": 6, "items": { "type": "string", "maxLength": 130 } },
          "milestones": { "type": "array", "minItems": 4, "maxItems": 6, "items": { "type": "string", "maxLength": 120 } },
          "rubric": { "type": "array", "minItems": 5, "maxItems": 6, "items": { "type": "string", "maxLength": 120 } },
          "risks":  { "type": "array", "minItems": 3, "maxItems": 4, "items": { "type": "string", "maxLength": 120 } },
          "stretch_goals": { "type": "array", "minItems": 3, "maxItems": 4, "items": { "type": "string", "maxLength": 120 } },
          "resume_bullets": { "type": "array", "minItems": 3, "maxItems": 5, "items": { "type": "string", "maxLength": 160 } },
          "stack": { "type": "array", "minItems": 4, "maxItems": 10, "items": { "type": "string", "maxLength": 32 } },
          "mentor_note": { "type": "string", "minLength": 30, "maxLength": 240 }
        }
      }
    },
    "meta": {
      "type": "object",
      "additionalProperties": false,
      "required": ["generated_at_utc", "inputs_digest"],
      "properties": {
        "generated_at_utc": { "type": "string" },
        "inputs_digest":    { "type": "string" },
        "profile_used":     { "type": "boolean" }
      }
    }
  }
}
"""

PORTFOLIO_FREE_PROMPT = """\
You are PortfolioBuilderFree, a friendly early-career project coach for a Flask web app.

Return ONLY valid JSON matching the JSON schema below.
No markdown, no commentary, no code fences.

Freshness: {freshness}

CONTEXT:
- The user is usually a student or early-career engineer in India or similar markets.
- They want 1 simple, but high-signal project idea, based on their skills and interests.
- Their Profile Portal may or may not be complete. Their resume may be noisy.

PROFILE:
- profile_json: {profile_json}
- skills_json (extracted from resume): {skills_json}
- extra_text (user-typed interests): {extra_text}

Expectations:
- Propose exactly 1 *beginner-friendly* project idea.
- The idea should be:
  - doable in 3–4 weeks part-time,
  - realistic for a student / junior,
  - something that can become a standout portfolio piece.
- "why" must be short and motivating, not generic fluff.
- "what" is 4–6 bullet points of concrete implementation tasks.
- "milestones": 3–4 steps to feel "done enough" for LinkedIn + resume.
- "resume_bullets": 3–4 first-draft bullets (student can tweak later).
- "stack": 3–8 tools/techs that the user is either already using or can reasonably pick up.

JSON Schema:
{json_schema}

Return JSON only.
"""

PORTFOLIO_PRO_PROMPT = """\
You are PortfolioBuilderPro, a deep project coach for a Flask web app.

Return ONLY valid JSON matching the JSON schema below.
No markdown, no commentary, no code fences.

Freshness: {freshness}

CONTEXT:
- The user is a serious student / early-career engineer.
- They want a *small portfolio* of 3 standout projects, not a random list.
- Each project should be:
  - realistically doable in 4–6 weeks part-time.
  - clearly aligned to *one job direction* (e.g. data analyst, frontend engineer, ML, cloud, etc.).
  - something that recruiters will *actually* want to discuss.

PROFILE:
- profile_json: {profile_json}
- skills_json (extracted from resume): {skills_json}
- extra_text (user-typed interests): {extra_text}

EXPECTATIONS:
- Generate exactly 3 project ideas.
- Each idea must include:
  - "title": crisp, 3–8 words, no buzzword salad.
  - "why": 2–4 sentences explaining the real hiring signal.
  - "what": 6 bullet points of concrete implementation tasks.
  - "milestones": 4–6 key milestones to track progress.
  - "rubric": 5–6 bullet points that an interviewer could use to judge the project (depth, clarity, robustness, impact).
  - "risks": 3–4 real risks or pitfalls students usually face.
  - "stretch_goals": 3–4 optional "if you have time" enhancements.
  - "resume_bullets": 3–5 draft resume bullets.
    - These should read like *finished outcomes* they can paste into resume/LinkedIn after doing the work.
    - Do NOT tell them to rewrite their entire resume; just give sharp bullets that plug into existing sections.
  - "stack": 4–10 specific tools/techs (Python, React, Power BI, etc.).
  - "mentor_note": a short pep-talk with 1–2 pieces of hard truth that will look good as a compact paragraph in a large-font UI.

JSON Schema:
{json_schema}

Return JSON only.
"""


def _light_validate_portfolio_free(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {"mode": "free", "ideas": [], "meta": {}}
    ideas = data.get("ideas") or []
    if not isinstance(ideas, list):
        ideas = []
    if ideas:
        i = ideas[0]
        i["title"] = (i.get("title") or "Portfolio Project")[:120]
        i["why"] = _to_sentence(i.get("why") or "")
        i["what"] = [(str(x)[:110]) for x in (i.get("what") or [])][:6]
        i["milestones"] = [(str(x)[:110]) for x in (i.get("milestones") or [])][:3]
        i["resume_bullets"] = [(str(x)[:160]) for x in (i.get("resume_bullets") or [])][:3]
        i["stack"] = [(str(x)[:32]) for x in (i.get("stack") or [])][:4]
        ideas = [i]
    meta = data.get("meta") or {}
    return {"mode": "free", "ideas": ideas, "meta": meta}


def _light_validate_portfolio_pro(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {"mode": "pro", "ideas": [], "meta": {}}
    ideas = data.get("ideas") or []
    if not isinstance(ideas, list):
        ideas = []
    out = []
    for i in ideas[:3]:
        i = dict(i)
        i["title"] = (i.get("title") or "Portfolio Project")[:120]
        i["why"] = _to_sentence(i.get("why") or "")
        i["what"] = [(str(x)[:120]) for x in (i.get("what") or [])][:6]
        i["milestones"] = [(str(x)[:120]) for x in (i.get("milestones") or [])][:6]
        i["rubric"] = [(str(x)[:120]) for x in (i.get("rubric") or [])][:6]
        i["risks"] = [(str(x)[:120]) for x in (i.get("risks") or [])][:4]
        i["stretch_goals"] = [(str(x)[:120]) for x in (i.get("stretch_goals") or [])][:4]
        i["resume_bullets"] = [(str(x)[:160]) for x in (i.get("resume_bullets") or [])][:5]
        i["stack"] = [(str(x)[:32]) for x in (i.get("stack") or [])][:10]
        i["mentor_note"] = (i.get("mentor_note") or "")[:260]
        out.append(i)
    meta = data.get("meta") or {}
    return {"mode": "pro", "ideas": out, "meta": meta}


def generate_portfolio_idea(
    *,
    pro_mode: bool,
    profile_json: Optional[Dict[str, Any]] = None,
    skills_json: Optional[Dict[str, Any]] = None,
    extra_text: str = "",
    return_source: bool = False,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    """
    Portfolio idea generator (free + pro).
    Returns structured JSON that templates can render without worrying about schema errors.
    """
    from openai import OpenAI

    client = OpenAI()
    profile_json = profile_json or {}
    skills_json = skills_json or {}

    prompt_template = PORTFOLIO_PRO_PROMPT if pro_mode else PORTFOLIO_FREE_PROMPT
    schema = PORTFOLIO_PRO_JSON_SCHEMA if pro_mode else PORTFOLIO_FREE_JSON_SCHEMA

    prompt = prompt_template.format(
        freshness=FRESHNESS_NOTE,
        profile_json=json.dumps(profile_json, ensure_ascii=False),
        skills_json=json.dumps(skills_json, ensure_ascii=False),
        extra_text=(extra_text or "")[:2000],
        json_schema=schema,
    )

    used_live_ai = False

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_DEEP if pro_mode else OPENAI_MODEL_FAST,
            messages=[
                {"role": "system", "content": "You output ONLY JSON matching the schema."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5 if pro_mode else 0.6,
            max_tokens=1800 if pro_mode else 1200,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw) if raw else {}
        used_live_ai = True
    except Exception:
        # Fallback: minimal, generic idea to avoid breaking UI
        data = {
            "mode": "pro" if pro_mode else "free",
            "ideas": [
                {
                    "title": "Skill-focused portfolio project",
                    "why": "We could not generate a personalized idea right now, so this is a generic placeholder.",
                    "what": [
                        "Define a small but real problem that you or friends actually face.",
                        "Design a simple solution you can implement with your current skills.",
                        "Implement an MVP focusing on correctness over complexity.",
                        "Document what you built and why in a short README.",
                    ],
                    "milestones": [
                        "Decide on the problem and target user.",
                        "Sketch the user journey and data model.",
                        "Implement a basic but usable version.",
                        "Test with 1–3 friends and write down feedback.",
                    ],
                    "rubric": [],
                    "risks": [],
                    "stretch_goals": [],
                    "resume_bullets": [
                        "Built and shipped a small project based on a real-world problem.",
                    ],
                    "stack": [],
                    "mentor_note": "This is a fallback response. Please try again when the system is stable.",
                }
            ],
            "meta": {
                "generated_at_utc": _utc_now_iso(),
                "inputs_digest": _inputs_digest(
                    {
                        "error": True,
                        "pro_mode": pro_mode,
                    }
                ),
                "profile_used": bool(profile_json),
            },
        }

    if pro_mode:
        clean = _light_validate_portfolio_pro(data)
    else:
        clean = _light_validate_portfolio_free(data)

    # Attach meta if missing
    meta = clean.get("meta") or {}
    if "generated_at_utc" not in meta:
        meta["generated_at_utc"] = _utc_now_iso()
    if "inputs_digest" not in meta:
        meta["inputs_digest"] = _inputs_digest(
            {"pro_mode": pro_mode, "has_profile": bool(profile_json), "has_skills": bool(skills_json)}
        )
    meta.setdefault("profile_used", bool(profile_json))
    clean["meta"] = meta

    return (clean, used_live_ai) if return_source else clean


# 🔁 Backwards-compatible wrapper for portfolio module
def generate_project_suggestions(*args, **kwargs):
    """
    Backwards-compat wrapper around `generate_portfolio_idea`.

    This exists because `modules/portfolio/routes.py` imports
    `generate_project_suggestions`. We delegate to the existing
    `generate_portfolio_idea` while accepting flexible arguments.

    Supported keyword args (any of these are optional):
      - pro_mode / is_pro       → bool
      - profile_json / profile  → dict
      - skills_json / resume_skills → dict
      - extra_text / user_notes → str
      - return_source           → bool

    Any unknown kwargs are passed through to `generate_portfolio_idea`.
    """
    # Defaults
    pro_mode = True
    profile_json = None
    skills_json = None
    extra_text = ""

    # Positional mapping (if any)
    if len(args) >= 1:
        pro_mode = bool(args[0])
    if len(args) >= 2:
        profile_json = args[1]
    if len(args) >= 3:
        skills_json = args[2]
    if len(args) >= 4:
        extra_text = args[3]

    # Keyword overrides
    if "pro_mode" in kwargs:
        pro_mode = bool(kwargs.pop("pro_mode"))
    elif "is_pro" in kwargs:
        pro_mode = bool(kwargs.pop("is_pro"))

    if "profile_json" in kwargs:
        profile_json = kwargs.pop("profile_json")
    elif "profile" in kwargs:
        profile_json = kwargs.pop("profile")

    if "skills_json" in kwargs:
        skills_json = kwargs.pop("skills_json")
    elif "resume_skills" in kwargs:
        skills_json = kwargs.pop("resume_skills")

    if "extra_text" in kwargs:
        extra_text = kwargs.pop("extra_text")
    elif "user_notes" in kwargs:
        extra_text = kwargs.pop("user_notes")

    return generate_portfolio_idea(
        pro_mode=pro_mode,
        profile_json=profile_json,
        skills_json=skills_json,
        extra_text=extra_text,
        **kwargs,
    )


# -------------------------------------------------------------------
# Internship Analyzer (Pro) JSON schema + prompt
# -------------------------------------------------------------------
INTERNSHIP_JSON_SCHEMA = r"""
{
  "type": "object",
  "additionalProperties": false,
  "required": ["mode", "skill_growth", "skill_enhancement", "new_paths", "resume_boost", "career_impact", "meta"],
  "properties": {
    "mode": { "type": "string", "enum": ["pro"] },
    "skill_growth": {
      "type": "array",
      "items": { "type": "string" }
    },
    "skill_enhancement": {
      "type": "array",
      "items": { "type": "string" }
    },
    "new_paths": {
      "type": "array",
      "items": { "type": "string" }
    },
    "resume_boost": {
      "type": "array",
      "items": { "type": "string" }
    },
    "career_impact": {
      "type": "string"
    },
    "meta": {
      "type": "object",
      "additionalProperties": false,
      "required": ["generated_at_utc", "inputs_digest"],
      "properties": {
        "generated_at_utc": { "type": "string" },
        "inputs_digest": { "type": "string" }
      }
    }
  }
}
"""

PRO_INTERNSHIP_ANALYZER_PROMPT = """\
You are InternshipAnalyzer, a Pro career coach.
Return ONLY valid JSON matching the schema.

Freshness: {freshness}

Rules:
- Mode: "pro".
- Input: internship description + student profile (paste-only; no scraping).
- Provide arrays for: skill_growth, skill_enhancement, new_paths, resume_boost.
- Provide a single string for career_impact.
- meta must include generated_at_utc + inputs_digest.
- Be grounded in the internship text and the profile.
- Your language should be clear and punchy so it looks great in a large-font UI.

Inputs:
- internship_text: {internship_text}
- profile_json: {profile_json}

JSON Schema:
{json_schema}

Respond with JSON only.
"""


def generate_internship_analysis(
    *,
    internship_text: str,
    profile_json: Optional[Dict[str, Any]] = None,
    return_source: bool = False,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    """Deep Internship Analyzer (Pro)."""
    from openai import OpenAI

    client = OpenAI()
    profile_json = profile_json or {}

    internship_text = (internship_text or "").strip()
    if len(internship_text) > 12000:
        internship_text = internship_text[:12000]

    prompt = PRO_INTERNSHIP_ANALYZER_PROMPT.format(
        freshness=FRESHNESS_NOTE,
        internship_text=internship_text,
        profile_json=json.dumps(profile_json, ensure_ascii=False),
        json_schema=INTERNSHIP_JSON_SCHEMA,
    )

    used_live_ai = False
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_DEEP,
            messages=[
                {"role": "system", "content": "You output ONLY JSON matching the schema."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=1600,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw) if raw else {}
        used_live_ai = True
    except Exception:
        # Fallback minimal
        data = {
            "mode": "pro",
            "skill_growth": [],
            "skill_enhancement": [],
            "new_paths": [],
            "resume_boost": [],
            "career_impact": "We could not complete the deep internship analysis due to an internal error.",
            "meta": {
                "generated_at_utc": _utc_now_iso(),
                "inputs_digest": _inputs_digest({"error": True}),
            },
        }

    # Light validation
    if not isinstance(data, dict):
        data = {}
    skill_growth = data.get("skill_growth") or []
    if not isinstance(skill_growth, list):
        skill_growth = []
    skill_enhancement = data.get("skill_enhancement") or []
    if not isinstance(skill_enhancement, list):
        skill_enhancement = []
    new_paths = data.get("new_paths") or []
    if not isinstance(new_paths, list):
        new_paths = []
    resume_boost = data.get("resume_boost") or []
    if not isinstance(resume_boost, list):
        resume_boost = []

    clean = {
        "mode": "pro",
        "skill_growth": [str(x)[:160] for x in skill_growth][:10],
        "skill_enhancement": [str(x)[:160] for x in skill_enhancement][:10],
        "new_paths": [str(x)[:160] for x in new_paths][:10],
        "resume_boost": [str(x)[:160] for x in resume_boost][:10],
        "career_impact": (data.get("career_impact") or "")[:800],
        "meta": data.get("meta") or {},
    }

    meta = clean["meta"] or {}
    if "generated_at_utc" not in meta:
        meta["generated_at_utc"] = _utc_now_iso()
    if "inputs_digest" not in meta:
        meta["inputs_digest"] = _inputs_digest(
            {
                "has_profile": bool(profile_json),
                "internship_len": len(internship_text or ""),
            }
        )
    clean["meta"] = meta

    return (clean, used_live_ai) if return_source else clean


# -------------------------------------------------------------------
# Referral Trainer — safe generator (no API)
# -------------------------------------------------------------------
def generate_referral_messages_ai(
    *,
    user_profile: Optional[Dict[str, Any]] = None,
    contact: Optional[Dict[str, Any]] = None,
    job_context: Optional[Dict[str, Any]] = None,
    tone: str = "warm",
    mode: str = "free",
    return_source: bool = False,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    """
    Simple, safe referral message generator.

    This is intentionally defensive:
    - No OpenAI call (so it never crashes due to API issues).
    - Always returns a dict with the same top-level shape.

    Expected by modules/referral.routes:
      - "mode": "free" or "pro"
      - "messages": list of templates
      - "meta": basic metadata
    """
    user_profile = user_profile or {}
    contact = contact or {}
    job_context = job_context or {}

    student_name = (user_profile.get("full_name") or user_profile.get("name") or "there").strip()
    if not student_name:
        student_name = "there"

    contact_name = (contact.get("name") or "Hi").strip()
    contact_role = (contact.get("role") or contact.get("title") or "").strip()
    contact_company = (contact.get("company") or "").strip()

    job_title = (job_context.get("job_title") or job_context.get("role") or "this role").strip()
    job_source = (job_context.get("source") or "LinkedIn / company careers page").strip()

    is_pro = (str(mode).lower() == "pro")

    # Base intro line depending on tone
    if tone == "formal":
        greeting = f"Hi {contact_name},"
    elif tone == "casual":
        greeting = f"Hey {contact_name},"
    else:
        greeting = f"Hi {contact_name},"

    linkedin_connect = f"""{greeting}

I came across your profile while exploring opportunities related to {job_title} at {contact_company or 'your company'}. I'm currently building my skills in this area and would love to learn from your experience.

If you're open to it, I'd appreciate connecting here on LinkedIn and following your work.

Thanks,
{student_name}
""".strip()

    linkedin_referral = f"""{greeting}

Hope you're doing well. I'm currently preparing for roles like {job_title} and noticed an opening via {job_source}. Based on your experience at {contact_company or 'the company'}, I wanted to ask for your honest guidance.

If, after reviewing my profile, you feel comfortable, it would mean a lot if you could either:
- Share a quick tip on how to stand out for {job_title}, or
- Point me to the best way to apply / any internal referral process.

Either way, I appreciate your time. Thanks for reading this.

Best,
{student_name}
""".strip()

    email_template = f"""Subject: Quick guidance on {job_title} at {contact_company or 'your company'}

{greeting}

I'm {student_name}, currently strengthening my skills in areas relevant to {job_title}. I found a role via {job_source} and your background at {contact_company or 'the company'} stood out.

I'm not expecting any favors, but if you have 2–3 minutes, I would be grateful for:
- One piece of advice on how to position myself for {job_title}, or
- Any suggestion on what the team looks for in strong candidates.

If it makes sense after seeing my profile, I'd be honoured if you could keep me in mind for future opportunities or share any internal application pointers.

Thank you for your time and consideration.

Warm regards,
{student_name}
""".strip()

    messages = [
        {
            "channel": "linkedin",
            "label": "LinkedIn — connection request",
            "body": linkedin_connect,
        },
        {
            "channel": "linkedin",
            "label": "LinkedIn — referral / guidance message",
            "body": linkedin_referral,
        },
        {
            "channel": "email",
            "label": "Email — guidance + soft referral",
            "body": email_template,
        },
    ]

    result = {
        "mode": "pro" if is_pro else "free",
        "tone": tone,
        "messages": messages,
        "meta": {
            "generated_at_utc": _utc_now_iso(),
            "inputs_digest": _inputs_digest(
                {
                    "mode": mode,
                    "tone": tone,
                    "has_user_profile": bool(user_profile),
                    "has_contact": bool(contact),
                    "has_job_context": bool(job_context),
                }
            ),
        },
    }

    used_live_ai = False
    return (result, used_live_ai) if return_source else result


# -------------------------------------------------------------------
# Skill Mapper (pipe-text) prompt & parser
# -------------------------------------------------------------------
SIMPLE_SKILLMAPPER_OUTPUT_FORMAT = """\
You must output lines in ONLY this format (no markdown, no extra text):

ROLE|title|level|match_score|why_fit|skills_csv|gaps_csv|projects_semi|salary_band|region
STEPS|step1; step2; step3; ...
SUMMARY|one paragraph summary of their overall positioning and next moves.

Where:
- title: short role name (e.g. "Data Analyst", "Frontend Engineer").
- level: one of ["Intern / Trainee", "Junior / Entry-level", "Mid-level", "Senior"].
- match_score: integer 0–100 (higher = better fit).
- why_fit: 1–2 sentences about why this role fits them.
- skills_csv: comma-separated skills that match (3–10 items).
- gaps_csv: comma-separated missing skills (3–10 items).
- projects_semi: semi-colon-separated micro-project ideas (3–6 items).
- salary_band: approximate range string (e.g. "5–8 LPA (India, tier-1 city)").
- region: market label (e.g. "India product companies", "Remote-friendly global", etc.).
"""


def _build_skillmapper_prompt(
    *,
    pro_mode: bool,
    profile_json: Optional[Dict[str, Any]] = None,
    resume_text: str = "",
    free_text_skills: str = "",
    hints: Optional[Dict[str, Any]] = None,
) -> str:
    profile_json = profile_json or {}
    hints = hints or {}
    region_focus = hints.get("region_focus") or hints.get("region_sector") or "India · early-career tech roles"
    focus = hints.get("focus") or "current_snapshot"
    target_domain = hints.get("target_domain") or ""
    path_type = hints.get("path_type") or "job"

    persona = "You are SkillMapper, a career path coach."
    if path_type == "startup":
        persona = "You are SkillMapperStartup, a startup path coach for students/freshers."
    elif path_type == "freelance":
        persona = "You are SkillMapperFreelance, a freelance income path coach for students/freshers."

    resume_excerpt = (resume_text or "").strip()
    if len(resume_excerpt) > 12000:
        resume_excerpt = resume_excerpt[:12000]

    free_text_skills = (free_text_skills or "").strip()
    if len(free_text_skills) > 4000:
        free_text_skills = free_text_skills[:4000]

    prompt = f"""{persona}

Freshness: {FRESHNESS_NOTE}

CONTEXT:
- The user is a student or early-career technologist.
- They want a realistic, encouraging map of roles they can target next.
- You must be honest about gaps, but never demotivating.
- Your ROLE/STEP/SUMMARY lines should be crisp and readable so they look awesome in a large-font UI.

PROFILE_JSON:
{json.dumps(profile_json, ensure_ascii=False)}

RESUME_TEXT (excerpt, may be noisy):
{resume_excerpt}

FREE_TEXT_SKILLS (optional extra info from user):
{free_text_skills}

SETTINGS:
- pro_mode: {pro_mode}
- region_focus: {region_focus}
- focus: {focus}
- target_domain_hint: {target_domain}
- path_type: {path_type}

INSTRUCTIONS:
- You MUST output only the ROLE|, STEPS| and SUMMARY| lines described in the format below.
- No markdown, no extra commentary, no JSON, no bullet markers.
- Think like a senior mentor who has seen thousands of CVs from Indian colleges and global bootcamps.
- Default to India early-career markets unless hints suggest otherwise.

If path_type == "startup":
- Interpret "roles" as startup-oriented directions (e.g. "AI tools micro-SaaS founder", "no-code MVP builder", etc.).
- Emphasize founder-like responsibilities, validation and MVPs, not titles like "CEO".
- Salary_band can be "founder income potential" in INR or "unknown / upside-based".

If path_type == "freelance":
- Interpret "roles" as freelance services (e.g. "Power BI dashboard freelancer", "React landing page developer").
- Salary_band can be phrased as typical monthly/side income bands instead of CTC.

FORMAT:
"""
    prompt += "\n\n" + SIMPLE_SKILLMAPPER_OUTPUT_FORMAT
    prompt += "\n\nRemember: Only output ROLE|, STEPS|, SUMMARY| lines. No other text."
    return prompt


def _parse_skillmapper_text(raw: str) -> Dict[str, Any]:
    """
    Parse the pipe-delimited SkillMapper output into a structured dict.

    Expected lines:
      ROLE|title|level|match_score|why_fit|skills_csv|gaps_csv|projects_semi|salary_band|region
      STEPS|step1; step2; ...
      SUMMARY|some text...

    We are tolerant: if a line is slightly malformed, we skip or trim.
    """
    roles: List[Dict[str, Any]] = []
    next_steps: List[str] = []
    impact_summary = ""

    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Empty SkillMapper response")

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("ROLE|"):
            parts = line.split("|")
            if len(parts) < 10:
                continue
            _, title, level, match_str, why_fit, skills_csv, gaps_csv, projects_semi, salary_band, region = parts[:10]
            try:
                match_score = int(match_str)
            except Exception:
                match_score = 0
            skills = [s.strip() for s in skills_csv.split(",") if s.strip()]
            gaps = [g.strip() for g in gaps_csv.split(",") if g.strip()]
            projects = [p.strip() for p in projects_semi.split(";") if p.strip()]
            roles.append(
                {
                    "title": title[:80],
                    "level": level[:60],
                    "match_score": max(0, min(100, match_score)),
                    "why_fit": why_fit[:400],
                    "skills": skills[:12],
                    "gaps": gaps[:12],
                    "micro_projects": projects[:8],
                    "salary": salary_band[:120],
                    "region": region[:120],
                }
            )
        elif line.startswith("STEPS|"):
            text = line[len("STEPS|") :].strip()
            if text:
                for part in text.split(";"):
                    p = part.strip()
                    if p:
                        next_steps.append(p[:240])
        elif line.startswith("SUMMARY|"):
            impact_summary = line[len("SUMMARY|") :].strip()[:800]

    return {
        "roles": roles,
        "top_roles": roles[:3],
        "hiring_now": [],
        "market_insights": {},
        "learning_paths": [],
        "next_steps": next_steps,
        "impact_summary": impact_summary,
    }


def _light_validate_skillmap(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {
            "mode": "free",
            "roles": [],
            "top_roles": [],
            "hiring_now": [],
            "market_insights": {},
            "learning_paths": [],
            "next_steps": [],
            "impact_summary": "",
            "call_to_action": "",
            "meta": {},
        }

    roles = data.get("roles") or []
    if not isinstance(roles, list):
        roles = []
    cleaned_roles = []
    for r in roles[:6]:
        r = dict(r)
        r["title"] = (r.get("title") or r.get("role_title") or "Role")[:80]
        r["level"] = (r.get("level") or r.get("seniority") or "Entry-level")[:60]
        try:
            ms = int(r.get("match_score") or r.get("fit_score") or 0)
        except Exception:
            ms = 0
        r["match_score"] = max(0, min(100, ms))
        r["why_fit"] = (r.get("why_fit") or r.get("summary") or "")[:400]
        r["skills"] = [(str(x)[:40]) for x in (r.get("skills") or [])][:12]
        r["gaps"] = [(str(x)[:40]) for x in (r.get("gaps") or [])][:12]
        r["micro_projects"] = [(str(x)[:120]) for x in (r.get("micro_projects") or [])][:8]
        r["salary"] = (r.get("salary") or "")[:120]
        r["region"] = (r.get("region") or "")[:120]
        cleaned_roles.append(r)

    next_steps = data.get("next_steps") or []
    if not isinstance(next_steps, list):
        next_steps = []
    next_steps = [(str(x)[:240]) for x in next_steps][:12]

    impact_summary = (data.get("impact_summary") or "")[:800]
    call_to_action = (data.get("call_to_action") or impact_summary)[:800]

    out = {
        "mode": data.get("mode") or "free",
        "roles": cleaned_roles,
        "top_roles": cleaned_roles[:3],
        "hiring_now": data.get("hiring_now") or [],
        "market_insights": data.get("market_insights") or {},
        "learning_paths": data.get("learning_paths") or [],
        "next_steps": next_steps,
        "impact_summary": impact_summary,
        "call_to_action": call_to_action,
        "meta": data.get("meta") or {},
    }
    return out


def generate_skillmap(
    *,
    pro_mode: bool,
    profile_json: Optional[Dict[str, Any]] = None,
    resume_text: str = "",
    free_text_skills: str = "",
    hints: Optional[Dict[str, Any]] = None,
    return_source: bool = False,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    """
    Skill Mapper generator (v1, pipe-text core).
    This is used by Skill Mapper v2 HTML flow; HTML routes wrap this with credit logic and snapshots.
    """
    from openai import OpenAI

    client = OpenAI()
    profile_json = profile_json or {}
    hints = hints or {}

    prompt = _build_skillmapper_prompt(
        pro_mode=pro_mode,
        profile_json=profile_json,
        resume_text=resume_text,
        free_text_skills=free_text_skills,
        hints=hints,
    )

    used_live_ai = False

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_DEEP if pro_mode else OPENAI_MODEL_FAST,
            messages=[
                {
                    "role": "system",
                    "content": "You follow the instructions exactly and output ONLY the specified line format.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.45 if pro_mode else 0.6,
            max_tokens=1200 if pro_mode else 900,
        )

        raw = (resp.choices[0].message.content or "").strip()
        parsed = _parse_skillmapper_text(raw)

        data = {
            "mode": "pro" if pro_mode else "free",
            "roles": parsed.get("roles") or [],
            "top_roles": parsed.get("top_roles") or parsed.get("roles") or [],
            "hiring_now": parsed.get("hiring_now") or [],
            "market_insights": parsed.get("market_insights") or {},
            "learning_paths": parsed.get("learning_paths") or [],
            "next_steps": parsed.get("next_steps") or [],
            "impact_summary": parsed.get("impact_summary") or "",
            "call_to_action": parsed.get("impact_summary") or "",
            "meta": {},
        }

        data = _light_validate_skillmap(data)

        meta = data.get("meta") or {}
        if "generated_at_utc" not in meta:
            meta["generated_at_utc"] = _utc_now_iso()
        if "inputs_digest" not in meta:
            meta["inputs_digest"] = _inputs_digest(
                {
                    "pro_mode": pro_mode,
                    "has_profile": bool(profile_json),
                    "has_resume": bool(resume_text),
                    "has_free_text": bool(free_text_skills),
                    "hints": hints,
                }
            )
        meta.setdefault("version", CAREER_AI_VERSION)
        data["meta"] = meta

        used_live_ai = True
        return (data, used_live_ai) if return_source else data

    except Exception as e:
        # Fallback: minimal safe struct
        fallback = {
            "mode": "pro" if pro_mode else "free",
            "roles": [],
            "top_roles": [],
            "hiring_now": [],
            "market_insights": {},
            "learning_paths": [],
            "next_steps": [],
            "impact_summary": "",
            "call_to_action": "ERROR: " + str(e),
            "meta": {
                "generated_at_utc": _utc_now_iso(),
                "inputs_digest": _inputs_digest({"error": True}),
            },
        }
        data = _light_validate_skillmap(fallback)
        return (data, used_live_ai) if return_source else data


# -------------------------------------------------------------------
# Referral Trainer — outreach message generator (Silver 🪙, OpenAI)
# -------------------------------------------------------------------
def generate_referral_messages(
    contact: Dict[str, Any],
    profile: Dict[str, Any],
    return_source: bool = False,
) -> Dict[str, str] | Tuple[Dict[str, str], bool]:
    """
    Generates warm, cold, and follow-up referral outreach templates.
    Used by Referral Trainer (Silver credit feature).

    contact = {
        "name": "...",
        "role": "...",
        "company": "...",
        "email": "...",
        "source": "LinkedIn / alumni / event / other"
    }

    profile = {
        "role": "Target role",
        "highlights": "Your key achievements / summary",
        "job_description": "Optional — for tailoring"
    }
    """
    from openai import OpenAI

    client = OpenAI()
    used_live_ai = False

    c = {k: (v or "").strip() for k, v in (contact or {}).items()}
    p = {k: (v or "").strip() for k, v in (profile or {}).items()}
    p["job_description"] = p.get("job_description", "")[:2500]

    prompt = f"""
You are ReferralTrainer, a polite, concise outreach-message generator.
Return ONLY a JSON object with keys: warm, cold, follow.

Context:
- Student is reaching out for a referral or guidance.
- Messages must be short, human, friendly, and professional.
- No emojis, no exaggeration, no long paragraphs.

Contact info:
{json.dumps(c, ensure_ascii=False, indent=2)}

Profile:
{json.dumps(p, ensure_ascii=False, indent=2)}

Rules:
- Keep each message 3–6 sentences maximum.
- Tone = respectful, confident, not needy.
- Include EXACT contact name if provided.
- Mention company & role naturally.
- No bullet points.
- If job_description is provided, reference the skills/requirements lightly.

Return JSON ONLY:
{{
  "warm": "text...",
  "cold": "text...",
  "follow": "text..."
}}
"""

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_FAST,
            messages=[
                {
                    "role": "system",
                    "content": "You output ONLY valid JSON with warm/cold/follow outreach messages."
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.55,
            max_tokens=600,
            response_format={"type": "json_object"},
        )

        raw = (resp.choices[0].message.content or "").strip()
        msgs = json.loads(raw) if raw else {}
        used_live_ai = True

    except Exception:
        fallback = {
            "warm": (
                "Hi, I'm reaching out because I'm exploring opportunities related to your team. "
                "I'd appreciate any quick guidance or a referral if appropriate."
            ),
            "cold": (
                "Hello, I came across your profile and wanted to ask if you might be open to briefly "
                "sharing hiring insights or pointing me in the right direction."
            ),
            "follow": (
                "Hi, just following up in case you missed my earlier note. Any small guidance "
                "would mean a lot. Thank you for your time."
            ),
        }
        msgs = fallback
        used_live_ai = False

    return (msgs, used_live_ai) if return_source else msgs


# -------------------------------------------------------------------
# Weekly / Daily Action Coach — AI engine
# -------------------------------------------------------------------
def _light_validate_daily_coach(data: Any) -> Dict[str, Any]:
    """
    Defensive validation so UI never explodes if the model returns odd JSON.

    Expected shape (minimum):
      {
        "session_date": "2025-12-04",
        "day_index": 7,                # interpreted as "week index" in Weekly Coach
        "ai_note": "...short coaching note...",
        "tasks": [
          {
            "id": 1,
            "title": "Finish SQL joins tutorial",
            "detail": "Do 20 practice questions on joins & aggregations.",
            "category": "skills",
            "sort_order": 1,
            "suggested_minutes": 45,
            "guide": "extra coaching note",
            "tags": ["sql", "practice"],
            "phase_label": "Phase 2 · Weeks 5–8 · Projects + practice",
            "week_index": 5,
            "difficulty": "medium",
            "project_label": "Game Portfolio Project",
            "milestone_title": "Implement core gameplay loop",
            "milestone_step": "Code basic player movement & collision",
            "is_done": false
          },
          ...
        ],
        "meta": {...}
      }

    NOTE:
    - This validator is intentionally tolerant:
      - Missing fields are filled with safe defaults.
      - Extra fields are ignored by the DB layer and only used by UI.
    """
    if not isinstance(data, dict):
        data = {}

    session_date = data.get("session_date") or ""
    day_index = data.get("day_index")
    ai_note = data.get("ai_note") or ""
    tasks = data.get("tasks") or []
    meta = data.get("meta") or {}

    clean_tasks: List[Dict[str, Any]] = []
    if isinstance(tasks, list):
        for idx, t in enumerate(tasks, start=1):
            if not isinstance(t, dict):
                continue

            title = (t.get("title") or "").strip()
            if not title:
                continue

            sort_order_raw = t.get("sort_order")
            sort_order = sort_order_raw if isinstance(sort_order_raw, int) else idx

            suggested_raw = t.get("suggested_minutes")
            suggested_minutes = suggested_raw if isinstance(suggested_raw, int) else None

            guide = (t.get("guide") or "").strip()
            tags_val = t.get("tags") or []
            if isinstance(tags_val, list):
                tags = [str(x)[:32] for x in tags_val[:8] if str(x).strip()]
            else:
                tags = []

            phase_label = (t.get("phase_label") or "").strip()[:80]
            week_index_val = t.get("week_index")
            week_index = week_index_val if isinstance(week_index_val, int) else None

            difficulty = (t.get("difficulty") or "").strip()[:32]
            project_label = (t.get("project_label") or "").strip()[:255]
            milestone_title = (t.get("milestone_title") or "").strip()[:255]
            milestone_step = (t.get("milestone_step") or "").strip()[:255]

            clean_tasks.append(
                {
                    "id": t.get("id") or idx,
                    "title": title[:255],
                    "detail": (t.get("detail") or "").strip(),
                    "category": (t.get("category") or "").strip()[:64],
                    "sort_order": sort_order,
                    "suggested_minutes": suggested_minutes,
                    "guide": guide[:400],
                    "tags": tags,
                    # Per-task roadmap hints
                    "phase_label": phase_label,
                    "week_index": week_index,
                    # Project / milestone awareness (P3)
                    "difficulty": difficulty,
                    "project_label": project_label,
                    "milestone_title": milestone_title,
                    "milestone_step": milestone_step,
                    # UI state
                    "is_done": bool(t.get("is_done", False)),
                }
            )

    if not isinstance(meta, dict):
        meta = {}
    if "generated_at_utc" not in meta:
        meta["generated_at_utc"] = _utc_now_iso()
    if "inputs_digest" not in meta:
        meta["inputs_digest"] = _inputs_digest({"source": "weekly_coach"})

    if isinstance(day_index, int):
        safe_day_index = day_index
    else:
        safe_day_index = None

    return {
        "session_date": str(session_date),
        "day_index": safe_day_index,
        "ai_note": ai_note.strip()[:1200],
        "tasks": clean_tasks,
        "meta": meta,
    }

def _light_validate_dualtrack_month(data: Any) -> Dict[str, Any]:
    """
    Defensive validator for 28-day dual-track month plan.

    Normalizes:
    - month_cycle: str
    - ai_note: str
    - weeks: exactly 4 entries (week_number 1..4)
      - each with daily_tasks (7 items day=1..7) + weekly_task
    """
    if not isinstance(data, dict):
        data = {}

    month_cycle = str(data.get("month_cycle") or "").strip()[:64]
    ai_note = str(data.get("ai_note") or "").strip()[:1600]
    weeks_raw = data.get("weeks") or []
    meta = data.get("meta") or {}

    if not isinstance(meta, dict):
        meta = {}

    # helper clamps
    def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
        try:
            x = int(v)
        except Exception:
            return default
        return max(lo, min(hi, x))

    def _clean_category(cat: Any) -> str:
        c = str(cat or "").strip().lower()
        allowed = {"skills", "projects", "career_capital", "planning", "mindset", "wellbeing"}
        return c if c in allowed else "skills"

    def _clean_difficulty(d: Any, default: str = "easy") -> str:
        v = str(d or "").strip().lower()
        allowed = {"easy", "medium", "hard"}
        return v if v in allowed else default

    def _clean_tags(tags_val: Any) -> List[str]:
        if not isinstance(tags_val, list):
            return []
        out = []
        for x in tags_val[:6]:
            s = str(x).strip()
            if s:
                out.append(s[:32])
        return out

    # Normalize weeks by mapping week_number => week payload
    week_map: Dict[int, Dict[str, Any]] = {}
    if isinstance(weeks_raw, list):
        for w in weeks_raw:
            if not isinstance(w, dict):
                continue
            wn = w.get("week_number")
            try:
                wn_int = int(wn)
            except Exception:
                continue
            if wn_int < 1 or wn_int > 4:
                continue
            week_map[wn_int] = w

    clean_weeks: List[Dict[str, Any]] = []

    for wn in range(1, 5):
        w = week_map.get(wn) or {}
        week_note = str(w.get("week_note") or f"Week {wn} focus.").strip()[:1200]

        # daily tasks
        dailies_raw = w.get("daily_tasks") or []
        day_map: Dict[int, Dict[str, Any]] = {}
        if isinstance(dailies_raw, list):
            for t in dailies_raw:
                if not isinstance(t, dict):
                    continue
                try:
                    day = int(t.get("day"))
                except Exception:
                    continue
                if 1 <= day <= 7:
                    day_map[day] = t

        daily_tasks: List[Dict[str, Any]] = []
        for day in range(1, 8):
            t = day_map.get(day) or {}
            title = str(t.get("title") or f"Day {day} task").strip()[:255]
            detail = str(t.get("detail") or "").strip()[:1200]
            category = _clean_category(t.get("category"))
            est = _clamp_int(t.get("estimated_minutes"), 5, 60, 10)
            # For dailies, prefer 5-20 (but keep within schema 5-60)
            if est > 20:
                est = 20
            difficulty = _clean_difficulty(t.get("difficulty"), default="easy")
            tags = _clean_tags(t.get("tags"))

            week_index_val = t.get("week_index")
            if not isinstance(week_index_val, int):
                week_index_val = wn

            daily_tasks.append(
                {
                    "day": day,
                    "title": title,
                    "detail": detail,
                    "category": category,
                    "estimated_minutes": est,
                    "difficulty": difficulty,
                    "tags": tags,
                    "phase_label": str(t.get("phase_label") or "").strip()[:160],
                    "week_index": week_index_val,
                    "project_label": str(t.get("project_label") or "").strip()[:255],
                    "milestone_title": str(t.get("milestone_title") or "").strip()[:255],
                    "milestone_step": str(t.get("milestone_step") or "").strip()[:255],
                }
            )

        # weekly task
        weekly_raw = w.get("weekly_task") or {}
        if not isinstance(weekly_raw, dict):
            weekly_raw = {}

        weekly_title = str(weekly_raw.get("title") or f"Week {wn} milestone").strip()[:255]
        weekly_detail = str(weekly_raw.get("detail") or "").strip()[:1800]
        weekly_category = _clean_category(weekly_raw.get("category"))
        weekly_est = _clamp_int(weekly_raw.get("estimated_minutes"), 90, 480, 240)
        badge = str(weekly_raw.get("milestone_badge") or f"Week {wn} Master").strip()[:64]
        deliverable = str(weekly_raw.get("deliverable") or "").strip()[:255]
        if not deliverable:
            deliverable = "Shipped weekly artifact + README proof"

        weekly_week_index = weekly_raw.get("week_index")
        if not isinstance(weekly_week_index, int):
            weekly_week_index = wn

        weekly_task = {
            "title": weekly_title,
            "detail": weekly_detail,
            "category": weekly_category,
            "estimated_minutes": weekly_est,
            "milestone_badge": badge,
            "phase_label": str(weekly_raw.get("phase_label") or "").strip()[:160],
            "week_index": weekly_week_index,
            "project_label": str(weekly_raw.get("project_label") or "").strip()[:255],
            "milestone_title": str(weekly_raw.get("milestone_title") or "").strip()[:255],
            "milestone_step": str(weekly_raw.get("milestone_step") or "").strip()[:255],
            "deliverable": deliverable,
        }

        clean_weeks.append(
            {
                "week_number": wn,
                "week_note": week_note,
                "daily_tasks": daily_tasks,
                "weekly_task": weekly_task,
            }
        )

    # meta defaults
    if "generated_at_utc" not in meta:
        meta["generated_at_utc"] = _utc_now_iso()
    if "inputs_digest" not in meta:
        meta["inputs_digest"] = _inputs_digest({"source": "dualtrack_month"})
    meta.setdefault("career_ai_version", CAREER_AI_VERSION)

    return {
        "month_cycle": month_cycle,
        "ai_note": ai_note,
        "weeks": clean_weeks,
        "meta": meta,
    }



def _extract_coach_roadmap(path_type: str, dream_plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Extracts a compact roadmap from a Dream Plan for the Coach engine.

    Works with:
      - "raw" DreamPlanner JSON (output of generate_dream_plan)
      - the processed `plan_view` dict from modules/dream/routes.py
      - augmented plan_view that includes selected projects (P3)

    Returns:
      {
        "target_role": str | None,
        "timeline_months": int | None,
        "hours_per_day": int | None,
        "phases": [ { "label": str, "items": [str, ...] }, ... ],
        "resources": {
          "mini_projects": [str, ...],
          "tutorials": [str, ...],
          "resume_bullets": [str, ...],
          "linkedin_actions": [str, ...],
        },
        "projects": [
          {
            "id": 123,                         # DreamPlanProject.id (if provided by routes)
            "title": "Game Portfolio Project",
            "week_start": 3,
            "week_end": 8,
            "milestones": [
              { "title": "...", "detail": "...", "week_hint": 3 },
              ...
            ]
          },
          ...
        ]
      }
    """
    if not isinstance(dream_plan, dict):
        dream_plan = {}

    pt = (path_type or "job").strip().lower()
    if pt not in ("job", "startup"):
        pt = "job"

    # Inputs: support multiple shapes for robustness
    input_block = (
        dream_plan.get("input")
        or dream_plan.get("inputs")
        or dream_plan.get("plan_input")
        or {}
    )
    if not isinstance(input_block, dict):
        input_block = {}

    target_role = (
        input_block.get("target_role")
        or input_block.get("startup_theme")
        or dream_plan.get("target_role")
        or ""
    )

    def _safe_int(val: Any) -> int | None:
        try:
            v = int(val)
            return v if v > 0 else None
        except Exception:
            return None

    timeline_months = (
        _safe_int(input_block.get("timeline_months"))
        or _safe_int(dream_plan.get("timeline_months"))
        or None
    )
    hours_per_day = (
        _safe_int(input_block.get("hours_per_day"))
        or _safe_int(dream_plan.get("hours_per_day"))
        or None
    )

    # Phases (primary for Weekly Coach)
    raw_phases = dream_plan.get("phases") or []
    phases: List[Dict[str, Any]] = []
    if isinstance(raw_phases, list):
        for idx, ph in enumerate(raw_phases, start=1):
            if not isinstance(ph, dict):
                continue
            label = str(ph.get("label") or f"Phase {idx}").strip()[:160]
            items_val = ph.get("items") or []
            if not isinstance(items_val, list):
                items_val = []
            items = [str(x).strip() for x in items_val if str(x).strip()]
            phases.append({"label": label, "items": items[:10]})

    # Fallback if no phases found
    if not phases:
        phases = [{"label": "Phase 1", "items": []}]

    # Resources (support raw + plan_view shapes)
    resources_block = {}
    if "resources" in dream_plan and isinstance(dream_plan["resources"], dict):
        resources_block = dream_plan["resources"]
    else:
        resources_block = {
            "tutorials": dream_plan.get("tutorials"),
            "mini_projects": dream_plan.get("mini_projects"),
            "resume_bullets": dream_plan.get("resume_bullets"),
            "linkedin_actions": dream_plan.get("linkedin_actions"),
        }

    def _coerce_str_list(val, limit: int) -> List[str]:
        if not isinstance(val, list):
            return []
        items = [str(x).strip() for x in val if str(x).strip()]
        return items[:limit]

    resources = {
        "mini_projects": _coerce_str_list(resources_block.get("mini_projects"), 12),
        "tutorials": _coerce_str_list(resources_block.get("tutorials"), 12),
        "resume_bullets": _coerce_str_list(resources_block.get("resume_bullets"), 12),
        "linkedin_actions": _coerce_str_list(resources_block.get("linkedin_actions"), 12),
    }

    # Optional: selected projects for P3 (routes will attach these to plan_view)
    raw_projects = (
        dream_plan.get("projects")
        or dream_plan.get("selected_projects")
        or dream_plan.get("project_selection")
        or []
    )
    projects: List[Dict[str, Any]] = []
    if isinstance(raw_projects, list):
        for p in raw_projects:
            if not isinstance(p, dict):
                continue
            pid = p.get("id") or p.get("project_id")
            title = (
                p.get("title")
                or p.get("custom_title")
                or p.get("name")
                or "Selected Project"
            )

            week_start = _safe_int(p.get("week_start"))
            week_end = _safe_int(p.get("week_end"))

            milestones_raw = p.get("milestones") or []
            milestones: List[Dict[str, Any]] = []
            if isinstance(milestones_raw, list):
                for m in milestones_raw:
                    if not isinstance(m, dict):
                        continue
                    m_title = str(m.get("title") or "").strip()
                    if not m_title:
                        continue
                    m_detail = str(m.get("detail") or m.get("description") or "").strip()
                    week_hint = _safe_int(
                        m.get("week_hint")
                        or m.get("week_index")
                        or m.get("week")
                    )
                    milestones.append(
                        {
                            "title": m_title[:255],
                            "detail": m_detail[:600],
                            "week_hint": week_hint,
                        }
                    )

            projects.append(
                {
                    "id": pid,
                    "title": str(title).strip()[:255],
                    "week_start": week_start,
                    "week_end": week_end,
                    "milestones": milestones,
                }
            )

    return {
        "target_role": target_role or None,
        "timeline_months": timeline_months,
        "hours_per_day": hours_per_day,
        "phases": phases,
        "resources": resources,
        "projects": projects,
    }


def _expand_phases_to_weeks(
    phases: List[Dict[str, Any]],
    timeline_months: Optional[int] = None,
    max_weeks: int = 24,
) -> List[Dict[str, Any]]:
    """
    Convert a list of phases into a "week roadmap", e.g.:

      [
        {"week_index": 1, "phase_index": 1, "phase_label": "Phase 1 · Foundations", "theme": "Fix resume basics"},
        ...
      ]

    Rules:
    - By default we map each phase.item → one week, in order.
    - If timeline_months is provided, we cap the total weeks at ~4 * timeline_months.
    - We never exceed max_weeks.
    """
    weeks: List[Dict[str, Any]] = []
    if not isinstance(phases, list):
        return weeks

    # Approx weeks from months (soft cap)
    if isinstance(timeline_months, int) and timeline_months > 0:
        approx_weeks = max(4, timeline_months * 4)
        hard_cap = min(max_weeks, approx_weeks)
    else:
        hard_cap = max_weeks

    week_idx = 1
    for p_index, ph in enumerate(phases, start=1):
        label = str(ph.get("label") or f"Phase {p_index}").strip()
        items = ph.get("items") or []
        if not isinstance(items, list):
            items = []
        for item in items:
            if week_idx > hard_cap:
                break
            theme = str(item).strip()
            if not theme:
                continue
            weeks.append(
                {
                    "week_index": week_idx,
                    "phase_index": p_index,
                    "phase_label": label[:160],
                    "theme": theme[:160],
                }
            )
            week_idx += 1
        if week_idx > hard_cap:
            break

    return weeks


def _analyze_progress_history(progress_history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Lightweight analysis of the last 10 sessions so the model can
    adjust intensity and difficulty (S4).

    Supports two shapes per session:
      1) Rich payload with a "tasks" list and per-task is_done flags.
      2) Aggregated stats from _progress_history_for_user():
         { "tasks_total": int, "tasks_done": int, ... }
    """
    if not isinstance(progress_history, list) or not progress_history:
        return {
            "avg_completion_ratio": None,
            "intensity_hint": "normal",
            "sessions_considered": 0,
        }

    total_tasks = 0
    total_done = 0
    sessions_considered = 0

    for sess in progress_history[-10:]:
        if not isinstance(sess, dict):
            continue

        sess_total = 0
        sess_done = 0

        # Shape 1: full tasks list
        tasks = sess.get("tasks")
        if isinstance(tasks, list) and tasks:
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                sess_total += 1
                if bool(t.get("is_done")):
                    sess_done += 1

        # Shape 2: aggregate stats (no explicit tasks list)
        elif (
            isinstance(sess.get("tasks_total"), int)
            and sess.get("tasks_total") > 0
            and isinstance(sess.get("tasks_done"), int)
        ):
            sess_total = max(0, int(sess["tasks_total"]))
            sess_done = max(0, min(int(sess["tasks_done"]), sess_total))

        if sess_total == 0:
            continue

        total_tasks += sess_total
        total_done += sess_done
        sessions_considered += 1

    if total_tasks == 0 or sessions_considered == 0:
        return {
            "avg_completion_ratio": None,
            "intensity_hint": "normal",
            "sessions_considered": 0,
        }

    completion_ratio: float = total_done / float(total_tasks)

    # Simple heuristic for intensity
    if completion_ratio < 0.4:
        intensity_hint = "light"
    elif completion_ratio > 0.8:
        intensity_hint = "intense"
    else:
        intensity_hint = "normal"

    return {
        "avg_completion_ratio": completion_ratio,
        "intensity_hint": intensity_hint,
        "sessions_considered": sessions_considered,
    }

# -------------------------------------------------------------------
# Weekly Coach (Dual-Track) — 28-day month plan (4 weeks) JSON schema + prompt
# -------------------------------------------------------------------

DUALTRACK_MONTH_JSON_SCHEMA = r"""\
{
  "type": "object",
  "required": ["month_cycle", "ai_note", "weeks", "meta"],
  "additionalProperties": false,
  "properties": {
    "month_cycle": { "type": "string", "minLength": 6, "maxLength": 64 },
    "ai_note": { "type": "string", "minLength": 10, "maxLength": 1600 },
    "weeks": {
      "type": "array",
      "minItems": 4,
      "maxItems": 4,
      "items": {
        "type": "object",
        "required": ["week_number", "week_note", "daily_tasks", "weekly_task"],
        "additionalProperties": false,
        "properties": {
          "week_number": { "type": "integer", "minimum": 1, "maximum": 4 },
          "week_note": { "type": "string", "minLength": 8, "maxLength": 1200 },

          "daily_tasks": {
            "type": "array",
            "minItems": 7,
            "maxItems": 7,
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": ["day", "title", "detail", "category", "estimated_minutes"],
              "properties": {
                "day": { "type": "integer", "minimum": 1, "maximum": 7 },
                "title": { "type": "string", "minLength": 3, "maxLength": 255 },
                "detail": { "type": "string", "minLength": 0, "maxLength": 1200 },
                "category": { "type": "string", "minLength": 0, "maxLength": 64 },
                "estimated_minutes": { "type": "integer", "minimum": 5, "maximum": 60 },
                "difficulty": { "type": "string", "minLength": 0, "maxLength": 32 },
                "tags": {
                  "type": "array",
                  "items": { "type": "string", "maxLength": 32 }
                },

                "phase_label": { "type": "string", "minLength": 0, "maxLength": 160 },
                "week_index": { "type": ["integer", "null"] },
                "project_label": { "type": "string", "minLength": 0, "maxLength": 255 },
                "milestone_title": { "type": "string", "minLength": 0, "maxLength": 255 },
                "milestone_step": { "type": "string", "minLength": 0, "maxLength": 255 }
              }
            }
          },

          "weekly_task": {
            "type": "object",
            "additionalProperties": false,
            "required": ["title", "detail", "category", "estimated_minutes", "milestone_badge", "deliverable"],
            "properties": {
              "title": { "type": "string", "minLength": 3, "maxLength": 255 },
              "detail": { "type": "string", "minLength": 0, "maxLength": 1800 },
              "category": { "type": "string", "minLength": 0, "maxLength": 64 },
              "estimated_minutes": { "type": "integer", "minimum": 90, "maximum": 480 },
              "milestone_badge": { "type": "string", "minLength": 3, "maxLength": 64 },

              "phase_label": { "type": "string", "minLength": 0, "maxLength": 160 },
              "week_index": { "type": ["integer", "null"] },
              "project_label": { "type": "string", "minLength": 0, "maxLength": 255 },
              "milestone_title": { "type": "string", "minLength": 0, "maxLength": 255 },
              "milestone_step": { "type": "string", "minLength": 0, "maxLength": 255 },
              "deliverable": { "type": "string", "minLength": 0, "maxLength": 255 }
            }
          }
        }
      }
    },
    "meta": {
      "type": "object",
      "additionalProperties": true,
      "required": ["generated_at_utc", "inputs_digest"],
      "properties": {
        "generated_at_utc": { "type": "string" },
        "inputs_digest": { "type": "string" },
        "path_type": { "type": "string" },
        "career_ai_version": { "type": "string" },
        "target_lpa": { "type": "string" }
      }
    }
  }
}
"""


DUALTRACK_MONTH_PROMPT = """\
You are CareerAI Coach for a Flask web app.
Return ONLY valid JSON matching the schema below.
No markdown. No commentary. No code fences.

Freshness: {freshness}

GOAL:
Generate a 28-day dual-track plan for ONE month cycle (4 weeks).

DUAL-TRACK RULES:
- Daily Tasks (Maintenance): 7 per week
  - Each is 5–15 minutes (but allow up to 20 if truly needed).
  - Must feel frictionless and streak-friendly.
  - Actionable and specific.
- Weekly Task (Momentum): 1 per week
  - 3–5 hours total (estimated_minutes 180–300 typical; allow 240–360 if needed).
  - Should ship a real portfolio artifact: feature, mini-demo, case study, README, blog, deploy, etc.
  - Should clearly tie to Dream Plan phases and/or selected projects.

INPUTS:
- path_type: {path_type}  # "job" or "startup"
- target_lpa: {target_lpa}  # "12" | "24" | "48" (difficulty alignment)
- month_cycle: {month_cycle}
- dream_plan (may be empty, but if present contains phases + resources + selected projects):
{dream_plan_json}

PROJECT CONTEXT:
- dream_plan may contain selected projects and milestones.
- If projects exist:
  - Each week should include:
    - at least 2 daily tasks that advance the project (category "projects")
    - the weekly task should be a meaningful weekly deliverable for that project
  - DO NOT invent new projects or IDs.
  - Use project_label/milestone_* fields when relevant.

DIFFICULTY / LPA ALIGNMENT:
- target_lpa = "12":
  - keep weekly deliverables simpler, reduce scope, prioritize consistency and basic portfolio quality.
- target_lpa = "24":
  - moderate scope; add one quality step (tests, deployment, metrics, better README).
- target_lpa = "48":
  - higher bar; weekly deliverables should look recruiter-grade; include deploy + proof + write-up.

OUTPUT REQUIREMENTS:
- Exactly 4 weeks in "weeks": week_number 1..4.
- Each week:
  - week_note: short note for that week
  - daily_tasks: exactly 7 items with day=1..7
    - estimated_minutes: 5–20 (prefer 8–15)
    - category: one of ["skills","projects","career_capital","planning","mindset","wellbeing"]
    - include difficulty: one of ["easy","medium","hard"] (daily tasks rarely "hard")
  - weekly_task: 1 item
    - estimated_minutes: 180–360 typical (90–480 allowed by schema)
    - category should usually be "projects" or "career_capital"
    - milestone_badge: short badge name like "Week 1 Master"
    - deliverable: a concrete output string (e.g. "Deployed MVP + README + screenshots")

COACHING NOTE:
- "ai_note" should explain the month strategy briefly:
  - how daily maintenance supports streak
  - how weekly momentum builds portfolio
  - what to do if they fall behind

JSON Schema:
{json_schema}

Return JSON only.
"""

def generate_dualtrack_month_plan(
    *,
    path_type: str,
    month_cycle: str,
    target_lpa: str = "12",
    dream_plan: Optional[Dict[str, Any]] = None,
    return_source: bool = False,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    """
    Generate a full 4-week dual-track month plan in ONE call.

    Output (validated):
      {
        "month_cycle": "...",
        "ai_note": "...",
        "weeks": [
          {
            "week_number": 1,
            "week_note": "...",
            "daily_tasks": [ {day 1..7 ...}, ... ],
            "weekly_task": { ... }
          },
          ... week 4 ...
        ],
        "meta": {...}
      }

    Routes should create:
      - 4 DailyCoachSession rows (day_index = week_number)
      - 7 daily tasks per week (day_number = (week-1)*7 + day)
      - 1 weekly task per week
    """
    from openai import OpenAI

    used_live_ai = False

    pt = (path_type or "job").strip().lower()
    if pt not in ("job", "startup"):
        pt = "job"

    tlpa = str(target_lpa or "12").strip()
    if tlpa not in ("12", "24", "48"):
        tlpa = "12"

    dp = dream_plan or {}
    if not isinstance(dp, dict):
        dp = {}

    month_cycle = str(month_cycle or "").strip()[:64]
    if not month_cycle:
        # still allow generation; validator will keep it but routes should pass proper id
        month_cycle = "month_cycle_unknown"

    prompt = DUALTRACK_MONTH_PROMPT.format(
        freshness=FRESHNESS_NOTE,
        path_type=pt,
        target_lpa=tlpa,
        month_cycle=month_cycle,
        dream_plan_json=json.dumps(dp, ensure_ascii=False, indent=2)[:18000],
        json_schema=DUALTRACK_MONTH_JSON_SCHEMA,
    )

    try:
        client = OpenAI()
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_DEEP,
            messages=[
                {"role": "system", "content": "You output ONLY valid JSON matching the schema."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.45,
            max_tokens=2600,
            response_format={"type": "json_object"},
        )

        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw) if raw else {}

        meta = data.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("generated_at_utc", _utc_now_iso())
        meta.setdefault(
            "inputs_digest",
            _inputs_digest(
                {
                    "path_type": pt,
                    "target_lpa": tlpa,
                    "month_cycle": month_cycle,
                    "has_dream_plan": bool(dp),
                }
            ),
        )
        meta.setdefault("path_type", pt)
        meta.setdefault("target_lpa", tlpa)
        meta.setdefault("career_ai_version", CAREER_AI_VERSION)
        data["meta"] = meta

        # Ensure month_cycle is carried
        if not data.get("month_cycle"):
            data["month_cycle"] = month_cycle

        clean = _light_validate_dualtrack_month(data)
        used_live_ai = True
        return (clean, used_live_ai) if return_source else clean

    except Exception as e:
        # Very defensive fallback: 4 weeks, simple but valid
        fallback_weeks = []
        for wn in range(1, 5):
            daily_tasks = []
            for day in range(1, 8):
                daily_tasks.append(
                    {
                        "day": day,
                        "title": f"Day {day}: 10-minute progress touch",
                        "detail": "Do a tiny step: read 1 page of notes, fix 1 bug, or write 3 lines in README. Keep the streak alive.",
                        "category": "planning" if day == 1 else "skills",
                        "estimated_minutes": 10,
                        "difficulty": "easy",
                        "tags": ["streak"],
                        "phase_label": "",
                        "week_index": wn,
                        "project_label": "",
                        "milestone_title": "",
                        "milestone_step": "",
                    }
                )

            weekly_task = {
                "title": f"Week {wn}: Ship a small deliverable",
                "detail": "Pick one project/skill focus and ship something tangible: a feature, a demo, a README update with screenshots, or a short write-up.",
                "category": "projects",
                "estimated_minutes": 240,
                "milestone_badge": f"Week {wn} Master",
                "phase_label": "",
                "week_index": wn,
                "project_label": "",
                "milestone_title": "",
                "milestone_step": "",
                "deliverable": "Small shipped artifact + README proof",
            }

            fallback_weeks.append(
                {
                    "week_number": wn,
                    "week_note": f"Week {wn}: keep momentum and ship proof.",
                    "daily_tasks": daily_tasks,
                    "weekly_task": weekly_task,
                }
            )

        fallback = {
            "month_cycle": month_cycle,
            "ai_note": (
                "We couldn’t generate a personalized month plan right now. "
                "Use this fallback plan: keep a daily 10-minute streak and ship one concrete deliverable each week."
            ),
            "weeks": fallback_weeks,
            "meta": {
                "generated_at_utc": _utc_now_iso(),
                "inputs_digest": _inputs_digest(
                    {"error": str(e), "path_type": pt, "target_lpa": tlpa, "month_cycle": month_cycle}
                ),
                "path_type": pt,
                "target_lpa": tlpa,
                "career_ai_version": CAREER_AI_VERSION,
            },
        }

        clean = _light_validate_dualtrack_month(fallback)
        used_live_ai = False
        return (clean, used_live_ai) if return_source else clean



def generate_daily_coach_plan(
    *,
    path_type: str,
    dream_plan: Dict[str, Any] | None,
    progress_history: List[Dict[str, Any]] | None = None,
    session_date: str | None = None,
    day_index: int | None = None,
    return_source: bool = False,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    """
    Coach engine (used both for "Daily" and the upgraded Weekly Coach UI).

    Inputs:
      - path_type: "job" | "startup"
      - dream_plan: full dict returned by Dream Planner *or* the processed `plan_view`.
        - If provided, we read its phases + resources and build a week-by-week roadmap.
        - If None, we still generate a reasonable generic checklist.
        - For P3, routes may enrich this with `selected_projects` / `projects` including milestones.
      - progress_history: list of past sessions with basic stats (can be empty).
        - S4 uses this to adjust intensity and difficulty.
      - session_date: string representation for "today" (e.g. '2025-12-04')
      - day_index: optional day/week number within the broader roadmap (Weekly Coach treats this as week_index).
      - return_source: if True, returns (plan_dict, used_live_ai: bool)

    Output shape (after validation):
      {
        "session_date": "2025-12-04",
        "day_index": 7,
        "ai_note": "...short coaching note...",
        "tasks": [ ... ],
        "meta": { ... }
      }

    The *UI* decides whether "day_index" is shown as "Day 7" or "Week 7".
    """
    from openai import OpenAI

    used_live_ai = False

    pt = (path_type or "job").strip().lower()
    if pt not in ("job", "startup"):
        pt = "job"

    dp = dream_plan or {}
    progress_history = progress_history or []

    # Extract a compact roadmap from Dream Planner (if provided).
    # This is safe to call even when dream_plan is {}.
    roadmap = _extract_coach_roadmap(pt, dp)
    roadmap_phases = roadmap.get("phases") or []
    roadmap_weeks = _expand_phases_to_weeks(
        roadmap_phases,
        timeline_months=roadmap.get("timeline_months"),
        max_weeks=24,
    )

    # Selected projects (P3) for this plan, if any
    roadmap_projects = roadmap.get("projects") or []

    # Determine current session date + index
    today_str = session_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_index = day_index

    # If not provided, infer from history (max existing index + 1)
    if current_index is None and progress_history:
        try:
            existing_indices = [
                s.get("day_index")
                for s in progress_history
                if isinstance(s, dict) and isinstance(s.get("day_index"), int)
            ]
            current_index = max(existing_indices) + 1 if existing_indices else 1
        except Exception:
            current_index = 1
    if current_index is None:
        current_index = 1

    # Find matching week_theme / phase for this index
    current_phase_label = None
    current_week_theme = None
    for w in roadmap_weeks:
        if w.get("week_index") == current_index:
            current_phase_label = w.get("phase_label")
            current_week_theme = w.get("theme")
            break

    # Progress history analysis for S4 (difficulty & intensity)
    history_analysis = _analyze_progress_history(progress_history)
    intensity_hint = history_analysis.get("intensity_hint") or "normal"
    avg_completion_ratio = history_analysis.get("avg_completion_ratio")

    # Phase-aware context for prompt
    job_context: Dict[str, Any] = {}
    startup_context: Dict[str, Any] = {}
    if pt == "job":
        job_context = {
            "target_role": roadmap.get("target_role"),
            "timeline_months": roadmap.get("timeline_months"),
            "hours_per_day": roadmap.get("hours_per_day"),
            "phases": roadmap_phases,
            "week_roadmap": roadmap_weeks,
            "resources": roadmap.get("resources") or {},
        }
    else:
        startup_context = {
            "target_role": roadmap.get("target_role"),
            "timeline_months": roadmap.get("timeline_months"),
            "hours_per_day": roadmap.get("hours_per_day"),
            "phases": roadmap_phases,
            "week_roadmap": roadmap_weeks,
            "resources": roadmap.get("resources") or {},
        }

    # Project context (P3) – shared for both path types
    project_context = {
        "projects": roadmap_projects,
    }

    header = (
        "You are CareerAI Coach, helping students execute their Dream Plan "
        "with realistic, small weekly or daily actions. "
        "You must output strictly valid JSON that matches the provided JSON Schema."
    )

    plan_json = {
        "mode": pt,
        "job_context": job_context,
        "startup_context": startup_context,
        "project_context": project_context,
    }

    history_json = {
        "sessions": progress_history[-10:],
        "analysis": history_analysis,
    }

    schema = r"""\
{
  "type": "object",
  "required": ["session_date", "ai_note", "tasks", "meta"],
  "additionalProperties": false,
  "properties": {
    "session_date": { "type": "string", "minLength": 4, "maxLength": 32 },
    "day_index": { "type": ["integer", "null"] },
    "ai_note": { "type": "string", "minLength": 5, "maxLength": 1200 },
    "tasks": {
      "type": "array",
      "minItems": 3,
      "maxItems": 8,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["title"],
        "properties": {
          "id": { "type": ["integer", "null"] },
          "title": { "type": "string", "minLength": 3, "maxLength": 255 },
          "detail": { "type": "string" },
          "category": { "type": "string", "minLength": 0, "maxLength": 64 },
          "sort_order": { "type": ["integer", "null"] },
          "suggested_minutes": { "type": ["integer", "null"] },
          "guide": { "type": "string" },
          "tags": {
            "type": "array",
            "items": { "type": "string" }
          },
          "phase_label": { "type": "string" },
          "week_index": { "type": ["integer", "null"] },
          "difficulty": { "type": "string" },
          "project_label": { "type": "string" },
          "milestone_title": { "type": "string" },
          "milestone_step": { "type": "string" },
          "is_done": { "type": "boolean" }
        }
      }
    },
    "meta": {
      "type": "object",
      "additionalProperties": true,
      "required": ["generated_at_utc", "inputs_digest"],
      "properties": {
        "generated_at_utc": { "type": "string" },
        "inputs_digest": { "type": "string" },
        "path_type": { "type": "string" },
        "career_ai_version": { "type": "string" }
      }
    }
  }
}
"""

    # High-level explanation text for the model
    execution_scope = "WEEK"  # current Flask UI is a Weekly Coach; we still keep daily-compatible wording

    # For prompt text, pretty-print completion ratio
    if avg_completion_ratio is None:
        completion_str = "unknown (not enough data)"
    else:
        # Clamp to [0, 1] and show as percentage-ish string
        cr = max(0.0, min(1.0, float(avg_completion_ratio)))
        completion_str = f"{cr:.2f}"

    user_prompt = f"""\
You are designing a realistic {execution_scope}-SIZED checklist for a student.

session_date: {today_str}
path_type: {pt}
current_index (interpret as week_index for Weekly Coach): {current_index}

phase_label_for_this_week: {current_phase_label}
week_theme_for_this_week: {current_week_theme}

Dream Plan context (simplified JSON; may have phases or be empty):
{json.dumps(plan_json, ensure_ascii=False, indent=2)}

Recent progress history (last 10 sessions, may be empty):
{json.dumps(history_json, ensure_ascii=False, indent=2)}

INTERPRETATION RULES (S4):

- Treat "day_index" as a flexible index:
  - In Weekly Coach mode it is the WEEK number inside their roadmap (Week 1, Week 2, ...).
- Use the phase + week_roadmap information:
  - week_roadmap[week_index] tells you which PHASE they are in and the main theme.
  - Make tasks that clearly ladder up to that week's theme.
- Use the progress analysis to adjust intensity:
  - intensity_hint: "{intensity_hint}"
  - avg_completion_ratio (0–1): {completion_str}
  - Heuristic:
    - "light"   → student struggles to finish tasks; keep plan gentle and focused.
    - "normal"  → student is doing okay; keep balanced intensity.
    - "intense" → student completes almost everything; you may push slightly harder.

PROJECT CONTEXT (P3):

- project_context.projects is an array of selected projects (if any).
- Each project has:
  - id          (internal DB id, MAY be null — do not invent values),
  - title       (short project name),
  - week_start  (when this project roughly starts in the roadmap, MAY be null),
  - week_end    (when this project roughly ends, MAY be null),
  - milestones: [{{ title, detail, week_hint }}].
- For the CURRENT week_index:
  - Prefer milestones whose week_hint matches or is close to this week_index.
  - If week_start/week_end define a range and the current week is inside that range,
    it's valid to schedule progress for that project this week.
- DO NOT fabricate project IDs or new projects.
- You MAY reference project titles and milestone titles inside tasks, e.g.:
  - project_label: "Game Portfolio Project"
  - milestone_title: "Implement core gameplay loop"
  - milestone_step: "Code basic player movement & collision"

TASK DESIGN RULES (very important):

- Create 3–8 tasks total.
- Each task must be small enough to do in 15–60 minutes.
- Every task must have:
  - "title": short, very clear action.
  - "detail": 1–3 sentences of EXACT instructions (what to open, what to practice, what to write, what to ship).
  - "category": one of ["skills", "projects", "career_capital", "planning", "mindset", "wellbeing"].
  - "suggested_minutes": integer between 15 and 60 (roughly).
  - "guide": an extra micro-coaching note (1–2 sentences) that explains HOW to approach the task.
  - Optional "tags": 2–4 short labels like ["sql", "joins"], ["unity", "movement"].
  - Optional "difficulty": one of ["easy", "medium", "hard"].
  - Optional "project_label", "milestone_title", "milestone_step" if the task advances a project milestone.
- Mix tasks across:
  - skills / learning (practice, tutorials, problem sets),
  - project or portfolio builds (feature implementation, refactor, bugfix, polish),
  - career capital (resume, LinkedIn, outreach, applications),
  - optional light wellbeing (short walk, breathing, journaling — MAX 1 task).
- If intensity_hint = "light":
  - 3–5 tasks only.
  - Prefer "easy" and "medium" tasks.
  - Focus on momentum and small wins.
- If intensity_hint = "normal":
  - 4–6 tasks.
  - Mostly "medium" tasks, at most 1 "hard" task (like a mini-project step or mock interview).
- If intensity_hint = "intense":
  - 5–8 tasks.
  - You MAY include up to 2 "hard" tasks but keep them well-scoped.

PROJECT TASKS (P3 specifics):

- If project_context.projects is non-empty:
  - Ensure at least 1–3 tasks this week are clearly marked as "projects" category.
  - These project tasks should:
    - reference project_label,
    - reference a concrete milestone_title where possible,
    - optionally include milestone_step as a fine-grained action.
  - The details should describe REAL engineering steps:
    - e.g. implement a module, refactor a component, debug a feature, write tests, connect an API, design DB schema, etc.
- Do NOT create vague project tasks like "work on project for 1 hour".
  - Instead: "Implement player jump mechanic with basic gravity and collision checks".

SKILL & CAREER TASKS (depth, not fluff):

- Avoid shallow tasks like "watch any video on topic X".
- Prefer:
  - build a small function / component / script,
  - solve 10 focused problems,
  - write a short reflection or summary,
  - improve 1–2 bullets on resume/LinkedIn,
  - send 1–3 targeted outreach / networking messages.
- For resume/LinkedIn:
  - NEVER tell them to rewrite the full resume.
  - Ask for specific micro-changes (e.g. rewrite ONE bullet, add ONE project, adjust headline).

COACH NOTE ("ai_note"):

- Short pep-talk + tactical instructions for THIS week.
- Tone: honest, encouraging, specific.
- Mention:
  - which phase they are in (if that info exists),
  - what this week is primarily about (ONE main theme),
  - how to approach consistency given the intensity_hint.
- Example structure:
  - 1–2 sentences acknowledging their current phase and time horizon,
  - 1 sentence about the main focus this week,
  - 1–2 sentences with concrete advice on how many days to touch their plan
    and what to do if they fall behind.

You MUST output ONLY a JSON object that matches this JSON Schema:

{schema}
"""

    try:
        client = OpenAI()

        resp = client.chat.completions.create(
            model=OPENAI_MODEL_DEEP,
            messages=[
                {
                    "role": "system",
                    "content": header,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            temperature=0.45,
            max_tokens=1400,
            response_format={"type": "json_object"},
        )

        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw) if raw else {}

        # Ensure meta is present + enriched
        meta = data.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        if "generated_at_utc" not in meta:
            meta["generated_at_utc"] = _utc_now_iso()
        if "inputs_digest" not in meta:
            meta["inputs_digest"] = _inputs_digest(
                {
                    "path_type": pt,
                    "session_date": today_str,
                    "has_history": bool(progress_history),
                    "has_roadmap": bool(roadmap_phases),
                    "has_projects": bool(roadmap_projects),
                    "day_index": current_index,
                }
            )
        meta.setdefault("path_type", pt)
        meta.setdefault("career_ai_version", CAREER_AI_VERSION)
        data["meta"] = meta

        # Ensure we keep the current index in the payload so routes don't lose it
        if "day_index" not in data or not isinstance(data.get("day_index"), int):
            data["day_index"] = current_index

        data = _light_validate_daily_coach(data)
        used_live_ai = True
        return (data, used_live_ai) if return_source else data

    except Exception as e:
        # Very defensive fallback — generic but still actionable, phase-aware where possible
        fallback_tasks = [
            {
                "id": 1,
                "title": "Re-read your Dream Plan and current phase",
                "detail": "Open your Dream Plan. Re-read the current phase label and its bullet points. Note down the top 3 skills or projects that appear repeatedly.",
                "category": "planning",
                "sort_order": 1,
                "suggested_minutes": 25,
                "guide": "This is about reminding yourself what matters, not overthinking. Jot the 3 priorities on paper.",
                "tags": ["dream_plan", "clarity"],
                "phase_label": current_phase_label or "",
                "week_index": current_index,
                "difficulty": "easy",
                "project_label": "",
                "milestone_title": "",
                "milestone_step": "",
                "is_done": False,
            },
            {
                "id": 2,
                "title": "Do one 30-minute deep learning block",
                "detail": "Pick a single topic from your priorities (e.g. SQL joins, React hooks, marketing funnels). Study just that topic for 30 minutes — one tab, no multitasking.",
                "category": "skills",
                "sort_order": 2,
                "suggested_minutes": 30,
                "guide": "Set a timer and treat it like a mini exam; distraction-free time compounds faster than long, scattered study.",
                "tags": ["learning", "deep_work"],
                "phase_label": current_phase_label or "",
                "week_index": current_index,
                "difficulty": "medium",
                "project_label": "",
                "milestone_title": "",
                "milestone_step": "",
                "is_done": False,
            },
            {
                "id": 3,
                "title": "Polish one resume or LinkedIn bullet",
                "detail": "Choose one existing bullet describing a project or internship. Rewrite it to be more specific: what you built, the tech used, and any measurable outcome.",
                "category": "career_capital",
                "sort_order": 3,
                "suggested_minutes": 20,
                "guide": "If you’re stuck, imagine explaining the work to a friend in one sentence, then turn that into a bullet.",
                "tags": ["resume", "linkedin"],
                "phase_label": current_phase_label or "",
                "week_index": current_index,
                "difficulty": "easy",
                "project_label": "",
                "milestone_title": "",
                "milestone_step": "",
                "is_done": False,
            },
        ]

        fallback = {
            "session_date": today_str,
            "day_index": current_index,
            "ai_note": (
                "We could not generate a personalised checklist right now. "
                "Use this week to reconnect with your Dream Plan, do one focused learning block, "
                "and clean up one small piece of your resume or LinkedIn."
            ),
            "tasks": fallback_tasks,
            "meta": {
                "generated_at_utc": _utc_now_iso(),
                "inputs_digest": _inputs_digest(
                    {
                        "error": str(e),
                        "path_type": pt,
                        "session_date": today_str,
                        "day_index": current_index,
                    }
                ),
                "path_type": pt,
                "career_ai_version": CAREER_AI_VERSION,
            },
        }
        data = _light_validate_daily_coach(fallback)
        used_live_ai = False
        return (data, used_live_ai) if return_source else data


# -------------------------------------------------------------------
# Dream Planner (Pro-only) — Job & Startup, PHASE-BASED
# -------------------------------------------------------------------
DREAM_PLANNER_JSON_SCHEMA = r"""\
{
  "type": "object",
  "additionalProperties": false,
  "required": ["mode", "summary", "phases", "resources", "meta"],
  "properties": {
    "mode": { "type": "string", "enum": ["job", "startup"] },
    "summary": { "type": "string" },

    "probabilities": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "lpa_12": { "type": "integer", "minimum": 0, "maximum": 100 },
        "lpa_24": { "type": "integer", "minimum": 0, "maximum": 100 },
        "lpa_48": { "type": "integer", "minimum": 0, "maximum": 100 }
      }
    },

    "missing_skills": {
      "type": "array",
      "items": { "type": "string" }
    },

    "phases": {
      "type": "array",
      "minItems": 3,
      "items": {
        "type": "object",
        "additionalProperties": true,
        "required": ["label", "items"],
        "properties": {
          "label": { "type": "string" },
          "items": {
            "type": "array",
            "items": { "type": "string" }
          }
        }
      }
    },

    "plan_core": {
      "type": "object",
      "additionalProperties": true
    },

    "resources": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "tutorials": {
          "type": "array",
          "items": { "type": "string" }
        },
        "mini_projects": {
          "type": "array",
          "items": { "type": "string" }
        },
        "resume_bullets": {
          "type": "array",
          "items": { "type": "string" }
        },
        "linkedin_actions": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    },

    "startup_extras": {
      "type": "object",
      "additionalProperties": true
    },

    "input": {
      "type": "object",
      "additionalProperties": true
    },

    "meta": {
      "type": "object",
      "additionalProperties": false,
      "required": ["generated_at_utc", "inputs_digest"],
      "properties": {
        "generated_at_utc": { "type": "string" },
        "inputs_digest": { "type": "string" }
      }
    }
  }
}
"""


DREAM_PLANNER_PROMPT = """\
You are DreamPlanner, the *deep* Pro-only career coach for a Flask web app.

Return ONLY valid JSON matching the schema below.
No markdown, no commentary, no code fences.

Freshness: {freshness}

GENERAL RULES:
- The user chooses a custom time horizon in months (timeline_months).
- 3 months ≈ a classic 90-day plan, but they may choose 6, 12, 18+ months.
- You ALWAYS respect the target timeline and daily hours when designing the plan.
- You are honest and realistic, especially when the goal is very aggressive.
- You turn vague goals into concrete phase-based moves.
- You do NOT fabricate specific companies that are unrealistic for the profile.
- You keep language friendly, direct, and practical (no fluff, no emojis).
- Your summary and phase descriptions should feel “premium”: clear sections, short paragraphs that will look great with larger fonts in the UI.

CONTEXT (PROFILE):
- profile_json: {profile_json}
- skills_json: {skills_json}
- resume_excerpt: {resume_excerpt}

USER INPUTS (FORM):
- mode: {mode}   # "job" or "startup"
- target_role: {target_role}
- target_salary_lpa: {target_salary_lpa}
- timeline_months: {timeline_months}
- hours_per_day: {hours_per_day}
- company_preferences: {company_preferences}
- startup_theme: {startup_theme}
- startup_budget_range: {startup_budget_range}
- startup_timeline_months: {startup_timeline_months}
- startup_notes: {startup_notes}

TIME HORIZON LOGIC (VERY IMPORTANT):
- timeline_months is the total horizon the student is aiming for (e.g. 3, 6, 12, 18).
- The JSON uses a PHASES array:
  - Each phase has:
    - "label": short label that includes phase number and time window (e.g. "Phase 1 · Months 1–2 · Foundations").
    - "items": 4–7 concise bullet-like strings describing weekly/monthly themes and key actions.
- You usually produce 3 phases, but 4 phases is acceptable for longer timelines (e.g. 12–18 months).

Examples:
- If timeline_months = 3:
  - Phase 1 label might be "Phase 1 · Weeks 1–4 · Fix basics".
  - Phase 2 label might be "Phase 2 · Weeks 5–8 · Projects + practice".
  - Phase 3 label might be "Phase 3 · Weeks 9–12 · Interview + applications".
- If timeline_months = 6:
  - Phase 1 ≈ Months 1–2.
  - Phase 2 ≈ Months 3–4.
  - Phase 3 ≈ Months 5–6.
- If timeline_months = 12:
  - Phase 1 ≈ Months 1–4.
  - Phase 2 ≈ Months 5–8.
  - Phase 3 ≈ Months 9–12.

REALISM & SANITY CHECKS (CRUCIAL):
- Combine:
  - target_role,
  - target_salary_lpa,
  - timeline_months,
  - profile_json + skills_json + resume_excerpt.
- Ask yourself: "Is this realistically achievable in this time for this person?"
- If the combo is VERY aggressive (e.g., 48 LPA in 3 months for a beginner game designer):
  - Be explicit that this is unlikely in that timeframe.
  - Set probabilities low and clearly explain why.
  - Suggest a more realistic time range (e.g. "Realistically 9–18+ months") while still giving a strong first-phase plan.
- If the timeline is longer and the profile is decent (e.g., 12 months for 48 LPA with relevant experience):
  - You may assign higher probabilities, but still be grounded, not optimistic fantasy.
- Your job is to be a brutally honest but kind mentor, not to promise miracles.

OUTPUT INTENT:

If mode = "job":
- Think like a senior recruiter + hiring manager + mentor.
- Give a clear 2–4 sentence "summary" of the realistic path, explicitly referencing the chosen timeline_months.
- "probabilities" should be rough, honest chances (0–100) for 12 / 24 / 48 LPA,
  *for the specified region and level*, assuming they follow the plan with the given timeline_months and hours_per_day.
- "missing_skills" must be specific, short skill phrases (e.g., "system design basics", "React state management", "basic statistics").
- "phases" is the 3-phase (or occasionally 4-phase) roadmap across their chosen timeline:
  - Phase 1: fundamentals, cleanup, core missing skills.
  - Phase 2: projects + interview prep.
  - Phase 3: applications, referrals, mock interviews.
  - Each phase "items" list is 4–7 concise bullet-like strings (e.g. "Month 1: Fix top 3 resume bullets + GitHub hygiene").
- "resources":
  - tutorials: 5–8 short items in the form "Topic — source (YouTube / docs / blog)".
    Do not include full URLs; keep it generic but recognizable.
  - mini_projects: 3–5 concrete project ideas with impact baked in.
    - These should be substantial enough to become "Dream Plan projects" that the Weekly Coach can break down into milestones later.
  - resume_bullets:
    - 4–6 bullets they can add AFTER completing the work.
    - Phrase them as finished outcomes, ready to paste into resume/LinkedIn.
    - Do NOT tell them to rewrite the whole resume; just give high-impact bullets.
  - linkedin_actions: 6–10 actions like "Connect with 20 SDEs in X", "Comment weekly on 5 posts in Y".

- "startup_extras" can be empty or minimal when mode="job".

If mode = "startup":
- Think like a pragmatic startup mentor.
- "summary": 2–4 sentences outlining the path to MVP + first revenue, referencing the chosen timeline_months.
- Do NOT use LPA probabilities for founder outcomes. Instead, focus on:
  - clarity of their personal founder role,
  - cofounder gaps (skills / roles they need),
  - lean validation and time realities, not big bang launches.
- "missing_skills": skills they personally must upgrade for the founder role.
- "phases":
  - Phase 1: market research, problem interviews, basic prototype.
  - Phase 2: MVP build, early users, feedback loop.
  - Phase 3: pricing tests, GTM experiments, tightening funnel.
  - Distribute the work realistically across timeline_months (e.g. mention which months roughly).
- "resources":
  - tutorials: lean startup, basic sales, GTM, tools relevant to their stack.
  - mini_projects: MVP-flavored experiments or landing pages.
  - resume_bullets: framed as "founder outcomes" they can later use once they hit real milestones.
  - linkedin_actions: focused on building a founder network + early adopters.
- "startup_extras":
  - Object with keys like "founder_role_fit", "cofounder_gaps", "mvp_outline",
    "go_to_market", "first_10_customers", "pricing_notes", "risk_analysis".
  - Each should have short, concrete text or bullet-like strings.
  - Be explicit about realistic timelines for validation vs real revenue.

INPUT ECHO (for Coach integration):

- You MUST include an "input" object in the JSON that echoes the key user choices:
  - target_role
  - target_salary_lpa
  - timeline_months
  - hours_per_day
  - company_preferences
  - startup_theme
  - startup_budget_range
  - startup_timeline_months
  - startup_notes

JSON Schema:
{json_schema}

Respond with JSON only.
"""


def _light_validate_dream_plan(data: Any, mode: str) -> Dict[str, Any]:
    """Keep Dream Planner payload UI-friendly and robust."""
    if not isinstance(data, dict):
        data = {}

    out: Dict[str, Any] = {}

    mode_clean = "startup" if (mode or "").lower() == "startup" else "job"
    out["mode"] = mode_clean

    summary = str(data.get("summary") or "")
    out["summary"] = summary[:1000]

    probs = data.get("probabilities") or {}
    if not isinstance(probs, dict):
        probs = {}

    def _clamp_pct(x: Any) -> int:
        try:
            v = int(x)
        except Exception:
            v = 0
        return max(0, min(100, v))

    out["probabilities"] = (
        {
            "lpa_12": _clamp_pct(probs.get("lpa_12")),
            "lpa_24": _clamp_pct(probs.get("lpa_24")),
            "lpa_48": _clamp_pct(probs.get("lpa_48")),
        }
        if mode_clean == "job"
        else {"lpa_12": 0, "lpa_24": 0, "lpa_48": 0}
    )

    missing = data.get("missing_skills") or []
    if not isinstance(missing, list):
        missing = []
    out["missing_skills"] = [str(x)[:80] for x in missing][:15]

    # Phases (primary for UI + Weekly Coach)
    raw_phases = data.get("phases") or []
    phases: List[Dict[str, Any]] = []
    if isinstance(raw_phases, list):
        for idx, ph in enumerate(raw_phases, start=1):
            if not isinstance(ph, dict):
                continue
            label = str(ph.get("label") or "").strip()
            if not label:
                label = f"Phase {idx}"
            items = ph.get("items") or []
            if not isinstance(items, list):
                items = []
            items_clean = [str(x).strip() for x in items if str(x).strip()]
            phases.append(
                {
                    "label": label[:160],
                    "items": items_clean[:10],
                }
            )

    # Fallback mapping from legacy plan_core if phases missing
    if not phases:
        plan = data.get("plan_core") or {}
        if not isinstance(plan, dict):
            plan = {}

        def _coerce_str_list(val, limit: int) -> List[str]:
            if not isinstance(val, list):
                return []
            items = [str(x).strip() for x in val if str(x).strip()]
            return items[:limit]

        weeks_30 = _coerce_str_list(plan.get("weeks_30"), 6)
        weeks_60 = _coerce_str_list(plan.get("weeks_60"), 6)
        weeks_90 = _coerce_str_list(plan.get("weeks_90"), 6)

        if weeks_30 or weeks_60 or weeks_90:
            phases = [
                {"label": "Phase 1", "items": weeks_30},
                {"label": "Phase 2", "items": weeks_60},
                {"label": "Phase 3", "items": weeks_90},
            ]

    if not phases:
        phases = [{"label": "Phase 1", "items": []}]

    out["phases"] = phases

    # Optional legacy plan_core preserved (mostly empty, for backward compat if needed)
    plan = data.get("plan_core") or {}
    if not isinstance(plan, dict):
        plan = {}
    out["plan_core"] = plan

    res = data.get("resources") or {}
    if not isinstance(res, dict):
        res = {}

    def _coerce_str_list(val, limit: int) -> List[str]:
        if not isinstance(val, list):
            return []
        items = [str(x).strip() for x in val if str(x).strip()]
        return items[:limit]

    out["resources"] = {
        "tutorials": _coerce_str_list(res.get("tutorials"), 10),
        "mini_projects": _coerce_str_list(res.get("mini_projects"), 8),
        "resume_bullets": _coerce_str_list(res.get("resume_bullets"), 10),
        "linkedin_actions": _coerce_str_list(res.get("linkedin_actions"), 12),
    }

    sx = data.get("startup_extras") or {}
    if not isinstance(sx, dict):
        sx = {}
    clean_sx: Dict[str, Any] = {}
    for k, v in sx.items():
        key = str(k)[:40]
        if isinstance(v, str):
            clean_sx[key] = v[:800]
        elif isinstance(v, list):
            clean_sx[key] = [str(x)[:200] for x in v[:15]]
        else:
            clean_sx[key] = v
    out["startup_extras"] = clean_sx

    # Echo input (for Coach integration)
    input_block = data.get("input") or {}
    if not isinstance(input_block, dict):
        input_block = {}
    clean_input: Dict[str, Any] = {}
    for k, v in input_block.items():
        key = str(k)[:60]
        if isinstance(v, str):
            clean_input[key] = v[:400]
        else:
            clean_input[key] = v
    out["input"] = clean_input

    meta = data.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    out["meta"] = meta

    return out


def generate_dream_plan(
    *,
    mode: str,
    inputs: Dict[str, Any],
    profile_json: Optional[Dict[str, Any]] = None,
    skills_json: Optional[Dict[str, Any]] = None,
    resume_text: Optional[str] = None,
    return_source: bool = False,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    """
    Dream Planner (Pro-only) engine.

    NOTE:
    - timeline_months is flexible (3, 6, 12, 18+).
    - We output a PHASE-BASED plan:
      - phases: list of { "label": str, "items": [str, ...] }
    - The UI and Coach consume these phases directly; they are interpreted
      as Phase 1 / Phase 2 / Phase 3 across the chosen timeline, not literally 90 days.
    - For P3/S4, we also embed a copy of the original form "inputs" under "input"
      so the Weekly Coach can reliably read timeline, hours_per_day, etc.
    """
    from openai import OpenAI

    client = OpenAI()

    mode_clean = "startup" if (mode or "").lower() == "startup" else "job"

    profile_json = profile_json or {}
    skills_json = skills_json or {}
    resume_excerpt = (resume_text or "").strip()[:2500]

    target_role = str(inputs.get("target_role") or "").strip()
    target_salary_lpa = str(inputs.get("target_salary_lpa") or "").strip()
    timeline_months = int(inputs.get("timeline_months") or 3)
    hours_per_day = int(inputs.get("hours_per_day") or 2)
    company_prefs = str(inputs.get("company_preferences") or "").strip()

    startup_theme = str(inputs.get("startup_theme") or "").strip()
    startup_budget_range = str(inputs.get("startup_budget_range") or "").strip()
    startup_timeline_months = int(
        inputs.get("startup_timeline_months") or timeline_months
    )
    startup_notes = str(inputs.get("startup_notes") or "").strip()

    used_live_ai = False

    try:
        prompt = DREAM_PLANNER_PROMPT.format(
            freshness=FRESHNESS_NOTE,
            mode=mode_clean,
            target_role=target_role or "Software Engineer",
            target_salary_lpa=target_salary_lpa or "12",
            timeline_months=timeline_months,
            hours_per_day=hours_per_day,
            company_preferences=company_prefs or "Open to any good engineering culture.",
            startup_theme=startup_theme or "Not specified",
            startup_budget_range=startup_budget_range or "Low budget / bootstrapped",
            startup_timeline_months=startup_timeline_months,
            startup_notes=startup_notes or "No extra notes.",
            profile_json=json.dumps(profile_json, ensure_ascii=False),
            skills_json=json.dumps(skills_json, ensure_ascii=False),
            resume_excerpt=resume_excerpt,
            json_schema=DREAM_PLANNER_JSON_SCHEMA,
        )

        resp = client.chat.completions.create(
            model=OPENAI_MODEL_DEEP,
            messages=[
                {
                    "role": "system",
                    "content": "You output ONLY valid JSON that exactly matches the provided schema.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.45,
            max_tokens=2400,
            response_format={"type": "json_object"},
        )

        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)

        # Ensure input echo is present for coach, even if the model forgot
        input_block = data.get("input") or {}
        if not isinstance(input_block, dict):
            input_block = {}
        # Minimal structured echo from inputs
        form_input = {
            "mode": mode_clean,
            "target_role": target_role,
            "target_salary_lpa": target_salary_lpa,
            "timeline_months": timeline_months,
            "hours_per_day": hours_per_day,
            "company_preferences": company_prefs,
            "startup_theme": startup_theme,
            "startup_budget_range": startup_budget_range,
            "startup_timeline_months": startup_timeline_months,
            "startup_notes": startup_notes,
        }
        # Do not erase anything model might have added, just overlay missing keys
        for k, v in form_input.items():
            input_block.setdefault(k, v)
        data["input"] = input_block

        meta = data.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        if "generated_at_utc" not in meta:
            meta["generated_at_utc"] = _utc_now_iso()
        if "inputs_digest" not in meta:
            meta["inputs_digest"] = _inputs_digest(
                {
                    "mode": mode_clean,
                    "target_role": target_role,
                    "target_salary_lpa": target_salary_lpa,
                    "timeline_months": timeline_months,
                    "hours_per_day": hours_per_day,
                    "company_prefs": company_prefs[:200],
                    "startup_theme": startup_theme,
                    "startup_budget_range": startup_budget_range,
                    "startup_timeline_months": startup_timeline_months,
                    "has_profile": bool(profile_json),
                    "has_skills_json": bool(skills_json),
                    "has_resume": bool(resume_excerpt),
                }
            )
        meta.setdefault("version", CAREER_AI_VERSION)
        data["meta"] = meta

        used_live_ai = True

        clean = _light_validate_dream_plan(data, mode_clean)
        return (clean, used_live_ai) if return_source else clean

    except Exception:
        fallback_input = {
            "mode": mode_clean,
            "target_role": target_role,
            "target_salary_lpa": target_salary_lpa,
            "timeline_months": timeline_months,
            "hours_per_day": hours_per_day,
            "company_preferences": company_prefs,
            "startup_theme": startup_theme,
            "startup_budget_range": startup_budget_range,
            "startup_timeline_months": startup_timeline_months,
            "startup_notes": startup_notes,
        }

        fallback = {
            "mode": mode_clean,
            "summary": "We could not generate a Dream Plan due to an internal error. Please try again later.",
            "probabilities": {"lpa_12": 0, "lpa_24": 0, "lpa_48": 0},
            "missing_skills": [],
            "phases": [
                {"label": "Phase 1", "items": []},
                {"label": "Phase 2", "items": []},
                {"label": "Phase 3", "items": []},
            ],
            "plan_core": {
                "focus_30": "",
                "focus_60": "",
                "focus_90": "",
                "weeks_30": [],
                "weeks_60": [],
                "weeks_90": [],
            },
            "resources": {
                "tutorials": [],
                "mini_projects": [],
                "resume_bullets": [],
                "linkedin_actions": [],
            },
            "startup_extras": {},
            "input": fallback_input,
            "meta": {
                "generated_at_utc": _utc_now_iso(),
                "inputs_digest": _inputs_digest(
                    {"error": True, "mode": mode_clean}
                ),
                "version": CAREER_AI_VERSION,
            },
        }
        clean = _light_validate_dream_plan(fallback, mode_clean)
        return (clean, False) if return_source else clean
