import io
import os, re, json, math, random, datetime as dt
from bs4 import BeautifulSoup
import requests

USE_MOCK = os.getenv("MOCK", "1") == "1"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def fetch_url_text(url:str)->str:
    try:
        if "linkedin.com" in url.lower():
            return ""
        resp = requests.get(url, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
        text = " ".join([t.get_text(" ", strip=True) for t in soup.select("body")])[:8000]
        return text
    except Exception:
        return ""

def mock_jobpack(role, jd_text, resume_text, mode="fast"):
    # Produce deterministic-looking mock with random but stable scores
    seed = abs(hash(role + jd_text[:60] + resume_text[:60] + mode)) % 10_000
    random.seed(seed)
    def score(label):
        base = {"skills":70,"experience":65,"impact":60,"domain_fit":68,"ats_format":80}[label]
        return max(35, min(98, base + random.randint(-20, 20)))
    bullets = [
        "Led a 3‑person team to deliver X, improving Y by 24% using Z (STAR).",
        "Automated A with B, cutting cycle time by 31% and saving 10 hrs/week.",
        "Designed C experiment, analyzed with Python; insights drove decision D.",
    ]
    questions = [
        {"question":"Tell me about a time you influenced without authority.", "type":"behavioral",
         "sample_answer":"Situation, Task, Action, Result... focus on stakeholders, metrics, lesson."},
        {"question":"How would you design a data pipeline for metric X?", "type":"technical",
         "sample_answer":"Clarify SLA, data sources, schema, quality, orchestration, monitoring."},
        {"question":"What gaps do you see in our product?", "type":"product",
         "sample_answer":"Frame with user needs, hypotheses, quick wins, and metrics."},
        {"question":"Walk me through your favorite project.", "type":"open",
         "sample_answer":"Context, constraints, your role, obstacles, quant impact."},
    ]
    recs = ["Tighten resume achievements with numbers", "Bridge 2 missing skills via short courses", "Add a relevant portfolio piece"]
    cover = ("I'm excited about the "+role+" role. My experience aligns with your needs, including..."
             " I’ve delivered measurable impact and can quickly contribute. Thank you for considering my application.")
    payload = {
        "meta": {"role": role, "seniority": "Intern/Entry", "analysis_mode": mode},
        "scores":{
            "skills":{"score":score("skills"), "notes":["Core stack partially met","Add 1–2 specific tools"]},
            "experience":{"score":score("experience"), "notes":["Projects are relevant","Could show internships"]},
            "impact":{"score":score("impact"), "notes":["Quantify results","Tie to business outcomes"]},
            "domain_fit":{"score":score("domain_fit"), "notes":["Understand industry context","Add domain keywords"]},
            "ats_format":{"score":score("ats_format"), "notes":["Simple formatting","Use standard section headers"]},
        },
        "overall_verdict":{"status": random.choice(["Strong","Borderline","Fix & Apply"]),
                           "summary":"Your profile is competitive with clear growth areas.",
                           "recommendations":recs},
        "tailored_resume_bullets": bullets,
        "missing_skills": ["SQL","Experimentation","Stakeholder comms"][:random.randint(1,3)],
        "cover_letter": cover[:220],
        "interview_questions": questions,
        "follow_up_actions": ["Connect with a team member","Ship a mini‑project","Apply via company portal"]
    }
    return payload

def deep_jobpack_llm(role, jd_text, resume_text):
    # Real model call if MOCK=0; otherwise mock
    if USE_MOCK:
        return mock_jobpack(role, jd_text, resume_text, mode="deep")
    from openai import OpenAI
    client = OpenAI()
    prompt = f"""
You are a hiring manager. Analyze JD and resume in 5 stages and return the JSON exactly in the schema:
<schema>
meta{{role, seniority, analysis_mode}}
scores{{skills{{score,notes}},experience{{score,notes}},impact{{score,notes}},domain_fit{{score,notes}},ats_format{{score,notes}}}}
overall_verdict{{status,summary,recommendations}}
tailored_resume_bullets[]
missing_skills[]
cover_letter
interview_questions[]
follow_up_actions[]
</schema>
Role: {role}
JD: {jd_text[:7000]}
Resume: {resume_text[:7000]}
Return only valid JSON.
"""
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role":"user","content":prompt}],
        temperature=0.4,
    )
    txt = resp.choices[0].message.content
    # Best effort JSON extraction
    start = txt.find("{"); end = txt.rfind("}")
    return json.loads(txt[start:end+1])

def fast_jobpack_llm(role, jd_text, resume_text):
    if USE_MOCK:
        return mock_jobpack(role, jd_text, resume_text, mode="fast")
    from openai import OpenAI
    client = OpenAI()
    prompt = f"""
Condense a fast hiring-manager Job Pack JSON for role "{role}" (same schema as deep but concise). JD: {jd_text[:5000]} Resume: {resume_text[:5000]}.
Return only JSON.
"""
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL","gpt-4o-mini"),
        messages=[{"role":"user","content":prompt}],
        temperature=0.2,
    )
    txt = resp.choices[0].message.content
    start = txt.find("{"); end = txt.rfind("}")
    return json.loads(txt[start:end+1])

def build_pdf_bytes(pack_json):
    # Minimal, pretty-enough PDF using reportlab
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    y = height - 50
    def line(t, size=12):
        nonlocal y
        t = (t or "")[:1200]
        c.setFont("Helvetica", size)
        for chunk in [t[i:i+95] for i in range(0,len(t),95)]:
            c.drawString(40, y, chunk); y -= 16
            if y < 60: c.showPage(); y = height - 50
    line("Job Pack Pro", 16)
    meta = pack_json.get("meta",{})
    line(f"Role: {meta.get('role','')} | Mode: {meta.get('analysis_mode','')}", 12)
    line("Scores:", 14)
    for k,v in pack_json.get("scores",{}).items():
        line(f"- {k.title()}: {v.get('score')}  Notes: {', '.join(v.get('notes',[]))}")
    ov = pack_json.get("overall_verdict",{})
    line(f"Verdict: {ov.get('status')} — {ov.get('summary')}")
    line("Recommendations: " + ", ".join(ov.get("recommendations",[])))
    line("Tailored Bullets:", 14)
    for b in pack_json.get("tailored_resume_bullets",[]):
        line(f"• {b}")
    line("Missing Skills: " + ", ".join(pack_json.get("missing_skills",[])))
    line("Cover Letter:", 14)
    line(pack_json.get("cover_letter",""))
    line("Interview Qs:", 14)
    for q in pack_json.get("interview_questions",[]):
        line(f"Q: {q.get('question')} | A: {q.get('sample_answer')}")
    line("Follow-ups: " + ", ".join(pack_json.get("follow_up_actions",[])))
    c.showPage(); c.save()
    pdf = buf.getvalue(); buf.close()
    return pdf
