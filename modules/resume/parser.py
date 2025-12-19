# modules/resume/parser.py

import json
import logging
import os
from typing import Any, Dict, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

PROMPT_TEMPLATE = """
You are a resume parser for a student/new-grad career platform called CareerAI.

Your job:
- Read the resume text.
- Extract as many concrete skills as possible.
- Return STRICT JSON ONLY in the exact structure given.
- NO commentary, NO markdown, NO extra keys.

When extracting skills, look for:

- Programming & scripting languages
  (Python, Java, JavaScript, TypeScript, C++, C#, Go, R, PHP, etc.)
- Sub-libraries and ML/data libraries
  (Pandas, NumPy, Scikit-learn, TensorFlow, Keras, PyTorch, XGBoost, etc.)
- BI / Visualization tools
  (Power BI, Tableau, Looker, Looker Studio, Google Data Studio, Superset, etc.)
- Databases
  (MySQL, PostgreSQL, SQL Server, MongoDB, Snowflake, BigQuery, Redshift, etc.)
- Cloud platforms
  (AWS, Azure, GCP, Google Cloud, Lambda, EC2, S3, App Engine, etc.)
- Data engineering tools
  (Airflow, dbt, Azure Data Factory, Kafka, Spark, Hadoop, etc.)
- General tools
  (Git, GitHub, GitLab, Docker, Kubernetes, Excel, Google Sheets, Jira, Postman, etc.)
- Soft skills
  (communication, teamwork, leadership, presentation, problem-solving, etc.)

Resume:
---
{resume_text}
---

Return JSON with this exact structure and keys:

{{
  "full_name": "string or null",
  "headline": "string or null",
  "summary": "string or null",
  "location": "string or null",
  "phone": "string or null",
  "links": {{
    "email": "string or null",
    "website": "string or null",
    "linkedin": "string or null",
    "github": "string or null"
  }},
  "skills": [
    {{
      "name": "string",
      "level": 1
    }}
  ],
  "education": [
    {{
      "degree": "string",
      "school": "string",
      "year": "string"
    }}
  ],
  "certifications": [
    {{
      "name": "string",
      "year": "string"
    }}
  ],
  "experience": [
    {{
      "role": "string",
      "company": "string",
      "start": "string",
      "end": "string",
      "bullets": ["string"]
    }}
  ]
}}

Rules:
- If you don't know a field, set it to null or [] as appropriate.
- "skills" MUST be a flat array (NOT grouped by category).
- Each item in "skills" MUST have "name" and "level".
- skills.level is an integer from 1 to 5, your rough guess of proficiency.
- Use short, clean text, no emojis.
"""


def parse_resume_to_profile(resume_text: str) -> Optional[Dict[str, Any]]:
    """
    Send resume_text to the OpenAI API and parse into a dict
    aligned to UserProfile + flat skills list.

    The categorization into Programming / Databases / Tools etc.
    is handled in backend Python, not by the model.

    Returns dict or None on failure.
    """
    if not resume_text or not resume_text.strip():
        return None

    prompt = PROMPT_TEMPLATE.format(resume_text=resume_text[:12000])  # safety truncation

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You output ONLY valid JSON. No prose."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1200,
        )

        raw_text = resp.choices[0].message.content or ""
        raw = raw_text.strip()

        # Strip ```json ... ``` if the model wraps it
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()

        data = json.loads(raw)

        if not isinstance(data, dict):
            logger.warning("parse_resume_to_profile: JSON is not an object")
            return None

        return data

    except Exception:
        logger.exception("parse_resume_to_profile: OpenAI call failed")
        return None
