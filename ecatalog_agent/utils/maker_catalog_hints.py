"""
메이커별 카탈로그/PDF 경향 설정 — data/maker_catalog_hints.json

코드 수정 없이 특정 제조사(예: SMC)의 알려진 패턴을 등록할 수 있다.
"""

from __future__ import annotations

import json
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
