"""PDF가 형번 표(셀 단위 텍스트) 위주일 때 모델명 조합 일치 판별."""

from __future__ import annotations

import re


def normalize_model_compact(s: str) -> str:
    """비교용: 알파벳·숫자만 소문자로 이어붙임."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def order_code_token_in_pdf(token_norm: str, pdf_n: str) -> bool:
    """하나의 하이픈 구간 토큰이 PDF 압축 본문에 표기 근거로 존재하는지."""
    s = token_norm
    if not s:
        return True
    if s in pdf_n:
        return True
    for i in range(len(s), 2, -1):
        if s[:i] in pdf_n:
            return order_code_token_in_pdf(s[i:], pdf_n)
    m = re.fullmatch(r"([a-z])(\d{2,})", s)
    if m is not None and m.group(2) in pdf_n:
        return True
    m2 = re.fullmatch(r"(\d{2,})([a-z]{1,2})", s)
    if m2 is not None and m2.group(1) in pdf_n and m2.group(2) in pdf_n:
        return True
    if re.fullmatch(r"\d{2,}", s) and s in pdf_n:
        return True
    if re.fullmatch(r"[a-z]{1,2}", s) and s in pdf_n:
        return True
    return False


def model_matches_order_code_table(sys_model: str, pdf_compact: str) -> bool:
    """
    하이픈으로 나뉜 각 부분이 카탈로그 표에 흩어진 텍스트로 설명 가능하면 True.
    pdf_compact: PDF 전체를 normalize_model_compact 한 문자열.
    """
    raw_parts = [p.strip() for p in re.split(r"[-_]", sys_model) if p.strip()]
    if len(raw_parts) < 2:
        return False
    sys_flat = normalize_model_compact(sys_model)
    if len(raw_parts) < 3 and len(sys_flat) < 12:
        return False
    return all(order_code_token_in_pdf(normalize_model_compact(p), pdf_compact) for p in raw_parts)
