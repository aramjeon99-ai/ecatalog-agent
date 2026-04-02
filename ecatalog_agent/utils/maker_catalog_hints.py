"""
메이커별 카탈로그/PDF 경향 설정 — data/maker_catalog_hints.json

코드 수정 없이 특정 제조사(예: SMC)의 알려진 패턴을 등록할 수 있다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ecatalog_agent.utils.text_normalize import normalize_maker

_HINTS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "maker_catalog_hints.json"


def _load_raw() -> dict[str, Any]:
    if not _HINTS_PATH.exists():
        return {"makers": {}}
    try:
        return json.loads(_HINTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"makers": {}}


def profile_for_maker(maker_name: str | None) -> dict[str, Any] | None:
    """시스템에 입력된 메이커명에 해당하는 프로필 한 개 또는 None."""
    if not maker_name or not str(maker_name).strip():
        return None
    data = _load_raw()
    makers = data.get("makers") or {}
    if not isinstance(makers, dict):
        return None
    target = normalize_maker(maker_name)
    for key, prof in makers.items():
        if not isinstance(prof, dict):
            continue
        if normalize_maker(str(key)) == target:
            return prof
        for alias in prof.get("match_names") or []:
            if normalize_maker(str(alias)) == target:
                return prof
    return None


def filename_suggests_maker(pdf_filename: str | None, tokens: list[str]) -> bool:
    if not pdf_filename or not tokens:
        return False
    low = str(pdf_filename).lower()
    return any(str(t).lower() in low for t in tokens)


@dataclass
class ModelCodePageResult:
    """형번 체계 페이지 탐색 결과."""
    page_indices: list[int] = field(default_factory=list)   # 0-base 페이지 번호
    page_texts: list[str] = field(default_factory=list)     # 해당 페이지 텍스트
    scores: list[float] = field(default_factory=list)       # 각 페이지 점수 (0~1)
    combined_text: str = ""                                  # 탐지 페이지 전체 합산 텍스트
    found: bool = False


@dataclass
class SpecExtractionResult:
    """메이커 힌트 기반 사양 추출 결과."""
    fields: dict[str, str] = field(default_factory=dict)    # 항목명 → 추출 값
    source_page_index: int | None = None                    # 0-base 페이지 번호
    found: bool = False


def _split_pages(full_text: str) -> list[str]:
    """pdf_parse() 가 삽입한 '---PAGE---' 구분자로 페이지 분리."""
    parts = re.split(r"---PAGE---", full_text)
    return [p.strip() for p in parts if p.strip()]


def _score_page(page_text: str, rules: dict[str, Any], page_no_1base: int) -> float:
    """
    단일 페이지에 대해 형번 체계 페이지 가능성 점수(0~1)를 계산한다.

    점수 구성
    ---------
    ① 1차 키워드         : primary_keywords 중 하나라도 포함 → +0.40
    ② 레이아웃 키워드    : layout_keywords 포함 수 × 0.05 (최대 +0.25)
    ③ 하이픈 코드        : 하이픈 포함 영숫자 코드 수 ≥ hyphen_code_min_count → +0.20
    ④ 위치 힌트          : position_priority_pages 에 해당 → +0.15
    ⑤ 상단 완성 코드 구조: top_code_then_breakdown=true 일 때,
                           페이지 앞부분에 완성 코드 문자열이 먼저 등장하면 → +0.20
                           (HYDAC 등 '완성 코드 → 항목별 분해' 구조)
    ⑥ 번호 구조          : circled_number_required=true 일 때,
                           원문자(①②③) 또는 괄호 번호((1)(2)) 2개 이상 → +0.25
                           (KCC 등 항목별 번호 부여 구조)
    """
    text_lower = page_text.lower()
    score = 0.0

    # ① 1차 키워드
    primary_kws: list[str] = rules.get("primary_keywords") or []
    if any(kw.lower() in text_lower for kw in primary_kws):
        score += 0.40

    # ② 레이아웃 보조 키워드
    layout_kws: list[str] = rules.get("layout_keywords") or []
    hits = sum(1 for kw in layout_kws if kw.lower() in text_lower)
    score += min(hits * 0.05, 0.25)

    # ③ 하이픈 포함 영숫자 코드 패턴 (반복 등장)
    hyphen_codes = re.findall(r"\b[A-Z0-9]{1,8}-[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8})*\b", page_text)
    min_count: int = rules.get("hyphen_code_min_count", 5)
    if len(hyphen_codes) >= min_count:
        score += 0.20

    # ④ 위치 힌트 (1-base)
    priority: list[int] = rules.get("position_priority_pages") or []
    if page_no_1base in priority:
        score += 0.15

    # ⑤ 상단 완성 코드 → 하단 항목 분해 구조 (HYDAC 등)
    # top_code_then_breakdown=true 이면: 페이지 앞 30% 영역에
    # 영숫자+하이픈/슬래시/공백 조합의 완성 코드 문자열이 등장하면 가산
    if rules.get("top_code_then_breakdown"):
        top_section = page_text[: max(len(page_text) // 3, 200)]
        # 완성 코드 패턴: 영문+숫자 혼합, 하이픈·슬래시·공백 포함, 최소 6자
        top_code_pattern = re.compile(
            r"\b[A-Z0-9]{2,}(?:[\s\-/][A-Z0-9]{1,10}){1,6}\b"
        )
        if top_code_pattern.search(top_section):
            score += 0.20

    # ⑥ 번호 구조 조건 (KCC 등) — circled_number_required=true
    # 원문자(①②③…) 또는 괄호 번호((1)(2)(3)…)가 2개 이상 등장하면 가산
    if rules.get("circled_number_required"):
        # 유니코드 원문자 ①-⑳ 또는 (숫자) 패턴
        circled = re.findall(r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|\(\d{1,2}\)", page_text)
        if len(circled) >= 2:
            score += 0.25

    return min(score, 1.0)


def _resolve_rules(
    full_pdf_text: str,
    profile: dict[str, Any],
) -> dict[str, Any] | None:
    """
    product_series_rules 가 있으면 PDF 전문에서 트리거 키워드를 검색해
    시리즈별 오버라이드 규칙을 반환한다. 없으면 기본 model_code_page_rules 반환.
    """
    text_lower = full_pdf_text.lower()
    series_rules: dict[str, Any] = profile.get("product_series_rules") or {}
    for _series_name, series_cfg in series_rules.items():
        trigger_kws: list[str] = series_cfg.get("trigger_keywords") or []
        if any(kw.lower() in text_lower for kw in trigger_kws):
            override = series_cfg.get("model_code_page_rules")
            if override:
                return override
    return profile.get("model_code_page_rules")


def find_model_code_structure_pages(
    full_pdf_text: str,
    profile: dict[str, Any] | None,
) -> ModelCodePageResult:
    """
    메이커 프로필의 model_code_page_rules를 적용해
    형번 체계 페이지(들)를 탐지하고 결과를 반환한다.

    특수 전략
    ---------
    scan_strategy="full_document"
        SEW·Siemens 등 문서 전체가 데이터시트인 경우.
        임계값 없이 max_search_pages 범위 전 페이지를 반환한다.
    product_series_rules
        LS DMPi 등 시리즈 트리거 키워드가 감지되면 해당 시리즈 규칙으로 전환.
    skip_pages_before
        운영 등 앞부분 페이지를 건너뛰어야 하는 경우 (1-base 번호 미만 제외).

    profile 이 None 이거나 rules 가 없으면 found=False 를 반환한다.
    """
    if not profile:
        return ModelCodePageResult()

    rules = _resolve_rules(full_pdf_text, profile)
    if not rules:
        return ModelCodePageResult()

    max_pages: int = rules.get("max_search_pages", 15)
    all_pages = _split_pages(full_pdf_text)

    # ── 전략 A: scan_strategy = "full_document" ─────────────────────────
    # 문서 전체를 데이터시트로 인식 (SEW, Siemens)
    if rules.get("scan_strategy") == "full_document":
        pages_to_use = all_pages[:max_pages]
        if not pages_to_use:
            return ModelCodePageResult()
        return ModelCodePageResult(
            page_indices=list(range(len(pages_to_use))),
            page_texts=pages_to_use,
            scores=[1.0] * len(pages_to_use),
            combined_text="\n\n".join(pages_to_use),
            found=True,
        )

    # ── 전략 B: skip_pages_before — 앞 페이지 제외 후 탐색 (운영 등) ────
    skip_before: int = rules.get("skip_pages_before", 0)  # 1-base
    threshold: float = rules.get("score_threshold", 0.35)

    candidate_pages = all_pages[:max_pages]
    scored: list[tuple[int, float, str]] = []
    for idx, page_text in enumerate(candidate_pages):
        page_no = idx + 1  # 1-base
        if page_no <= skip_before:
            continue
        s = _score_page(page_text, rules, page_no_1base=page_no)
        scored.append((idx, s, page_text))

    hits = [(i, s, t) for i, s, t in scored if s >= threshold]
    hits.sort(key=lambda x: x[1], reverse=True)

    if not hits:
        return ModelCodePageResult()

    return ModelCodePageResult(
        page_indices=[i for i, _, _ in hits],
        page_texts=[t for _, _, t in hits],
        scores=[s for _, s, _ in hits],
        combined_text="\n\n".join(t for _, _, t in hits),
        found=True,
    )


def extract_specs_from_page(
    page_text: str,
    profile: dict[str, Any] | None,
    page_index: int | None = None,
) -> SpecExtractionResult:
    """
    메이커 프로필의 spec_extraction 설정을 적용해
    단일 페이지 텍스트에서 사양 항목을 추출한다.

    ``spec_extraction.enabled`` 가 true 이고 ``fields`` 에 패턴이 정의된
    경우에만 동작한다. 패턴 미지정이거나 추출 값이 하나도 없으면
    found=False 를 반환한다.
    """
    if not profile:
        return SpecExtractionResult()

    spec_cfg: dict[str, Any] | None = profile.get("spec_extraction")
    if not spec_cfg or not spec_cfg.get("enabled"):
        return SpecExtractionResult()

    field_patterns: dict[str, list[str]] = spec_cfg.get("fields") or {}
    extracted: dict[str, str] = {}

    for field_name, patterns in field_patterns.items():
        for pat in patterns:
            try:
                m = re.search(pat, page_text, re.IGNORECASE)
            except re.error:
                continue
            if m:
                extracted[field_name] = m.group(1).strip()
                break  # 첫 번째 매칭 패턴으로 확정

    if not extracted:
        return SpecExtractionResult()

    return SpecExtractionResult(
        fields=extracted,
        source_page_index=page_index,
        found=True,
    )


def extract_specs_from_model_code_pages(
    mc_result: ModelCodePageResult,
    profile: dict[str, Any] | None,
) -> SpecExtractionResult:
    """
    형번 체계 페이지 탐색 결과(ModelCodePageResult)에서
    가장 점수가 높은 페이지를 대상으로 사양을 추출한다.
    """
    if not mc_result.found or not mc_result.page_texts:
        return SpecExtractionResult()

    # 점수 가장 높은 페이지(인덱스 0)를 기준으로 추출
    best_text = mc_result.page_texts[0]
    best_idx = mc_result.page_indices[0] if mc_result.page_indices else None
    return extract_specs_from_page(best_text, profile, page_index=best_idx)


def apply_maker_relax_pdf_source(
    *,
    profile: dict[str, Any] | None,
    model_matched: bool,
    pdf_maker_verified: bool,
    pdf_filename: str | None,
) -> tuple[bool, str | None]:
    """
    프로필에 따라 PDF 제조사 출처 검증을 완화할지 결정.

    Returns:
        (new_pdf_maker_verified, reason_or_none)
    """
    if pdf_maker_verified or not profile:
        return pdf_maker_verified, None
    if not profile.get("pdf_text_maker_often_absent"):
        return pdf_maker_verified, None
    toks = profile.get("relax_pdf_maker_if_filename_contains") or []
    if not isinstance(toks, list):
        toks = []
    if profile.get("relax_only_when_model_matched", True) and not model_matched:
        return pdf_maker_verified, None
    if filename_suggests_maker(pdf_filename, [str(x) for x in toks]):
        return True, "maker_catalog_hints: 파일명 토큰·모델일치로 출처 완화"
    return pdf_maker_verified, None
