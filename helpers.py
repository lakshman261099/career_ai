import json
import logging
import math
import os
import re
import time
from typing import Any, Dict, List, Optional

# Optional imports (guarded)
try:
    from PyPDF2 import PdfReader  # pip install pypdf
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

try:
    from docx import Document  # pip install python-docx
except Exception:  # pragma: no cover
    Document = None  # type: ignore

# OpenAI client (1.x)
try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

# ---------------------------------------------------------------------
# AI-only mode configuration
# ---------------------------------------------------------------------
# Historical env flag retained for compatibility but **ignored** by helpers below.
# Set MOCK=0 in your environment; this file does not emit mocked content.
MOCK = False  # deprecated — do not rely on this flag anywhere else

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


# ---------------------------------------------------------------------
# OpenAI helpers (AI-only; no mock fallbacks)
# ---------------------------------------------------------------------


def _client() -> Optional["OpenAI"]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return None
    return OpenAI(api_key=api_key)


def _approx_tokens(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4))


def _cost(input_toks: int, output_toks: int, deep: bool) -> Dict[str, float]:
    if deep:
        cin = (input_toks / 1000.0) * PRICE_IN_PER_1K_DEEP
        cout = (output_toks / 1000.0) * PRICE_OUT_PER_1K_DEEP
    else:
        cin = (input_toks / 1000.0) * PRICE_IN_PER_1K
        cout = (output_toks / 1000.0) * PRICE_OUT_PER_1K
    return {
        "input_usd": round(cin, 4),
        "output_usd": round(cout, 4),
        "total_usd": round(cin + cout, 4),
    }


def _truncate(text: str, max_chars: int = MAX_INPUT_CHARS) -> str:
    if not text:
        return ""
    return text if len(text) <= max_chars else text[:max_chars]


def _call_openai(
    messages: List[Dict[str, str]], deep: bool, temperature: float = 0.2
) -> Dict[str, Any]:
    """Unified wrapper. Returns {ok, text, usage{...}, error}. AI-only.
    No mocked responses are returned by this function.
    """
    client = _client()
    if client is None:
        err = "Missing OPENAI_API_KEY or OpenAI SDK not available"
        logger.error(err)
        return {"ok": False, "text": "", "usage": {}, "error": err}

    model = OPENAI_MODEL_DEEP if deep else OPENAI_MODEL_FAST
    last_err = None
    for attempt in range(REQUEST_RETRIES + 1):
        try:
            rsp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
            text = (rsp.choices[0].message.content or "").strip()
            in_toks = getattr(rsp, "usage", None) and getattr(
                rsp.usage, "prompt_tokens", None
            )
            comp_toks = getattr(rsp, "usage", None) and getattr(
                rsp.usage, "completion_tokens", None
            )
            in_toks = in_toks or sum(
                _approx_tokens(m.get("content", "")) for m in messages
            )
            out_toks = comp_toks or _approx_tokens(text)
            return {
                "ok": True,
                "text": text,
                "usage": {
                    "input_tokens": in_toks,
                    "output_tokens": out_toks,
                    "cost_usd": _cost(in_toks, out_toks, deep),
                    "model": model,
                    "mock": False,
                },
                "error": None,
            }
        except Exception as e:  # pragma: no cover
            last_err = str(e)
            logger.warning(
                f"OpenAI call failed ({attempt+1}/{REQUEST_RETRIES+1}): {last_err}"
            )
            time.sleep(0.8 * (attempt + 1))
    return {"ok": False, "text": "", "usage": {}, "error": last_err}


# ---------------------------------------------------------------------
# File parsing (best-effort, optional deps)
# ---------------------------------------------------------------------


def extract_text_from_file(file_path: str) -> str:
    p = (file_path or "").lower()
    try:
        if p.endswith(".pdf") and PdfReader:
            reader = PdfReader(file_path)
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        if p.endswith(".docx") and Document:
            doc = Document(file_path)
            return "\n".join(par.text for par in doc.paragraphs)
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:  # pragma: no cover
        logger.error(f"file parse failed {file_path}: {e}")
        return ""


# ---------------------------------------------------------------------
# Resume critique (AI-only)
# ---------------------------------------------------------------------


def ai_resume_critique(resume_text: str, deep: bool = False) -> str:
    resume_text = _truncate(resume_text)
    sys = (
        "You are a precise career coach for students/new grads. "
        "Return concise bullets, quantify impact, include ATS keywords."
    )
    usr = (
        "Analyze the following resume. Return sections:\n"
        "1) Summary (2 sentences)\n"
        "2) Top 5 Actionable Fixes (bullets)\n"
        "3) Missing Keywords (comma-separated)\n"
        "4) Rewriting Examples (2 bullet points: before → after)\n\n"
        f"{resume_text}"
    )
    rsp = _call_openai(
        [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        deep=deep,
        temperature=0.3,
    )
    if not rsp["ok"]:
        return f"ERROR: {rsp['error'] or 'AI unavailable'}"
    return rsp["text"]


# ---------------------------------------------------------------------
# Portfolio helper (AI-only)
# ---------------------------------------------------------------------


def portfolio_suggestions(name: str, role: str, deep: bool = False) -> List[str]:
    role = (role or "Software Engineer Intern").strip()
    sys = (
        "You generate concrete, scoped portfolio projects. "
        "Each idea must be doable ≤ 2 weeks, with stack and measurable outcome."
    )
    usr = (
        f"Suggest 3 project ideas for {name or 'a student'} targeting '{role}'. "
        "Each bullet: Title — Tech — 3 features — Outcome."
    )
    rsp = _call_openai(
        [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        deep=deep,
        temperature=0.5,
    )
    if not rsp["ok"]:
        return [f"ERROR: {rsp['error'] or 'AI unavailable'}"]
    text = rsp["text"].strip()
    items = re.split(r"\n[-*•]\s*", "\n" + text)
    cleaned = [i.strip() for i in items if i.strip()]
    return cleaned[:3] if cleaned else [text]


# ---------------------------------------------------------------------
# Learning links (static)
# ---------------------------------------------------------------------
_LEARNING_LINKS = [
    {"label": "CS50x fundamentals", "url": "https://cs50.harvard.edu/x/"},
    {"label": "Python for Everybody", "url": "https://www.py4e.com/"},
    {"label": "SQLBolt (SQL basics)", "url": "https://sqlbolt.com/"},
    {
        "label": "Portfolio site guide",
        "url": "https://www.freecodecamp.org/news/how-to-build-a-portfolio-website/",
    },
    {"label": "LeetCode patterns", "url": "https://seanprashad.com/leetcode-patterns/"},
]


# ---------------------------------------------------------------------
# Internships (placeholder; paste-only flows elsewhere should use JD text)
# ---------------------------------------------------------------------


def internships_search(role: str, location: str) -> List[Dict]:
    """This helper is non-AI and returns a placeholder list; keep or replace
    with paste-only JD analysis elsewhere. No scraping occurs here.
    """
    role = (role or "Software Engineer").strip()
    location = (location or "Remote").strip()
    base = [
        {
            "title": f"{role} Intern",
            "company": "Acme Studios",
            "location": location,
            "apply_url": "#",
            "match": 86,
            "why": "Role title match; core skills overlap.",
        },
        {
            "title": f"Junior {role}",
            "company": "Nova Labs",
            "location": location,
            "apply_url": "#",
            "match": 79,
            "why": "Title variant accepted; shared stack.",
        },
        {
            "title": f"{role} Trainee",
            "company": "PixelWorks",
            "location": location,
            "apply_url": "#",
            "match": 73,
            "why": "Training included; entry-friendly.",
        },
    ]
    base.append({"learning": _LEARNING_LINKS})
    return base


# ---------------------------------------------------------------------
# Referral helper (AI-only; returns short outreach messages)
# ---------------------------------------------------------------------


def referral_messages(
    contact: Dict, candidate_profile: Dict, deep: bool = False
) -> Dict:
    """
    Generate 2–3 short outreach scripts:
    - warm: you have some connection (alumni, mutuals, same college, etc.)
    - cold: no connection, purely cold LinkedIn/email
    - follow: polite nudge ~5–7 days later

    All messages:
    - Are written in the student's voice (first person: "I").
    - Are concise: ~4–7 short sentences each.
    - Sound normal, friendly, and respectful (not corporate or sales-y).
    - Can be pasted into LinkedIn or email with minimal editing.
    """
    payload = {
        "contact": contact or {},
        "candidate_profile": candidate_profile or {},
    }

    sys = (
        "You are ReferralCoach for students and new grads.\n"
        "You ONLY write short outreach messages asking for advice or a possible referral.\n\n"
        "OUTPUT RULES:\n"
        "- Return a single JSON object with exactly these keys: warm, cold, follow.\n"
        "- Each value is a single plain-text message (no bullets, no markdown).\n"
        "- Write as if the student is speaking in first person (\"I\", \"my\").\n"
        "- Keep each message concise: about 4–7 short sentences.\n"
        "- Sound friendly and respectful, not over-formal and not salesy.\n"
        "- Avoid buzzwords and generic flattery (no \"exceptional organization\", \"synergies\", etc.).\n"
        "- If information is missing (e.g., no company name), just omit it naturally.\n"
    )

    usr = (
        "Use this JSON as context. Do NOT echo it back. "
        "Use it to personalize the messages:\n\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n\n"
        "Guidance:\n"
        "- If contact.source mentions alumni / same school, treat as a warm connection.\n"
        "- Use candidate_profile.role as the target role.\n"
        "- Use candidate_profile.highlights as 2–3 reasons why they might be a good fit.\n"
        "- If candidate_profile.job_description is present, reference 1–2 specific skills or responsibilities from it.\n"
        "- Make it easy for the student to lightly edit and send."
    )

    rsp = _call_openai(
        [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        deep=deep,
        temperature=0.4,
    )

    if not rsp["ok"]:
        err = f"ERROR: {rsp['error'] or 'AI unavailable'}"
        return {"warm": err, "cold": err, "follow": err}

    text = rsp["text"].strip()

    # First try: assume model followed instructions and returned JSON.
    try:
        data = json.loads(text)
        out = {
            "warm": str(data.get("warm", "")).strip(),
            "cold": str(data.get("cold", "")).strip(),
            "follow": str(data.get("follow", "")).strip(),
        }
        # Ensure we don't accidentally return all empties
        if any(out.values()):
            return out
    except Exception:
        pass

    # Fallback: try to scrape sections if model added labels instead of JSON.
    def pick(section: str) -> str:
        m = re.search(rf"(?i){section}[^:]*:\s*(.+?)(?:\n\n|$)", text, re.S)
        return m.group(1).strip() if m else ""

    warm = pick("warm") or text
    cold = pick("cold") or ""
    follow = pick("follow") or ""

    return {
        "warm": warm.strip(),
        "cold": cold.strip() or warm.strip(),
        "follow": follow.strip() or "",
    }


# ---------------------------------------------------------------------
# Job Pack (AI-only; strict JSON normalization)
# ---------------------------------------------------------------------


def jobpack_analyze(jd_text: str, resume_text: str = "") -> Dict:
    jd_text = _truncate(jd_text, 16000)
    resume_text = _truncate(resume_text, 6000)
    if not jd_text:
        return {
            "fit": {"score": 0, "gaps": [], "keywords": []},
            "ats": {"pass": False, "notes": []},
            "cover": "",
            "qna": [],
        }

    sys = (
        "You are a strict JSON generator for a Job Pack. "
        "Return only JSON with keys: fit{score,gaps[],keywords[]}, ats{pass,notes[]}, cover, qna[{q,a}]."
    )
    usr = (
        f"JOB DESCRIPTION:\n{jd_text}\n\nRESUME (optional):\n{resume_text}\n\n"
        "Build the Job Pack JSON."
    )
    rsp = _call_openai(
        [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        deep=True,
        temperature=0.2,
    )

    if not rsp["ok"]:
        # Preserve schema, no mock prose
        return {
            "fit": {"score": 0, "gaps": [], "keywords": []},
            "ats": {
                "pass": False,
                "notes": [f"ERROR: {rsp['error'] or 'AI unavailable'}"],
            },
            "cover": "",
            "qna": [],
        }

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
        data["qna"] = [
            {"q": d.get("q", ""), "a": d.get("a", "")}
            for d in data["qna"]
            if isinstance(d, dict)
        ]
        return data
    except Exception:
        m = re.search(r"(\{.*\})", text, re.S)
        if m:
            try:
                parsed = json.loads(m.group(1))
                # Ensure schema keys exist
                parsed.setdefault("fit", {}).setdefault("score", 0)
                parsed["fit"].setdefault("gaps", [])
                parsed["fit"].setdefault("keywords", [])
                parsed.setdefault("ats", {}).setdefault("pass", False)
                parsed["ats"].setdefault("notes", [])
                parsed.setdefault("cover", "")
                parsed.setdefault("qna", [])
                if not isinstance(parsed["qna"], list):
                    parsed["qna"] = []
                parsed["qna"] = [
                    {"q": d.get("q", ""), "a": d.get("a", "")}
                    for d in parsed["qna"]
                    if isinstance(d, dict)
                ]
                return parsed
            except Exception:
                pass
        return {
            "fit": {"score": 0, "gaps": [], "keywords": []},
            "ats": {"pass": False, "notes": ["ERROR: invalid AI JSON"]},
            "cover": "",
            "qna": [],
        }


# ---------------------------------------------------------------------
# Skill Mapper (AI-preferred; deep=True uses LLM; no mocked content)
# ---------------------------------------------------------------------
_SKILL_SEEDS = {
    "Programming": [
        "python",
        "java",
        "c++",
        "c#",
        "javascript",
        "typescript",
        "go",
        "ruby",
        "rust",
        "kotlin",
        "swift",
    ],
    "Data": [
        "sql",
        "pandas",
        "numpy",
        "excel",
        "tableau",
        "power bi",
        "r",
        "matplotlib",
    ],
    "Backend": [
        "flask",
        "django",
        "fastapi",
        "node",
        "express",
        "spring",
        "dotnet",
        "graphql",
        "rest",
        "grpc",
    ],
    "Frontend": ["react", "vue", "angular", "html", "css", "sass", "webpack", "vite"],
    "Cloud/DevOps": [
        "aws",
        "gcp",
        "azure",
        "docker",
        "kubernetes",
        "terraform",
        "ci/cd",
        "linux",
    ],
    "ML/AI": [
        "pytorch",
        "tensorflow",
        "scikit-learn",
        "opencv",
        "nlp",
        "llm",
        "hugging face",
    ],
    "Mobile": ["android", "ios", "react native", "flutter", "swiftui"],
    "Testing": ["pytest", "jest", "unittest", "cypress"],
    "Other": ["git", "jira", "agile", "scrum"],
}


def _extract_seed_skills(text: str) -> Dict[str, List[str]]:
    t = (text or "").lower()
    found: Dict[str, List[str]] = {}
    for cat, seeds in _SKILL_SEEDS.items():
        hits = []
        for s in seeds:
            pat = r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])"
            if re.search(pat, t):
                hits.append(s)
        if hits:
            seen = set()
            uniq = [h for h in hits if not (h in seen or seen.add(h))]
            found[cat] = uniq
    return found


def skillmap_analyze(text: str, deep: bool = False) -> Dict[str, Any]:
    """
    Returns a dict with keys:
    {
        "skills": [...],
        "categories": {"Programming":[...], ...}
    }
    - No mocked content. If deep=True and API key present, use LLM to refine clustering.
    - If deep=False, returns seed extraction (deterministic, not a mock; still paste-only).
    """
    text = _truncate(text, 16000)
    base = _extract_seed_skills(text)
    flat: List[str] = []
    for arr in base.values():
        for s in arr:
            if s not in flat:
                flat.append(s)

    if deep:
        sys = (
            "You normalize and cluster technical skills from text. "
            "Return strict JSON with keys: skills[], categories{Category: [skills...]}. "
            "Use lowercase skills and do not invent skills not present."
        )
        usr = json.dumps(
            {
                "text_excerpt": text[:4000],
                "seed_skills": flat,
                "seed_categories": base,
            }
        )
        rsp = _call_openai(
            [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            deep=True,
            temperature=0.2,
        )
        if rsp["ok"]:
            try:
                data = json.loads(rsp["text"])
                skills = data.get("skills") or flat
                cats = data.get("categories") or base
                skills = sorted({str(s).lower() for s in skills})
                cats = {k: sorted({str(s).lower() for s in v}) for k, v in cats.items()}
                return {"skills": skills, "categories": cats}
            except Exception:
                return {"skills": flat, "categories": base}
        else:
            # Preserve deterministic extractor output; do not emit mock prose
            return {"skills": flat, "categories": base}

    return {"skills": flat, "categories": base}
