# career_ai/modules/referral/routes.py
from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required, current_user
from models import db, OutreachContact, Subscription
from .helpers import search_contacts, generate_messages, _cooldown_blocked

referral_bp = Blueprint("referral", __name__)  # use app-level templates/ folder

def _is_pro() -> bool:
    if not current_user or not current_user.is_authenticated:
        return False
    if current_user.plan and str(current_user.plan).lower().startswith("pro"):
        return True
    sub = Subscription.query.filter_by(user_id=current_user.id, status="active") \
                            .order_by(Subscription.current_period_end.desc()).first()
    return bool(sub)

@referral_bp.get("/")
@login_required
def page():
    # Renders templates/referral.html
    return render_template("referral.html")

@referral_bp.post("/find")
@login_required
def find_contacts():
    if not _is_pro():
        return jsonify({"error": "Pro required for contacts‑only referrals"}), 403

    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400

    company = (payload.get("company") or "").strip()
    role    = (payload.get("role") or "").strip()
    geo     = (payload.get("geo") or "").strip()

    if not company or not role:
        return jsonify({"error": "company and role are required"}), 400

    # Validate provider key early for clearer errors
    provider = current_app.config.get("PUBLIC_SEARCH_PROVIDER", "brave")
    key = current_app.config.get("PUBLIC_SEARCH_KEY", "")
    if not key:
        current_app.logger.error("PUBLIC_SEARCH_KEY is missing; provider=%s", provider)
        return jsonify({"error": "Search provider key not configured. Ask admin to set PUBLIC_SEARCH_KEY."}), 500

    try:
        items = search_contacts(current_user.id, company, role, geo)
    except Exception as e:
        current_app.logger.exception("search_contacts failed")
        return jsonify({"error": f"Search failed: {e}"}), 500

    cooldown_days = int(current_app.config.get("REFERRAL_CONTACT_COOLDOWN_DAYS", 14))
    results = []
    for c in items:
        try:
            if _cooldown_blocked(current_user.id, c.get("name",""), c.get("company",""), cooldown_days):
                continue
            msgs = generate_messages(c, company, role)
            results.append({"contact": c, "messages": msgs})
        except Exception:
            # Don’t break the whole batch if one contact fails message gen
            current_app.logger.exception("generate_messages failed for contact: %s", c)
            continue

    max_contacts = int(current_app.config.get("REFERRAL_MAX_CONTACTS", 25))
    clipped = results[:max_contacts]
    cached_flag = (len(results) < len(items))  # heuristic

    return jsonify({"results": clipped, "cached": cached_flag})

@referral_bp.post("/save")
@login_required
def save_selected():
    if not _is_pro():
        return jsonify({"error": "Pro required"}), 403

    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400

    selected = payload.get("selected") or []
    if not isinstance(selected, list) or not selected:
        return jsonify({"error": "selected must be a non-empty list"}), 400

    saved = 0
    try:
        for c in selected:
            oc = OutreachContact(
                user_id=current_user.id,
                name=(c.get("name","") or "")[:200],
                role=(c.get("title","") or "")[:200],
                company=(c.get("company","") or "")[:200],
                email=None,
                source=(c.get("source","manual") or "")[:100],
                notes="",
                public_url=(c.get("public_url","") or "")[:600],
                approx_location=(c.get("approx_location","") or "")[:200]
            )
            db.session.add(oc)
            saved += 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed saving OutreachContact rows")
        return jsonify({"error": "Failed to save contacts"}), 500

    return jsonify({"ok": True, "saved": saved})
