# modules/referral/routes.py
import csv, io
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, OutreachContact

referral_bp = Blueprint("referral", __name__)

@referral_bp.route("", methods=["GET"])
@login_required
def index():
    # Render the advanced UI page we created earlier
    return render_template("referral_index.html")

@referral_bp.route("/upload", methods=["POST"])
@login_required
def upload():
    file = request.files.get("file")
    mode = (request.form.get("mode") or request.args.get("mode") or "fast").strip().lower()
    if not file or not file.filename.endswith(".csv"):
        flash("Please upload a CSV with headers: name,role,company,email", "error")
        return redirect(url_for("referral.index"))

    content = file.read().decode("utf-8", errors="ignore")
    rdr = csv.DictReader(io.StringIO(content))
    rows = []
    for row in rdr:
        name = (row.get("name") or "").strip()
        email = (row.get("email") or "").strip()
        role = (row.get("role") or "").strip()
        company = (row.get("company") or "").strip()
        if not name or not email:
            # Skip incomplete rows
            continue

        # Persist a simple contact record (optional)
        contact = OutreachContact(user_id=current_user.id, name=name, email=email, role=role, company=company, source="csv")
        db.session.add(contact)

        # Messages (mock/fast)
        msg_warm = f"Hi {name.split()[0]}, hope you’re well! I’m exploring {role} opportunities at {company}..."
        msg_cold = f"Hey {name.split()[0]}, I admire the work at {company}. I’m targeting {role} intern roles..."
        msg_follow = f"Hi {name.split()[0]}, just following up on my note about {role} at {company}..."

        rec = {
            "name": name, "email": email, "role": role, "company": company,
            "msg_warm": msg_warm, "msg_cold": msg_cold, "msg_follow": msg_follow
        }

        # Deep mode adds a simple cadence list
        if mode == "deep":
            rec["cadence"] = [
                "Day 0: connect with note",
                "Day 2: share mini‑project link",
                "Day 5: thoughtful follow‑up",
                "Day 10: gentle nudge with a new insight"
            ]
        rows.append(rec)

    db.session.commit()
    # Re-render the advanced UI template with results
    return render_template("referral_index.html", rows=rows)
