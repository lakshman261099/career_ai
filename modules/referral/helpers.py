# career_ai/modules/referral/helpers.py
import os, time, re, json, hashlib, datetime as dt
import requests
from flask import current_app
from openai import OpenAI
from models import db, OutreachContact

# In-memory caches (per dyno)
_CONTACTS_CACHE = {}  # key -> {ts, items}
_MSG_CACHE = {}       # contact_hash -> {ts, variants}

NAME_RX = re.compile(r"(?i)\b([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,2})\b")

def _cache_get(cache, key, ttl):
    now = time.time()
    item = cache.get(key)
    if not item: return None
    if now - item["ts"] > ttl:
        cache.pop(key, None)
        return None
    return item["val"]

def _cache_set(cache, key, val):
    cache[key] = {"ts": time.time(), "val": val}

def _cooldown_blocked(user_id:int, contact_name:str, company:str, cooldown_days:int)->bool:
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=cooldown_days)
    q = OutreachContact.query.filter(
        OutreachContact.user_id==user_id,
        OutreachContact.name.ilike(contact_name),
        OutreachContact.company.ilike(company),
        OutreachContact.created_at>=cutoff
    ).first()
    return q is not None

def _brave_contacts(company:str, role:str, geo:str, limit:int):
    key = current_app.config.get("PUBLIC_SEARCH_KEY","")
    if not key:
        return []
    q = f'site:linkedin.com "{company}" "{role}" {geo}'.strip()
    # Brave Search API (Web)
    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": key},
        params={"q": q, "count": 20}
    )
    items = []
    if resp.status_code != 200:
        return items
    data = resp.json()
    for r in data.get("web", {}).get("results", []):
        title = r.get("title","")
        url = r.get("url","")
        snippet = r.get("snippet","")
        # Heuristic parsing
        name_match = NAME_RX.search(title)
        name = name_match.group(1) if name_match else title.split(" - ")[0][:80]
        approx_loc = geo or ""
        # derive role/company from title/snippet if possible
        role_guess = role
        comp_guess = company
        if " at " in title:
            parts = title.split(" at ")
            role_guess = parts[0][:120]
            comp_guess = parts[1].split(" | ")[0][:120]
        items.append({
            "name": name.strip(),
            "title": role_guess.strip(),
            "company": comp_guess.strip(),
            "approx_location": approx_loc.strip(),
            "public_url": url,
            "source": "brave"
        })
        if len(items) >= limit:
            break
    return items

def search_contacts(user_id:int, company:str, role:str, geo:str):
    key = f"{company}|{role}|{geo}".lower()
    ttl = current_app.config["REFERRAL_CACHE_TTL_SEC"]
    cached = _cache_get(_CONTACTS_CACHE, key, ttl)
    if cached is not None:
        return cached

    provider = current_app.config["PUBLIC_SEARCH_PROVIDER"]
    limit = current_app.config["REFERRAL_MAX_CONTACTS"]
    items = []
    try:
        if provider == "brave":
            items = _brave_contacts(company, role, geo, limit*2)  # fetch extra, filter later
        # You can add serpapi/google_cse branches here similarly.
    except Exception:
        items = []

    # Post-filter & de-dup
    seen = set()
    cleaned = []
    for it in items:
        name = it.get("name","").strip()
        comp = it.get("company","").strip()
        if not name or not it.get("public_url"): 
            continue
        sig = (name.lower(), it["public_url"].lower())
        if sig in seen: 
            continue
        seen.add(sig)
        cleaned.append(it)
        if len(cleaned) >= limit:
            break

    _cache_set(_CONTACTS_CACHE, key, cleaned)
    return cleaned

def _messages_prompt(contact, company, role):
    return (
        "You are helping a student write 3 concise LinkedIn messages. "
        "Return JSON with keys warm, cold, follow.\n\n"
        f"Contact: {json.dumps(contact)}\n"
        f"Target company: {company}\n"
        f"Target role: {role}\n"
        "Rules: 280 chars max each, specific, polite, zero fluff, no email ask, offer a 2-min portfolio link placeholder."
    )

def generate_messages(contact:dict, company:str, role:str):
    # Cache on stable hash
    h = hashlib.sha256(json.dumps([contact, company, role], sort_keys=True).encode()).hexdigest()
    cached = _cache_get(_MSG_CACHE, h, 60*60*48)
    if cached is not None:
        return cached

    client = OpenAI()
    model = current_app.config.get("OPENAI_MODEL","gpt-4o-mini")

    prompt = _messages_prompt(contact, company, role)
    try:
        resp = client.responses.create(
            model=model,
            input=prompt,
            temperature=0.5,
        )
        text = resp.output_text
        try:
            data = json.loads(text)
        except Exception:
            # best-effort extract
            data = {"warm": text[:280], "cold": text[:280], "follow": text[:280]}
    except Exception:
        # If AI fails or MOCK path, fallback short
        data = {
            "warm": f"Hi {contact['name']}, I’m a student aiming for {role} at {company}. Could I get 5 mins of advice? Happy to share a 2‑min portfolio.",
            "cold": f"Hi {contact['name']}, I built a small {role}-aligned project for {company}. May I DM a 2‑min portfolio link for feedback?",
            "follow": f"Hi {contact['name']}, following up in case my note got buried—would value a quick pointer on breaking into {role} at {company}."
        }

    _cache_set(_MSG_CACHE, h, data)
    return data
