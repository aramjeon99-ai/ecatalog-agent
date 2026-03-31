"""manufacturer_verifier.py
제조업 여부 2단계 검증

1단계: 기준 제조사 목록 등록 여부 → 기존 제조업
2단계: 목록 미등록 시 인터넷 검색 2회 (AI 분석 + 일반 검색)
       - 해외 제조사 확인 → 해외제조사
       - 미확인 → 신규공급사 (사람검토)
"""
from __future__ import annotations

import re
from typing import Any

from duckduckgo_search import DDGS

_MAX_RESULTS = 5

# 해외 법인 형태 키워드
_FOREIGN_ENTITY_KW = [
    "inc.", "ltd.", "co., ltd", "co.,ltd", "gmbh", "s.r.o", "s.a.", "s.p.a",
    "corporation", "corp.", "plc", "a.g.", " ag ", "b.v.", "llc", "pte. ltd",
    "co. ltd", "k.k.", "s.l.", "oy ", "oyj",
]
# 제조업 확인 키워드
_MFR_KW = [
    "manufacturer", "manufacturing", "manufactured by", "factory", "production facility",
    "제조사", "제조원", "제조업", "제조공장", "생산",
]
# 유통·판매 키워드 (있으면 제조사 아님)
_DIST_KW = [
    "distributor", "reseller", "dealer", "authorized dealer", "import", "trading",
    "대리점", "판매점", "총판", "수입사", "무역", "상사",
]


def _ddg_search(query: str, max_results: int = _MAX_RESULTS) -> list[dict]:
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []


def _score_mfr(hits: list[dict]) -> tuple[int, int, bool]:
    """(mfr_score, dist_score, is_foreign)"""
    combined = " ".join(
        (h.get("title", "") + " " + (h.get("body") or h.get("description") or "")).lower()
        for h in hits
    )
    mfr_score  = sum(1 for kw in _MFR_KW  if kw in combined)
    dist_score = sum(1 for kw in _DIST_KW if kw in combined)
    is_foreign = any(kw in combined for kw in _FOREIGN_ENTITY_KW)
    return mfr_score, dist_score, is_foreign


def verify_manufacturer(
    maker_name: str,
    *,
    is_in_list: bool,
    homepage_url: str | None = None,  # 현재 미사용, 시그니처 호환용
) -> dict[str, Any]:
    """
    제조업 여부 검증.

    Returns:
        {
            "maker_type": "기존 제조업" | "해외제조사" | "신규공급사",
            "is_manufacturer": True | False | None,
            "needs_human_review": bool,
            "verification_step": 1 | 2 | 0,
            "is_foreign": bool | None,
            "evidence": str,
            "web_snippets": list[dict],
        }
    """
    result: dict[str, Any] = {
        "maker_type": "신규공급사",
        "is_manufacturer": None,
        "needs_human_review": True,
        "verification_step": 0,
        "is_foreign": None,
        "evidence": "",
        "web_snippets": [],
    }

    # ── 1단계: 기준 목록 ────────────────────────────────────────────
    if is_in_list:
        result.update({
            "maker_type": "기존 제조업",
            "is_manufacturer": True,
            "needs_human_review": False,
            "verification_step": 1,
            "evidence": "기준 제조사 목록(industrial_manufacturers_list)에 등록된 업체",
        })
        return result

    if not maker_name:
        result["evidence"] = "메이커명 없음"
        return result

    # ── 2단계: 인터넷 검색 1차 (AI 분석 목적 쿼리) ─────────────────
    hits1 = _ddg_search(f'"{maker_name}" manufacturer company official site')
    # 인터넷 검색 2차 (일반 검색)
    hits2 = _ddg_search(f"{maker_name} 제조사 manufacturer 해외")

    all_hits = hits1 + hits2
    result["web_snippets"] = [
        {
            "title":   h.get("title", ""),
            "url":     h.get("href", ""),
            "snippet": (h.get("body") or h.get("description") or "")[:200],
        }
        for h in all_hits[:8]
    ]

    mfr1, dist1, foreign1 = _score_mfr(hits1)
    mfr2, dist2, foreign2 = _score_mfr(hits2)

    mfr_total  = mfr1  + mfr2
    dist_total = dist1 + dist2
    is_foreign = foreign1 or foreign2

    evidence_parts = [
        f"1차 검색 — 제조사 점수 {mfr1}, 유통 점수 {dist1}, 해외 법인형태 {'감지' if foreign1 else '미감지'}",
        f"2차 검색 — 제조사 점수 {mfr2}, 유통 점수 {dist2}, 해외 법인형태 {'감지' if foreign2 else '미감지'}",
    ]

    # 판정
    is_mfr = mfr_total > dist_total if (mfr_total + dist_total) > 0 else None

    if is_foreign and is_mfr is not False:
        result.update({
            "maker_type": "해외제조사",
            "is_manufacturer": True,
            "needs_human_review": False,
            "verification_step": 2,
            "is_foreign": True,
            "evidence": " | ".join(evidence_parts) + " → 해외 제조사로 판단",
        })
    elif is_mfr is True and not is_foreign:
        # 국내 제조사로 보이나 목록 미등록 → 신규공급사 사람검토
        result.update({
            "maker_type": "신규공급사",
            "is_manufacturer": None,
            "needs_human_review": True,
            "verification_step": 2,
            "is_foreign": False,
            "evidence": " | ".join(evidence_parts) + " → 국내 신규 공급사, 담당자 확인 필요",
        })
    else:
        result.update({
            "verification_step": 2,
            "is_foreign": is_foreign,
            "evidence": " | ".join(evidence_parts) + " → 정보 불충분, 신규공급사로 분류",
        })

    return result
