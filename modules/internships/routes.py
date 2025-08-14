from flask import Blueprint, request, render_template_string
from flask_login import login_required, current_user
from models import db, InternshipRecord
from .helpers import mock_fetch, compute_learning_links

internships_bp = Blueprint("internships", __name__)

HTML = """
{% extends "base.html" %}
{% block content %}
<a href="/dashboard" class="underline text-sm">← Back</a>
<h2 class="text-3xl font-bold mb-4">Internship Finder</h2>
<p class="opacity-80">Showing {{ jobs|length }} results for <strong>{{ role }}</strong> in {{ location or 'anywhere' }}</p>
<div class="space-y-3">
  {% for j in jobs %}
    <div class="glass p-4 card">
      <div class="flex justify-between">
        <div>
          <div class="font-bold">{{ j.title }} — {{ j.company }}</div>
          <div class="text-sm opacity-80">Source: {{ j.source }}</div>
          <div class="text-sm">Match: {{ j.match_score }}%</div>
          <div class="text-sm">Gaps: {{ ", ".join(j.missing_skills) }}</div>
          <div class="text-sm">Learn: {% for l in j.learning_links %}<a class="underline" target="_blank" href="{{ l.link }}">{{ l.skill }}</a>{% if not loop.last %}, {% endif %}{% endfor %}</div>
        </div>
        <div><a class="underline" href="{{ j.link }}" target="_blank">View</a></div>
      </div>
    </div>
  {% endfor %}
</div>
{% endblock %}
"""

@internships_bp.route("/search")
@login_required
def search():
    role = request.args.get("role","Intern")
    location = request.args.get("location","")
    jobs = mock_fetch(role, location)
    for j in jobs:
        j["learning_links"] = compute_learning_links(j["missing_skills"])
        rec = InternshipRecord(user_id=current_user.id, role=role, location=location, source=j["source"], title=j["title"], company=j["company"], link=j["link"], match_score=j["match_score"], missing_skills=",".join(j["missing_skills"]))
        db.session.add(rec)
    db.session.commit()
    return render_template_string(HTML, jobs=jobs, role=role, location=location)
