"""
시스템 데이터의 URL(PDF 카탈로그 또는 HTML 제품 페이지)을 크롤링하여
등록된 모델의 사양을 추출하고 시스템 등록 사양과 비교한다.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import tempfile
import urllib.parse
from typing import Any


# ── URL 유효성 판단 ──────────────────────────────────────────────────────────

def _is_useful_url(url: str) -> bool:
    """루트 도메인·빈 경로·범용 검색 URL은 제외한다."""
    if not url or not url.startswith("http"):
        return False
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/")
    # 경로가 없거나 너무 짧으면 홈페이지
    if not path or len(path) < 4:
        return False
    return True


def _is_pdf_url(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")


# ── PDF URL 처리 ─────────────────────────────────────────────────────────────

def _fetch_pdf_text(url: str, timeout: int = 20) -> str | None:
    """PDF URL에서 텍스트를 추출한다. 실패 시 None."""
    try:
        import requests
    except ImportError:
        return None

    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return None

    # PDF 바이트를 임시 파일로 저장 후 pdf_parse
    # PDF 매직 바이트 확인
    if resp.content[:4] != b"%PDF":
        return None

    try:
        from ecatalog_agent.tools.pdf_parser import pdf_parse
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(resp.content)
            tmp_path = f.name
        try:
            result = pdf_parse(tmp_path, use_ocr=False)
            text = (result.get("text") or "")[:100000]
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return text if text.strip() else None
    except Exception:
        return None


def _fetch_pdf_images(url: str, timeout: int = 20, zoom: float = 1.5) -> list[bytes]:
    """PDF URL의 첫 4페이지를 PNG로 렌더링한다."""
    try:
        import requests
        import fitz
    except ImportError:
        return []

    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return []

    # PDF 매직 바이트 확인 (HTML 오류 페이지 차단)
    if not resp.content[:4] == b"%PDF":
        return []

    try:
        doc = fitz.open(stream=io.BytesIO(resp.content), filetype="pdf")
        mat = fitz.Matrix(zoom, zoom)
        images = []
        for i in range(min(4, doc.page_count)):
            pix = doc.load_page(i).get_pixmap(matrix=mat, alpha=False)
            images.append(pix.tobytes("png"))
        doc.close()
        return images
    except Exception:
        return []


# ── HTML URL 처리 ─────────────────────────────────────────────────────────────

def _fetch_html_text(url: str, timeout: int = 15) -> str | None:
    """HTML 페이지에서 가시 텍스트를 추출한다."""
    try:
        import requests
    except ImportError:
        return None

    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype.lower():
            return None
        html = resp.text
    except Exception:
        return None

    # BeautifulSoup로 텍스트 추출
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # 연속 빈줄 제거
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:50000]
    except ImportError:
        # BeautifulSoup 없으면 간단 태그 제거
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s{3,}", "\n", text)
        return text[:50000]
    except Exception:
        return None


# ── GPT 사양 추출 ─────────────────────────────────────────────────────────────

_SPEC_EXTRACT_SYSTEM = """당신은 산업 부품 사양 추출 전문가입니다.
주어진 텍스트(또는 이미지)에서 특정 모델의 사양을 찾아 JSON으로 출력하세요.
모르거나 없는 항목은 null, 확실하지 않은 항목은 포함하지 마세요."""


def _gpt_extract_specs_from_text(
    *,
    text: str,
    maker_name: str,
    model_name: str,
    expected_specs: list[dict],
    model_id: str = "gpt-4o",
) -> dict[str, Any]:
    """텍스트에서 GPT로 사양 추출 + 시스템 등록 사양과 비교."""
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai 패키지 없음"}

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY 없음"}

    client = OpenAI(api_key=api_key)

    # 기대 사양 요약
    expected_summary = json.dumps(expected_specs, ensure_ascii=False) if expected_specs else "없음"

    prompt = "\n".join([
        f"제조사: {maker_name}",
        f"모델명: {model_name}",
        "",
        "시스템에 등록된 기대 사양 목록:",
        expected_summary,
        "",
        "아래 문서 텍스트에서 이 모델의 사양을 추출하고, 기대 사양과 비교하세요.",
        "텍스트가 너무 길면 모델명과 관련된 부분에 집중하세요.",
        "",
        "출력 스키마 (JSON 한 개만):",
        '{"model_found": true|false,',
        ' "maker_confirmed": true|false,',
        ' "extracted_specs": [{"title": string, "value": string}],',
        ' "spec_match_summary": [{"title": string, "expected": string, "actual": string|null, "match": "OK"|"MISMATCH"|"NOT_FOUND"}],',
        ' "overall_match": "MATCH"|"PARTIAL"|"MISMATCH"|"UNKNOWN",',
        ' "confidence": 0.0~1.0,',
        ' "reason_ko": string}',
        "",
        "=== 문서 텍스트 (일부) ===",
        text[:15000],
    ])

    # 403 시 gpt-4o-mini 폴백
    _FALLBACK_MODEL = "gpt-4o-mini"
    raw = None
    for _mid in [model_id, _FALLBACK_MODEL]:
        try:
            resp = client.chat.completions.create(
                model=_mid,
                messages=[
                    {"role": "system", "content": _SPEC_EXTRACT_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1500,
                temperature=0.1,
            )
            raw = (resp.choices[0].message.content or "").strip()
            break
        except Exception as e:
            err_str = str(e)
            if "403" in err_str or "model" in err_str.lower():
                continue  # 다음 모델로 폴백
            return {"ok": False, "error": err_str}
    if raw is None:
        return {"ok": False, "error": f"모델 접근 실패 ({model_id}, {_FALLBACK_MODEL})"}

    # JSON 파싱
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {"ok": False, "error": "JSON 파싱 실패", "raw_text": raw}
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": False, "error": "JSON 파싱 실패", "raw_text": raw}

    return {"ok": True, "parsed": parsed, "raw_text": raw}


def _gpt_extract_specs_from_images(
    *,
    images: list[bytes],
    maker_name: str,
    model_name: str,
    expected_specs: list[dict],
    model_id: str = "gpt-4o",
) -> dict[str, Any]:
    """이미지(PDF 렌더링)에서 GPT Vision으로 사양 추출."""
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai 패키지 없음"}

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY 없음"}

    client = OpenAI(api_key=api_key)
    model_id_env = os.environ.get("OPENAI_VISION_MODEL", model_id).strip()

    expected_summary = json.dumps(expected_specs, ensure_ascii=False) if expected_specs else "없음"

    content: list[dict] = [
        {"type": "text", "text": "\n".join([
            f"제조사: {maker_name}",
            f"모델명: {model_name}",
            "",
            "시스템 등록 기대 사양:",
            expected_summary,
            "",
            "이미지는 제품 카탈로그 PDF 페이지입니다.",
            "이 모델의 사양을 추출하고 기대 사양과 비교하세요.",
            "",
            "출력 스키마 (JSON 한 개만):",
            '{"model_found": true|false,',
            ' "maker_confirmed": true|false,',
            ' "extracted_specs": [{"title": string, "value": string}],',
            ' "spec_match_summary": [{"title": string, "expected": string, "actual": string|null, "match": "OK"|"MISMATCH"|"NOT_FOUND"}],',
            ' "overall_match": "MATCH"|"PARTIAL"|"MISMATCH"|"UNKNOWN",',
            ' "confidence": 0.0~1.0,',
            ' "reason_ko": string}',
        ])},
    ]
    for i, png in enumerate(images):
        b64 = base64.standard_b64encode(png).decode("ascii")
        content.append({"type": "text", "text": f"[페이지 {i + 1}]"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
        })

    _FALLBACK_VISION = "gpt-4o-mini"
    raw = None
    for _mid in [model_id_env, _FALLBACK_VISION]:
        try:
            resp = client.chat.completions.create(
                model=_mid,
                messages=[
                    {"role": "system", "content": _SPEC_EXTRACT_SYSTEM},
                    {"role": "user", "content": content},
                ],
                max_tokens=1500,
                temperature=0.1,
            )
            raw = (resp.choices[0].message.content or "").strip()
            break
        except Exception as e:
            err_str = str(e)
            if "403" in err_str or "model" in err_str.lower():
                continue
            return {"ok": False, "error": err_str}
    if raw is None:
        return {"ok": False, "error": f"Vision 모델 접근 실패 ({model_id_env})"}

    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {"ok": False, "error": "JSON 파싱 실패", "raw_text": raw}
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": False, "error": "JSON 파싱 실패", "raw_text": raw}

    return {"ok": True, "parsed": parsed, "raw_text": raw}


# ── 공개 엔트리포인트 ─────────────────────────────────────────────────────────

def fetch_url_and_compare_specs(
    *,
    url: str,
    maker_name: str,
    model_name: str,
    expected_specs: list[dict],
) -> dict[str, Any]:
    """
    URL을 크롤링해 사양을 추출하고 시스템 등록 사양과 비교한다.

    Returns dict:
        ok: bool
        url: str
        url_type: "PDF" | "HTML" | "SKIPPED"
        model_found: bool | None
        maker_confirmed: bool | None
        extracted_specs: list[{"title", "value"}]
        spec_match_summary: list[{"title", "expected", "actual", "match"}]
        overall_match: "MATCH" | "PARTIAL" | "MISMATCH" | "UNKNOWN"
        confidence: float
        reason_ko: str
        error: str (only if ok=False)
    """
    if not _is_useful_url(url):
        return {
            "ok": True,
            "url": url,
            "url_type": "SKIPPED",
            "overall_match": "UNKNOWN",
            "reason_ko": "홈페이지 루트 URL — 사양 비교 건너뜀",
            "extracted_specs": [],
            "spec_match_summary": [],
            "model_found": None,
            "maker_confirmed": None,
            "confidence": 0.0,
        }

    if _is_pdf_url(url):
        # PDF: 텍스트 추출 시도 → 실패 시 Vision
        text = _fetch_pdf_text(url)
        if text and len(text.strip()) > 200:
            result = _gpt_extract_specs_from_text(
                text=text,
                maker_name=maker_name,
                model_name=model_name,
                expected_specs=expected_specs,
            )
            url_type = "PDF"
        else:
            # 이미지 기반 PDF → Vision
            images = _fetch_pdf_images(url)
            if not images:
                return {"ok": False, "url": url, "url_type": "PDF", "error": "PDF 다운로드 또는 렌더링 실패"}
            result = _gpt_extract_specs_from_images(
                images=images,
                maker_name=maker_name,
                model_name=model_name,
                expected_specs=expected_specs,
            )
            url_type = "PDF(Vision)"
    else:
        # HTML
        text = _fetch_html_text(url)
        if not text:
            return {"ok": False, "url": url, "url_type": "HTML", "error": "HTML 크롤링 실패"}
        result = _gpt_extract_specs_from_text(
            text=text,
            maker_name=maker_name,
            model_name=model_name,
            expected_specs=expected_specs,
        )
        url_type = "HTML"

    if not result.get("ok"):
        return {"ok": False, "url": url, "url_type": url_type, "error": result.get("error", "GPT 오류")}

    p = result.get("parsed") or {}
    return {
        "ok": True,
        "url": url,
        "url_type": url_type,
        "model_found": p.get("model_found"),
        "maker_confirmed": p.get("maker_confirmed"),
        "extracted_specs": p.get("extracted_specs") or [],
        "spec_match_summary": p.get("spec_match_summary") or [],
        "overall_match": p.get("overall_match", "UNKNOWN"),
        "confidence": float(p.get("confidence") or 0.0),
        "reason_ko": p.get("reason_ko") or "",
    }
