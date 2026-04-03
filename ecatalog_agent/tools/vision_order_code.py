"""
형번(주문 코드) 조합이 가능해 보이는 PDF 페이지를 이미지로 렌더링한 뒤
GPT 비전으로 모델 조합 가능 여부·동일 제조사 자료 여부를 판별한다.

모델명 텍스트 매칭과 달리, 표/도해 OCR이 필요한 경우에 사용한다.
사양 일부를 같은 응답에서 힌트로 받을 수 있다(후속 구조화 파이프라인과 분리).
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import fitz  # PyMuPDF

# 형번·형식 표가 있을 가능성이 높은 키워드 (페이지 스코어링)
_ORDER_CODE_PAGE_KEYWORDS = (
    "형번",
    "형식",
    "표시",
    "주문",
    "order code",
    "how to order",
    "model no",
    "품번",
    "기호",
    "notation",
    "option",
    "조합",
    "시리즈",
    "series",
    "부착",
    "스트로크",
    "stroke",
)

_SYSTEM_PROMPT = """당신은 산업 부품 카탈로그 검증 보조입니다.
이미지는 PDF 페이지를 렌더링한 것입니다. 표·도해·로고를 읽고 JSON만 출력하세요.
모르는 항목은 null을 사용합니다."""

_DRAWING_SYSTEM_PROMPT = """당신은 기계 도면(Engineering Drawing) 판독 전문가입니다.
이미지는 산업 부품 도면 PDF를 렌더링한 것입니다.
도면의 표제란(Title Block)과 사양 표(Specification Box)를 정확히 읽어 JSON만 출력하세요.
숫자·영문·단위·기호를 있는 그대로 읽어야 합니다. 모르는 항목은 null을 사용합니다."""

# 도면 형식 감지 키워드 (텍스트 레이어 또는 파일명)
_DRAWING_KEYWORDS = (
    "drawing no", "dwg no", "dwg. no", "drawing number",
    "title block", "tolerance", "drawn by", "checked by",
    "approved by", "scale", "revision", "sheet",
    "도면", "도번", "공차", "제도", "검도", "승인",
)


def is_drawing_document(
    pdf_path: str,
    pdf_text: str,
    *,
    pdf_filename: str = "",
) -> bool:
    """도면(Engineering Drawing) 형식 PDF 여부 판단.

    조건: 이미지 기반 + 페이지 수 적음 + 도면 키워드/파일명.
    """
    try:
        doc = fitz.open(pdf_path)
        n_pages = doc.page_count
        doc.close()
    except Exception:
        return False

    # 파일명에 도면 암시 문자열
    fname_lower = (pdf_filename or "").lower()
    fname_hints = any(k in fname_lower for k in ("dwg", "도면", "drawing", "assembly", "assy"))

    # 텍스트 레이어 키워드
    text_lower = (pdf_text or "").lower()
    kw_hits = sum(1 for k in _DRAWING_KEYWORDS if k in text_lower)

    # 판단: 페이지 적고 (파일명 암시 OR 키워드 2개 이상)
    return n_pages <= 4 and (fname_hints or kw_hits >= 2)


def _split_pdf_pages(full_text: str) -> list[str]:
    if not full_text.strip():
        return []
    parts = re.split(r"\n---PAGE---\n", full_text.strip())
    return [p.strip() for p in parts if p.strip()]


def score_page_for_order_code(page_text: str, extra_keywords: tuple[str, ...] = ()) -> int:
    t = page_text.lower()
    n = sum(1 for k in _ORDER_CODE_PAGE_KEYWORDS if k.lower() in t)
    n += sum(1 for k in extra_keywords if k and str(k).lower() in t)
    return n


def select_order_code_candidate_pages(
    full_text: str,
    *,
    max_pages: int = 4,
    min_score: int = 2,
    extra_keywords: tuple[str, ...] = (),
) -> list[int]:
    """텍스트 레이어 기준으로 형번 표 후보 페이지 인덱스(0부터)."""
    pages = _split_pdf_pages(full_text)
    if not pages:
        return []
    scored: list[tuple[int, int]] = [
        (i, score_page_for_order_code(p, extra_keywords)) for i, p in enumerate(pages)
    ]
    scored.sort(key=lambda x: (-x[1], x[0]))
    picked: list[int] = []
    for i, sc in scored:
        if sc >= min_score and i not in picked:
            picked.append(i)
        if len(picked) >= max_pages:
            break
    if not picked:
        # 점수 낮아도 상위 2페이지는 후보로 (표가 이미지-only일 수 있음)
        picked = [i for i, _ in sorted(enumerate(pages), key=lambda x: x[0])[: min(2, len(pages))]]
    return picked


def render_pdf_pages_png(pdf_path: str, page_indices: list[int], *, zoom: float = 1.5) -> list[tuple[int, bytes]]:
    """페이지를 PNG 바이트로 렌더링. (page_index, png_bytes)"""
    doc = fitz.open(pdf_path)
    out: list[tuple[int, bytes]] = []
    try:
        mat = fitz.Matrix(zoom, zoom)
        for pi in page_indices:
            if pi < 0 or pi >= doc.page_count:
                continue
            page = doc.load_page(pi)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            out.append((pi, pix.tobytes("png")))
    finally:
        doc.close()
    return out


def _png_to_data_url(png: bytes) -> str:
    b64 = base64.standard_b64encode(png).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def gpt_vision_order_code_and_maker(
    *,
    maker_name: str,
    model_name: str,
    page_images: list[tuple[int, bytes]],
    model: str | None = None,
) -> dict[str, Any]:
    """
    GPT 비전에 형번 후보 페이지 + (가능하면) 표지 쪽 이미지를 넣고 판별.

    Returns:
        dict with keys: ok, error?, parsed?, raw_text?, page_indices?
    """
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai 패키지가 설치되지 않았습니다. pip install openai"}

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY 환경 변수가 없습니다."}

    client = OpenAI(api_key=api_key)
    model_id = (model or os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")).strip()

    user_lines = [
        f"등록된 제조사(시스템): {maker_name or '(없음)'}",
        f"시스템 모델명(주문 코드): {model_name or '(없음)'}",
        "",
        "이미지는 동일 PDF에서 뽑은 페이지입니다. 일부는 형번·형식 표가 있을 것으로 선정했습니다.",
        "",
        "다음을 판단하여 JSON 한 개만 출력하세요:",
        "1) 이 자료가 위에 적은 제조사(또는 그 브랜드/계열사)에서 발행한 카탈로그·데이터시트로 보이는지.",
        "2) 표·도해를 근거로, 위 모델명이 형번 체계에 따라 조합·해석 가능한지(각 자리·옵션 규칙과 모순 없이).",
        "3) 이미지에 보이는 사양 항목(압력, 스트로크, 전압 등)을 짧게 key-value 배열로 추출(확실한 것만).",
        "",
        "출력 스키마:",
        '{"is_same_manufacturer_document": true|false|null,',
        ' "can_compose_model_from_order_tables": true|false|null,',
        ' "confidence": 0.0~1.0,',
        ' "visible_brand_or_company": string|null,',
        ' "spec_hints": [{"title": string, "value": string}],',
        ' "reason_ko": string}',
    ]
    user_text = "\n".join(user_lines)

    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for idx, (_, png) in enumerate(page_images):
        content.append({"type": "text", "text": f"[페이지 이미지 {idx + 1}]"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _png_to_data_url(png), "detail": "high"},
            }
        )

    try:
        resp = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            max_tokens=1200,
            temperature=0.2,
        )
        raw_text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    parsed = _parse_json_object(raw_text)
    if not parsed:
        return {"ok": False, "error": "JSON 파싱 실패", "raw_text": raw_text}

    return {
        "ok": True,
        "parsed": parsed,
        "raw_text": raw_text,
        "page_indices": [p[0] for p in page_images],
    }


def run_pdf_vision_validation(
    *,
    pdf_path: str,
    pdf_full_text: str,
    maker_name: str,
    model_name: str,
    include_first_page: bool = True,
    max_order_code_pages: int = 3,
    extra_order_code_keywords: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """
    1) 0페이지(표지·브랜드) + 형번 후보 페이지를 렌더링
    2) GPT 비전 호출
    """
    doc = fitz.open(pdf_path)
    n = doc.page_count
    doc.close()

    extras = tuple(str(x).strip() for x in (extra_order_code_keywords or ()) if str(x).strip())

    indices: list[int] = []
    if include_first_page and n > 0:
        indices.append(0)

    candidates = select_order_code_candidate_pages(
        pdf_full_text,
        max_pages=max_order_code_pages,
        min_score=2,
        extra_keywords=extras,
    )
    for c in candidates:
        if c not in indices and len(indices) < 1 + max_order_code_pages:
            indices.append(c)

    if not indices and n > 0:
        indices = [0]

    images = render_pdf_pages_png(pdf_path, indices)
    if not images:
        return {"ok": False, "error": "렌더링된 이미지가 없습니다."}

    vision = gpt_vision_order_code_and_maker(
        maker_name=maker_name,
        model_name=model_name,
        page_images=images,
    )
    vision["selected_page_indices"] = indices
    return vision


def maker_evidence_in_pdf_text(
    maker_name: str | None,
    pdf_text: str,
    *,
    extra_aliases: list[str] | None = None,
) -> bool:
    """텍스트 레이어에서 제조사명이 보이는지(간단 휴리스틱). extra_aliases는 설정 파일에서 추가."""
    if not maker_name or not pdf_text:
        return False
    from ecatalog_agent.utils.text_normalize import normalize_maker

    mn = normalize_maker(maker_name)
    low = pdf_text.lower()
    if mn and mn in low:
        return True
    raw = maker_name.strip().lower()
    if bool(raw) and raw in low:
        return True
    for alias in extra_aliases or []:
        a = str(alias).strip()
        if not a:
            continue
        an = normalize_maker(a)
        if an and an in low:
            return True
        if a.lower() in low:
            return True
    return False


def _gpt_vision_drawing(
    *,
    maker_name: str,
    model_name: str,
    page_images: list[tuple[int, bytes]],
    model: str | None = None,
) -> dict[str, Any]:
    """도면 전용 GPT Vision 호출.

    표제란(DRAWING NO., DRAWING NAME, 제조사 로고)과
    사양 표(SPECIFICATIONS / SPEC BOX)를 함께 추출한다.
    2회 호출: 1회차 전체 파악, 2회차 사양 표 재확인(정밀도 향상).
    """
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai 패키지 없음"}

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY 없음"}

    client = OpenAI(api_key=api_key)
    model_id = (model or os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")).strip()

    def _build_image_content(images: list[tuple[int, bytes]]) -> list[dict]:
        content: list[dict] = []
        for idx, (_, png) in enumerate(images):
            content.append({"type": "text", "text": f"[도면 페이지 {idx + 1}]"})
            content.append({
                "type": "image_url",
                "image_url": {"url": _png_to_data_url(png), "detail": "high"},
            })
        return content

    # ── 1차 호출: 표제란 + 사양 표 전체 파악 ──────────────────────────
    prompt_1 = "\n".join([
        f"시스템 등록 모델명: {model_name}",
        f"시스템 등록 제조사: {maker_name}",
        "",
        "이 도면 이미지에서 다음 항목을 찾아 JSON으로 출력하세요:",
        "",
        "1. 표제란(Title Block, 보통 우측 하단):",
        "   - drawing_no: DRAWING NO. 또는 DWG NO. 값 (정확히 읽을 것)",
        "   - drawing_name: DRAWING NAME 값",
        "   - maker_in_titleblock: 제조사/회사명 또는 로고 텍스트",
        "   - revision: REVISION 값",
        "   - scale: SCALE 값",
        "",
        "2. 사양 표(SPECIFICATIONS 또는 SPEC BOX):",
        "   - specs: 표에서 읽은 항목을 [{\"title\": ..., \"value\": ...}] 배열로",
        "   (예: BORE SIZE, STROKE, PRESSURE, FLUID, TEMPERATURE 등)",
        "",
        "3. 도면 판단:",
        "   - is_drawing: 이 문서가 도면 형식인지 (true/false)",
        "   - is_same_maker: 표제란의 제조사가 시스템 등록 제조사와 동일한지 (true/false/null)",
        "   - drawing_no_matches_model: drawing_no가 시스템 모델명과 일치 또는 포함관계인지 (true/false/null)",
        "   - reason_ko: 판단 근거 한 줄",
        "",
        "출력: JSON 한 개만. 없는 값은 null.",
    ])

    content_1: list[dict] = [{"type": "text", "text": prompt_1}]
    content_1.extend(_build_image_content(page_images))

    try:
        resp1 = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": _DRAWING_SYSTEM_PROMPT},
                {"role": "user", "content": content_1},
            ],
            max_tokens=1500,
            temperature=0.1,
        )
        raw1 = (resp1.choices[0].message.content or "").strip()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    parsed1 = _parse_json_object(raw1)
    if not parsed1:
        return {"ok": False, "error": "1차 JSON 파싱 실패", "raw_text": raw1}

    # ── 2차 호출: 사양 표 정밀 재확인 ─────────────────────────────────
    existing_specs = parsed1.get("specs") or []
    prompt_2 = "\n".join([
        "위 도면의 SPECIFICATIONS 표를 다시 한 번 꼼꼼히 읽어주세요.",
        "1차에서 추출한 사양 목록:",
        json.dumps(existing_specs, ensure_ascii=False),
        "",
        "누락되거나 잘못 읽은 항목이 있으면 수정하고,",
        "확실히 읽을 수 있는 항목을 [{\"title\": ..., \"value\": ...}] 배열로 다시 출력하세요.",
        "단위(mm, MPa, °C 등)도 value에 포함하세요.",
        "",
        "출력: {\"specs\": [...]} JSON 한 개만.",
    ])

    content_2: list[dict] = [{"type": "text", "text": prompt_2}]
    content_2.extend(_build_image_content(page_images))

    try:
        resp2 = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": _DRAWING_SYSTEM_PROMPT},
                {"role": "user", "content": content_1},
                {"role": "assistant", "content": raw1},
                {"role": "user", "content": content_2},
            ],
            max_tokens=1000,
            temperature=0.1,
        )
        raw2 = (resp2.choices[0].message.content or "").strip()
    except Exception:
        raw2 = None

    parsed2 = _parse_json_object(raw2) if raw2 else None
    if parsed2 and parsed2.get("specs"):
        parsed1["specs"] = parsed2["specs"]

    return {
        "ok": True,
        "parsed": parsed1,
        "raw_text": raw1,
        "raw_text_2nd": raw2,
        "page_indices": [p[0] for p in page_images],
    }


def run_drawing_validation(
    *,
    pdf_path: str,
    maker_name: str,
    model_name: str,
    zoom: float = 2.0,
) -> dict[str, Any]:
    """도면 PDF 전용 검증.

    - 전체 페이지를 고해상도 렌더링 (도면은 1~4페이지)
    - GPT Vision 2-pass로 표제란 + 사양 표 추출
    - drawing_no vs model_name 일치 여부 반환

    Returns dict with keys:
        ok, drawing_no, drawing_name, maker_in_titleblock,
        drawing_no_matches_model, is_same_maker, specs, reason_ko,
        raw_parsed, error?
    """
    try:
        doc = fitz.open(pdf_path)
        n_pages = doc.page_count
        doc.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    indices = list(range(n_pages))
    images = render_pdf_pages_png(pdf_path, indices, zoom=zoom)
    if not images:
        return {"ok": False, "error": "렌더링 실패"}

    result = _gpt_vision_drawing(
        maker_name=maker_name,
        model_name=model_name,
        page_images=images,
    )
    if not result.get("ok"):
        return result

    p = result.get("parsed") or {}
    return {
        "ok": True,
        "drawing_no": p.get("drawing_no"),
        "drawing_name": p.get("drawing_name"),
        "maker_in_titleblock": p.get("maker_in_titleblock"),
        "revision": p.get("revision"),
        "scale": p.get("scale"),
        "drawing_no_matches_model": p.get("drawing_no_matches_model"),
        "is_same_maker": p.get("is_same_maker"),
        "specs": p.get("specs") or [],
        "reason_ko": p.get("reason_ko"),
        "raw_parsed": p,
        "page_indices": result.get("page_indices", []),
    }
