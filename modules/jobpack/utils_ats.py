# modules/jobpack/utils_ats.py
import json
import logging
import os
import re
from typing import Any, Dict, List

# ------------------------------------------------------------------
# Model + freshness config (env-driven)
# ------------------------------------------------------------------
DEEP_MODEL = os.getenv("OPENAI_MODEL_DEEP", "gpt-4o")
FAST_MODEL = os.getenv("OPENAI_MODEL_FAST", "gpt-4o-mini")

CAREER_AI_VERSION = os.getenv("CAREER_AI_VERSION", "2025-Q4")
FRESHNESS_NOTE = (
    "Use up-to-date knowledge as of " + CAREER_AI_VERSION + ". "
    "Prefer exact dates when relevant (e.g., '06 Nov 2025'). "
    "If information could have changed, add a short 'Check latest' note."
)


# ------------------------------------------------------------------
# Clean JD
# ------------------------------------------------------------------
def _clean_jd(text: str) -> str:
    if not text:
        return ""
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # introduce headings to help the model structure
    text = re.sub(
        r"(Responsibilities|Requirements|Skills|Key Skills|Experience|Qualifications|Nice to have)[:\-]?",
        r"\n\n### \1\n",
        text,
        flags=re.I,
    )
    # keep prompt context bounded
    return text[:4000]


# ------------------------------------------------------------------
# JSON Schema (hardened)
# ------------------------------------------------------------------
JOBPACK_JSON_SCHEMA = r"""
{
  "type": "object",
  "additionalProperties": true,
  "required": [
    "summary","role_detected","fit_overview","ats_score",
    "skill_table","rewrite_suggestions","next_steps",
    "impact_summary","subscores","resume_ats",
    "learning_links","interview_qa","practice_plan","application_checklist"
  ],
  "properties": {
    "summary": {"type":"string","minLength": 20},
    "role_detected": {"type":"string","minLength": 2},
    "fit_overview": {
      "type":"array",
      "minItems": 3,
      "maxItems": 5,
      "items": {
        "type":"object",
        "required":["category","match","comment"],
        "properties":{
          "category":{"type":"string","minLength": 3},
          "match":{"type":"integer","minimum":0,"maximum":100},
          "comment":{"type":"string","minLength": 12}
        }
      }
    },
    "ats_score":{"type":"integer","minimum":0,"maximum":100},
    "skill_table":{
      "type":"array",
      "minItems": 6,
      "items":{
        "type":"object",
        "required":["skill","status"],
        "properties":{
          "skill":{"type":"string","minLength":2},
          "status":{"type":"string","enum":["Matched","Missing","Weak Mention"]}
        }
      }
    },
    "rewrite_suggestions":{"type":"array","minItems":3,"items":{"type":"string","minLength": 10}},
    "next_steps":{"type":"array","minItems":3,"items":{"type":"string","minLength": 10}},
    "impact_summary":{"type":"string","minLength": 12},
    "subscores":{
      "type":"object",
      "required":["keyword_relevance","quantifiable_impact","formatting_clarity","professional_tone"],
      "properties":{
        "keyword_relevance":{"type":"integer","minimum":0,"maximum":100},
        "quantifiable_impact":{"type":"integer","minimum":0,"maximum":100},
        "formatting_clarity":{"type":"integer","minimum":0,"maximum":100},
        "professional_tone":{"type":"integer","minimum":0,"maximum":100}
      }
    },
    "resume_ats":{
      "type":"object",
      "required":["resume_ats_score","blockers","warnings","keyword_coverage","resume_rewrite_actions","exact_phrases_to_add"],
      "properties":{
        "resume_ats_score":{"type":"integer","minimum":0,"maximum":100},
        "blockers":{"type":"array","items":{"type":"string"}},
        "warnings":{"type":"array","items":{"type":"string"}},
        "keyword_coverage":{
          "type":"object",
          "required":["required_keywords","present_keywords","missing_keywords"],
          "properties":{
            "required_keywords":{"type":"array","items":{"type":"string"}},
            "present_keywords":{"type":"array","items":{"type":"string"}},
            "missing_keywords":{"type":"array","items":{"type":"string"}}
          }
        },
        "resume_rewrite_actions":{"type":"array","minItems":3,"items":{"type":"string","minLength":12}},
        "exact_phrases_to_add":{"type":"array","minItems":6,"items":{"type":"string","minLength":2}}
      }
    },
    "learning_links":{
      "type":"array",
      "minItems":3,
      "items":{
        "type":"object",
        "required":["label","url","why"],
        "properties":{
          "label":{"type":"string","minLength":3},
          "url":{"type":"string","pattern":"^https?://"},
          "why":{"type":"string","minLength":10}
        }
      }
    },
    "interview_qa":{
      "type":"array",
      "minItems":6,
      "items":{
        "type":"object",
        "required":["q","a_outline","why_it_matters","followup"],
        "properties":{
          "q":{"type":"string","minLength":6},
          "a_outline":{"type":"array","minItems":3,"items":{"type":"string","minLength":4}},
          "why_it_matters":{"type":"string","minLength":8},
          "followup":{"type":"string","minLength":6}
        }
      }
    },
    "practice_plan":{
      "type":"array",
      "minItems":3,
      "items":{
        "type":"object",
        "required":["period","goals","tasks","output"],
        "properties":{
          "period":{"type":"string"},
          "goals":{"type":"string","minLength":6},
          "tasks":{"type":"array","minItems":3,"items":{"type":"string","minLength":4}},
          "output":{"type":"string","minLength":6}
        }
      }
    },
    "application_checklist":{"type":"array","minItems":6,"items":{"type":"string","minLength":6}}
  }
}
"""


# ------------------------------------------------------------------
# Prompt templates (freshness-aware)
# ------------------------------------------------------------------
JOBPACK_PROMPT = """\
You are CareerAI — a professional resume & job-fit evaluator for students and new grads.

CONTEXT / FRESHNESS
- {freshness}

ATS & LIMITATIONS
- You do NOT run a real ATS scanner. All ATS-related scores are model ESTIMATES of how well the resume text aligns to the job description.
- Do NOT claim that the resume "passed" or "failed" any external system; instead, talk about "likelihood of passing keyword filters".
- If the resume/profile text is empty, treat this as "no resume on file": keep resume_ats_score conservative and explicitly mention the missing resume in blockers/warnings.
- Guidance must be realistic and grounded strictly in the provided text.

RESUME CONTEXT
- {resume_hint}

STRICT RULES
- Use ONLY the provided JD and Resume/Profile text; do not invent employers or projects.
- Be specific and grounded in the JD’s terminology. Prefer exact tokens from the JD.
- Never use placeholders like "Overall", "Resource", "N/A", "None" unless factually true.
- Learning links must be real, specific URLs (prefer official docs/tutorials). Include a short "why".
- "exact_phrases_to_add" should be literal strings the student can paste into their resume to pass ATS (e.g., "Adobe Illustrator", "2D animation with Spine", "slot game assets", "3DS Max").
- If a capability is not evidenced in the resume, write "Not evidenced in resume — add via project or bullet" in comments or warnings.

OUTPUT
Return ONLY valid JSON that matches this schema:
{schema}

INPUTS
JD (cleaned):
{jd}

Resume/Profile:
{resume}
"""

REPAIR_PROMPT = """\
You previously produced JSON for the CareerAI Job Pack, but some parts are too generic or violated constraints.

CONTEXT / FRESHNESS
- {freshness}

ISSUES TO FIX (list):
{issues}

CURRENT JSON (fix this; keep keys):
{current_json}

TASK
- Repair low-quality fields. Replace placeholders such as "Overall" categories, "Resource" labels, empty links, sparse practice plans, and generic Q&A.
- Ensure everything is grounded in the JD and Resume text.
- Return a FULL corrected JSON object (not a diff), valid per the same schema. Output JSON only.
"""


# ------------------------------------------------------------------
# Normalizers (template safety)
# ------------------------------------------------------------------
def _normalize_for_template(data: Dict[str, Any]) -> Dict[str, Any]:
    # fit_overview items: ensure "category" + "match" + "comment"
    fo = data.get("fit_overview")
    if isinstance(fo, list):
        for item in fo:
            if not isinstance(item, dict):
                continue
            item["category"] = (
                item.get("category")
                or item.get("name")
                or item.get("area")
                or "Technical Skills"
            )
            if "match" not in item and "score" in item:
                try:
                    item["match"] = int(item["score"])
                except Exception:
                    item["match"] = 0
            item["match"] = int(item.get("match") or 0)
            item["comment"] = item.get("comment", "")

    # resume_ats defaults
    ra = data.get("resume_ats") or {}
    if isinstance(ra, dict):
        ra.setdefault("resume_ats_score", data.get("ats_score", 0))
        ra.setdefault("blockers", [])
        ra.setdefault("warnings", [])
        ra.setdefault(
            "keyword_coverage",
            {"required_keywords": [], "present_keywords": [], "missing_keywords": []},
        )
        ra.setdefault("resume_rewrite_actions", [])
        ra.setdefault("exact_phrases_to_add", [])
        data["resume_ats"] = ra

    # learning_links: drop empty; forbid "Resource" without url
    ll = data.get("learning_links")
    if isinstance(ll, list):
        cleaned = []
        for item in ll:
            if isinstance(item, dict):
                label = (item.get("label") or "").strip()
                url = (item.get("url") or "").strip()
                why = (item.get("why") or "").strip()
                if url.startswith("http") and label and label.lower() != "resource":
                    cleaned.append(
                        {"label": label, "url": url, "why": why or "Good primer."}
                    )
        data["learning_links"] = cleaned

    # interview_qa: ensure keys exist
    iqa = data.get("interview_qa")
    if isinstance(iqa, list):
        fixed: List[Dict[str, Any]] = []
        for qa in iqa:
            if isinstance(qa, dict):
                qa["q"] = qa.get("q") or qa.get("question") or ""
                qa.setdefault("a_outline", [])
                qa.setdefault("why_it_matters", "")
                qa.setdefault("followup", "")
                fixed.append(qa)
        data["interview_qa"] = fixed

    # helper chips + counts for UI
    if "detected_keywords" not in data and isinstance(data.get("skill_table"), list):
        kw = []
        for row in data["skill_table"]:
            if isinstance(row, dict) and "skill" in row:
                kw.append(str(row["skill"]))
        data["detected_keywords"] = kw
        data["matched_count"] = sum(
            1
            for r in data["skill_table"]
            if isinstance(r, dict) and r.get("status") == "Matched"
        )
        data["missing_count"] = sum(
            1
            for r in data["skill_table"]
            if isinstance(r, dict) and r.get("status") == "Missing"
        )

    data.setdefault("report_tier", "CareerAI Deep Evaluation")
    data.setdefault("resume_missing", False)
    return data


# ------------------------------------------------------------------
# Quality gate — detect low-value outputs to trigger repair
# ------------------------------------------------------------------
def _find_quality_issues(d: Dict[str, Any]) -> List[str]:
    issues: List[str] = []

    # Fit overview
    cats = [
        (isinstance(x, dict) and (x.get("category") or ""))
        for x in (d.get("fit_overview") or [])
    ]
    if any(isinstance(c, str) and c.lower() == "overall" for c in cats):
        issues.append(
            "Replace 'Overall' categories with role-specific categories (e.g., Technical Skills, Tools & Software, Domain Experience, Collaboration & Leadership)."
        )
    if len(d.get("fit_overview") or []) < 3:
        issues.append(
            "Add at least 3 fit_overview items with match% and specific comments."
        )

    # Learning links
    for link in d.get("learning_links") or []:
        if isinstance(link, dict):
            if (link.get("label", "").lower() == "resource") or not str(
                link.get("url", "")
            ).startswith("http"):
                issues.append(
                    "Learning links must have descriptive label and real URL; avoid 'Resource'."
                )
                break
    if not d.get("learning_links"):
        issues.append("Provide at least 3 learning links with label,url,why.")

    # Keywords/ATS
    ra = d.get("resume_ats") or {}
    kc = ra.get("keyword_coverage") or {}
    if not kc.get("missing_keywords"):
        issues.append(
            "keyword_coverage.missing_keywords cannot be empty when JD lists explicit tools; extract from JD."
        )
    if not ra.get("exact_phrases_to_add"):
        issues.append("Provide exact_phrases_to_add with literal tokens from JD.")

    # Q&A and practice plan depth
    if len(d.get("interview_qa") or []) < 6:
        issues.append("Provide 6–10 interview_qa items tailored to the JD domain.")
    if len(d.get("practice_plan") or []) < 3:
        issues.append(
            "Provide a 2–3 week practice_plan with concrete tasks and outputs."
        )

    return issues


# ------------------------------------------------------------------
# Analyzer — model depends on mode, with repair pass (AI-only)
# ------------------------------------------------------------------
def analyze_jobpack(
    jd_text: str, resume_text: str, pro_mode: bool = False
) -> Dict[str, Any]:
    """
    AI-powered ATS + Resume Evaluator (AI-only; no mocks)
    - Free → FAST_MODEL (gpt-4o-mini by default)
    - Pro  → DEEP_MODEL (gpt-4o by default)
    Returns dict (never None). On failure, returns an error-shaped dict.
    """
    from openai import OpenAI

    log = logging.getLogger("jobpack_ai")

    # Fail fast if no key
    if not os.getenv("OPENAI_API_KEY"):
        log.error("Missing OPENAI_API_KEY")
        return {
            "error": "Missing OPENAI_API_KEY",
            "summary": "An error occurred during AI analysis.",
            "impact_summary": "Missing OPENAI_API_KEY",
            "ats_score": 0,
            "fit_overview": [],
            "_usage": {"model": None, "error": True},
        }

    client = OpenAI()

    clean_jd = _clean_jd(jd_text or "")
    model = DEEP_MODEL if pro_mode else FAST_MODEL

    # Resume handling
    resume_raw = (resume_text or "")
    resume_trimmed = resume_raw[:4000]
    resume_missing = not bool(resume_trimmed.strip())

    resume_hint = (
        "Resume/Profile text is EMPTY — treat this as no resume on file. "
        "Keep resume_ats_score conservative and explicitly call out the missing resume in blockers/warnings."
        if resume_missing
        else "Resume/Profile text is provided — use it heavily for ATS and fit analysis."
    )

    # Prompt with freshness
    prompt = JOBPACK_PROMPT.format(
        freshness=FRESHNESS_NOTE,
        schema=JOBPACK_JSON_SCHEMA,
        jd=clean_jd,
        resume=resume_trimmed,
        resume_hint=resume_hint,
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.3,
            max_tokens=3200,
            messages=[
                {
                    "role": "system",
                    "content": "You output only valid JSON that matches the provided schema.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            timeout=90,
        )

        raw = (resp.choices[0].message.content or "").strip()
        if not raw:
            raise ValueError("Empty response from model")

        # Parse JSON (strip any accidental code fences)
        raw_strip = raw.strip()
        if raw_strip.startswith("```"):
            raw_strip = re.sub(r"^```[a-zA-Z0-9]*\s*\n", "", raw_strip)
            if raw_strip.endswith("```"):
                raw_strip = raw_strip[:-3].strip()

        try:
            data = json.loads(raw_strip)
        except json.JSONDecodeError:
            m = re.search(r"(\{[\s\S]+\})", raw_strip)
            if not m:
                raise
            data = json.loads(m.group(1))

        # Defaults + normalization
        defaults = {
            "summary": "",
            "role_detected": "",
            "fit_overview": [],
            "ats_score": 0,
            "skill_table": [],
            "rewrite_suggestions": [],
            "next_steps": [],
            "impact_summary": "",
            "subscores": {},
            "resume_ats": {},
            "learning_links": [],
            "interview_qa": [],
            "practice_plan": [],
            "application_checklist": [],
        }
        for k, v in defaults.items():
            data.setdefault(k, v)
        data = _normalize_for_template(data)

        # Wire resume_missing for the template (used for the yellow warning card)
        if "resume_missing" not in data:
            data["resume_missing"] = resume_missing

        # Heuristic: flag obviously old JDs
        if re.search(r"(2019|2020|2021|2022|2023)\b", clean_jd) and not re.search(
            r"\b2024\b|\b2025\b", clean_jd
        ):
            ra = data.setdefault("resume_ats", {})
            notes = ra.setdefault("warnings", [])
            notes.append(
                "JD may be outdated — please verify posting date (Check latest)."
            )

        # Quality gate — if weak, ask model to repair once
        issues = _find_quality_issues(data)
        if issues:
            issues_text = "- " + "\n- ".join(issues)
            repair_prompt = REPAIR_PROMPT.format(
                freshness=FRESHNESS_NOTE,
                issues=issues_text,
                current_json=json.dumps(data, ensure_ascii=False),
            )
            repair = client.chat.completions.create(
                model=model,
                temperature=0.25,
                max_tokens=3200,
                messages=[
                    {
                        "role": "system",
                        "content": "You output only valid JSON that matches the provided schema.",
                    },
                    {"role": "user", "content": repair_prompt},
                    {
                        "role": "user",
                        "content": "Return a FULL corrected JSON object only.",
                    },
                ],
                response_format={"type": "json_object"},
                timeout=90,
            )
            raw2 = (repair.choices[0].message.content or "").strip()
            try:
                data2 = json.loads(raw2)
            except Exception:
                m2 = re.search(r"(\{[\s\S]+\})", raw2)
                data2 = json.loads(m2.group(1)) if m2 else data
            for k, v in defaults.items():
                data2.setdefault(k, v)
            data = _normalize_for_template(data2)

            # Keep resume_missing flag consistent after repair
            if "resume_missing" not in data:
                data["resume_missing"] = resume_missing

        # Usage (best-effort)
        usage = getattr(resp, "usage", None)
        data["_usage"] = {
            "model": model,
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }

        return data

    except Exception as e:
        log.exception("JobPack AI analysis failed: %s", e)
        return {
            "error": str(e),
            "summary": "An error occurred during AI analysis.",
            "impact_summary": str(e),
            "ats_score": 0,
            "fit_overview": [],
            "skill_table": [],
            "rewrite_suggestions": [],
            "next_steps": [],
            "subscores": {},
            "resume_ats": {
                "resume_ats_score": 0,
                "blockers": [],
                "warnings": [],
                "keyword_coverage": {
                    "required_keywords": [],
                    "present_keywords": [],
                    "missing_keywords": [],
                },
                "resume_rewrite_actions": [],
                "exact_phrases_to_add": [],
            },
            "learning_links": [],
            "interview_qa": [],
            "practice_plan": [],
            "application_checklist": [],
            "resume_missing": not bool((resume_text or "").strip()),
            "_usage": {"model": model, "error": True},
        }
