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

def find_jobs_mock(role, location):
    """Return mock jobs with links."""
    return [
        {"title": f"{role} - Graduate Program", "company": "Google", "location": location, "link": "https://careers.google.com"},
        {"title": f"Junior {role}", "company": "Microsoft", "location": location, "link": "https://careers.microsoft.com"},
        {"title": f"{role} Trainee", "company": "Amazon", "location": location, "link": "https://amazon.jobs"}
    ]

def generate_application_pack(job, skills):
    resume_text = ai_call(f"Tailor a resume for job: {job['title']} at {job['company']} for skills: {skills}")
    cover_letter = ai_call(f"Write a short cover letter for job: {job['title']} at {job['company']}")
    follow_up = ai_call(f"Write a polite follow-up email after applying to {job['title']} at {job['company']}")
    return {
        "resume": resume_text,
        "cover_letter": cover_letter,
        "follow_up": follow_up
    }
