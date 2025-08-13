import os
from openai import OpenAI

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = None
if not MOCK_MODE and OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

def ai_call(prompt):
    if MOCK_MODE or not client:
        return "[MOCK AI OUTPUT] " + prompt[:80] + "..."
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return resp.choices[0].message.content.strip()

def sanitize(s, n=500):
    return (s or "").strip()[:n]

def find_alumni_mock(university, role):
    """Simulate an alumni search."""
    return [
        {"name": "Priya Sharma", "company": "Google", "role": role, "grad_year": 2021},
        {"name": "Amit Kumar", "company": "Microsoft", "role": role, "grad_year": 2020},
        {"name": "Neha Verma", "company": "Amazon", "role": role, "grad_year": 2022}
    ]

def generate_outreach_message(person, skills, goal):
    prompt = f"""
    Write a short, warm LinkedIn connection message to {person['name']} 
    who works as {person['role']} at {person['company']}. 
    The sender has skills: {skills} and career goal: {goal}.
    """
    return ai_call(prompt)
