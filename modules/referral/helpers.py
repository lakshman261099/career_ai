import os, csv, io
from typing import List, Dict

MOCK = os.getenv("MOCK", "1") == "1"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def generate_messages(contact: Dict, candidate_profile: Dict, deep: bool) -> Dict:
    """
    contact: {name, role, company, email, source}
    candidate_profile: {role, highlights}
    Returns 3 variants: warm, cold, follow
    """
    if MOCK or not deep or not os.getenv("OPENAI_API_KEY"):
        base = f"Hi {contact.get('name','there')}, I'm applying for {candidate_profile.get('role','an internship')} and would value your perspective."
        return {
            "warm": base + " Could we grab 10 minutes? I built a small project relevant to your team.",
            "cold": base + " I built a small role‑aligned project; may I share a 2‑min Loom?",
            "follow": base + " Following up in case my earlier note got buried — appreciate any guidance!",
        }

    try:
        from openai import OpenAI
        client = OpenAI()
        prompt = f"""Write 3 concise LinkedIn messages for contacting someone at {contact.get('company','a company')}:
- Warm intro
- Cold connect
- Follow-up
Context: Candidate for {candidate_profile.get('role','')} with highlights: {candidate_profile.get('highlights','')}
Output JSON with keys warm, cold, follow."""
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role":"user","content":prompt}],
            temperature=0.5,
        )
        content = resp.choices[0].message.content.strip()
        import json
        try:
            return json.loads(content)
        except Exception:
            return {"warm": content[:280], "cold": content[:280], "follow": "Following up on my previous note."}
    except Exception:
        return {
            "warm": "Hi — quick warm note regarding my application and a small project I built.",
            "cold": "Hello — reaching out cold with a short project aligned to your stack.",
            "follow": "Just following up in case my earlier message was missed."
        }
