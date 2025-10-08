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
    Full AI-powered ATS + Resume Evaluator
    - pro_mode=True → uses GPT-4o (deep)
    - pro_mode=False → uses GPT-4o-mini (fast, cheaper)
    """
    from openai import OpenAI
    client = OpenAI()

    clean_jd = _clean_jd(jd_text)
    model = OPENAI_MODEL_DEEP if pro_mode else OPENAI_MODEL_FAST

    prompt = JOBPACK_PROMPT.format(
        jd=clean_jd,
        resume=(resume_text or "")[:4000],
        schema=JOBPACK_JSON_SCHEMA,
    )

    completion = client.chat.completions.create(
        model=model,
        temperature=0.45,
        max_tokens=2200,
        messages=[
            {"role": "system", "content": "You are CareerAI. Output only JSON per schema."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
    )

    raw = completion.choices[0].message.content.strip()
    data = json.loads(raw)
    return data
