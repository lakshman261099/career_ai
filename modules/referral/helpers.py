import time, re, json, hashlib
import requests
from flask import current_app
from openai import OpenAI
from models import OutreachContact

_CONTACTS_CACHE = {}
_MSG_CACHE = {}
NAME_RX = re.compile(r"(?i)\b([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,2})\b")

def _cache_get(cache, key, ttl):
    now = time.time(); item = cache.get(key)
    if not item: return None
    if now - item["ts"] > ttl: cache.pop(key, None); return None
    return item["val"]

def _cache_set(cache, key, val): cache[key] = {"ts": time.time(), "val": val}

def _cooldown_blocked(user_id:int, contact_name:str, company:str, cooldown_days:int)->bool:
    import datetime as dt
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
    if not key: return []
    q = f'site:linkedin.com "{company}" "{role}" {geo}'.strip()
    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": key},
        params={"q": q, "count": 20}
    )
    items = []
    if resp.status_code != 200: return items
    data = resp.json()
    for r in data.get("web", {}).get("results", []):
        title = r.get("title",""); url = r.get("url",""); snippet = r.get("snippet","")
        name_match = NAME_RX.search(title)
        name = name_match.group(1) if name_match else title.split(" - ")[0][:80]
        role_guess, comp_guess = role, company
        if " at " in title:
            parts = title.split(" at ")
            role_guess = parts[0][:120]
            comp_guess = parts[1].split(" | ")[0][:120]
        items.append({
            "name": name.strip(),
            "title": role_guess.strip(),
            "company": comp_guess.strip(),
            "approx_location": (geo or "").strip(),
            "public_url": url,
            "source": "brave"
        })
        if len(items) >= limit: break
    return items

def search_contacts(user_id:int, company:str, role:str, geo:str):
    key = f"{company}|{role}|{geo}".lower()
    ttl = int(current_app.config.get("REFERRAL_CACHE_TTL_SEC", 172800))
    cached = _cache_get(_CONTACTS_CACHE, key, ttl)
    if cached is not None: return cached

    provider = current_app.config.get("PUBLIC_SEARCH_PROVIDER","brave")
    limit = int(current_app.config.get("REFERRAL_MAX_CONTACTS",25))
    items = []
    if provider == "brave": items = _brave_contacts(company, role, geo, limit*2)

    # de-dup & trim
    seen=set(); cleaned=[]
    for it in items:
        nm = (it.get("name","") or "").strip()
        if not nm or not it.get("public_url"): continue
        sig = (nm.lower(), it["public_url"].lower())
        if sig in seen: continue
        seen.add(sig); cleaned.append(it)
        if len(cleaned) >= limit: break
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
    h = hashlib.sha256(json.dumps([contact, company, role], sort_keys=True).encode()).hexdigest()
    cached = _cache_get(_MSG_CACHE, h, 60*60*48)
    if cached is not None: return cached
    model = current_app.config.get("OPENAI_MODEL","gpt-4o-mini")
    try:
        client = OpenAI()
        prompt = _messages_prompt(contact, company, role)
        resp = client.responses.create(model=model, input=prompt, temperature=0.5)
        text = resp.output_text
        try:
            data = json.loads(text)
        except Exception:
            data = {"warm": text[:280], "cold": text[:280], "follow": text[:280]}
    except Exception:
        data = {
            "warm": f"Hi {contact['name']}, I’m aiming for {role} at {company}. Could I get 5 mins of advice? I can share a 2‑min portfolio.",
            "cold": f"Hi {contact['name']}, I built a small {role}-aligned project for {company}. May I DM a 2‑min portfolio link?",
            "follow": f"Hi {contact['name']}, following up—would value a quick pointer on breaking into {role} at {company}."
        }
    _cache_set(_MSG_CACHE, h, data)
    return data
