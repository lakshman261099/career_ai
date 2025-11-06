# modules/common/ai.py
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

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


@dataclass
class Suggestion:
    title: str
    why: str
    what: List[str]
    resume_bullets: List[str]
    stack: List[str]
    differentiation: str = ""


def _coerce_skill_names(skills_list: Any) -> List[str]:
    out = []
    for s in skills_list or []:
        if isinstance(s, dict) and (s.get("name") or "").strip():
            out.append(s["name"].strip())
        elif isinstance(s, str) and s.strip():
            out.append(s.strip())
    return out


import json

# -------------------------------------------------------------------
# Portfolio Builder — High-quality (uses Profile Portal when available)
# -------------------------------------------------------------------
import os
from typing import Any, Dict, List, Tuple

from helpers import portfolio_suggestions as _portfolio_suggestions_helper

PROJECT_SUGGESTIONS_SCHEMA = r"""
{
  "type": "object",
  "additionalProperties": false,
  "required": ["mode", "ideas", "meta"],
  "properties": {
    "mode": { "type": "string", "enum": ["free", "pro"] },
    "ideas": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["title","why","what","resume_bullets","stack"],
        "properties": {
          "title": { "type": "string", "minLength": 8 },
          "why": { "type": "string", "minLength": 12 },
          "stack": { "type": "array", "items": { "type": "string" }, "minItems": 3, "maxItems": 10 },
          "what": { "type": "array", "items": { "type": "string" }, "minItems": 3, "maxItems": 8 },
          "resume_bullets": { "type": "array", "items": { "type": "string" }, "minItems": 3, "maxItems": 6 },
          "milestones": { "type": "array", "items": { "type": "string" }, "minItems": 3, "maxItems": 6 },
          "differentiation": { "type": "string" }
        }
      }
    },
    "meta": {
      "type": "object",
      "additionalProperties": false,
      "required": ["generated_at_utc","inputs_digest","used_profile_fields"],
      "properties": {
        "generated_at_utc": { "type": "string" },
        "inputs_digest": { "type": "string" },
        "used_profile_fields": { "type": "array", "items": { "type": "string" } }
      }
    }
  }
}
"""

PORTFOLIO_FREE_PROMPT = """\
You are PortfolioWizard for a student career app.

GOAL: Produce ONE practical, beginner-friendly project idea that can be completed in ~2–4 weeks.
It must be tailored to the student's target role and industry.

Context:
- Freshness: {freshness}
- Target role: {target_role}
- Industry: {industry}
- Experience level: {experience_level}
- Student skills (from profile): {skill_names}

Rules:
- Output JSON matching the schema.
- Keep the stack realistic for the student's current skills.
- "what" are concrete features; "resume_bullets" are quantified, recruiter-friendly.
- "milestones" are chronological and deliverable-focused (prototype → v1 → polish).
- DO NOT include any commentary—JSON only.

JSON Schema:
{json_schema}
"""

PORTFOLIO_PRO_PROMPT = """\
You are PortfolioWizard Pro for a student career app.

GOAL: Produce THREE resume-ready project ideas tailored to the profile and target domain.
Each idea should be distinct and showcase a different facet (e.g., data, backend, UI, systems, etc.).

Context:
- Freshness: {freshness}
- Target role: {target_role}
- Industry: {industry}
- Experience level: {experience_level}
- Full Profile JSON (authoritative): {profile_json}

Guidelines:
- Output JSON matching the schema.
- Use the student's skills, experience, and past projects to pick an appropriate stack and scope.
- "what" are concrete features; "resume_bullets" are quantified and employer-friendly.
- "milestones" show a 2–6 week plan with shippable checkpoints.
- "differentiation" explains how this idea stands out from generic student projects.
- DO NOT include any commentary—JSON only.

JSON Schema:
{json_schema}
"""


def _inputs_digest(obj: Any) -> str:
    try:
        import hashlib

        s = json.dumps(obj, sort_keys=True)[:5000]
        return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()
    except Exception:
        return "sha256:na"


def _coerce_skill_names(skills_list: Any) -> List[str]:
    out = []
    for s in skills_list or []:
        if isinstance(s, dict) and (s.get("name") or "").strip():
            out.append(s["name"].strip())
        elif isinstance(s, str) and s.strip():
            out.append(s.strip())
    return out


def _postprocess_ideas(ideas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tidy fields so templates always render nicely."""
    cleaned = []
    for it in ideas or []:
        cleaned.append(
            {
                "title": (it.get("title") or "Project Idea").strip(),
                "why": (it.get("why") or "").strip(),
                "stack": [s.strip() for s in (it.get("stack") or []) if str(s).strip()][
                    :10
                ],
                "what": [w.strip() for w in (it.get("what") or []) if str(w).strip()][
                    :8
                ],
                "resume_bullets": [
                    b.strip()
                    for b in (it.get("resume_bullets") or [])
                    if str(b).strip()
                ][:6],
                "milestones": [
                    m.strip() for m in (it.get("milestones") or []) if str(m).strip()
                ][:6],
                "differentiation": (it.get("differentiation") or "").strip(),
            }
        )
    return cleaned


def generate_project_suggestions(
    target_role: str,
    industry: str,
    experience_level: str,
    skills_list: Any,
    pro_mode: bool,
    return_source: bool = False,
    *,
    profile_json: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]] | Tuple[List[Dict[str, Any]], bool]:
    """
    High-quality suggestions.
    - Free: 1 idea (beginner-friendly)
    - Pro: 3 ideas (resume-ready, milestones, impact bullets, tailored stack)
    Uses OpenAI when available; falls back to helper on failure.
    """
    used_live_ai = False
    target_role = (target_role or "").strip() or "Software Engineer Intern"
    industry = (industry or "").strip() or "technology"
    experience_level = (experience_level or "").strip() or "student"
    skill_names = _coerce_skill_names(skills_list)

    try:
        from openai import OpenAI

        client = OpenAI()

        if pro_mode:
            prompt = PORTFOLIO_PRO_PROMPT.format(
                freshness=FRESHNESS_NOTE,
                target_role=target_role,
                industry=industry,
                experience_level=experience_level,
                profile_json=json.dumps(profile_json or {}, ensure_ascii=False),
                json_schema=PROJECT_SUGGESTIONS_SCHEMA,
            )
        else:
            prompt = PORTFOLIO_FREE_PROMPT.format(
                freshness=FRESHNESS_NOTE,
                target_role=target_role,
                industry=industry,
                experience_level=experience_level,
                skill_names=", ".join(skill_names[:20]),
                json_schema=PROJECT_SUGGESTIONS_SCHEMA,
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
            temperature=0.45 if pro_mode else 0.7,
            max_tokens=1200 if pro_mode else 700,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        ideas = _postprocess_ideas(data.get("ideas") or [])
        # enforce 3 vs 1
        ideas = ideas[:3] if pro_mode else ideas[:1]
        used_live_ai = True

        return (ideas, used_live_ai) if return_source else ideas

    except Exception:
        # Fallback: legacy helper (keeps app functional if API/config missing)
        raw_list = _portfolio_suggestions_helper(
            name="", role=target_role, deep=pro_mode
        )

        # Convert legacy list into structured items
        def _legacy_to_struct(txt: str) -> Dict[str, Any]:
            parts = [p.strip() for p in (txt or "").split("—") if p.strip()]
            title = parts[0] if parts else "Project Idea"
            tech = parts[1] if len(parts) > 1 else ""
            features = parts[2] if len(parts) > 2 else ""
            outcome = parts[3] if len(parts) > 3 else ""
            stack = [s.strip() for s in tech.replace(",", " ").split() if s.strip()][:6]
            what = [
                w.strip() for w in features.replace(";", ",").split(",") if w.strip()
            ][:6]
            resume_bullets = [
                f"Implemented {w}" + (f" using {', '.join(stack[:3])}" if stack else "")
                for w in what[:3]
            ]
            if outcome:
                resume_bullets.append(f"Achieved: {outcome}")
            milestones = [
                "Week 1: Scope + repo + basic scaffold",
                "Week 2: Core feature set",
                "Week 3: Polish + README + demo",
            ]
            return {
                "title": title,
                "why": outcome,
                "stack": stack,
                "what": what,
                "resume_bullets": resume_bullets,
                "milestones": milestones,
                "differentiation": "",
            }

        structured = [_legacy_to_struct(i) for i in raw_list]
        ideas = structured[:3] if pro_mode else structured[:1]
        return (ideas, False) if return_source else ideas


# -------------------------------------------------------------------
# Internship Analyzer — AI-only (paste-only; no scraping)
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

        # Ensure meta exists
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
        # Schema-preserving error fallback (not a mock suggestion)
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
# Referral Trainer — AI-only (Free today; Pro templates coming soon)
# -------------------------------------------------------------------
from helpers import referral_messages as _referral_helper


def generate_referral_messages(
    contact: Dict[str, Any],
    candidate_profile: Dict[str, Any],
    return_source: bool = False,
) -> Dict[str, str] | Tuple[Dict[str, str], bool]:
    """
    Returns {"warm","cold","follow"} using AI; on failure returns error strings.
    """
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
# SkillMapper — AI-only (Free & Pro)
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

Context & Rules:
- Mode: "pro".
- The profile object is authoritative: {profile_json}
- If resume_text is provided, prefer profile fields, then use resume_text to fill gaps.
- Produce three DISTINCT, specialized roles (not generic).
- Match depth to the domain in profile; avoid vague titles.
- Give a candid match_score (0–100).
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


def build_skillmapper_messages(
    pro_mode: bool, inputs: Dict[str, Any]
) -> List[Dict[str, str]]:
    if pro_mode:
        prompt = PRO_SKILLMAPPER_PROMPT.format(
            profile_json=json.dumps(
                inputs.get("profile_json") or {}, ensure_ascii=False
            ),
            json_schema=SKILLMAPPER_JSON_SCHEMA,
            freshness=FRESHNESS_NOTE,
        )
        resume_text = (inputs.get("resume_text") or "").strip()
        if resume_text:
            prompt += f"\n\nAdditional resume_text:\n{resume_text}"
    else:
        prompt = FREE_SKILLMAPPER_PROMPT.format(
            free_text_skills=(inputs.get("free_text_skills") or "").strip(),
            json_schema=SKILLMAPPER_JSON_SCHEMA,
            freshness=FRESHNESS_NOTE,
        )
    return [
        {"role": "system", "content": "You output only valid JSON and nothing else."},
        {"role": "user", "content": prompt},
    ]


def _light_validate_skillmap(data: Any) -> Dict[str, Any]:
    """
    Tolerant validator: patch missing fields instead of throwing.
    """
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
            data[k] = (
                [] if k in ("top_roles", "hiring_now") else {} if k == "meta" else ""
            )

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
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    from openai import OpenAI

    client = OpenAI()

    used_live_ai = False
    try:
        inputs: Dict[str, Any]
        if pro_mode:
            inputs = {
                "profile_json": profile_json or {},
                "resume_text": resume_text or "",
            }
        else:
            inputs = {"free_text_skills": (free_text_skills or "").strip()}

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

        # Ensure meta
        meta = data.get("meta") or {}
        if "generated_at_utc" not in meta:
            meta["generated_at_utc"] = _utc_now_iso()
        if "inputs_digest" not in meta:
            meta["inputs_digest"] = _inputs_digest(inputs)
        data["meta"] = meta

        used_live_ai = True
        return (data, used_live_ai) if return_source else data

    except Exception as e:
        # Schema-friendly error payload (not a mock suggestion)
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
