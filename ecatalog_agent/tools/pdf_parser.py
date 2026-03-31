from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


def pdf_parse(pdf_path: str, use_ocr: bool = False, max_pages: int = 30) -> dict:
    """
    Parse a PDF and return extracted text.

    MVP notes:
    - OCR is not enabled by default (use_ocr is kept for spec compatibility).
    - If extracted text looks empty, we mark it as image-based and still return page renders.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    doc = fitz.open(str(path))
    pages_total = doc.page_count
    pages_to_read = min(pages_total, max_pages)

    texts: list[str] = []
    for i in range(pages_to_read):
        page = doc.load_page(i)
        txt = page.get_text("text") or ""
        texts.append(f"---PAGE---\n{txt}".strip())

    full_text = "\n".join(texts).strip()
    is_image_based = len(full_text) < 50  # heuristic for MVP

    images: list[bytes] = []
    # Render only first 3 pages for lightweight evidence.
    render_pages = min(pages_total, 3)
    for i in range(render_pages):
        page = doc.load_page(i)
        pix = page.get_pixmap(alpha=False)
        images.append(pix.tobytes("png"))

    return {
        "text": full_text,
        "pages": pages_total,
        "is_image_based": is_image_based,
        "images": images,
        "use_ocr": bool(use_ocr),
    }

