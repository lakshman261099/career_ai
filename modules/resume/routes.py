from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import io
from models import db, ResumeAsset, Subscription
from PyPDF2 import PdfReader
import docx

resume_bp = Blueprint("resume", __name__)
ALLOWED = {"pdf","docx","txt"}

def _is_pro():
    if not current_user or not current_user.is_authenticated: return False
    if current_user.plan and current_user.plan.lower().startswith("pro"): return True
    sub = Subscription.query.filter_by(user_id=current_user.id, status="active").first()
    return bool(sub)

def _extract_text(file_storage):
    fname = secure_filename(file_storage.filename or "upload")
    ext = fname.rsplit(".",1)[-1].lower()
    data = file_storage.read()
    if ext == "pdf":
        reader = PdfReader(io.BytesIO(data))
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\n".join(pages).strip(), "application/pdf", fname
    elif ext == "docx":
        d = docx.Document(io.BytesIO(data))
        return "\n".join([p.text for p in d.paragraphs]).strip(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", fname
    elif ext == "txt":
        return data.decode("utf-8", errors="ignore"), "text/plain", fname
    else:
        raise ValueError("Unsupported file type")

@resume_bp.post("/upload")
@login_required
def upload_resume():
    if "file" not in request.files:
        return jsonify({"error":"file missing"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error":"filename missing"}), 400
    ext = f.filename.rsplit(".",1)[-1].lower()
    if ext not in ALLOWED:
        return jsonify({"error":"Only PDF/DOCX/TXT allowed"}), 400
    try:
        text, mime, fname = _extract_text(f)
    except Exception as e:
        return jsonify({"error": f"Parse failed: {e}"}), 400

    persisted_id = None
    if (request.form.get("persist") == "true" or request.args.get("persist") == "true") and _is_pro():
        asset = ResumeAsset(user_id=current_user.id, filename=fname, mime=mime, content_text=text, persisted=True)
        db.session.add(asset); db.session.commit()
        persisted_id = asset.id

    return jsonify({"ok": True, "filename": fname, "mime": mime, "chars": len(text), "text": text[:40000], "persisted_id": persisted_id})

@resume_bp.post("/delete")
@login_required
def delete_resume():
    rid = request.form.get("id") or request.args.get("id")
    if not rid: return jsonify({"error":"id required"}), 400
    asset = ResumeAsset.query.filter_by(id=int(rid), user_id=current_user.id).first()
    if not asset: return jsonify({"error":"not found"}), 404
    db.session.delete(asset); db.session.commit()
    return jsonify({"ok": True})
