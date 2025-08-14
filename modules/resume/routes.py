import os, io
from flask import Blueprint, request, jsonify, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import db, ResumeAsset, Subscription
from PyPDF2 import PdfReader
from docx import Document

resume_bp = Blueprint("resume", __name__)

ALLOWED = {"pdf", "docx", "txt"}

def _is_pro(user_id) -> bool:
    from models import Subscription
    sub = Subscription.query.filter_by(user_id=user_id).first()
    return bool(sub and sub.status == "active")

def _extract_text(file_storage) -> tuple[str, str]:
    filename = secure_filename(file_storage.filename or "")
    ext = (filename.rsplit(".", 1)[-1] or "").lower()
    if ext not in ALLOWED:
        abort(400, description="Unsupported file type.")
    mime = file_storage.mimetype or "application/octet-stream"
    data = file_storage.read()

    if ext == "pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join([page.extract_text() or "" for page in reader.pages])
        except Exception:
            text = ""
    elif ext == "docx":
        try:
            doc = Document(io.BytesIO(data))
            text = "\n".join([p.text for p in doc.paragraphs])
        except Exception:
            text = ""
    else:  # txt
        try:
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            text = ""
    return text.strip(), mime

@resume_bp.route("/upload", methods=["POST"])
@login_required
def upload():
    """
    POST /resume/upload
    form-data:
      - file: (pdf/docx/txt)
      - persist: '1' to save (Pro only)
    """
    if "file" not in request.files:
        abort(400, description="No file part")
    f = request.files["file"]
    if not f or not f.filename:
        abort(400, description="No selected file")

    text, mime = _extract_text(f)
    if not text:
        abort(400, description="Could not extract text")

    persist = request.form.get("persist") == "1"
    saved_id = None
    if persist and _is_pro(current_user.id):
        ra = ResumeAsset(
            user_id=current_user.id,
            filename=secure_filename(f.filename),
            mime=mime,
            content_text=text,
            persisted=True,
        )
        db.session.add(ra); db.session.commit()
        saved_id = ra.id

    return jsonify({"ok": True, "text": text, "saved_id": saved_id})

@resume_bp.route("/last", methods=["GET"])
@login_required
def last():
    ra = ResumeAsset.query.filter_by(user_id=current_user.id, persisted=True)\
        .order_by(ResumeAsset.created_at.desc()).first()
    if not ra:
        return jsonify({"ok": False, "reason": "none"}), 404
    # return a truncated preview and full text
    preview = (ra.content_text[:400] + "…") if len(ra.content_text) > 400 else ra.content_text
    return jsonify({"ok": True, "id": ra.id, "preview": preview, "text": ra.content_text})
