# modules/common/ai.py
import os
import json
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple

# Models from env
OPENAI_MODEL_FAST = os.getenv("OPENAI_MODEL_FAST", "gpt-4o-mini")
OPENAI_MODEL_DEEP = os.getenv("OPENAI_MODEL_DEEP", "gpt-4o")

def _is_mock() -> bool:
    # Dynamic read every call so changing .env takes effect without restart (if app re-reads env).
    return (os.getenv("MOCK", "1").strip() == "1")

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
    target_role: str,
    industry: str,
    experience_level: str,
    skills_list: Any,
    is_pro_user: bool,
    return_source: bool = False,
) -> List[Dict[str, Any]] | Tuple[List[Dict[str, Any]], bool]:
    """
    Returns project suggestions for portfolio.
    Free → 1 suggestion (simple prompt)
    Pro  → 3 suggestions (advanced structured prompt)

    If return_source=True, returns (ideas, used_live_ai: bool)
    """
    skills = _coerce_skill_names(skills_list)
    used_live_ai = False

    # --- MOCK / DEV MODE ---
    if _is_mock():
        ideas = [s.__dict__ for s in _mock_suggestions(target_role, industry, skills, is_pro_user)]
        return (ideas, used_live_ai) if return_source else ideas

    # --- REAL AI CALL ---
    from openai import OpenAI
    client = OpenAI()

    if not is_pro_user:
        # Simple prompt for Free users
        prompt = f"""
        Suggest 1 beginner-friendly project idea for a student.
        Target Role: {target_role}
        Industry: {industry}
        Experience: {experience_level}
        Skills: {", ".join(skills) or "None"}

        Keep it simple and practical.
        Return a JSON array with exactly 1 object, keys:
        title, why, what (list of 4 steps), resume_bullets (list of 3), stack (list of ~5).
        """
    else:
        # Advanced high-level engineered prompt for Pro users
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

        Output strictly as a JSON array with 3 objects. Each object must have:
        - title: strong professional title
        - why: why this project matters for hiring signals
        - what: 4–6 concrete build steps
        - resume_bullets: 3 concise, measurable bullets
        - stack: 5–8 relevant tools/technologies
        """

    try:
        # Ask the model to ensure valid JSON
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_FAST,
            messages=[
                {"role": "system", "content": "You are an expert career AI that outputs valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6 if is_pro_user else 0.7,
            max_tokens=900 if is_pro_user else 700,
            response_format={"type": "json_object"},  # forces a JSON object
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Expect a JSON object; support either {"ideas":[...]} or direct list fallback
        data = json.loads(raw)
        if isinstance(data, dict) and "ideas" in data and isinstance(data["ideas"], list):
            ideas = data["ideas"]
        elif isinstance(data, list):
            ideas = data
        else:
            # Try to find a top-level array key by convention
            for k, v in (data.items() if isinstance(data, dict) else []):
                if isinstance(v, list):
                    ideas = v
                    break
            else:
                raise ValueError("No list found in JSON output")

        # Normalize & trim
        out: List[Dict[str, Any]] = []
        limit = 3 if is_pro_user else 1
        for d in ideas[:limit]:
            out.append({
                "title": (d.get("title") or "").strip()[:200],
                "why": (d.get("why") or "").strip()[:400],
                "what": [str(x).strip() for x in (d.get("what") or [])][:6],
                "resume_bullets": [str(x).strip() for x in (d.get("resume_bullets") or [])][:3],
                "stack": [str(x).strip() for x in (d.get("stack") or [])][:8],
            })
        used_live_ai = True
        return (out, used_live_ai) if return_source else out

    except Exception:
        # Fallback to mock if anything goes wrong
        ideas = [s.__dict__ for s in _mock_suggestions(target_role, industry, skills, is_pro_user)]
        return (ideas, used_live_ai) if return_source else ideas
