import os
from openai import OpenAI

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = None
if not MOCK_MODE and OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

def ai_call(prompt):
    if MOCK_MODE or not client:
        return "[MOCK PROJECT BRIEF] Create a sample project for the given role..."
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return resp.choices[0].message.content.strip()

def sanitize(s, n=200):
    return (s or "").strip()[:n]

def generate_project_brief(role):
    prompt = f"""
    You are a hiring manager for a {role} role.
    Create a short, realistic 1–2 hour project task that could be used in a job simulation.
    Include:
    - Project title
    - Detailed task description
    - Deliverables
    - Evaluation criteria
    Keep it under 250 words.
    """
    return ai_call(prompt)
