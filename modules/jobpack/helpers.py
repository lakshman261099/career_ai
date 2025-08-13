import os, re
from typing import List, Dict
import requests
from bs4 import BeautifulSoup

# ---------- Config / LLM ----------
def is_mock() -> bool:
    return str(os.getenv("MOCK_MODE", "false")).lower() == "true"

def call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 700) -> str:
    """
    Calls OpenAI chat API or returns a mock string when MOCK_MODE=true.
    """
    if is_mock() or not os.getenv("OPENAI_API_KEY"):
        # Deterministic mock text for local testing
        return (
            "- Requirement fit: SQL, Python, dashboards, stakeholder comms\n"
            "- Impact focus: metrics, timelines, deliverables"
        )
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        # Fall back to mock if anything fails
        return f"(mock due to error) {e}"

# ---------- Utilities ----------
def sanitize(s: str, n: int = 20000) -> str:
    return (s or "").strip()[:n]

# ---------- Scraper (safe) ----------
BLOCKED_DOMAINS = {"linkedin.com", "www.linkedin.com"}

def scrape_job_posting(url: str) -> str:
    """Fetch job description text from public job URLs (avoid LinkedIn)."""
    from urllib.parse import urlparse
    if not url:
        return ""
    try:
        domain = urlparse(url).netloc.lower()
        if domain in BLOCKED_DOMAINS:
            return ""  # ask user to paste JD text instead
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        text_parts = []
        for sel in ["div.job", "div.posting", "div.content", "article", "main", "section"]:
            for el in soup.select(sel):
                text = el.get_text(" ", strip=True)
                if text and len(text) > 400:
                    text_parts.append(text)
        page_text = max(text_parts, key=len) if text_parts else soup.get_text(" ", strip=True)
        page_text = re.sub(r"\s{2,}", " ", page_text)
        return page_text[:20000]
    except Exception:
        return ""

# ---------- Simple ATS coverage ----------
def extract_keywords(text: str) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\-\+\.#]{1,}", text.lower())
    stop = set("a an the and or for to of in with on by at from as is are be your you we they it this that".split())
    freq = {}
    for w in words:
        if len(w) < 2 or w in stop:
            continue
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:25]]

def coverage_score(jd: str, resume: str) -> Dict:
    jd_keys = set(extract_keywords(jd))
    cv_keys = set(extract_keywords(resume))
    covered = sorted(list(jd_keys & cv_keys))
    missing = sorted(list(jd_keys - cv_keys))
    score = int(round(100 * (len(covered) / max(1, len(jd_keys)))))
    explain = [
        f"Keyword coverage: {len(covered)}/{len(jd_keys)}",
        f"Top matches: {', '.join(covered[:8]) or 'none'}",
        f"Top missing: {', '.join(missing[:8]) or 'none'}"
    ]
    return {"score": score, "covered": covered, "missing": missing, "explain": explain}

# ---------- Generators ----------
def generate_jd_summary(jd_text: str) -> str:
    sys = "Summarize the job description in 5 concise bullet points."
    usr = jd_text[:8000]
    return call_llm(sys, usr, max_tokens=260)

def tailor_resume_bullets(jd_text: str, resume_text: str) -> List[str]:
    sys = "Rewrite or add exactly 3 resume bullets tailored to the job. Use strong verbs and measurable outcomes."
    usr = f"JOB DESCRIPTION:\n{jd_text[:6000]}\n\nRESUME:\n{resume_text[:6000]}"
    out = call_llm(sys, usr, max_tokens=320)
    lines = [l.strip("-• ").strip() for l in out.split("\n") if l.strip()]
    return lines[:3] or [out]

def generate_cover_letter(jd_text: str, resume_text: str) -> str:
    sys = ("Write a concise 150–220 word cover letter tailored to this job. "
           "Start with a 1-sentence hook; highlight 2–3 relevant achievements; "
           "mirror the company's language; end with a clear CTA.")
    usr = f"JOB DESCRIPTION:\n{jd_text[:6000]}\n\nCANDIDATE RESUME:\n{resume_text[:6000]}"
    return call_llm(sys, usr, max_tokens=380)

def find_missing_skills(jd_text: str, resume_text: str) -> List[str]:
    cov = coverage_score(jd_text, resume_text)
    # Heuristic list; could call LLM to beautify later
    candidates = [k for k in cov["missing"] if re.search(r"[a-z]{3,}", k)]
    return candidates[:6]
