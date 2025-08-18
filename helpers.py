import os, json, re, time, math, logging
from typing import List, Dict, Any
from PyPDF2 import PdfReader
from docx import Document
from openai import OpenAI

MOCK = os.getenv("MOCK", "1") == "1"
OPENAI_MODEL_DEEP = os.getenv("OPENAI_MODEL_DEEP", "gpt-4o")
OPENAI_MODEL_FAST = os.getenv("OPENAI_MODEL_FAST", "gpt-4o-mini")
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "18000"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "800"))
REQUEST_RETRIES = int(os.getenv("OPENAI_RETRIES", "2"))
REQUEST_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT_SECS", "40"))

PRICE_IN_PER_1K = float(os.getenv("PRICE_IN_PER_1K", "0.005"))
PRICE_OUT_PER_1K = float(os.getenv("PRICE_OUT_PER_1K", "0.015"))
PRICE_IN_PER_1K_DEEP = float(os.getenv("PRICE_IN_PER_1K_DEEP", "0.01"))
PRICE_OUT_PER_1K_DEEP = float(os.getenv("PRICE_OUT_PER_1K_DEEP", "0.03"))

logger = logging.getLogger("helpers")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

def _client():
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def _approx_tokens(text: str) -> int:
    return max(1, math.ceil((len(text or "")) / 4))

def _cost(input_toks: int, output_toks: int, deep: bool) -> Dict[str, float]:
    if deep:
        cin = (input_toks / 1000.0) * PRICE_IN_PER_1K_DEEP
        cout = (output_toks / 1000.0) * PRICE_OUT_PER_1K_DEEP
    else:
        cin = (input_toks / 1000.0) * PRICE_IN_PER_1K
        cout = (output_toks / 1000.0) * PRICE_OUT_PER_1K
    return {"input_usd": round(cin, 4), "output_usd": round(cout, 4), "total_usd": round(cin + cout, 4)}

def _truncate(text: str, max_chars: int = MAX_INPUT_CHARS) -> str:
    if not text:
        return ""
    return text if len(text) <= max_chars else text[:max_chars]

def _call_openai(messages: List[Dict[str, str]], deep: bool, temperature: float = 0.2) -> Dict[str, Any]:
    if MOCK or not os.getenv("OPENAI_API_KEY"):
        dummy = "MOCK RESPONSE: Set MOCK=0 and OPENAI_API_KEY to get real output."
        in_toks = sum(_approx_tokens(m.get("content", "")) for m in messages)
        out_toks = _approx_tokens(dummy)
        return {"ok": True, "text": dummy, "usage": {
            "input_tokens": in_toks, "output_tokens": out_toks, "cost_usd": _cost(in_toks, out_toks, deep),
            "model": OPENAI_MODEL_DEEP if deep else OPENAI_MODEL_FAST, "mock": True
        }, "error": None}

    model = OPENAI_MODEL_DEEP if deep else OPENAI_MODEL_FAST
    last_err = None
    for attempt in range(REQUEST_RETRIES + 1):
        try:
            rsp = _client().chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=MAX_OUTPUT_TOKENS,
                timeout=REQUEST_TIMEOUT
            )
            text = (rsp.choices[0].message.content or "").strip()
            in_toks = getattr(rsp.usage, "prompt_tokens", None) or sum(_approx_tokens(m.get("content", "")) for m in messages)
            out_toks = getattr(rsp.usage, "completion_tokens", None) or _approx_tokens(text)
            return {"ok": True, "text": text, "usage": {
                "input_tokens": in_toks, "output_tokens": out_toks, "cost_usd": _cost(in_toks, out_toks, deep),
                "model": model, "mock": False
            }, "error": None}
        except Exception as e:
            last_err = str(e)
            logger.warning(f"OpenAI call failed ({attempt+1}/{REQUEST_RETRIES+1}): {last_err}")
            time.sleep(0.8 * (attempt + 1))
    return {"ok": False, "text": "", "usage": {}, "error": last_err}

def extract_text_from_file(file_path: str) -> str:
    p = file_path.lower()
    try:
        if p.endswith(".pdf"):
            reader = PdfReader(file_path)
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if p.endswith(".docx"):
            doc = Document(file_path)
            return "\n".join(par.text for par in doc.paragraphs)
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        logger.error(f"file parse failed {file_path}: {e}")
        return ""

def ai_resume_critique(resume_text: str, deep: bool = False) -> str:
    resume_text = _truncate(resume_text)
    sys = ("You are a precise career coach for students/new grads. "
           "Return concise bullets, quantify impact, include ATS keywords.")
    usr = ("Analyze the following resume. Return sections:\n"
           "1) Summary (2 sentences)\n"
           "2) Top 5 Actionable Fixes (bullets)\n"
           "3) Missing Keywords (comma-separated)\n"
           "4) Rewriting Examples (2 bullet points: before → after)\n\n"
           f"{resume_text}")
    rsp = _call_openai([{"role": "system", "content": sys}, {"role": "user", "content": usr}], deep=deep, temperature=0.3)
    return rsp["text"] if rsp["ok"] else "Error contacting AI. Try again."

def portfolio_suggestions(name: str, role: str, deep: bool = False) -> List[str]:
    role = (role or "Software Engineer Intern").strip()
    sys = ("You generate concrete, scoped portfolio projects. "
           "Each idea must be doable ≤ 2 weeks, with stack and measurable outcome.")
    usr = (f"Suggest 3 project ideas for {name or 'a student'} targeting '{role}'. "
           "Each bullet: Title — Tech — 3 features — Outcome.")
    rsp = _call_openai([{"role": "system", "content": sys}, {"role": "user", "content": usr}], deep=deep, temperature=0.5)
    if not rsp["ok"]:
        return [
            f"{role} Mini App — Tech: Flask/React — Features: auth, CRUD, search — Outcome: 50 users.",
            f"{role} Data Project — Tech: Pandas — Features: ingest, clean, visualize — Outcome: blog post.",
            f"{role} API Wrapper — Tech: Python — Features: SDK, tests, docs — Outcome: pip installs.",
        ]
    text = rsp["text"]
    items = re.split(r"\n[-*•]\s*", "\n" + text.strip())
    cleaned = [i.strip() for i in items if i.strip()]
    return cleaned[:3] if cleaned else [text]

_LEARNING_LINKS = [
    {"label": "CS50x fundamentals", "url": "https://cs50.harvard.edu/x/"},
    {"label": "Python for Everybody", "url": "https://www.py4e.com/"},
    {"label": "SQLBolt (SQL basics)", "url": "https://sqlbolt.com/"},
    {"label": "Portfolio site guide", "url": "https://www.freecodecamp.org/news/how-to-build-a-portfolio-website/"},
    {"label": "LeetCode patterns", "url": "https://seanprashad.com/leetcode-patterns/"},
]

def internships_search(role: str, location: str) -> List[Dict]:
    role = (role or "Software Engineer").strip()
    location = (location or "Remote").strip()
    base = [
        {"title": f"{role} Intern", "company": "Acme Studios", "location": location, "apply_url": "#", "match": 86,
         "why": "Role title match; core skills overlap."},
        {"title": f"Junior {role}", "company": "Nova Labs", "location": location, "apply_url": "#", "match": 79,
         "why": "Title variant accepted; shared stack."},
        {"title": f"{role} Trainee", "company": "PixelWorks", "location": location, "apply_url": "#", "match": 73,
         "why": "Training included; entry-friendly."},
    ]
    base.append({"learning": _LEARNING_LINKS})
    return base

def referral_messages(contact: Dict, candidate_profile: Dict, deep: bool = False) -> Dict:
    base = f"Hi {contact.get('name', 'there')}, I'm applying for {candidate_profile.get('role','an internship')}."
    mock = {
        "warm": base + " Could we grab 10 minutes? I built a small project relevant to your team.",
        "cold": base + " I built a small role-aligned project; may I share a 2-min Loom?",
        "follow": base + " Following up in case my earlier note got buried — appreciate your time!",
    }
    if MOCK or not os.getenv("OPENAI_API_KEY"):
        return mock
    sys = "Write concise referral outreach. Return strict JSON with keys: warm, cold, follow."
    usr = json.dumps({"contact": contact, "candidate_profile": candidate_profile})
    rsp = _call_openai([{"role": "system", "content": sys}, {"role": "user", "content": usr}], deep=deep, temperature=0.5)
    if not rsp["ok"]:
        return mock
    text = rsp["text"]
    try:
        data = json.loads(text)
        if all(k in data for k in ("warm", "cold", "follow")):
            return data
    except Exception:
        pass
    def pick(section: str) -> str:
        m = re.search(rf"(?i){section}[^:]*:\s*(.+?)(?:\n\n|$)", text, re.S)
        return m.group(1).strip() if m else mock[section]
    return {"warm": pick("warm"), "cold": pick("cold"), "follow": pick("follow")}

def jobpack_analyze(jd_text: str, resume_text: str = "") -> Dict:
    jd_text = _truncate(jd_text, 16000)
    resume_text = _truncate(resume_text, 6000)
    mock = {
        "fit": {"score": 78, "gaps": ["Lack of shipped title", "Missing unit tests"], "keywords": ["Unity", "C#", "VR"]},
        "ats": {"pass": True, "notes": ["Add exact phrases from JD"]},
        "cover": "Dear Hiring Manager,\n\nI'm excited to apply... (mock)\n\nSincerely,\nCandidate",
        "qna": [{"q": "Tell me about a challenge", "a": "I used the STAR method to..."}],
    }
    if not jd_text:
        return mock
    if MOCK or not os.getenv("OPENAI_API_KEY"):
        return mock
    sys = ("You are a strict JSON generator for a Job Pack. "
           "Return only JSON with keys: fit{score,gaps[],keywords[]}, ats{pass,notes[]}, cover, qna[{q,a}].")
    usr = f"JOB DESCRIPTION:\n{jd_text}\n\nRESUME (optional):\n{resume_text}\n\nBuild the Job Pack JSON."
    rsp = _call_openai([{"role": "system", "content": sys}, {"role": "user", "content": usr}], deep=True, temperature=0.2)
    if not rsp["ok"]:
        return mock
    text = rsp["text"]
    try:
        data = json.loads(text)
        data.setdefault("fit", {}).setdefault("score", 0)
        data["fit"].setdefault("gaps", [])
        data["fit"].setdefault("keywords", [])
        data.setdefault("ats", {}).setdefault("pass", False)
        data["ats"].setdefault("notes", [])
        data.setdefault("cover", "")
        data.setdefault("qna", [])
        if not isinstance(data["qna"], list):
            data["qna"] = []
        data["qna"] = [{"q": d.get("q", ""), "a": d.get("a", "")} for d in data["qna"] if isinstance(d, dict)]
        return data
    except Exception:
        m = re.search(r"(\{.*\})", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        return mock
