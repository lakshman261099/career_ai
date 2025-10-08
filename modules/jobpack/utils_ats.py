# modules/jobpack/utils_ats.py
import re
import json
from typing import Dict, Any
from modules.common.ai import OPENAI_MODEL_DEEP, OPENAI_MODEL_FAST  # define these in your config

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
# GPT Prompt
# ------------------------------------------------------------------

JOBPACK_PROMPT = """\
You are **CareerAI**, a professional-grade career analysis system.

Analyze the provided **Job Description (JD)** and **Resume**.
Generate a comprehensive JSON report — everything must be inferred by AI, not templated.

---

### 🎯 Objectives
1. Identify candidate’s fit for the job and likely role.
2. Compute a realistic ATS score (0–100).
3. Detect matched and missing keywords.
4. Suggest targeted rewrite actions for ATS compatibility.
5. Provide a complete “Resume ATS Audit” (score, blockers, warnings, missing keywords, rewrite actions).
6. Suggest *learning resources* for missing skills (real, specific, free or open tutorials).
7. Generate *interview Q&A* (6–10 items) derived from both JD and Resume context.
8. Create a *practice plan* (2–3 weeks) to close skill gaps.
9. Provide an *application checklist* (6–10 actionable items).
10. Provide *fit overview*, *subscores* (keyword relevance, quantifiable impact, clarity, tone), and a 1–2 line *impact summary*.

---

### 🧩 OUTPUT FORMAT
Return **only valid JSON** that matches this schema:
{schema}

---

### 📄 INPUTS
**Job Description (cleaned):**
{jd}

**Resume (or Profile Portal):**
{resume}

---

Respond only with valid JSON — no explanations, markdown, or commentary.
"""

# ------------------------------------------------------------------
# Analyzer — always AI, model depends on mode
# ------------------------------------------------------------------
def analyze_jobpack(jd_text: str, resume_text: str, pro_mode: bool = False) -> Dict[str, Any]:
    """
    AI-powered ATS + Resume Evaluator
    - Free → gpt-4o-mini
    - Pro  → gpt-4o
    """
    from openai import OpenAI
    import logging, json, re

    log = logging.getLogger("jobpack_ai")
    client = OpenAI()

    clean_jd = _clean_job_description(jd_text or "")
    model = "gpt-4o" if pro_mode else "gpt-4o-mini"

    prompt = f"""
You are CareerAI — a professional AI career assistant.
Analyze the following Job Description (JD) and Resume text.

Return a single valid JSON object strictly matching this schema:
{JOBPACK_JSON_SCHEMA}

Rules:
- Output ONLY JSON (no markdown, no explanations, no text outside {{...}}).
- Include realistic numeric scores (0–100), helpful rewrite suggestions, and concrete next steps.
- If resume text is empty, still infer likely missing skills.

Inputs:
JD:
{clean_jd}

Resume:
{(resume_text or "")[:4000]}
"""

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.4,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": "You output only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            timeout=60,
        )

        raw = (resp.choices[0].message.content or "").strip()
        if not raw:
            raise ValueError("Empty response from model")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to recover if GPT returned formatted JSON
            m = re.search(r"(\{[\s\S]+\})", raw)
            if not m:
                raise
            data = json.loads(m.group(1))

        # Default safety net so template never breaks
        defaults = {
            "summary": "", "role_detected": "", "fit_overview": [],
            "ats_score": 0, "skill_table": [], "rewrite_suggestions": [],
            "next_steps": [], "impact_summary": "", "subscores": {},
            "resume_ats": {}, "learning_links": [], "interview_qa": [],
            "practice_plan": [], "application_checklist": []
        }
        for k, v in defaults.items():
            data.setdefault(k, v)

        # Add usage info for debugging/logs
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
            "summary": "An error occurred during AI analysis.",
            "impact_summary": str(e),
            "ats_score": 0,
            "fit_overview": [],
            "_usage": {"model": model, "error": True},
        }
