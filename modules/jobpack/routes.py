import os
from flask import Blueprint, request, render_template
from .helpers import (
    scrape_job_posting,
    coverage_score,
    generate_jd_summary,
    tailor_resume_bullets,
    generate_cover_letter,
    find_missing_skills,
    sanitize,
)

bp = Blueprint("jobpack", __name__)

@bp.post("/generate")
def generate():
    job_url = sanitize(request.form.get("job_url"))
    job_text = sanitize(request.form.get("job_text"))
    resume_text = sanitize(request.form.get("resume_text"))

    if not job_text and job_url:
        job_text = scrape_job_posting(job_url)

    if not job_text:
        return render_template(
            "dashboard.html",
            active_tab="jobpack",
            error="No job text found. Paste the job description or use a non-LinkedIn URL."
        )

    if not resume_text:
        return render_template(
            "dashboard.html",
            active_tab="jobpack",
            error="Please paste your resume text."
        )

    jd_summary = generate_jd_summary(job_text)
    ats = coverage_score(job_text, resume_text)
    bullets = tailor_resume_bullets(job_text, resume_text)
    cover_letter = generate_cover_letter(job_text, resume_text)
    missing = find_missing_skills(job_text, resume_text)

    result = {
        "jd_summary": jd_summary,
        "ats_score": {"score": ats["score"], "explain": ats["explain"]},
        "tailored_bullets": bullets,
        "cover_letter": cover_letter,
        "missing_skills": missing
    }

    return render_template(
        "dashboard.html",
        active_tab="jobpack",
        jobpack_result=result
    )
