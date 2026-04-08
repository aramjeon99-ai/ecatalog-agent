"""
도면(Engineering Drawing) PDF에서 DWG NO를 고정밀로 추출한다.

전략:
  1. 페이지 우하단 25% 영역만 crop (표제란 위치)
  2. crop 이미지에 GPT Vision 집중 호출 (DWG NO 전용 프롬프트)
  3. 텍스트 레이어가 있으면 regex 패턴으로도 추출
  4. O/0, I/1 등 OCR 혼동 문자 보정
  5. 여러 후보 생성 → 패턴 점수 + 모델명 유사도로 최종 선택
  6. confidence score 반환
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import fitz  # PyMuPDF


# ── 상수 ─────────────────────────────────────────────────────────────────────

# DWG NO 패턴: 영문+숫자 세그먼트 2개 이상, 하이픈 구분
_DWG_PATTERN = re.compile(
    r'\b([A-Z0-9]{2,}(?:-[A-Z0-9]{2,}){1,8})\b',
    re.IGNORECASE,
)

# DWG NO 앞에 나타나는 레이블 키워드
_DWG_LABEL_PATTERN = re.compile(
    r'(?:DWG\.?\s*NO\.?|DRAWING\s*NO\.?|DRAWING\s*NUMBER|도면\s*번호|도번)[:\s]*([A-Z0-9][A-Z0-9\-]{3,40})',
    re.IGNORECASE,
)

# 제외 패턴 (날짜, 단순 숫자, 단위 등)
_EXCLUDE_PATTERNS = (
    re.compile(r'^\d{4}-\d{2}-\d{2}$'),         # 날짜
    re.compile(r'^[0-9\-\.]+$'),                  # 숫자·점만
    re.compile(r'^[A-Z]{1,2}-[0-9]{1,3}$'),      # 너무 짧은 코드 (A-1 등)
    re.compile(r'^REV[A-Z0-9\-]*$', re.I),        # REV 레이블
    re.compile(r'^SHEET\b', re.I),
)

# 문자 혼동 보정 테이블 (OCR 오인식)
# key: 잘못 읽힌 문자, value: 올바른 문자 (컨텍스트 없이 적용)
_CHAR_CONFUSION: dict[str, str] = {}  # 적극적 보정은 GPT에 위임, 여기선 보수적으로

# 파트넘버에서 세그먼트 길이 기대치
_MIN_SEGMENT_LEN = 2
_MIN_TOTAL_LEN = 6
_MAX_TOTAL_LEN = 50


# ── 이미지 처리 ───────────────────────────────────────────────────────────────

def _crop_titleblock_png(
    pdf_path: str,
    page_idx: int = 0,
    zoom: float = 3.0,
) -> bytes | None:
    """페이지 우하단 타이틀블록 영역을 고해상도 PNG로 렌더링한다.

    타이틀블록은 도면 우하단에 위치한다.
    - x: 55% ~ 100% (우측 45%)
    - y: 70% ~ 100% (하단 30%)
    """
    try:
        doc = fitz.open(pdf_path)
        if page_idx >= doc.page_count:
            page_idx = doc.page_count - 1
        page = doc.load_page(page_idx)
        rect = page.rect

        w, h = rect.width, rect.height
        clip = fitz.Rect(rect.x0 + w * 0.55, rect.y0 + h * 0.70, rect.x1, rect.y1)

        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes
    except Exception:
        return None


def _full_page_png(
    pdf_path: str,
    page_idx: int = 0,
    zoom: float = 2.0,
) -> bytes | None:
    """페이지 전체를 PNG로 렌더링한다 (폴백용)."""
    try:
        doc = fitz.open(pdf_path)
        if page_idx >= doc.page_count:
            page_idx = doc.page_count - 1
        page = doc.load_page(page_idx)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes
    except Exception:
        return None


def _crop_right_edge_vertical_png(
    pdf_path: str,
    page_idx: int = 0,
    zoom: float = 3.0,
) -> bytes | None:
    """페이지 우측 가장자리 세로 표기 영역을 고해상도 PNG로 렌더링한다.

    도면에 따라 모델명/도번이 우측 가장자리(세로 텍스트)로 인쇄되는 케이스를 대응한다.
    - x: 88% ~ 100% (우측 12%)
    - y: 5% ~ 95% (상하 여백 제외)
    """
    try:
        doc = fitz.open(pdf_path)
        if page_idx >= doc.page_count:
            page_idx = doc.page_count - 1
        page = doc.load_page(page_idx)
        rect = page.rect
        w, h = rect.width, rect.height
        clip = fitz.Rect(rect.x0 + w * 0.88, rect.y0 + h * 0.05, rect.x1, rect.y0 + h * 0.95)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes
    except Exception:
        return None


# ── 텍스트 레이어 추출 ────────────────────────────────────────────────────────

def _extract_titleblock_text(pdf_path: str, page_idx: int = 0) -> str:
    """우하단 타이틀블록 영역의 텍스트 레이어를 추출한다."""
    try:
        doc = fitz.open(pdf_path)
        if page_idx >= doc.page_count:
            page_idx = doc.page_count - 1
        page = doc.load_page(page_idx)
        rect = page.rect
        w, h = rect.width, rect.height
        clip = fitz.Rect(rect.x0 + w * 0.55, rect.y0 + h * 0.70, rect.x1, rect.y1)
        text = page.get_text("text", clip=clip)
        doc.close()
        return text or ""
    except Exception:
        return ""


# ── OCR 혼동 문자 보정 ────────────────────────────────────────────────────────

def _apply_char_corrections(s: str) -> list[str]:
    """
    O/0, I/1/l, S/5, B/8 혼동 가능 위치에 대해 보정 후보를 생성한다.
    원본을 포함한 후보 목록을 반환한다 (최대 4개).
    """
    candidates = {s}

    # O↔0 보정: 숫자 자리에 O가 오거나, 영문자 자리에 0이 오는 경우
    def swap(text: str, old: str, new: str) -> str:
        return text.replace(old, new)

    variants = []
    if "O" in s:
        variants.append(swap(s, "O", "0"))
    if "0" in s:
        variants.append(swap(s, "0", "O"))
    if "I" in s:
        variants.append(swap(s, "I", "1"))
        variants.append(swap(s, "I", "L"))
    if "l" in s:
        variants.append(swap(s, "l", "1"))

    for v in variants:
        candidates.add(v)

    return list(candidates)[:6]


# ── 패턴 기반 후보 추출 ───────────────────────────────────────────────────────

def _is_valid_dwg_candidate(s: str) -> bool:
    """DWG NO 후보 문자열이 유효한지 확인한다."""
    s = s.strip()
    if len(s) < _MIN_TOTAL_LEN or len(s) > _MAX_TOTAL_LEN:
        return False
    if not re.search(r'[A-Z]', s, re.I):  # 영문자 없으면 제외
        return False
    if not re.search(r'[0-9]', s):          # 숫자 없으면 제외
        return False
    for pat in _EXCLUDE_PATTERNS:
        if pat.match(s):
            return False
    return True


def _extract_candidates_from_text(text: str) -> list[dict[str, Any]]:
    """텍스트에서 DWG NO 후보를 추출한다."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1순위: DWG/DRAWING NO 레이블 바로 뒤 값
    for m in _DWG_LABEL_PATTERN.finditer(text):
        raw = m.group(1).strip().rstrip(".,;")
        if raw and raw not in seen and _is_valid_dwg_candidate(raw):
            seen.add(raw)
            candidates.append({"value": raw, "source": "label", "score": 1.0})

    # 2순위: DWG NO 레이블 근처 줄에서 패턴 추출
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r'\b(?:DWG|DRAWING)\b', line, re.I):
            # 해당 줄 + 다음 2줄 검색
            context = "\n".join(lines[i:i + 3])
            for m in _DWG_PATTERN.finditer(context):
                raw = m.group(1).strip()
                if raw not in seen and _is_valid_dwg_candidate(raw):
                    seen.add(raw)
                    candidates.append({"value": raw, "source": "nearby_label", "score": 0.75})

    # 3순위: 전체 텍스트에서 패턴 매칭
    for m in _DWG_PATTERN.finditer(text):
        raw = m.group(1).strip()
        if raw not in seen and _is_valid_dwg_candidate(raw):
            seen.add(raw)
            candidates.append({"value": raw, "source": "pattern", "score": 0.4})

    return candidates


# ── GPT Vision 집중 호출 ──────────────────────────────────────────────────────

_DWG_NO_SYSTEM = (
    "당신은 기계 도면 표제란(Title Block) 판독 전문가입니다. "
    "이미지는 도면 우하단 표제란 영역입니다. "
    "DWG NO. / DRAWING NO. / 도번 항목의 값을 정확히 읽어야 합니다. "
    "숫자와 영문자를 혼동하지 마세요 (O≠0, I≠1). JSON만 출력하세요."
)


def _gpt_extract_dwg_no(
    image_png: bytes,
    *,
    model_name: str = "",
    area_hint: str = "titleblock",
    model_id: str = "gpt-4o",
) -> dict[str, Any]:
    """GPT Vision으로 DWG NO만 집중 추출한다."""
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai 패키지 없음"}

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY 없음"}

    client = OpenAI(api_key=api_key)
    b64 = base64.standard_b64encode(image_png).decode("ascii")

    _area_desc = (
        "이 이미지는 도면의 우측 가장자리 영역입니다. 세로(90도 회전된) 텍스트까지 읽으세요."
        if area_hint == "right_edge_vertical"
        else "이 이미지는 도면의 우하단 표제란(Title Block) 영역입니다."
    )
    prompt_lines = [
        _area_desc,
        "",
        f"시스템 등록 모델명(참고용): {model_name}" if model_name else "",
        "",
        "다음을 읽어 JSON으로 출력하세요:",
        "1. DWG NO. (또는 DRAWING NO., 도번) 항목의 값 → dwg_no",
        "   - 영문 대문자와 숫자 조합, 하이픈(-)으로 구분된 파트넘버 형식",
        "   - 알파벳 O(오)와 숫자 0(영), 알파벳 I(아이)와 숫자 1(일)을 정확히 구분",
        "   - 우측 가장자리 세로 인쇄 모델/도번도 후보로 포함",
        "   - 읽기 어렵거나 해당 항목이 없으면 null",
        "2. 후보가 여러 개 보이면 candidates 배열에 모두 포함",
        "3. 읽은 근거와 불확실 요소를 reason에 간단히 기술",
        "",
        '출력 스키마: {"dwg_no": string|null, "candidates": [string], "reason": string}',
    ]
    prompt = "\n".join(l for l in prompt_lines if l is not None)

    content: list[dict] = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}},
    ]

    _FALLBACK = "gpt-4o-mini"
    raw = None
    used_model = model_id
    for _mid in [model_id, _FALLBACK]:
        try:
            resp = client.chat.completions.create(
                model=_mid,
                messages=[
                    {"role": "system", "content": _DWG_NO_SYSTEM},
                    {"role": "user", "content": content},
                ],
                max_tokens=400,
                temperature=0.0,
            )
            raw = (resp.choices[0].message.content or "").strip()
            used_model = _mid
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

    return {"ok": True, "parsed": parsed, "model_used": used_model}


# ── 신뢰도 계산 ───────────────────────────────────────────────────────────────

def _score_candidate(
    value: str,
    source: str,
    model_name: str,
    gpt_dwg_no: str | None,
    gpt_candidates: list[str],
) -> float:
    """후보값의 신뢰도 점수(0~1)를 계산한다."""
    score = 0.0

    # 출처 기본 점수
    source_scores = {"label": 0.5, "gpt_primary": 0.5, "nearby_label": 0.35, "pattern": 0.2, "correction": 0.1}
    score += source_scores.get(source, 0.1)

    # GPT 1차 결과와 일치
    if gpt_dwg_no and value.upper() == gpt_dwg_no.upper():
        score += 0.35
    elif gpt_dwg_no and _norm(value) == _norm(gpt_dwg_no):
        score += 0.25

    # GPT 후보 목록에 포함
    if any(_norm(value) == _norm(c) for c in gpt_candidates):
        score += 0.1

    # 모델명과 유사도
    if model_name:
        nm = _norm(model_name)
        nv = _norm(value)
        if nv == nm:
            score += 0.25
        elif nv in nm or nm in nv:
            score += 0.15
        elif _common_prefix_ratio(nv, nm) >= 0.7:
            score += 0.10

    # 패턴 품질 (세그먼트 수, 길이)
    segs = value.split("-")
    if 2 <= len(segs) <= 8:
        score += 0.05
    if 8 <= len(value) <= 30:
        score += 0.05

    return min(score, 1.0)


def _norm(s: str) -> str:
    return re.sub(r"[-_\s]", "", s.upper())


def _common_prefix_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    common = 0
    for x, y in zip(a, b):
        if x == y:
            common += 1
        else:
            break
    return common / max(len(a), len(b))


# ── 메이커명 전용 추출 ────────────────────────────────────────────────────────

_MAKER_SYSTEM = (
    "당신은 기계 도면 표제란(Title Block) 판독 전문가입니다. "
    "이미지는 도면의 우하단 표제란 영역입니다. "
    "회사명·제조사명·로고 텍스트를 정확히 읽어야 합니다. JSON만 출력하세요."
)


def _crop_titleblock_bottom_right(
    pdf_path: str,
    page_idx: int = 0,
    zoom: float = 3.0,
    *,
    x_start: float = 0.0,
    y_start: float = 0.70,
) -> bytes | None:
    """표제란 우하단 영역을 크롭 (x_start·y_start 비율로 조정 가능)."""
    try:
        doc = fitz.open(pdf_path)
        if page_idx >= doc.page_count:
            page_idx = doc.page_count - 1
        page = doc.load_page(page_idx)
        rect = page.rect
        w, h = rect.width, rect.height
        clip = fitz.Rect(
            rect.x0 + w * x_start,
            rect.y0 + h * y_start,
            rect.x1,
            rect.y1,
        )
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        png = pix.tobytes("png")
        doc.close()
        return png
    except Exception:
        return None


def _gpt_extract_maker_from_titleblock(
    image_png: bytes,
    *,
    known_maker: str = "",
    attempt: int = 1,
    model_id: str = "gpt-4o",
) -> dict[str, Any]:
    """GPT Vision으로 표제란에서 회사명/메이커명을 집중 추출한다.

    attempt=1: 로고 텍스트·회사명 전반 탐색
    attempt=2: 1차 미확인 시 더 넓은 관점으로 재확인
    """
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai 패키지 없음"}

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY 없음"}

    client = OpenAI(api_key=api_key)
    b64 = base64.standard_b64encode(image_png).decode("ascii")

    if attempt == 1:
        prompt = "\n".join([
            "이 이미지는 도면(Engineering Drawing)의 우하단 표제란(Title Block) 영역입니다.",
            "",
            "표제란에서 회사명·제조사명을 찾아주세요.",
            "로고 이미지 옆 텍스트, 'COMPANY', 'MANUFACTURER', '제조사', 또는 인쇄된 브랜드명을 확인하세요.",
            "예: 'SMC Pneumatics Korea', 'SMC Corporation', 'HYOSUNG MOTORS' 등",
            f"{'참고 - 시스템 등록 제조사: ' + known_maker if known_maker else ''}",
            "",
            "출력 스키마 (JSON 한 개만):",
            '{"company_name": string|null,',
            ' "logo_text": string|null,',
            ' "confidence": 0.0~1.0,',
            ' "reason": string}',
        ])
    else:
        prompt = "\n".join([
            "이 이미지는 도면 우하단 표제란입니다. 1차 분석에서 회사명이 불명확했습니다.",
            "",
            "다시 한 번 꼼꼼히 확인해 주세요:",
            "- 로고 그래픽 옆의 모든 텍스트",
            "- 표제란 하단·좌하단·중앙 영역의 회사명",
            "- 작은 글씨로 인쇄된 브랜드명·도메인·주소",
            "- 영문·한글·혼합 표기 모두 포함",
            f"{'참고 - 시스템 등록 제조사: ' + known_maker if known_maker else ''}",
            "",
            "출력 스키마 (JSON 한 개만):",
            '{"company_name": string|null,',
            ' "logo_text": string|null,',
            ' "confidence": 0.0~1.0,',
            ' "reason": string}',
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
                    {"role": "system", "content": _MAKER_SYSTEM},
                    {"role": "user", "content": content},
                ],
                max_tokens=300,
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


def extract_maker_from_titleblock(
    pdf_path: str,
    *,
    known_maker: str = "",
    page_idx: int = 0,
    crop_zoom: float = 3.0,
) -> dict[str, Any]:
    """도면 표제란 우하단에서 회사명/메이커명을 2회 호출로 고정밀 추출한다.

    1차: 전체 하단 30% 크롭 → GPT 집중 추출
    2차: 1차 confidence < 0.6 이거나 null이면, 더 넓은 영역(하단 40%)으로 재확인

    Returns:
        {
            "company_name": str | None,
            "logo_text": str | None,
            "confidence": float,
            "attempts": int,
            "method": str,
        }
    """
    result: dict[str, Any] = {
        "company_name": None,
        "logo_text": None,
        "confidence": 0.0,
        "attempts": 0,
        "method": "none",
    }

    # ── 1차: 우하단 30% (표제란 전형 위치) ──────────────────────────────
    crop1 = _crop_titleblock_bottom_right(pdf_path, page_idx, zoom=crop_zoom,
                                          x_start=0.0, y_start=0.70)
    if crop1 is None:
        return result

    r1 = _gpt_extract_maker_from_titleblock(crop1, known_maker=known_maker, attempt=1)
    result["attempts"] = 1

    if r1.get("ok"):
        p1 = r1.get("parsed") or {}
        company = (p1.get("company_name") or "").strip() or None
        logo = (p1.get("logo_text") or "").strip() or None
        conf = float(p1.get("confidence") or 0.0)
        result.update({
            "company_name": company,
            "logo_text": logo,
            "confidence": conf,
            "method": "gpt_crop_1st",
        })

        # 1차에서 충분히 확인됐으면 종료
        if company and conf >= 0.6:
            return result

    # ── 2차: 더 넓은 하단 40% 재확인 (x 전체, y 60~100%) ───────────────
    crop2 = _crop_titleblock_bottom_right(pdf_path, page_idx, zoom=crop_zoom,
                                          x_start=0.0, y_start=0.60)
    if crop2 is None:
        return result

    r2 = _gpt_extract_maker_from_titleblock(crop2, known_maker=known_maker, attempt=2)
    result["attempts"] = 2

    if r2.get("ok"):
        p2 = r2.get("parsed") or {}
        company2 = (p2.get("company_name") or "").strip() or None
        logo2 = (p2.get("logo_text") or "").strip() or None
        conf2 = float(p2.get("confidence") or 0.0)

        # 2차가 더 높은 신뢰도면 교체
        if conf2 > result["confidence"] or (company2 and not result["company_name"]):
            result.update({
                "company_name": company2,
                "logo_text": logo2,
                "confidence": conf2,
                "method": "gpt_crop_2nd",
            })

    return result


# ── 공개 엔트리포인트 ─────────────────────────────────────────────────────────

def extract_dwg_no(
    pdf_path: str,
    *,
    model_name: str = "",
    page_idx: int = 0,
    crop_zoom: float = 3.0,
) -> dict[str, Any]:
    """
    도면 PDF에서 DWG NO를 고정밀로 추출한다.

    Returns:
        {
            "dwg_no": str | None,      # 최종 선택된 DWG NO
            "confidence": float,        # 0.0~1.0
            "candidates": [             # 전체 후보 목록
                {"value": str, "source": str, "score": float}
            ],
            "method": str,             # 추출 방법 요약
        }
    """
    all_candidates: list[dict[str, Any]] = []

    # ── Step 1: 텍스트 레이어에서 추출 ──────────────────────────────────
    tb_text = _extract_titleblock_text(pdf_path, page_idx)
    if tb_text.strip():
        text_cands = _extract_candidates_from_text(tb_text)
        all_candidates.extend(text_cands)

    # ── Step 2: 우하단 25% 크롭 이미지 생성 ─────────────────────────────
    crop_png = _crop_titleblock_png(pdf_path, page_idx, zoom=crop_zoom)
    if crop_png is None:
        crop_png = _full_page_png(pdf_path, page_idx, zoom=2.0)

    # ── Step 3: GPT Vision으로 DWG NO 집중 추출 (표제란 + 우측세로영역) ────
    gpt_dwg_no: str | None = None
    gpt_candidates: list[str] = []
    gpt_method = "none"

    if crop_png:
        gpt_result = _gpt_extract_dwg_no(crop_png, model_name=model_name, area_hint="titleblock")
        if gpt_result.get("ok"):
            p = gpt_result.get("parsed") or {}
            gpt_dwg_no = (p.get("dwg_no") or "").strip() or None
            gpt_candidates = [c.strip() for c in (p.get("candidates") or []) if c and c.strip()]
            gpt_method = f"gpt_crop({gpt_result.get('model_used','')})"

            # GPT 1차 결과를 후보에 추가
            if gpt_dwg_no and _is_valid_dwg_candidate(gpt_dwg_no):
                existing = [c["value"] for c in all_candidates]
                if gpt_dwg_no not in existing:
                    all_candidates.insert(0, {"value": gpt_dwg_no, "source": "gpt_primary", "score": 0.0})

            for gc in gpt_candidates:
                if gc and _is_valid_dwg_candidate(gc):
                    existing = [c["value"] for c in all_candidates]
                    if gc not in existing:
                        all_candidates.append({"value": gc, "source": "gpt_candidate", "score": 0.0})

    # 우측 가장자리 세로 텍스트 영역 추가 시도
    right_edge_png = _crop_right_edge_vertical_png(pdf_path, page_idx, zoom=crop_zoom)
    if right_edge_png:
        gpt_right = _gpt_extract_dwg_no(
            right_edge_png,
            model_name=model_name,
            area_hint="right_edge_vertical",
        )
        if gpt_right.get("ok"):
            rp = gpt_right.get("parsed") or {}
            right_dwg = (rp.get("dwg_no") or "").strip() or None
            right_candidates = [c.strip() for c in (rp.get("candidates") or []) if c and c.strip()]
            if right_dwg and _is_valid_dwg_candidate(right_dwg):
                existing = [c["value"] for c in all_candidates]
                if right_dwg not in existing:
                    all_candidates.insert(0, {"value": right_dwg, "source": "gpt_primary", "score": 0.0})
                if not gpt_dwg_no:
                    gpt_dwg_no = right_dwg
            for rc in right_candidates:
                if rc and _is_valid_dwg_candidate(rc):
                    existing = [c["value"] for c in all_candidates]
                    if rc not in existing:
                        all_candidates.append({"value": rc, "source": "gpt_candidate", "score": 0.0})

    # ── Step 4: 혼동 문자 보정 후보 생성 ────────────────────────────────
    base_for_correction = gpt_dwg_no or (all_candidates[0]["value"] if all_candidates else None)
    if base_for_correction:
        corrected = _apply_char_corrections(base_for_correction)
        for cv in corrected:
            if cv != base_for_correction and _is_valid_dwg_candidate(cv):
                existing = [c["value"] for c in all_candidates]
                if cv not in existing:
                    all_candidates.append({"value": cv, "source": "correction", "score": 0.0})

    # ── Step 5: 전체 후보 점수 계산 ─────────────────────────────────────
    for cand in all_candidates:
        cand["score"] = _score_candidate(
            cand["value"],
            cand["source"],
            model_name,
            gpt_dwg_no,
            gpt_candidates,
        )

    # 점수 내림차순 정렬
    all_candidates.sort(key=lambda x: -x["score"])

    # ── Step 6: 최종 선택 ────────────────────────────────────────────────
    best = all_candidates[0] if all_candidates else None
    dwg_no = best["value"] if best else None
    confidence = best["score"] if best else 0.0

    # 후보가 하나뿐이거나 점수 차이가 크면 신뢰도 상향
    if len(all_candidates) >= 2:
        gap = all_candidates[0]["score"] - all_candidates[1]["score"]
        if gap >= 0.3:
            confidence = min(confidence + 0.05, 1.0)
    elif len(all_candidates) == 1:
        confidence = min(confidence + 0.05, 1.0)

    return {
        "dwg_no": dwg_no,
        "confidence": round(confidence, 3),
        "candidates": [
            {"value": c["value"], "source": c["source"], "score": round(c["score"], 3)}
            for c in all_candidates
        ],
        "method": gpt_method,
        "gpt_raw": gpt_dwg_no,
    }
