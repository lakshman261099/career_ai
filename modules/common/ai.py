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
# SkillMapper — AI-only (simple, robust, live)
# -------------------------------------------------------------------

SIMPLE_SKILLMAPPER_INSTRUCTIONS = """\
You are SkillMapper, an expert career coach for students and early-career engineers.

Freshness: {freshness}

Goal:
- Suggest exactly THREE realistic roles for this student.
- For each role, include:
  - A specific job title (not generic "developer").
  - Level: intern / entry / junior / mid (pick what fits the profile).
  - A match score from 0–100 (be honest).
  - 1–2 sentences on why this role fits ("why_fit").
  - 5–10 key skills as a comma-separated list.
  - 3–6 key gaps as a comma-separated list (topics/skills they should learn).
  - 2–4 micro-project ideas as a semicolon-separated list.
  - A realistic, directional salary band string (no scraping) like:
    * "3–7 LPA (India internships)"
    * "6–12 LPA (India entry-level product)"
    * "₹25k–₹60k/month stipend (India, big city)"
  - A short region/geo label like:
    * "India · tier-1 cities"
    * "India · remote-friendly"
    * "Global remote"
    * "Local city / tier-2"

IMPORTANT:
- You MUST follow the output format below exactly.
- Do NOT include markdown, bullets, JSON, or explanations.
- Do NOT use the '|' character inside any field values.
- Be concise but useful (like you're talking to a smart student).
"""

SIMPLE_SKILLMAPPER_OUTPUT_FORMAT = """\
OUTPUT FORMAT (exact):

ROLE|<title>|<level>|<match_score_0_100>|<why_fit>|<skills_comma_separated>|<gaps_comma_separated>|<micro_projects_semicolon_separated>|<salary_band>|<region>
ROLE|...
ROLE|...

STEPS|<4-8 short action steps separated by '; '>

SUMMARY|<2-4 sentence summary of how these roles + steps help the student>
"""

def _build_skillmapper_prompt(pro_mode: bool, inputs: Dict[str, Any]) -> str:
    profile_json = inputs.get("profile_json") or {}
    resume_text = (inputs.get("resume_text") or "").strip()
    free_text_skills = (inputs.get("free_text_skills") or "").strip()
    hints = inputs.get("hints") or {}

    region_hint = (
        (hints.get("region_sector") if isinstance(hints, dict) else None)
        or (hints.get("region_focus") if isinstance(hints, dict) else None)
        or "India · early-career tech roles"
    )

    target_domain = ""
    if isinstance(hints, dict):
        target_domain = (hints.get("target_domain") or "").strip()

    mode_label = "PRO" if pro_mode else "FREE"
    extra_mode_text = (
        "The student is a paying PRO user. You can assume they are a bit more serious and may handle slightly more ambitious roles and gaps."
        if pro_mode
        else "The student is on the FREE plan. Keep roles and projects friendly for beginners or early-career students."
    )

    prompt = SIMPLE_SKILLMAPPER_INSTRUCTIONS.format(freshness=FRESHNESS_NOTE)
    prompt += "\n\n" + extra_mode_text + "\n\n"
    prompt += f"Region / market emphasis: {region_hint}\n"
    if target_domain:
        prompt += f"Target domain hint from student: {target_domain}\n"
    prompt += "\nStudent profile (JSON-like):\n"
    prompt += json.dumps(profile_json, ensure_ascii=False)[:3000]
    if resume_text:
        prompt += "\n\nResume text (truncated):\n"
        prompt += resume_text[:3000]
    if free_text_skills:
        prompt += "\n\nExtra free-text skills / context:\n"
        prompt += free_text_skills[:1500]

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
            # Expected 10 fields including "ROLE"
            # [0]=ROLE, 1=title, 2=level, 3=match_score, 4=why_fit,
            # 5=skills, 6=gaps, 7=projects, 8=salary, 9=region
            while len(parts) < 10:
                parts.append("")
            _, title, level, ms_str, why_fit, skills_csv, gaps_csv, proj_semi, salary_band, region = parts[:10]

            title = title.strip() or "Suggested role"
            level = level.strip() or "entry"
            why_fit = why_fit.strip()
            if not why_fit:
                why_fit = "This role aligns with your current skills and growth potential."
            try:
                match_score = int(ms_str.strip())
            except Exception:
                match_score = 0

            skills = [
                s.strip() for s in skills_csv.split(",") if s.strip()
            ] if skills_csv else []

            gaps_raw = [
                g.strip() for g in gaps_csv.split(",") if g.strip()
            ] if gaps_csv else []

            projects_raw = [
                p.strip() for p in proj_semi.split(";") if p.strip()
            ] if proj_semi else []

            # Build role structure compatible with templates + history
            primary_skill_clusters = []
            if skills:
                primary_skill_clusters.append(
                    {"name": "Core skills", "skills": skills}
                )

            gaps = []
            for g in gaps_raw:
                gaps.append(
                    {
                        "skill": g,
                        "priority": 3,
                        "how_to_learn": "",
                        "time_weeks": 4,
                    }
                )

            micro_projects = []
            for p in projects_raw:
                micro_projects.append(
                    {
                        "title": p,
                        "outcome": "",
                    }
                )

            comp_obj = {
                "range": salary_band.strip(),
                "ctc_range": salary_band.strip(),
                "entry": salary_band.strip(),
                "entry_level": salary_band.strip(),
                "intern_range": salary_band.strip(),
                "stipend": salary_band.strip(),
            }

            role = {
                "title": title,
                "level": level,
                "match_score": match_score,
                "why_fit": why_fit,
                "summary": why_fit,
                "primary_skill_clusters": primary_skill_clusters,
                "gaps": gaps,
                "micro_projects": micro_projects,
                "example_titles": [],
                "skills": skills,
                "stack": skills,  # reuse skills as stack hint
                "salary": salary_band.strip(),
                "compensation": comp_obj,
                "region": region.strip(),
                "geo": region.strip(),
            }
            roles.append(role)

        elif line.startswith("STEPS|"):
            payload = line[len("STEPS|") :].strip()
            if payload:
                parts = [p.strip() for p in payload.split(";") if p.strip()]
                next_steps.extend(parts)

        elif line.startswith("SUMMARY|") and not impact_summary:
            impact_summary = line[len("SUMMARY|") :].strip()

    return {
        "roles": roles,
        "top_roles": roles,  # for back-compat with _normalize_roles
        "hiring_now": [],    # not used in new UI; keep key for safety
        "market_insights": {},
        "learning_paths": [],
        "next_steps": next_steps,
        "impact_summary": impact_summary,
    }


def _light_validate_skillmap(data: Any) -> Dict[str, Any]:
    """
    Keep structure predictable for templates/routes.
    """
    if not isinstance(data, dict):
        data = {}

    mode = data.get("mode") or "free"
    if mode not in ("free", "pro"):
        mode = "free"

    roles = data.get("roles") or data.get("top_roles") or []
    if not isinstance(roles, list):
        roles = []

    cleaned_roles = []
    for r in roles[:3]:
        if not isinstance(r, dict):
            continue
        role = dict(r)
        role.setdefault("title", "Suggested role")
        role.setdefault("level", "entry")
        role.setdefault("match_score", 0)
        role.setdefault("why_fit", "")
        role.setdefault("summary", role.get("why_fit", ""))
        role.setdefault("primary_skill_clusters", [])
        role.setdefault("gaps", [])
        role.setdefault("micro_projects", [])
        role.setdefault("example_titles", [])
        role.setdefault("salary", "")
        role.setdefault("compensation", {"range": role.get("salary", "")})
        role.setdefault("region", "")
        role.setdefault("geo", role.get("region", ""))
        cleaned_roles.append(role)

    hiring_now = data.get("hiring_now") or []
    if not isinstance(hiring_now, list):
        hiring_now = []

    learning_paths = data.get("learning_paths") or []
    if not isinstance(learning_paths, list):
        learning_paths = []

    next_steps = data.get("next_steps") or []
    if not isinstance(next_steps, list):
        next_steps = []

    impact_summary = data.get("impact_summary") or ""
    call_to_action = data.get("call_to_action") or ""
    meta = data.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}

    out = {
        "mode": mode,
        "roles": cleaned_roles,
        "top_roles": cleaned_roles,
        "hiring_now": hiring_now[:5],
        "market_insights": data.get("market_insights") or {},
        "learning_paths": learning_paths,
        "next_steps": next_steps,
        "impact_summary": impact_summary,
        "call_to_action": call_to_action,
        "meta": meta,
    }
    return out


def generate_skillmap(
    pro_mode: bool,
    *,
    profile_json: Dict[str, Any] | None = None,
    resume_text: str | None = None,
    free_text_skills: str | None = None,
    return_source: bool = False,
    hints: Dict[str, Any] | None = None,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    """
    Main SkillMapper entry.

    IMPORTANT:
    - We no longer ask the model for JSON.
    - Instead we ask for a simple pipe-delimited text format and parse it ourselves.
    - This avoids all JSON delimiter / brace / schema errors.
    """
    from openai import OpenAI

    client = OpenAI()

    used_live_ai = False
    inputs: Dict[str, Any]
    if pro_mode:
        inputs = {
            "profile_json": profile_json or {},
            "resume_text": (resume_text or "").strip(),
            "hints": hints or {},
        }
    else:
        inputs = {
            "profile_json": profile_json or {},
            "resume_text": (resume_text or "").strip(),
            "free_text_skills": (free_text_skills or "").strip(),
            "hints": hints or {},
        }

    try:
        prompt = _build_skillmapper_prompt(pro_mode, inputs)

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
            meta["inputs_digest"] = _inputs_digest(inputs)
        meta.setdefault("source", "pro" if pro_mode else "free")
        meta.setdefault("using_profile", bool(inputs.get("profile_json")))

        hints_obj = inputs.get("hints") or {}
        region_hint = None
        if isinstance(hints_obj, dict):
            region_hint = (
                hints_obj.get("region_sector")
                or hints_obj.get("region_focus")
                or None
            )
        if region_hint:
            meta.setdefault("region_focus", region_hint)
        meta.setdefault("version", CAREER_AI_VERSION)

        # Optional profile snapshot for UI
        try:
            prof = inputs.get("profile_json") or {}
            if isinstance(prof, dict) and prof:
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
            pass

        data["meta"] = meta
        used_live_ai = True

        return (data, used_live_ai) if return_source else data

    except Exception as e:
        # Hard fail: return empty but safe payload; routes/templates will show debug note
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
