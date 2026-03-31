"""web_searcher.py
웹 검색을 통해 메이커/모델 정보를 2차 검증한다.
- DuckDuckGo 검색으로 제조사 여부·모델 존재 확인
- 검색 결과의 PDF URL 발견 시 다운로드 후 텍스트 파싱
"""
from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path
from typing import Any

import requests
from duckduckgo_search import DDGS

# ── 상수 ──────────────────────────────────────────────────────────────
_TIMEOUT_S   = 10
_MAX_PDF_MB  = 10
_MAX_RESULTS = 5
_PDF_PAGES   = 5        # 다운로드 PDF에서 읽을 최대 페이지
_USER_AGENT  = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 제조업 확인 키워드 (영/한)
_MANUFACTURER_KW = [
    "manufacturer", "manufactured by", "designed by", "produced by",
    "제조사", "제조원", "제조", "생산",
]
# 총판·대리점 키워드 (있으면 제조사 아님)
_DISTRIBUTOR_KW = [
    "distributor", "reseller", "dealer", "authorized dealer",
    "대리점", "판매점", "총판",
]


# ── 헬퍼 ──────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"[-_/.\s]", "", s.lower())


def _extract_pdf_urls(results: list[dict]) -> list[str]:
    urls = []
    for r in results:
        href = r.get("href") or r.get("url") or ""
        if href.lower().endswith(".pdf"):
            urls.append(href)
    return urls


def _download_and_parse_pdf(url: str) -> str:
    """URL에서 PDF를 다운로드하고 텍스트를 반환한다."""
    try:
        import fitz  # PyMuPDF

        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_S,
            stream=True,
        )
        if resp.status_code != 200:
            return ""
        # Content-Type 확인
        ct = resp.headers.get("Content-Type", "")
        if "pdf" not in ct.lower() and not url.lower().endswith(".pdf"):
            return ""
        # 크기 제한
        content = b""
        for chunk in resp.iter_content(chunk_size=65536):
            content += chunk
            if len(content) > _MAX_PDF_MB * 1024 * 1024:
                break
        if not content:
            return ""

        doc = fitz.open(stream=content, filetype="pdf")
        texts = []
        for i in range(min(doc.page_count, _PDF_PAGES)):
            texts.append(doc.load_page(i).get_text("text") or "")
        return "\n".join(texts).strip()

    except Exception:
        return ""


# ── 메인 함수 ─────────────────────────────────────────────────────────
def web_search_verify(
    maker: str,
    model: str,
    *,
    norm_model_fn=None,          # 외부 정규화 함수 (없으면 내부 _norm 사용)
    check_model_fn=None,         # 외부 모델 매칭 함수 (없으면 단순 substring)
) -> dict[str, Any]:
    """
    웹 검색으로 메이커/모델을 2차 검증한다.

    Returns:
        {
            "searched": bool,
            "manufacturer_confirmed": bool | None,
            "model_found_online": bool,
            "matched_pdf_url": str | None,
            "matched_pdf_text": str,
            "search_snippets": list[dict],   # [{title, url, snippet}]
            "evidence_summary": str,
        }
    """
    norm_fn = norm_model_fn or _norm

    result: dict[str, Any] = {
        "searched": False,
        "manufacturer_confirmed": None,
        "model_found_online": False,
        "matched_pdf_url": None,
        "matched_pdf_text": "",
        "search_snippets": [],
        "evidence_summary": "",
    }

    if not maker or not model:
        result["evidence_summary"] = "검색 입력값 없음"
        return result

    queries = [
        f"{maker} {model} catalog datasheet filetype:pdf",
        f"{maker} {model} manufacturer specifications",
        f'"{model}" "{maker}" site:.com OR site:.co.kr',
    ]

    all_results: list[dict] = []
    pdf_urls: list[str] = []

    try:
        with DDGS() as ddgs:
            for q in queries:
                hits = list(ddgs.text(q, max_results=_MAX_RESULTS))
                all_results.extend(hits)
                pdf_urls.extend(_extract_pdf_urls(hits))
    except Exception as e:
        result["evidence_summary"] = f"검색 실패: {e}"
        return result

    result["searched"] = True

    # 스니펫 정리 (중복 URL 제거)
    seen_urls: set[str] = set()
    snippets: list[dict] = []
    for r in all_results:
        url  = r.get("href") or r.get("url") or ""
        if url in seen_urls:
            continue
        seen_urls.add(url)
        snippets.append({
            "title":   r.get("title", ""),
            "url":     url,
            "snippet": r.get("body") or r.get("description") or "",
        })
    result["search_snippets"] = snippets[:10]

    # ── 제조사 여부 판단 ──────────────────────────────────────────
    combined_text = " ".join(
        (s["title"] + " " + s["snippet"]).lower()
        for s in snippets
    )
    mfr_score = sum(1 for kw in _MANUFACTURER_KW if kw in combined_text)
    dis_score = sum(1 for kw in _DISTRIBUTOR_KW if kw in combined_text)
    if mfr_score > dis_score:
        result["manufacturer_confirmed"] = True
    elif dis_score > 0:
        result["manufacturer_confirmed"] = False

    # ── 모델 온라인 존재 여부 ────────────────────────────────────
    model_n = norm_fn(model)
    for s in snippets:
        if model_n and model_n in norm_fn(s["title"] + s["snippet"]):
            result["model_found_online"] = True
            break

    # ── PDF 다운로드 및 모델명 재확인 ────────────────────────────
    for pdf_url in pdf_urls[:3]:          # 최대 3개 시도
        pdf_text = _download_and_parse_pdf(pdf_url)
        if not pdf_text:
            continue
        # 모델명이 PDF에 있으면 채택
        if check_model_fn:
            matched, _ = check_model_fn(model, pdf_text)
        else:
            matched = model_n in norm_fn(pdf_text)

        if matched:
            result["model_found_online"] = True
            result["matched_pdf_url"]  = pdf_url
            result["matched_pdf_text"] = pdf_text[:8000]
            break

    # ── 근거 요약 ─────────────────────────────────────────────────
    parts = []
    if result["model_found_online"]:
        parts.append(f"모델명 '{model}' 온라인 확인됨")
        if result["matched_pdf_url"]:
            parts.append(f"PDF 출처: {result['matched_pdf_url']}")
    else:
        parts.append(f"모델명 '{model}' 온라인 미확인")

    if result["manufacturer_confirmed"] is True:
        parts.append(f"'{maker}' 제조사로 확인됨 (웹 검색)")
    elif result["manufacturer_confirmed"] is False:
        parts.append(f"'{maker}' 대리점/판매사 의심")
    else:
        parts.append("제조사 여부 웹 미확인")

    result["evidence_summary"] = " | ".join(parts)
    return result
