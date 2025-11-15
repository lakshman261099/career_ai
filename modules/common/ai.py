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
    "Use up-to-date knowledge as of " + CAREER_AI_VERSION + ". "
    "Prefer exact dates when relevant (e.g., '06 Nov 2025'). "
    "If information could have changed, add a short 'Check latest' note."
)

# -------------------------------------------------------------------
# Utilities
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


def _coerce_skill_names(skills_list: Any) -> List[str]:
    out = []
    for s in (skills_list or []):
        if isinstance(s, dict) and (s.get("name") or "").strip():
            out.append(s["name"].strip())
        elif isinstance(s, str) and s.strip():
            out.append(s.strip())
    return out


def _to_sentence(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).rstrip(".")
    return s + "."

# -------------------------------------------------------------------
# Portfolio Builder — REAL-TIME (no stored ideas)
# -------------------------------------------------------------------
# We ask the model to return STRICT JSON. We then lightly validate/trim so UI stays tidy.

FREE_PORTFOLIO_JSON_SCHEMA = r"""
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
          "why":   { "type": "string", "minLength": 8, "maxLength": 280 },
          "what":  { "type": "array", "minItems": 3, "maxItems": 3, "items": { "type": "string", "maxLength": 110 } },
          "milestones": { "type": "array", "minItems": 3, "maxItems": 3, "items": { "type": "string", "maxLength": 110 } },
          "resume_bullets": { "type": "array", "minItems": 2, "maxItems": 3, "items": { "type": "string", "maxLength": 140 } },
          "stack": { "type": "array", "minItems": 2, "maxItems": 4, "items": { "type": "string", "maxLength": 32 } }
        }
      }
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

PRO_PORTFOLIO_JSON_SCHEMA = r"""
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
        "inputs_digest": { "type": "string" }
      }
    }
  }
}
"""

FREE_PORTFOLIO_PROMPT = """\
You are PortfolioCoach for FREE users inside a Flask web app.

Return ONLY valid JSON that strictly matches the JSON Schema below.
Do not include markdown, code fences, or commentary.

Freshness: {freshness}

Goal:
- Produce exactly ONE concise, practical project idea.
- Match the student's target role, industry, and level.
- Keep it less complex than a senior project but still portfolio-worthy.
- Make "what" 3 clear build steps, and "milestones" 3 high-confidence weekly checkpoints.
- "resume_bullets" should be recruiter-friendly and measurable when possible.
- Prefer technologies the student knows; otherwise pick common, teachable tools.

Inputs:
- target_role: {target_role}
- industry: {industry}
- level: {level}
- student_skills: {student_skills}

JSON Schema:
{json_schema}

Respond with JSON only.
"""

PRO_PORTFOLIO_PROMPT = """\
You are PortfolioCoach for PRO users in a Flask web app.

Return ONLY valid JSON that strictly matches the JSON Schema below.
Do not include markdown, code fences, or commentary.

Freshness: {freshness}

Goal:
- Produce EXACTLY THREE distinct project ideas; they must not feel like reskins.
- Each idea should read like guidance from an experienced mentor—clear, direct, and scoped to the level.
- Use the student's profile (skills, experience) when "use_profile" is true; otherwise use inputs.
- "what" must be 6 concrete build items that could map to tickets.
- "milestones" must match the provided time budget (2w, 4w, or 6w).
- "rubric" defines how success is judged.
- "risks" explain likely failure modes with mitigation.
- "stretch_goals" are optional extensions if time remains.
- "resume_bullets" are outcome-oriented and truthful.
- "stack" should be realistic for the student (favor known tools, or mainstream choices).
- Write "mentor_note" as a single short paragraph with pragmatic advice.

Inputs:
- target_role: {target_role}
- industry: {industry}
- level: {level}
- time_budget: {time_budget}
- focus_area: {focus_area}
- preferred_stack: {preferred_stack}
- use_profile: {use_profile}
- profile_json (optional): {profile_json}
- student_skills (fallback): {student_skills}

JSON Schema:
{json_schema}

Respond with JSON only.
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
        i["what"] = [(str(x)[:110]) for x in (i.get("what") or [])][:3]
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
        obj = {
            "title": (i.get("title") or "Project")[:120],
            "why": _to_sentence(i.get("why") or ""),
            "what": [(str(x)[:130]) for x in (i.get("what") or [])][:6],
            "milestones": [(str(x)[:120]) for x in (i.get("milestones") or [])][:6],
            "rubric": [(str(x)[:120]) for x in (i.get("rubric") or [])][:6],
            "risks": [(str(x)[:120]) for x in (i.get("risks") or [])][:4],
            "stretch_goals": [(str(x)[:120]) for x in (i.get("stretch_goals") or [])][:4],
            "resume_bullets": [(str(x)[:160]) for x in (i.get("resume_bullets") or [])][:5],
            "stack": [(str(x)[:32]) for x in (i.get("stack") or [])][:10],
            "mentor_note": (i.get("mentor_note") or "")[:240],
            "differentiation": "",
        }
        out.append(obj)
    meta = data.get("meta") or {}
    return {"mode": "pro", "ideas": out, "meta": meta}


def generate_project_suggestions(
    target_role: str,
    industry: str,
    experience_level: str,
    skills_list: Any,
    pro_mode: bool,
    return_source: bool = False,
    profile_json: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]] | Tuple[List[Dict[str, Any]], bool]:
    """
    Real-time generation via OpenAI (no stored archetypes).
    - Free: 1 concise, beginner-friendly idea.
    - Pro:  3 distinct, level/time-budget aware ideas with rubric/risks/stretch/mentor_note.
    """
    from openai import OpenAI

    client = OpenAI()

    role = (target_role or "").strip() or "Software Engineer Intern"
    industry = (industry or "").strip() or "General"
    level = (experience_level or "").strip() or "Student"
    student_skills = _coerce_skill_names(skills_list)
    used_live_ai = False

    try:
        if pro_mode:
            prefs = (profile_json or {}).get("preferences", {}) if profile_json else {}
            prompt = PRO_PORTFOLIO_PROMPT.format(
                freshness=FRESHNESS_NOTE,
                target_role=role,
                industry=industry,
                level=level,
                time_budget=prefs.get("time_budget", "4w"),
                focus_area=(prefs.get("focus_area") or []),
                preferred_stack=(prefs.get("preferred_stack") or []),
                use_profile=bool(profile_json is not None),
                profile_json=json.dumps(profile_json or {}, ensure_ascii=False),
                student_skills=student_skills,
                json_schema=PRO_PORTFOLIO_JSON_SCHEMA,
            )
        else:
            prompt = FREE_PORTFOLIO_PROMPT.format(
                freshness=FRESHNESS_NOTE,
                target_role=role,
                industry=industry,
                level=level,
                student_skills=student_skills,
                json_schema=FREE_PORTFOLIO_JSON_SCHEMA,
            )

        resp = client.chat.completions.create(
            model=OPENAI_MODEL_DEEP if pro_mode else OPENAI_MODEL_FAST,
            messages=[
                {
                    "role": "system",
                    "content": "You output only valid JSON that exactly matches the provided schema.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.5 if pro_mode else 0.7,
            max_tokens=1700 if pro_mode else 700,
            response_format={"type": "json_object"},
        )

        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)

        # Ensure meta (timestamp + input hash)
        meta = data.get("meta") or {}
        if "generated_at_utc" not in meta:
            meta["generated_at_utc"] = _utc_now_iso()
        if "inputs_digest" not in meta:
            meta["inputs_digest"] = _inputs_digest(
                {
                    "role": role,
                    "industry": industry,
                    "level": level,
                    "skills": student_skills[:20],
                    "pro_mode": pro_mode,
                    "profile": bool(profile_json),
                }
            )
        data["meta"] = meta

        used_live_ai = True

        # Light validation/trim to keep UI clean
        clean = (
            _light_validate_portfolio_pro(data)
            if pro_mode
            else _light_validate_portfolio_free(data)
        )
        ideas = clean.get("ideas") or []
        return (ideas, used_live_ai) if return_source else ideas

    except Exception as e:
        # Fallback: schema-preserving minimal payload (not a mock idea)
        if pro_mode:
            ideas = [
                {
                    "title": "Generation Error",
                    "why": f"ERROR: {e}",
                    "what": [],
                    "milestones": [],
                    "rubric": [],
                    "risks": [],
                    "stretch_goals": [],
                    "resume_bullets": [],
                    "stack": [],
                    "mentor_note": "",
                    "differentiation": "",
                }
                for _ in range(3)
            ]
        else:
            ideas = [
                {
                    "title": role,
                    "why": f"ERROR: {e}",
                    "what": [],
                    "milestones": [],
                    "resume_bullets": [],
                    "stack": [],
                    "differentiation": "",
                }
            ]
        return (ideas, False) if return_source else ideas

# -------------------------------------------------------------------
# Internship Analyzer
# -------------------------------------------------------------------
INTERNSHIP_ANALYZER_JSON_SCHEMA = r"""
{
  "type": "object",
  "additionalProperties": false,
  "required": ["mode", "meta"],
  "properties": {
    "mode": { "type": "string", "enum": ["free", "pro"] },
    "summary": { "type": "string" },
    "skill_growth": { "type": "array", "items": { "type": "string" } },
    "skill_enhancement": { "type": "array", "items": { "type": "string" } },
    "career_impact": { "type": "string" },
    "new_paths": { "type": "array", "items": { "type": "string" } },
    "resume_boost": { "type": "array", "items": { "type": "string" } },
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

FREE_INTERNSHIP_ANALYZER_PROMPT = """\
You are InternshipAnalyzer, a career coach for Free users.
Return ONLY valid JSON matching the schema.

Freshness: {freshness}

Rules:
- Mode: "free".
- Input is a pasted internship description (no scraping).
- Produce a short 3–4 sentence "summary" describing how this internship will help the student (exposure, learning, growth).
- Only include the keys allowed by the schema.

Inputs:
- internship_text: {internship_text}

JSON Schema:
{json_schema}

Respond with JSON only.
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

Inputs:
- internship_text: {internship_text}
- profile_json: {profile_json}

JSON Schema:
{json_schema}

Respond with JSON only.
"""

def generate_internship_analysis(
    pro_mode: bool,
    *,
    internship_text: str,
    profile_json: Dict[str, Any] | None = None,
    return_source: bool = False,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    from openai import OpenAI

    client = OpenAI()

    used_live_ai = False
    try:
        if pro_mode:
            prompt = PRO_INTERNSHIP_ANALYZER_PROMPT.format(
                internship_text=(internship_text or "")[:4000],
                profile_json=json.dumps(profile_json or {}, ensure_ascii=False),
                json_schema=INTERNSHIP_ANALYZER_JSON_SCHEMA,
                freshness=FRESHNESS_NOTE,
            )
        else:
            prompt = FREE_INTERNSHIP_ANALYZER_PROMPT.format(
                internship_text=(internship_text or "")[:4000],
                json_schema=INTERNSHIP_ANALYZER_JSON_SCHEMA,
                freshness=FRESHNESS_NOTE,
            )

        resp = client.chat.completions.create(
            model=OPENAI_MODEL_DEEP if pro_mode else OPENAI_MODEL_FAST,
            messages=[
                {
                    "role": "system",
                    "content": "You output only valid JSON and nothing else.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4 if pro_mode else 0.6,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)

        meta = data.get("meta") or {}
        if "generated_at_utc" not in meta:
            meta["generated_at_utc"] = _utc_now_iso()
        if "inputs_digest" not in meta:
            meta["inputs_digest"] = _inputs_digest(
                {
                    "pro_mode": pro_mode,
                    "internship_text": (internship_text or "")[:256],
                    "profile_keys": list((profile_json or {}).keys()),
                }
            )
        data["meta"] = meta

        used_live_ai = True
        return (data, used_live_ai) if return_source else data

    except Exception as e:
        if pro_mode:
            data = {
                "mode": "pro",
                "skill_growth": [],
                "skill_enhancement": [],
                "career_impact": "ERROR: " + str(e),
                "new_paths": [],
                "resume_boost": [],
                "meta": {
                    "generated_at_utc": _utc_now_iso(),
                    "inputs_digest": _inputs_digest({"error": True}),
                },
            }
        else:
            data = {
                "mode": "free",
                "summary": "ERROR: " + str(e),
                "meta": {
                    "generated_at_utc": _utc_now_iso(),
                    "inputs_digest": _inputs_digest({"error": True}),
                },
            }
        return (data, used_live_ai) if return_source else data

# -------------------------------------------------------------------
# Referral Trainer — AI-only
# -------------------------------------------------------------------
from helpers import referral_messages as _referral_helper

def generate_referral_messages(
    contact: Dict[str, Any],
    candidate_profile: Dict[str, Any],
    return_source: bool = False,
) -> Dict[str, str] | Tuple[Dict[str, str], bool]:
    try:
        data = _referral_helper(contact, candidate_profile, deep=False)
        used_live_ai = True
        return (data, used_live_ai) if return_source else data
    except Exception as e:
        err = "ERROR: " + str(e)
        data = {"warm": err, "cold": err, "follow": err}
        used_live_ai = False
        return (data, used_live_ai) if return_source else data

# -------------------------------------------------------------------
# SkillMapper — AI-only
# -------------------------------------------------------------------
SKILLMAPPER_JSON_SCHEMA = r"""
{
  "type": "object",
  "additionalProperties": false,
  "required": ["mode", "top_roles", "hiring_now", "call_to_action", "meta"],
  "properties": {
    "mode": { "type": "string", "enum": ["free", "pro"] },
    "top_roles": {
      "type": "array",
      "minItems": 3,
      "maxItems": 3,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "title", "seniority_target", "match_score",
          "why_fit", "primary_skill_clusters",
          "gaps", "micro_projects", "example_titles"
        ],
        "properties": {
          "title": { "type": "string", "minLength": 3 },
          "seniority_target": { "type": "string", "enum": ["intern", "junior", "entry", "mid"] },
          "match_score": { "type": "integer", "minimum": 0, "maximum": 100 },
          "why_fit": { "type": "string", "minLength": 10 },
          "primary_skill_clusters": {
            "type": "array",
            "minItems": 2,
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": ["name", "skills"],
              "properties": {
                "name": { "type": "string" },
                "skills": {
                  "type": "array",
                  "minItems": 3,
                  "items": { "type": "string" }
                }
              }
            }
          },
          "gaps": {
            "type": "array",
            "minItems": 2,
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": ["skill", "priority", "how_to_learn", "time_estimate_weeks"],
              "properties": {
                "skill": { "type": "string" },
                "priority": { "type": "integer", "minimum": 1, "maximum": 5 },
                "how_to_learn": { "type": "string" },
                "time_estimate_weeks": { "type": "integer", "minimum": 1, "maximum": 24 }
              }
            }
          },
          "micro_projects": {
            "type": "array",
            "minItems": 2,
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": ["title", "outcome", "deliverables", "difficulty"],
              "properties": {
                "title": { "type": "string" },
                "outcome": { "type": "string" },
                "deliverables": {
                  "type": "array",
                  "minItems": 2,
                  "items": { "type": "string" }
                },
                "difficulty": { "type": "string", "enum": ["easy", "medium", "hard"] }
              }
            }
          },
          "example_titles": {
            "type": "array",
            "minItems": 3,
            "items": { "type": "string" }
          }
        }
      }
    },
    "hiring_now": {
      "type": "array",
      "minItems": 3,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["role_group", "roles", "share_estimate_pct", "est_count_estimate_global", "note"],
        "properties": {
          "role_group": { "type": "string" },
          "roles": {
            "type": "array",
            "minItems": 2,
            "items": { "type": "string" }
          },
          "share_estimate_pct": { "type": "number", "minimum": 0, "maximum": 100 },
          "est_count_estimate_global": { "type": "integer", "minimum": 1000 },
          "note": { "type": "string" }
        }
      }
    },
    "call_to_action": { "type": "string", "minLength": 8 },
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

PRO_SKILLMAPPER_PROMPT = """\
You are SkillMapper, an expert career coach embedded in a Flask multi-tenant app.
Return ONLY valid JSON matching the provided JSON Schema—no markdown or code fences.

Freshness: {freshness}

Region & market emphasis:
{region_line}

Planning horizon:
The student has a {time_horizon_months}-month time horizon.
{horizon_text}

Style rules:
{style_rules}

Context & Rules:
- Mode: "pro".
- The profile object is authoritative: {profile_json}
- If resume_text is provided, prefer profile fields, then use resume_text to fill gaps.
- Produce three DISTINCT, specialized roles (not generic reskins).
- Match depth to the domain in profile; avoid vague titles.
- Give a candid match_score (0–100); avoid clustering all scores together.
- "hiring_now" is MODEL ESTIMATES (directional), not scraped; include a brief regional/sector note if relevant.
- Gaps must be specific, prioritized, and time-bounded with weeks.
- Micro-projects must be resume-ready with concrete deliverables.
- Keep language concise and recruiter-friendly.

JSON Schema:
{json_schema}

Respond with JSON only.
"""

FREE_SKILLMAPPER_PROMPT = """\
You are SkillMapper, an expert career coach for Free users in a Flask app.
Return ONLY valid JSON matching the provided JSON Schema—no markdown or commentary.

Freshness: {freshness}

{target_domain_line}

Context & Rules:
- Mode: "free".
- Input is free_text_skills (student pasted skills/interests). No scraping.
- Infer a plausible target domain and produce three DISTINCT, specialized roles within that domain.
- Keep outputs actionable but concise; assume beginner to entry level.
- "hiring_now" is MODEL ESTIMATES (directional), not scraped.
- Micro-projects should be simple but portfolio-worthy.

Inputs:
- free_text_skills: {free_text_skills}

JSON Schema:
{json_schema}

Respond with JSON only.
"""

def build_skillmapper_messages(pro_mode: bool, inputs: Dict[str, Any]) -> List[Dict[str, str]]:
    if pro_mode:
        profile_json = inputs.get("profile_json") or {}
        hints = inputs.get("hints") or {}

        opts: Dict[str, Any] = {}
        if isinstance(profile_json, dict):
            opts = profile_json.get("_skillmapper_options") or {}

        def _pick(name: str, default: Any = None) -> Any:
            if isinstance(hints, dict) and hints.get(name) not in (None, ""):
                return hints.get(name)
            if isinstance(opts, dict) and opts.get(name) not in (None, ""):
                return opts.get(name)
            return default

        # Time horizon (months)
        raw_horizon = _pick("time_horizon_months", 6)
        try:
            time_horizon = int(raw_horizon)
        except Exception:
            time_horizon = 6
        time_horizon = max(3, min(12, time_horizon))

        region_sector = str(_pick("region_sector", "") or "").strip()
        region_line = (
            f"Emphasize region/sector context: {region_sector}."
            if region_sector
            else "Emphasize global hiring context (no specific region provided)."
        )

        # Horizon narrative used in prompt
        if time_horizon <= 4:
            horizon_text = (
                "Treat this as a short 3–4 month horizon: "
                "prioritize quick wins and near-fit roles. Limit major skill gaps "
                "and keep micro-projects small (2–4 weeks each). Avoid recommending "
                "large pivots that would realistically take more than a few months."
            )
        elif time_horizon <= 8:
            horizon_text = (
                "Treat this as a medium 6–8 month horizon: "
                "it's reasonable to recommend 2–3 substantial gaps and a moderate "
                "career step up. Micro-projects can span 4–8 weeks and should build "
                "toward a stronger role within the same lane."
            )
        else:
            horizon_text = (
                "Treat this as a longer 9–12 month horizon: "
                "larger pivots are acceptable if justified. You may recommend more "
                "ambitious roles with several gaps, but micro-projects and learning "
                "plans must still be realistic for 12 months of focused work."
            )

        style_rules = "\n".join(
            [
                "- Use tight, recruiter-friendly language.",
                "- Roles must be distinct; avoid duplicates and near-duplicates.",
                "- Target level is junior/intern/entry unless the profile clearly supports mid.",
                "- For each gap: include priority (1–5), a concrete learning step, and a realistic time_estimate_weeks.",
                "- Micro-projects should align with the stated time horizon (shorter and simpler for 3 months, deeper for 12).",
            ]
        )

        prompt = PRO_SKILLMAPPER_PROMPT.format(
            profile_json=json.dumps(profile_json, ensure_ascii=False),
            json_schema=SKILLMAPPER_JSON_SCHEMA,
            freshness=FRESHNESS_NOTE,
            region_line=region_line,
            time_horizon_months=time_horizon,
            horizon_text=horizon_text,
            style_rules=style_rules,
        )
        resume_text = (inputs.get("resume_text") or "").strip()
        if resume_text:
            prompt += f"\n\nAdditional resume_text (truncated as needed):\n{resume_text}"
    else:
        free_text = (inputs.get("free_text_skills") or "").strip()
        hints = inputs.get("hints") or {}
        target_domain = ""
        if isinstance(hints, dict):
            target_domain = (hints.get("target_domain") or "").strip()
        target_domain_line = (
            f"Target domain hint from user: {target_domain}"
            if target_domain
            else "No explicit domain; infer a logical domain from the text."
        )
        prompt = FREE_SKILLMAPPER_PROMPT.format(
            free_text_skills=free_text,
            json_schema=SKILLMAPPER_JSON_SCHEMA,
            freshness=FRESHNESS_NOTE,
            target_domain_line=target_domain_line,
        )

    return [
        {"role": "system", "content": "You output only valid JSON and nothing else."},
        {"role": "user", "content": prompt},
    ]


def _light_validate_skillmap(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {
            "mode": "free",
            "top_roles": [],
            "hiring_now": [],
            "call_to_action": "",
            "meta": {},
        }
    for k in ["mode", "top_roles", "hiring_now", "call_to_action", "meta"]:
        if k not in data:
            if k in ("top_roles", "hiring_now"):
                data[k] = []
            elif k == "meta":
                data[k] = {}
            else:
                data[k] = ""
    if data["mode"] not in ("free", "pro"):
        data["mode"] = "free"
    if not isinstance(data.get("top_roles"), list):
        data["top_roles"] = []
    else:
        data["top_roles"] = data["top_roles"][:3]
    if not isinstance(data.get("hiring_now"), list):
        data["hiring_now"] = []
    elif len(data["hiring_now"]) > 5:
        data["hiring_now"] = data["hiring_now"][:5]
    if not isinstance(data.get("meta"), dict):
        data["meta"] = {}
    return data


def generate_skillmap(
    pro_mode: bool,
    *,
    profile_json: Dict[str, Any] | None = None,
    resume_text: str | None = None,
    free_text_skills: str | None = None,
    return_source: bool = False,
    hints: Dict[str, Any] | None = None,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    from openai import OpenAI

    client = OpenAI()

    used_live_ai = False
    try:
        if pro_mode:
            inputs: Dict[str, Any] = {
                "profile_json": profile_json or {},
                "resume_text": (resume_text or "").strip(),
                "hints": hints or {},
            }
        else:
            inputs = {
                "free_text_skills": (free_text_skills or "").strip(),
                "hints": hints or {},
            }

        messages = build_skillmapper_messages(pro_mode, inputs)

        resp = client.chat.completions.create(
            model=OPENAI_MODEL_DEEP if pro_mode else OPENAI_MODEL_FAST,
            messages=messages,
            temperature=0.4 if pro_mode else 0.6,
            max_tokens=1400 if pro_mode else 900,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)

        data = _light_validate_skillmap(data)

        meta = data.get("meta") or {}
        if "generated_at_utc" not in meta:
            meta["generated_at_utc"] = _utc_now_iso()
        if "inputs_digest" not in meta:
            meta["inputs_digest"] = _inputs_digest(inputs)

        # Optional profile snapshot for Pro (used in right-rail UI)
        try:
            if pro_mode and isinstance(inputs.get("profile_json"), dict):
                prof = inputs["profile_json"]
                ident = prof.get("identity") or {}
                skills_field = prof.get("skills") or []
                key_skills: List[str] = []
                if isinstance(skills_field, list):
                    for s in skills_field:
                        if isinstance(s, str) and s.strip():
                            key_skills.append(s.strip())
                        elif isinstance(s, dict):
                            n = (s.get("name") or s.get("skill") or "").strip()
                            if n:
                                key_skills.append(n)
                meta["profile_snapshot"] = {
                    "full_name": ident.get("full_name") or "",
                    "headline": ident.get("headline") or "",
                    "key_skills": key_skills[:20],
                }
        except Exception:
            # Snapshot is optional; never break generation
            pass

        data["meta"] = meta

        used_live_ai = True
        return (data, used_live_ai) if return_source else data

    except Exception as e:
        data = _light_validate_skillmap(
            {
                "mode": "pro" if pro_mode else "free",
                "top_roles": [],
                "hiring_now": [],
                "call_to_action": "ERROR: " + str(e),
                "meta": {
                    "generated_at_utc": _utc_now_iso(),
                    "inputs_digest": _inputs_digest({"error": True}),
                },
            }
        )
        return (data, used_live_ai) if return_source else data
