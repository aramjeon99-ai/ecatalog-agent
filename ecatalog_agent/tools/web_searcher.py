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


def _fetch_html_text(url: str) -> str:
    """웹 페이지 HTML에서 순수 텍스트를 추출한다."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return ""
        # HTML 태그 제거
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text)
        return text[:100000]
    except Exception:
        return ""


def verify_model_from_url(
    url: str,
    model_name: str,
    *,
    norm_model_fn=None,
    check_model_fn=None,
) -> dict[str, Any]:
    """
    시스템 데이터의 Model-1 첨부URL1을 직접 방문해 모델명을 확인한다.

    - PDF URL → 다운로드 후 텍스트 파싱
    - 웹 페이지 → HTML 텍스트 추출

    Returns:
        {
            "checked": bool,
            "model_found": bool,
            "evidence": str,       # 확인 근거 요약
            "page_text": str,      # 추출 텍스트 (최대 50000자)
        }
    """
    norm_fn = norm_model_fn or _norm
    result: dict[str, Any] = {
        "checked": False,
        "model_found": False,
        "evidence": "",
        "page_text": "",
    }

    if not url or not model_name:
        return result

    url = url.strip()
    is_pdf = url.lower().endswith(".pdf") or "pdf" in url.lower().split("?")[0][-10:]

    # 텍스트 추출
    if is_pdf:
        page_text = _download_and_parse_pdf(url)
    else:
        page_text = _fetch_html_text(url)

    if not page_text:
        result["evidence"] = f"URL 접근 실패 또는 내용 없음: {url}"
        return result

    result["checked"] = True
    result["page_text"] = page_text[:50000]

    # 모델명 확인
    norm_model = norm_fn(model_name)
    text_lower = page_text.lower()
    text_norm = norm_fn(page_text)

    # 1) 정규화 substring 매칭
    if norm_model and norm_model in text_norm:
        result["model_found"] = True
        result["evidence"] = f"URL 텍스트에서 모델 확인: {url}"
        return result

    # 2) check_model_fn 활용 (형번 분해 일치)
    if check_model_fn:
        matched, val = check_model_fn(model_name, page_text[:80000])
        if matched:
            result["model_found"] = True
            result["evidence"] = f"URL 형번체계 일치({val}): {url}"
            return result

    # 3) 원본 모델명 직접 포함 (대소문자 무시)
    if model_name.lower() in text_lower:
        result["model_found"] = True
        result["evidence"] = f"URL에서 모델명 직접 확인: {url}"
        return result

    result["evidence"] = f"URL 확인했으나 모델 미발견: {url}"
    return result


# ── 메인 함수 ─────────────────────────────────────────────────────────
def web_search_verify(
    maker: str,
    model: str,
    *,
    norm_model_fn=None,          # 외부 정규화 함수 (없으면 내부 _norm 사용)
    check_model_fn=None,         # 외부 모델 매칭 함수 (없으면 단순 substring)
    expected_specs: list[dict[str, str]] | None = None,
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
            "matched_page_url": str | None,
            "matched_page_text": str,
            "online_spec_hints": list[{"title": str, "value": str}],
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
        "matched_page_url": None,
        "matched_page_text": "",
        "online_spec_hints": [],
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
    matched_page_candidate_url: str | None = None
    matched_page_candidate_text: str | None = None
    for s in snippets:
        if model_n and model_n in norm_fn(s["title"] + s["snippet"]):
            result["model_found_online"] = True
            matched_page_candidate_url = s.get("url") or None
            matched_page_candidate_text = (s.get("title") or "") + " " + (s.get("snippet") or "")
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

    # ── HTML 페이지에서 사양(키: title, 값) 추출 ─────────────────
    # PDF가 아닌 “제품 페이지(표)”에서 바로 값을 긁어오려는 목적.
    if result["model_found_online"] and expected_specs and not result.get("online_spec_hints"):
        # 1) 모델이 잡힌 “페이지 후보(URL)” 우선
        # 2) 없으면 “웹에서 채택한 PDF URL”을 사용
        page_url = matched_page_candidate_url or result.get("matched_pdf_url") or None
        if page_url:
            url_check = verify_model_from_url(
                page_url,
                model,
                norm_model_fn=norm_model_fn,
                check_model_fn=check_model_fn,
            )
            if url_check.get("checked") and url_check.get("model_found"):
                result["matched_page_url"] = page_url
                result["matched_page_text"] = (url_check.get("page_text") or "")[:50000]

                try:
                    online_hints: list[dict[str, str]] = []
                    page_text = result["matched_page_text"]
                    page_text_norm = page_text.lower()

                    for item in expected_specs:
                        title = (item.get("title") or "").strip()
                        expected_value = (item.get("value") or "").strip()
                        if not title:
                            continue

                        # title이 등장하는 근처 세그먼트를 잘라서 value 추정
                        # (MISUMI 같은 표 페이지에서 "Bore Size 100" 형태를 기대)
                        idx = page_text_norm.find(title.lower())
                        if idx == -1:
                            continue
                        seg = page_text[idx : idx + 200]

                        # ':' 또는 공백 뒤 첫 숫자/토큰을 value로 후보 선정
                        #  - 숫자 우선 (예: 100, 25Z, 10m)
                        m_num = re.search(r"[:\-\s]\s*([0-9][0-9,\.]*\s*[A-Za-z°/%]*)", seg)
                        if m_num:
                            found_val = m_num.group(1).strip()
                        else:
                            # 숫자가 아니어도 값이 문자열로 나온 경우의 폴백
                            # (예: 'Oil Lubrication Type' 같은 문장형)
                            m_any = re.search(r"[:\-]\s*([^\n\r]{1,80})", seg)
                            found_val = (m_any.group(1).strip() if m_any else seg[:80].strip())

                        # expected_value가 비어있지 않으면 "포함"으로 간단 검증
                        if expected_value and expected_value.lower() not in found_val.lower():
                            # 완전 일치가 아니라도, 숫자 포함이면 허용(예: 100mm ↔ 100)
                            online_hints.append({"title": title, "value": found_val})
                        else:
                            online_hints.append({"title": title, "value": found_val})

                    # 중복 title 제거(앞에서 잡힌 값 우선)
                    dedup: dict[str, dict[str, str]] = {}
                    for h in online_hints:
                        dedup[h["title"]] = h
                    result["online_spec_hints"] = list(dedup.values())
                except Exception:
                    pass

    # ── 근거 요약 ─────────────────────────────────────────────────
    parts = []
    if result["model_found_online"]:
        parts.append(f"모델명 '{model}' 온라인 확인됨")
        if result["matched_pdf_url"]:
            parts.append(f"PDF 출처: {result['matched_pdf_url']}")
        if result.get("matched_page_url"):
            parts.append(f"웹 페이지 출처: {result['matched_page_url']}")
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
