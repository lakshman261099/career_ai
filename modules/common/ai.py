# modules/common/ai.py
import os
import json
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple

OPENAI_MODEL_FAST = os.getenv("OPENAI_MODEL_FAST", "gpt-4o-mini")
OPENAI_MODEL_DEEP = os.getenv("OPENAI_MODEL_DEEP", "gpt-4o")

def _is_mock() -> bool:
    return (os.getenv("MOCK", "1").strip() == "1")

@dataclass
class Suggestion:
    title: str
    why: str
    what: List[str]
    resume_bullets: List[str]
    stack: List[str]

# ---------- helpers ----------
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
                "Drove measurable improvements in a key KPI via insights",
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

# ---------- main ----------
def generate_project_suggestions(
    target_role: str,
    industry: str,
    experience_level: str,
    skills_list: Any,
    pro_mode: bool,
    return_source: bool = False,
) -> List[Dict[str, Any]] | Tuple[List[Dict[str, Any]], bool]:
    """
    Returns suggestions.
    Free mode  -> 1 suggestion, simple prompt
    Pro mode   -> 3 suggestions, advanced prompt
    If return_source=True => (ideas, used_live_ai: bool)
    """
    skills = _coerce_skill_names(skills_list)
    used_live_ai = False

    # --- MOCK path ---
    if _is_mock():
        ideas = [s.__dict__ for s in _mock_suggestions(target_role, industry, skills, pro_mode)]
        return (ideas, used_live_ai) if return_source else ideas

    # --- REAL AI path ---
    from openai import OpenAI
    client = OpenAI()

    if not pro_mode:
        prompt = f"""
        You are a career coach. Suggest ONE simple but meaningful project idea for a student:
        - Target Role: {target_role}
        - Industry: {industry}
        - Experience: {experience_level}
        - Skills: {", ".join(skills) or "None"}

        Keep it beginner-friendly and practical.

        Return JSON object with key "ideas" whose value is an array with EXACTLY 1 item having:
        title, why, what (4 steps), resume_bullets (2), stack (about 5).
        """
    else:
        prompt = f"""
        You are an expert career and hiring consultant.
        Generate THREE advanced, resume-ready project suggestions tailored for:
        - Target Role: {target_role}
        - Industry: {industry}
        - Experience Level: {experience_level}
        - Skills available: {", ".join(skills) or "None"}

        For each project, ensure it is:
        - Aligned to the hiring signals for the role in this industry
        - Realistic for 4–8 weeks with clear milestones
        - Focused on measurable business or technical outcomes

        Each project must include:
        1) title — role-relevant and professional
        2) why — connect to hiring signals (metrics/KPIs/real scenarios)
        3) what — 4–6 concrete build steps
        4) resume_bullets — 3 STAR-style bullets with quantifiable impact
        5) stack — 5–8 tools/technologies aligned to the role
        6) differentiation — how it stands out vs typical student projects

        Output STRICTLY as a JSON object with key "ideas" whose value is an array of EXACTLY 3 items.
        """

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_FAST if not pro_mode else OPENAI_MODEL_DEEP,
            messages=[
                {"role": "system", "content": "You output only valid JSON and nothing else."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6 if pro_mode else 0.7,
            max_tokens=1200 if pro_mode else 700,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)

        ideas_in = []
        if isinstance(data, dict) and isinstance(data.get("ideas"), list):
            ideas_in = data["ideas"]
        elif isinstance(data, list):
            ideas_in = data
        else:
            for k, v in (data.items() if isinstance(data, dict) else []):
                if isinstance(v, list):
                    ideas_in = v
                    break
            if not ideas_in:
                raise ValueError("No ideas list found in model output")

        limit = 3 if pro_mode else 1
        out: List[Dict[str, Any]] = []
        for d in ideas_in[:limit]:
            out.append({
                "title": (d.get("title") or "").strip()[:200],
                "why": (d.get("why") or "").strip()[:500],
                "what": [str(x).strip() for x in (d.get("what") or [])][:6],
                "resume_bullets": [str(x).strip() for x in (d.get("resume_bullets") or [])][:3],
                "stack": [str(x).strip() for x in (d.get("stack") or [])][:8],
                # optional extra for pro
                "differentiation": (d.get("differentiation") or "").strip()[:400],
            })
        used_live_ai = True
        return (out, used_live_ai) if return_source else out

    except Exception:
        ideas = [s.__dict__ for s in _mock_suggestions(target_role, industry, skills, pro_mode)]
        return (ideas, used_live_ai) if return_source else ideas
