import csv, io
from flask import Blueprint, request, render_template_string
from flask_login import login_required, current_user

referral_bp = Blueprint("referral", __name__)

HTML = """
{% extends "base.html" %}
{% block content %}
<a href="/dashboard" class="underline text-sm">← Back</a>
<h2 class="text-3xl font-bold mb-4">Referral Builder</h2>
{% if rows %}
  <p class="opacity-80 mb-2">Messages generated for {{ rows|length }} contacts.</p>
  <div class="space-y-3">
  {% for r in rows %}
    <div class="glass p-4 card">
      <div class="font-bold">{{ r.name }} — {{ r.role }} at {{ r.company }}</div>
      <div class="text-sm opacity-80">{{ r.email }}</div>
      <h4 class="font-bold mt-2">Warm Intro</h4><pre class="text-sm whitespace-pre-wrap">{{ r.msg_warm }}</pre>
      <h4 class="font-bold mt-2">Cold Connect</h4><pre class="text-sm whitespace-pre-wrap">{{ r.msg_cold }}</pre>
      <h4 class="font-bold mt-2">Follow‑up</h4><pre class="text-sm whitespace-pre-wrap">{{ r.msg_follow }}</pre>
    </div>
  {% endfor %}
  </div>
{% else %}
  <p>No rows parsed.</p>
{% endif %}
{% endblock %}
"""

def make_messages(name, role, company):
    warm = f"Hi {name}, a mutual connection suggested I reach out. I'm exploring {role} opportunities at {company}. Could I ask 2 quick questions about your team?"
    cold = f"Hi {name} — I'm a student focused on {role}. I built a small project relevant to {company}. Would you be open to a brief chat this week?"
    follow = f"Hi {name}, looping back in case you had a minute. I’m excited about {role} at {company}. Happy to share concise bullets or a portfolio link."
    return warm, cold, follow

@referral_bp.route("/upload", methods=["POST"])
@login_required
def upload():
    file = request.files.get("file")
    rows=[]
    if file:
        data = file.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(data))
        for r in reader:
            name = r.get("name","").strip() or "Contact"
            role = r.get("role","").strip() or "Role"
            company = r.get("company","").strip() or "Company"
            email = r.get("email","").strip()
            warm,cold,follow = make_messages(name, role, company)
            rows.append({"name":name,"role":role,"company":company,"email":email,"msg_warm":warm,"msg_cold":cold,"msg_follow":follow})
    return render_template_string(HTML, rows=rows)
