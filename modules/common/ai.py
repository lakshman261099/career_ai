# modules/common/ai.py
import os
import json
from dataclasses import dataclass
from typing import List, Dict, Any

# --- Env flags / defaults ---
AI_MOCK = (os.getenv("MOCK", "1").strip() == "1")  # uses MOCK=1 or 0 from .env
OPENAI_MODEL_FAST = os.getenv("OPENAI_MODEL_FAST", "gpt-4o-mini")
OPENAI_MODEL_DEEP = os.getenv("OPENAI_MODEL_DEEP", "gpt-4o")

@dataclass
class Suggestion:
    title: str
    why: str
    what: List[str]
    resume_bullets: List[str]
    stack: List[str]

# ---------------------------
# Helpers
# ---------------------------
def _coerce_skill_names(skills_list: Any) -> List[str]:
    out = []
    for s in (skills_list or []):
        if isinstance(s, dict) and (s.get("name") or "").strip():
            out.append(s["name"].strip())
        elif isinstance(s, str) and s.strip():
            out.append(s.strip())
    return out

def _mock_suggestions(role: str, industry: str, skills: List[str], pro: bool) -> List[Suggestion]:
    role = (role or "").strip() or "Portfolio"
    industry = (industry or "").strip() or "your domain"
    base_stack = (skills[:6] if skills else ["Python", "SQL", "Git"])

    def mk(title, why, outcomes, stack):
        return Suggestion(
            title=title,
            why=why,
            what=[
                "Define scope and success metrics",
                "Ship MVP in milestones with a changelog",
                "Add tests, telemetry, and docs",
                "Capture before/after impact",
            ],
            resume_bullets=outcomes,
            stack=stack,
        )

    ideas = [
        mk(
            f"{role} Project in {industry}",
            f"Directly aligns with {role} within {industry}.",
            [
                f"Designed and shipped a {industry}-focused {role} aligned to hiring signals",
                "Planned milestones and hit delivery dates",
                "Built clean, testable components with CI",
            ],
            base_stack,
        ),
        mk(
            f"{industry} KPI & Insights Dashboard",
            "Proves you can convert business questions into measurable metrics.",
            [
                "Implemented data pipeline + dashboard for KPIs",
                "Automated refresh & alerting on thresholds",
                "Drove measurable improvements in key KPI via insights",
            ],
            list({*base_stack, "Pandas", "Matplotlib", "Streamlit"}),
        ),
        mk(
            f"{role} Systems Integration Mini-Platform",
            "Highlights systems thinking and integration quality.",
            [
                "Designed modular architecture with clear contracts",
                "Instrumented telemetry; validated under load",
                "Documented trade-offs & rollback strategy",
            ],
            list({*base_stack, "Docker", "FastAPI"}),
        ),
    ]
    return ideas[:3] if pro else ideas[:1]

# ---------------------------
# Main AI entrypoint
# ---------------------------
def generate_project_suggestions(
    target_role: str, industry: str, experience_level: str, skills_list: Any, is_pro_user: bool
) -> List[Dict[str, Any]]:
    """
    Returns project suggestions for portfolio.
    Free → 1 suggestion (simple prompt)
    Pro  → 3 suggestions (advanced structured prompt)
    """
    skills = _coerce_skill_names(skills_list)

    # --- MOCK / DEV MODE ---
    if AI_MOCK:
        return [s.__dict__ for s in _mock_suggestions(target_role, industry, skills, is_pro_user)]

    # --- REAL AI CALL ---
    from openai import OpenAI
    client = OpenAI()

    if not is_pro_user:
        # 🔹 Simple prompt for Free users
        prompt = f"""
        Suggest 1 beginner-friendly project idea for a student.
        Target Role: {target_role}
        Industry: {industry}
        Experience: {experience_level}
        Skills: {", ".join(skills) or "None"}

        Keep it simple and practical. 
        Respond in JSON with: 
        title, why, what (list of steps), resume_bullets (list), stack (list).
        """
    else:
        # 🔹 Advanced high-level engineered prompt for Pro users
        prompt = f"""
        You are a career coach and portfolio strategist. 
        Generate 3 highly valuable, resume-worthy project ideas for a student. 
        Make each project:
        - Directly aligned to the Target Role and Industry
        - Realistic to build in 4–8 weeks
        - Resume-friendly with measurable outcomes
        - Showcasing technical + problem-solving depth

        Context:
        Role: {target_role}
        Industry: {industry}
        Experience: {experience_level}
        Skills: {", ".join(skills) or "None"}

        For each project, output JSON with:
        - title: a strong, professional project title
        - why: why this project matters for hiring signals
        - what: 4–6 concrete steps to build the project
        - resume_bullets: 3 concise resume points (achievement-oriented, measurable)
        - stack: 5–8 relevant tools/technologies

        Output must be a JSON list with 3 objects.
        """

    resp = client.chat.completions.create(
        model=OPENAI_MODEL_FAST,
        messages=[
            {"role": "system", "content": "You are an expert career AI that designs portfolio projects."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=700,
        temperature=0.7,
    )

    raw = resp.choices[0].message.content.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return [data]
    except Exception:
        return [s.__dict__ for s in _mock_suggestions(target_role, industry, skills, is_pro_user)]
