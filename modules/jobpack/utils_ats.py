# modules/jobpack/utils_ats.py
import re
import json
from typing import Dict, Any, List

# Prefer configured model names; fall back to literals
try:
    from modules.common.ai import OPENAI_MODEL_DEEP, OPENAI_MODEL_FAST
    DEEP_MODEL = OPENAI_MODEL_DEEP or "gpt-4o"
    FAST_MODEL = OPENAI_MODEL_FAST or "gpt-4o-mini"
except Exception:
    DEEP_MODEL = "gpt-4o"
    FAST_MODEL = "gpt-4o-mini"


# ------------------------------------------------------------------
# Clean JD
# ------------------------------------------------------------------
def _clean_jd(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"(Responsibilities|Requirements|Skills|Key Skills|Experience|Qualifications)[:\-]?",
        r"\n\n### \1\n",
        text,
        flags=re.I,
    )
    return text[:4000]


# ------------------------------------------------------------------
# JSON Schema (enforced structure)
# ------------------------------------------------------------------
JOBPACK_JSON_SCHEMA = r"""
{
  "type": "object",
  "required": [
    "summary","role_detected","fit_overview","ats_score",
    "skill_table","rewrite_suggestions","next_steps",
    "impact_summary","subscores","resume_ats",
    "learning_links","interview_qa","practice_plan","application_checklist"
  ],
  "properties": {
    "summary": {"type":"string"},
    "role_detected": {"type":"string"},
    "fit_overview": {"type":"array","items":{"type":"object"}},
    "ats_score":{"type":"integer"},
    "skill_table":{"type":"array","items":{"type":"object"}},
    "rewrite_suggestions":{"type":"array","items":{"type":"string"}},
    "next_steps":{"type":"array","items":{"type":"string"}},
    "impact_summary":{"type":"string"},
    "subscores":{"type":"object"},
    "resume_ats":{"type":"object"},
    "learning_links":{"type":"array","items":{"type":"object"}},
    "interview_qa":{"type":"array","items":{"type":"object"}},
    "practice_plan":{"type":"array","items":{"type":"object"}},
    "application_checklist":{"type":"array","items":{"type":"string"}}
  }
}
"""


# ------------------------------------------------------------------
# Prompt template (we will .format() this — do NOT f-string it)
# ------------------------------------------------------------------
JOBPACK_PROMPT = """\
You are **CareerAI**, a professional-grade career analysis system.

Analyze the provided **Job Description (JD)** and **Resume**.
Generate a comprehensive JSON report — everything must be inferred by AI, not templated.

### 🎯 Objectives
1) Identify candidate’s fit and likely role
2) Compute realistic ATS score (0–100)
3) Matched vs missing keywords
4) Targeted resume rewrite actions (ATS)
5) Resume ATS Audit (score, blockers, warnings, missing keywords, rewrite actions)
6) Learning resources for gaps (specific, relevant)
7) Interview Q&A (6–10) grounded in JD & resume
8) Practice plan (2–3 weeks)
9) Application checklist (6–10 items)
10) Fit overview + subscores + 1–2 line impact summary

### 🧩 OUTPUT FORMAT
Return **only valid JSON** that matches this schema:
{schema}

### 📄 INPUTS
**Job Description (cleaned):**
{jd}

**Resume (or Profile Portal):**
{resume}

Output must be pure JSON — no markdown, no preamble, no text outside braces.
"""


# ------------------------------------------------------------------
# Light normalization so the template never breaks if model uses synonyms
# ------------------------------------------------------------------
def _normalize_for_template(data: Dict[str, Any]) -> Dict[str, Any]:
    # fit_overview items: ensure "category" + "match"
    fo = data.get("fit_overview")
    if isinstance(fo, list):
        for item in fo:
            if not isinstance(item, dict):
                continue
            if "category" not in item:
                if "name" in item:
                    item["category"] = item["name"]
                elif "area" in item:
                    item["category"] = item["area"]
                else:
                    item["category"] = "Overall"
            if "match" not in item:
                if "score" in item:
                    try: item["match"] = int(item["score"])
                    except: item["match"] = 0
                elif "pct" in item:
                    try: item["match"] = int(item["pct"])
                    except: item["match"] = 0
                else:
                    item["match"] = 0
            item.setdefault("comment", "")

    # resume_ats object: expected keys
    ra = data.get("resume_ats") or {}
    if isinstance(ra, dict):
        ra.setdefault("resume_ats_score", data.get("ats_score", 0))
        ra.setdefault("blockers", [])
        ra.setdefault("warnings", [])
        ra.setdefault("keyword_coverage", {
            "required_keywords": [],
            "present_keywords": [],
            "missing_keywords": []
        })
        ra.setdefault("resume_rewrite_actions", [])
        data["resume_ats"] = ra

    # learning_links: coerce strings into {label,url}
    ll = data.get("learning_links")
    if isinstance(ll, list):
        coerced = []
        for item in ll:
            if isinstance(item, dict):
                label = item.get("label") or item.get("title") or item.get("name") or "Resource"
                url = item.get("url") or item.get("link") or ""
                coerced.append({"label": label, "url": url})
            elif isinstance(item, str):
                s = item.strip()
                if s:
                    coerced.append({"label": "Resource", "url": s if s.startswith("http") else ""})
        data["learning_links"] = coerced

    # interview_qa: ensure 'q'
    iqa = data.get("interview_qa")
    if isinstance(iqa, list):
        fixed: List[Dict[str, Any]] = []
        for qa in iqa:
            if isinstance(qa, dict):
                if "q" not in qa and "question" in qa:
                    qa["q"] = qa.get("question")
                fixed.append(qa)
        data["interview_qa"] = fixed

    # simple detected keywords counts if not present (for header chip)
    if "detected_keywords" not in data and isinstance(data.get("skill_table"), list):
        # attempt to derive a set from any 'skill' fields
        kw = []
        for row in data["skill_table"]:
            if isinstance(row, dict) and "skill" in row:
                kw.append(str(row["skill"]))
        data["detected_keywords"] = kw
        data["matched_count"] = 0
        data["missing_count"] = 0

    # report tier + resume_missing flags for UI
    data.setdefault("report_tier", "CareerAI Deep Evaluation")
    data.setdefault("resume_missing", False)

    return data


# ------------------------------------------------------------------
# Analyzer — always AI, model depends on mode
# ------------------------------------------------------------------
def analyze_jobpack(jd_text: str, resume_text: str, pro_mode: bool = False) -> Dict[str, Any]:
    """
    AI-powered ATS + Resume Evaluator
    - Free → gpt-4o-mini
    - Pro  → gpt-4o
    Returns a dict (never None). On failure, returns a small dict with 'error'.
    """
    from openai import OpenAI
    import logging

    log = logging.getLogger("jobpack_ai")
    client = OpenAI()

    clean_jd = _clean_jd(jd_text or "")
    model = DEEP_MODEL if pro_mode else FAST_MODEL

    # Inject schema, JD, and resume into the master prompt
    prompt = JOBPACK_PROMPT.format(
        schema=JOBPACK_JSON_SCHEMA,
        jd=clean_jd,
        resume=(resume_text or "")[:4000],
    )

    try:
        print(f"🔎 JobPack: using model={model} pro_mode={pro_mode}")

        resp = client.chat.completions.create(
            model=model,
            temperature=0.4,
            max_tokens=3000,
            messages=[
                {"role": "system", "content": "You output only valid JSON that matches the provided schema."},
                {"role": "user", "content": prompt},
            ],
            # Force JSON-mode so the model returns a raw JSON object (no ``` fences)
            response_format={"type": "json_object"},
            timeout=90,
        )

        raw = (resp.choices[0].message.content or "").strip()
        if not raw:
            raise ValueError("Empty response from model")

        # Debug log (shows first ~1.5k chars)
        print("\n\n===== RAW GPT OUTPUT START =====")
        print(raw[:1500])
        print("===== RAW GPT OUTPUT END =====\n\n")

        # Strip markdown code fences if present (belt & suspenders)
        raw_strip = raw.strip()
        if raw_strip.startswith("```"):
            raw_strip = re.sub(r"^```[a-zA-Z0-9]*\s*\n", "", raw_strip)
            if raw_strip.endswith("```"):
                raw_strip = raw_strip[:-3].strip()

        # Parse JSON safely
        try:
            data = json.loads(raw_strip)
        except json.JSONDecodeError:
            m = re.search(r"(\{[\s\S]+\})", raw_strip)
            if not m:
                raise
            try:
                data = json.loads(m.group(1))
            except Exception as e:
                print("⚠️ JSON recovery failed:", e)
                raise

        # Ensure minimal shape for template resilience
        defaults = {
            "summary": "", "role_detected": "", "fit_overview": [],
            "ats_score": 0, "skill_table": [], "rewrite_suggestions": [],
            "next_steps": [], "impact_summary": "", "subscores": {},
            "resume_ats": {}, "learning_links": [], "interview_qa": [],
            "practice_plan": [], "application_checklist": []
        }
        for k, v in defaults.items():
            data.setdefault(k, v)

        # Normalize common shape mismatches
        data = _normalize_for_template(data)

        # Usage tracking (best-effort)
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
            "_usage": {"model": model, "error": True},
        }
