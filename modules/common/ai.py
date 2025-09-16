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
        print("[SkillMapper] RAW OpenAI output:", raw[:400], flush=True)
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

# ---------- SkillMapper (Free & Pro) ----------

# JSON schema as a string (used for prompting and light sanity checks)
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
Your job: read a student's structured profile (Pro mode) and return ONLY valid JSON that matches the provided JSON Schema.
Do not include markdown, commentary, or code fences—return pure JSON.

Context & Rules:
- Mode: "pro".
- The profile object is authoritative: {profile_json}
- If resume_text is provided, prefer profile fields, then use resume_text to fill gaps.
- Produce three DISTINCT, specialized roles (not generic). For game design, go deep like:
  "Level Designer", "Technical Designer (Blueprints)", "Character Artist (Stylized)", etc.
- Match depth to the domain in profile. Avoid vague titles.
- For each role, give a candid match_score (0–100) based on skills/experiences.
- "hiring_now" is MODEL ESTIMATES (directional), not scraped. Provide brief regional or sector notes if obviously relevant from the profile or resume.
- Gaps must be specific, prioritized, and time-bounded with weeks.
- Micro-projects must be resume-ready (portfolio friendly) with concrete deliverables.
- Keep language concise and recruiter-friendly.

Inputs:
- profile_json: JSON with identity, links, skills (with levels 1–5), education, experience, projects, certifications, location.
- resume_text: optional plain text.

Output:
- Must validate exactly against the JSON Schema below.
- No extra keys, no nulls, no markdown, no prose outside JSON.

JSON Schema:
{json_schema}

Respond with JSON only.
"""

FREE_SKILLMAPPER_PROMPT = """\
You are SkillMapper, an expert career coach for Free users in a Flask app.
Return ONLY valid JSON matching the provided JSON Schema—no markdown or commentary.

Context & Rules:
- Mode: "free".
- Input is free_text_skills (student pasted skills/interests). No scraping.
- Infer a plausible target domain and produce three DISTINCT, specialized roles within that domain.
- Keep outputs actionable but concise; assume beginner to entry level.
- "hiring_now" is MODEL ESTIMATES (directional), not scraped.
- Micro-projects should be simple but portfolio-worthy.

Inputs:
- free_text_skills: {free_text_skills}

Output:
- Must validate exactly against the JSON Schema below.

JSON Schema:
{json_schema}

Respond with JSON only.
"""

def _digest_inputs_for_meta(obj: Any) -> str:
    try:
        import hashlib
        s = json.dumps(obj, sort_keys=True)[:5000]
        return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()
    except Exception:
        return "sha256:na"

def _mock_skillmap(pro_mode: bool) -> Dict[str, Any]:
    # Deterministic, pretty mock (safe for both Free and Pro previews)
    data = {
      "mode": "pro" if pro_mode else "free",
      "top_roles": [
        {
          "title": "Level Designer (UE5)",
          "seniority_target": "entry",
          "match_score": 82,
          "why_fit": "Your UE5 prototypes, greyboxing, and gameplay scripting align with encounter and pacing design.",
          "primary_skill_clusters": [
            {"name":"Game Design","skills":["greyboxing","encounter pacing","combat loops"]},
            {"name":"Engines","skills":["Unreal Engine 5","Blueprints","World Partition"]}
          ],
          "gaps":[
            {"skill":"Telemetry for A/B tests","priority":1,"how_to_learn":"Instrument events in a UE5 prototype and run a 20-player test.","time_estimate_weeks":3},
            {"skill":"Accessibility heuristics","priority":2,"how_to_learn":"Apply Xbox & WCAG heuristics to a level; run 5-player audit.","time_estimate_weeks":2}
          ],
          "micro_projects":[
            {"title":"Stealth Level Greybox","outcome":"Show mastery of line-of-sight and patrol design.","deliverables":["Video walkthrough","Design doc (2 pages)"],"difficulty":"medium"},
            {"title":"Combat Arena Telemetry","outcome":"Demonstrate data-informed iteration.","deliverables":["CSV events","before/after heatmap"],"difficulty":"hard"}
          ],
          "example_titles":["Level Designer I","Junior Level Designer","Design Intern (Levels)"]
        },
        {
          "title":"Technical Designer (Blueprints)",
          "seniority_target":"entry",
          "match_score":78,
          "why_fit":"You bridge design and scripting with Blueprint-based gameplay systems.",
          "primary_skill_clusters":[
            {"name":"Scripting","skills":["Blueprints","State machines","Behavior Trees"]},
            {"name":"Design Systems","skills":["combat tuning","ability systems","economy"]}
          ],
          "gaps":[
            {"skill":"C++ in UE","priority":1,"how_to_learn":"Port two Blueprints to C++ with UFUNCTION/UPROPERTY.","time_estimate_weeks":4},
            {"skill":"Network replication","priority":2,"how_to_learn":"Make abilities replicate in a 2-player demo.","time_estimate_weeks":3}
          ],
          "micro_projects":[
            {"title":"Ability System Prototype","outcome":"Show modular abilities with cooldowns.","deliverables":["repo","gif demo"],"difficulty":"medium"},
            {"title":"Behavior Tree AI","outcome":"Patrol/chase/attack enemy AI.","deliverables":["BT asset","tuning sheet"],"difficulty":"easy"}
          ],
          "example_titles":["Associate Technical Designer","Gameplay Scripter (UE)","Junior Designer (Systems)"]
        },
        {
          "title":"Character Artist (Stylized)",
          "seniority_target":"intern",
          "match_score":69,
          "why_fit":"Strong sculpting and retopo fundamentals fit stylized pipelines.",
          "primary_skill_clusters":[
            {"name":"Art","skills":["sculpting","retopo","UVs","baking","PBR"]},
            {"name":"Tools","skills":["ZBrush","Substance Painter","Marmoset Toolbag"]}
          ],
          "gaps":[
            {"skill":"Rig-friendly topology","priority":1,"how_to_learn":"Study edge flow for deformation; re-topo a test mesh.","time_estimate_weeks":2},
            {"skill":"Hair cards","priority":2,"how_to_learn":"Create stylized hair with cards in UE5.","time_estimate_weeks":2}
          ],
          "micro_projects":[
            {"title":"Stylized NPC","outcome":"Game-ready model with textures and LODs.","deliverables":["turntable","Marmoset viewer"],"difficulty":"medium"},
            {"title":"Rig Test","outcome":"Validate deformation with walk cycle.","deliverables":["fbx","short clip"],"difficulty":"hard"}
          ],
          "example_titles":["Character Art Intern","Junior Character Artist","3D Artist (Stylized)"]
        }
      ],
      "hiring_now":[
        {"role_group":"Game Design & Tech","roles":["Level Design","Technical Design"],"share_estimate_pct":22.0,"est_count_estimate_global":120000,"note":"Steady demand in AA/AAA and mobile/live ops."},
        {"role_group":"Art & Content","roles":["Character Artist","Environment Artist"],"share_estimate_pct":18.0,"est_count_estimate_global":90000,"note":"Stylized art demand strong in mobile + indie."},
        {"role_group":"Engineering-adjacent","roles":["Gameplay Scripter","Tools Designer"],"share_estimate_pct":12.0,"est_count_estimate_global":60000,"note":"Blueprint-heavy teams value hybrid profiles."}
      ],
      "call_to_action":"Pick one role focus and ship one micro-project per week; add outcomes to your Portfolio.",
      "meta":{"generated_at_utc":"2025-09-05T12:00:00Z","inputs_digest":"sha256:example"}
    }
    return data

def build_skillmapper_messages(pro_mode: bool, inputs: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Returns chat messages for OpenAI based on mode.
    inputs:
      - if pro_mode: {"profile_json": {...}, "resume_text": "..."}
      - if free:     {"free_text_skills": "..."}
    """
    if pro_mode:
        prompt = PRO_SKILLMAPPER_PROMPT.format(
            profile_json=json.dumps(inputs.get("profile_json") or {}, ensure_ascii=False),
            json_schema=SKILLMAPPER_JSON_SCHEMA,
        )
        # append resume_text separately to keep prompt length stable
        resume_text = (inputs.get("resume_text") or "").strip()
        if resume_text:
            prompt += f"\n\nAdditional resume_text:\n{resume_text}"
    else:
        prompt = FREE_SKILLMAPPER_PROMPT.format(
            free_text_skills=(inputs.get("free_text_skills") or "").strip(),
            json_schema=SKILLMAPPER_JSON_SCHEMA,
        )
    return [
        {"role": "system", "content": "You output only valid JSON and nothing else."},
        {"role": "user", "content": prompt},
    ]

def _light_validate_skillmap(data: Any) -> Dict[str, Any]:
    """
    Lightweight sanity checks to avoid hard dependency on jsonschema.
    On failure, raises ValueError.
    """
    if not isinstance(data, dict):
        raise ValueError("SkillMap must be a JSON object")
    for k in ["mode", "top_roles", "hiring_now", "call_to_action", "meta"]:
        if k not in data:
            raise ValueError(f"Missing key: {k}")
    if data["mode"] not in ("free", "pro"):
        raise ValueError("Invalid mode")
    if not (isinstance(data["top_roles"], list) and len(data["top_roles"]) == 3):
        raise ValueError("top_roles must be array of exactly 3")
    for role in data["top_roles"]:
        if not isinstance(role, dict) or "title" not in role or "match_score" not in role:
            raise ValueError("Invalid role item")
    if not isinstance(data["hiring_now"], list) or len(data["hiring_now"]) < 3:
        raise ValueError("hiring_now must have at least 3 items")
    return data

def generate_skillmap(
    pro_mode: bool,
    *,
    profile_json: Dict[str, Any] | None = None,
    resume_text: str | None = None,
    free_text_skills: str | None = None,
    return_source: bool = False,
) -> Dict[str, Any] | Tuple[Dict[str, Any], bool]:
    used_live_ai = False

    if _is_mock():
        data = _mock_skillmap(pro_mode)
        return (data, used_live_ai) if return_source else data

    try:
        from openai import OpenAI
        client = OpenAI()

        inputs = {}
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
        if isinstance(data.get("meta"), dict) and not data["meta"].get("inputs_digest"):
            data["meta"]["inputs_digest"] = _digest_inputs_for_meta(inputs)

        used_live_ai = True
        return (data, used_live_ai) if return_source else data

    except Exception as e:
        import sys, traceback
        print("=== SkillMapper ERROR ===", file=sys.stderr)
        print(str(e), file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

        data = _mock_skillmap(pro_mode)
        return (data, used_live_ai) if return_source else data

