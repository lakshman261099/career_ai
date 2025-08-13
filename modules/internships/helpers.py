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

def find_internships_mock(role, location):
    """Return mock internships with a match score and missing skills."""
    return [
        {
            "title": f"{role} Intern",
            "company": "Google",
            "location": location,
            "match_score": 87,
            "missing_skills": ["Tableau", "Data Visualization"],
            "link": "https://careers.google.com/"
        },
        {
            "title": f"Junior {role}",
            "company": "Microsoft",
            "location": location,
            "match_score": 78,
            "missing_skills": ["Excel Macros", "PowerBI"],
            "link": "https://careers.microsoft.com/"
        },
        {
            "title": f"{role} Trainee",
            "company": "Amazon",
            "location": location,
            "match_score": 72,
            "missing_skills": ["SQL Optimization", "Data Warehousing"],
            "link": "https://amazon.jobs/"
        }
    ]

def suggest_learning_links(skills):
    """Generate free learning links for each skill."""
    links = []
    for skill in skills:
        links.append({
            "skill": skill,
            "link": f"https://www.google.com/search?q=free+course+{skill.replace(' ', '+')}"
        })
    return links
