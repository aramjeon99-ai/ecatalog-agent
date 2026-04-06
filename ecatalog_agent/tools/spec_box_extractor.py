"""
도면(Engineering Drawing) PDF에서 SPECIFICATIONS 박스를 찾아
key-value 형태로 파싱한다.

전략:
  1. 페이지에서 'SPECIFICATIONS' 텍스트 블록 위치를 찾는다
  2. 해당 영역(좌하단 ~40% 영역)만 crop해 GPT Vision 집중 호출
  3. 표의 각 행을 spec_name / spec_value로 파싱
  4. 단위(mm, MPa, °C 등) 보존, 불확실 항목 낮은 confidence 표시
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import fitz  # PyMuPDF


# ── 상수 ─────────────────────────────────────────────────────────────────────

_SPEC_BOX_TITLES = (
    "SPECIFICATIONS",
    "SPECIFICATION",
    "SPEC.",
    "SPECS",
    "사양",
    "사 양",
)

_SPEC_BOX_SYSTEM = (
    "당신은 기계 도면의 사양 표(Specifications Box) 판독 전문가입니다. "
    "이미지에서 SPECIFICATIONS 표를 찾아 각 행의 항목명과 값을 정확히 읽어야 합니다. "
    "단위(mm, MPa, °C, mm/s 등)를 값에 포함시켜 보존하세요. "
    "JSON만 출력하세요."
)


# ── 텍스트 레이어에서 SPEC BOX 위치 탐지 ─────────────────────────────────────

def _find_specbox_rect(pdf_path: str, page_idx: int = 0) -> fitz.Rect | None:
    """텍스트 레이어에서 SPECIFICATIONS 블록 위치를 찾는다."""
    try:
        doc = fitz.open(pdf_path)
        if page_idx >= doc.page_count:
            page_idx = doc.page_count - 1
        page = doc.load_page(page_idx)

        # 텍스트 블록 검색
        blocks = page.get_text("blocks")  # [(x0,y0,x1,y1,text,block_no,block_type)]
        for b in blocks:
            text = b[4].strip().upper()
            if any(title in text for title in _SPEC_BOX_TITLES):
                # SPEC 제목 블록 발견 → 그 아래 영역을 spec box로 간주
                bx0, by0, bx1, by1 = b[0], b[1], b[2], b[3]
                page_rect = page.rect
                # 제목 블록 아래~페이지 하단 50%, 같은 x 범위 확장
                spec_rect = fitz.Rect(
                    max(0, bx0 - 10),
                    by0,
                    min(page_rect.width, bx1 + 200),
                    page_rect.y1 * 0.85,
                )
                doc.close()
                return spec_rect
        doc.close()
    except Exception:
        pass
    return None


def _crop_specbox_png(
    pdf_path: str,
    page_idx: int = 0,
    zoom: float = 3.0,
) -> tuple[bytes | None, str]:
    """
    SPEC BOX 영역을 PNG로 렌더링한다.
    텍스트 레이어로 위치를 찾지 못하면 좌하단 40% 영역을 사용한다.

    Returns: (png_bytes, method)
    """
    try:
        doc = fitz.open(pdf_path)
        if page_idx >= doc.page_count:
            page_idx = doc.page_count - 1
        page = doc.load_page(page_idx)
        rect = page.rect
        mat = fitz.Matrix(zoom, zoom)
        doc.close()
    except Exception:
        return None, "error"

    # 1단계: 텍스트 레이어로 위치 탐지
    spec_rect = _find_specbox_rect(pdf_path, page_idx)
    method = "text_detected"

    if spec_rect is None:
        # 폴백: 좌하단 40% × 40% (SPEC BOX는 보통 좌하단)
        w, h = rect.width, rect.height
        spec_rect = fitz.Rect(rect.x0, rect.y0 + h * 0.60, rect.x0 + w * 0.55, rect.y1)
        method = "fallback_bottomleft"

    try:
        doc = fitz.open(pdf_path)
        page = doc.load_page(page_idx)
        pix = page.get_pixmap(matrix=mat, clip=spec_rect, alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes, method
    except Exception:
        return None, "error"


# ── GPT Vision 집중 호출 ──────────────────────────────────────────────────────

def _gpt_parse_specbox(
    image_png: bytes,
    *,
    model_id: str = "gpt-4o",
) -> dict[str, Any]:
    """GPT Vision으로 SPEC BOX를 key-value 파싱한다."""
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai 패키지 없음"}

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY 없음"}

    client = OpenAI(api_key=api_key)
    b64 = base64.standard_b64encode(image_png).decode("ascii")

    prompt = "\n".join([
        "이 이미지는 도면의 SPECIFICATIONS(사양) 표 영역입니다.",
        "",
        "표에서 각 행의 항목명(왼쪽)과 값(오른쪽)을 읽어 JSON으로 출력하세요.",
        "",
        "규칙:",
        "- spec_box_title: 표 상단의 제목 (예: 'SPECIFICATIONS (CYLINDER)')",
        "- fields: 각 행의 {name, value, confidence} 배열",
        "  - name: 항목명 (예: BORE SIZE, STROKE, PRESSURE 등)",
        "  - value: 값과 단위 포함 (예: '160 mm', '0.97 MPa', '0 ~ 70 °C')",
        "  - confidence: 읽기 확실도 0.0~1.0 (흐리거나 불명확하면 낮게)",
        "- 표가 없거나 읽을 수 없으면 fields를 빈 배열로",
        "",
        '출력: {"spec_box_title": string|null, "fields": [{name, value, confidence}]}',
    ])

    content: list[dict] = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}},
    ]

    _FALLBACK = "gpt-4o-mini"
    raw = None
    for _mid in [model_id, _FALLBACK]:
        try:
            resp = client.chat.completions.create(
                model=_mid,
                messages=[
                    {"role": "system", "content": _SPEC_BOX_SYSTEM},
                    {"role": "user", "content": content},
                ],
                max_tokens=800,
                temperature=0.0,
            )
            raw = (resp.choices[0].message.content or "").strip()
            break
        except Exception as e:
            err = str(e)
            if "403" in err or "model_not_found" in err or "model" in err.lower():
                continue
            return {"ok": False, "error": err}

    if raw is None:
        return {"ok": False, "error": f"모델 접근 실패 ({model_id})"}

    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {"ok": False, "error": "JSON 파싱 실패", "raw": raw}
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": False, "error": "JSON 파싱 실패", "raw": raw}

    return {"ok": True, "parsed": parsed}


# ── 2-pass: 재확인 호출 ───────────────────────────────────────────────────────

def _gpt_recheck_specbox(
    image_png: bytes,
    first_result: list[dict],
    *,
    model_id: str = "gpt-4o-mini",
) -> list[dict]:
    """1차 결과를 기반으로 누락·오류 항목을 재확인한다."""
    try:
        from openai import OpenAI
    except ImportError:
        return first_result

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return first_result

    client = OpenAI(api_key=api_key)
    b64 = base64.standard_b64encode(image_png).decode("ascii")

    first_summary = json.dumps(first_result, ensure_ascii=False)
    prompt = "\n".join([
        "아래는 SPECIFICATIONS 표에서 1차로 읽은 항목 목록입니다:",
        first_summary,
        "",
        "같은 이미지를 다시 꼼꼼히 읽어 누락되거나 잘못 읽은 항목을 수정하세요.",
        "단위(mm, MPa, °C, mm/s 등)를 값에 반드시 포함하세요.",
        "",
        '출력: {"fields": [{name, value, confidence}]} — JSON 한 개만',
    ])

    content: list[dict] = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}},
    ]

    try:
        resp = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": _SPEC_BOX_SYSTEM},
                {"role": "user", "content": content},
            ],
            max_tokens=600,
            temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            parsed = json.loads(m.group(0))
            fields = parsed.get("fields") or []
            if fields:
                return fields
    except Exception:
        pass
    return first_result


# ── 공개 엔트리포인트 ─────────────────────────────────────────────────────────

def extract_spec_box(
    pdf_path: str,
    *,
    page_idx: int = 0,
    crop_zoom: float = 3.0,
    two_pass: bool = True,
) -> dict[str, Any]:
    """
    도면 PDF에서 SPECIFICATIONS 박스를 추출한다.

    Returns:
        {
            "spec_box_title": str | None,
            "fields": [
                {"name": str, "value": str, "confidence": float}
            ],
            "crop_method": str,   # "text_detected" | "fallback_bottomleft"
            "ok": bool,
            "error": str,         # ok=False 일 때만
        }
    """
    # 이미지 생성
    crop_png, crop_method = _crop_specbox_png(pdf_path, page_idx, zoom=crop_zoom)
    if crop_png is None:
        return {"ok": False, "error": "이미지 렌더링 실패", "fields": []}

    # 1차 GPT Vision
    result = _gpt_parse_specbox(crop_png)
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error", "GPT 오류"),
            "fields": [],
            "crop_method": crop_method,
        }

    p = result["parsed"]
    title = p.get("spec_box_title")
    fields = p.get("fields") or []

    # 2차 재확인
    if two_pass and fields:
        fields = _gpt_recheck_specbox(crop_png, fields)

    return {
        "ok": True,
        "spec_box_title": title,
        "fields": fields,
        "crop_method": crop_method,
    }
