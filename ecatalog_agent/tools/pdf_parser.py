from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF


def _extract_rows_text(page) -> str:
    """
    blocks 기반으로 페이지 텍스트를 행 단위로 재구성한다.

    PyMuPDF get_text("text") 는 텍스트 블록을 위에서 아래 순서로 나열하지만,
    표(Table) 셀은 좌우 인접 블록이 분리되어 모델코드가 잘려 추출될 수 있다.
    이 함수는 y0 좌표가 유사한 블록을 같은 행으로 묶어 좌→우 순으로 이어붙임으로써
    같은 행에 있는 셀 텍스트를 하나의 문자열로 연결한다.

    예: "CDUK25 | 30D | A93L" 형태의 표가 있을 때
        "text" 추출: "CDUK25\n30D\nA93L"
        row 추출:    "CDUK25 30D A93L"  ← 모델코드 연결 가능
    """
    try:
        blocks = page.get_text("blocks")
        if not blocks:
            return ""
        # block_type == 0: 텍스트 블록
        text_blocks: list[tuple[float, float, float, float, str]] = []
        for entry in blocks:
            if len(entry) < 7:
                continue
            x0, y0, x1, y1, text, _bno, btype = entry[:7]
            if int(btype) == 0 and str(text).strip():
                text_blocks.append((float(x0), float(y0), float(x1), float(y1), str(text)))

        if not text_blocks:
            return ""

        # y0 기준 정렬
        text_blocks.sort(key=lambda b: (b[1], b[0]))

        # 같은 y0(±threshold) 블록을 동일 행으로 그룹핑
        ROW_THRESHOLD = 8.0
        rows: list[list[tuple]] = []
        for block in text_blocks:
            y = block[1]
            placed = False
            for row in rows:
                if abs(y - row[0][1]) <= ROW_THRESHOLD:
                    row.append(block)
                    placed = True
                    break
            if not placed:
                rows.append([block])

        # 각 행을 x0 순으로 정렬 후 텍스트 이어붙임
        row_texts: list[str] = []
        for row in sorted(rows, key=lambda r: r[0][1]):
            sorted_row = sorted(row, key=lambda b: b[0])
            row_text = " ".join(
                b[4].strip().replace("\n", " ") for b in sorted_row if b[4].strip()
            )
            if row_text.strip():
                row_texts.append(row_text.strip())

        return "\n".join(row_texts)
    except Exception:
        return ""


def pdf_parse(pdf_path: str, use_ocr: bool = False, max_pages: int = 30) -> dict:
    """
    Parse a PDF and return extracted text.

    반환 필드
    ---------
    text         : get_text("text") 기반 전체 텍스트 (---PAGE--- 구분자 포함)
    text_rows    : blocks 행단위 재구성 텍스트 — 표 셀 연결에 유리
    pages        : 전체 페이지 수
    is_image_based: 텍스트 추출량이 극히 적으면 True
    images       : 앞 3페이지 PNG bytes
    use_ocr      : OCR 활성화 여부 (현재 MVP 미지원)
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    doc = fitz.open(str(path))
    pages_total = doc.page_count
    pages_to_read = min(pages_total, max_pages)

    texts: list[str] = []
    rows_texts: list[str] = []

    for i in range(pages_to_read):
        page = doc.load_page(i)

        # ① 기본 텍스트 추출
        txt = page.get_text("text") or ""
        texts.append(f"---PAGE---\n{txt}".strip())

        # ② blocks 기반 행단위 재구성 (표 셀 이어붙임)
        rows_txt = _extract_rows_text(page)
        if rows_txt:
            rows_texts.append(f"---PAGE---\n{rows_txt}".strip())

    full_text = "\n".join(texts).strip()
    full_text_rows = "\n".join(rows_texts).strip()

    is_image_based = len(full_text) < 50  # heuristic for MVP

    images: list[bytes] = []
    render_pages = min(pages_total, 3)
    for i in range(render_pages):
        page = doc.load_page(i)
        pix = page.get_pixmap(alpha=False)
        images.append(pix.tobytes("png"))

    return {
        "text": full_text,
        "text_rows": full_text_rows,
        "pages": pages_total,
        "is_image_based": is_image_based,
        "images": images,
        "use_ocr": bool(use_ocr),
    }
