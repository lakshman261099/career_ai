import os, random
from typing import List, Dict

MOCK = os.getenv("MOCK", "1") == "1"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def mock_fetch(role: str, location: str) -> List[Dict]:
    # existing simple mock list; adjust as needed
    seeds = [
        {"title": f"{role} Intern", "company": "Acme Labs", "source": "MockBoard", "link": "https://example.com/role1"},
        {"title": f"Junior {role}", "company": "Nova Co", "source": "MockBoard", "link": "https://example.com/role2"},
        {"title": f"{role} Summer", "company": "Luma", "source": "MockBoard", "link": "https://example.com/role3"},
    ]
    for j in seeds:
        j["match_score"] = random.randint(62, 92)
        j["missing_skills"] = random.sample(["SQL","Python","Tableau","React","APIs","Docker","Figma"], k=3)
    return seeds

def compute_learning_links(missing_skills: List[str]) -> List[Dict]:
    links = []
    for s in missing_skills:
        q = s.lower().replace(" ", "-")
        links.append({"skill": s, "link": f"https://www.freecodecamp.org/learn/{q}"})
    return links

def deep_enrich_jobs(jobs: List[Dict], role: str) -> List[Dict]:
    """
    Adds premium fields:
      - portfolio_suggestions (list)
      - outreach_blurb (str)
    Uses MOCK unless MOCK=0 and OPENAI_API_KEY present.
    """
    if MOCK or not os.getenv("OPENAI_API_KEY"):
        for j in jobs:
            j["portfolio_suggestions"] = [
                "Rebuild a feature that mirrors the product",
                "KPI dashboard using public dataset",
                "Mini ETL → report with insights"
            ]
            j["outreach_blurb"] = "I built a role-aligned mini‑project; happy to share a 2‑min Loom."
        return jobs

    # Real LLM enrichment
    try:
        from openai import OpenAI
        client = OpenAI()
        for j in jobs:
            prompt = f"""Act as a hiring manager. For a {role} intern job titled "{j.get('title','')}" at {j.get('company','')}:
- Give 3 short portfolio mini-project ideas (actionable).
- Give a one-line outreach blurb for LinkedIn DM.
Return JSON with keys: portfolio_suggestions (list of strings), outreach_blurb (string)."""
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role":"user","content":prompt}],
                temperature=0.4,
            )
            content = resp.choices[0].message.content.strip()
            # naive parse; tolerate non-JSON by fallback
            import json
            try:
                data = json.loads(content)
            except Exception:
                data = {"portfolio_suggestions": [], "outreach_blurb": content[:240]}
            j["portfolio_suggestions"] = data.get("portfolio_suggestions", [])[:3]
            j["outreach_blurb"] = data.get("outreach_blurb","")[:240]
        return jobs
    except Exception:
        # fail-safe: still return something
        for j in jobs:
            j["portfolio_suggestions"] = ["Small feature clone", "Public KPI dashboard", "Mini ETL + report"]
            j["outreach_blurb"] = "Built a mini‑project aligned to this role—can share highlights."
        return jobs
