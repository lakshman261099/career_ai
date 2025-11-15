# modules/resume/utils.py

from io import BytesIO
from typing import Optional

from pypdf import PdfReader  # make sure pypdf is in requirements.txt


def extract_text_from_pdf(file_storage) -> Optional[str]:
    """
    Accepts a werkzeug FileStorage (from request.files['file']),
    returns extracted text or None on failure.
    """
    try:
        file_data = file_storage.read()
        if not file_data:
            return None

        reader = PdfReader(BytesIO(file_data))
        texts = []
        for page in reader.pages:
            try:
                page_text = page.extract_text() or ""
                texts.append(page_text)
            except Exception:
                # Skip pages that fail to parse
                continue

        text = "\n".join(t for t in texts if t)
        # Reset the stream pointer in case the caller needs to reuse it
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass

        return text.strip() or None
    except Exception:
        return None
