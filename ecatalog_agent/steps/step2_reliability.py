from __future__ import annotations

import time
from typing import Any

from ecatalog_agent.models.state import ErrorFlag, NPRRecord, StepResult
from ecatalog_agent.utils.text_normalize import normalize_maker


QUOTE_HEADER_KEYWORDS = [
    # 문서 제목/상단 라벨(견적/발주/quotation 류)
    "견적",
    "quotation",
    "quote",
    "발주",
    "purchase order",
]

# 사용자의 기준: 견적서는 "수량 + 금액"이 함께 기재된 경우.
QUANTITY_KEYWORDS = [
    "수량",
    "qty",
    "quantity",
    "pcs",
    "ea",
    "개수",
]

AMOUNT_KEYWORDS = [
    "금액",
    "amount",
    "total",
    "합계",
    "총액",
    "price",
    "단가",
    "견적금액",
    "vat",
    "부가세",
    # 통화 기호/통화명
    "₩",
    "원",
    "$",
    "usd",
    "krw",
    "€",
    "£",
    "¥",
]


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(k and (k.lower() in text) for k in keywords)


def step2_reliability_check(
    record: NPRRecord,
    *,
    pdf_text_sample: str,
    reliability_threshold: float = 70.0,
) -> StepResult:
    start = time.time()
    flags: list[ErrorFlag] = []

    maker_norm = normalize_maker(record.maker_name)
    text = (pdf_text_sample or "").lower()

    has_quote_header = _contains_any(text, QUOTE_HEADER_KEYWORDS)
    has_qty = _contains_any(text, QUANTITY_KEYWORDS)
    has_amount = _contains_any(text, AMOUNT_KEYWORDS)

    # 견적서 판정: 견적/발주 헤더 + 수량 + 금액 동시 존재
    is_quote = has_quote_header and has_qty and has_amount
    if is_quote:
        flags.append(
            ErrorFlag(
                code="ERR_QUOTE_DOCUMENT",
                step="STEP2",
                message="PDF appears to be a quote/order document (qty+amount signature).",
                evidence=f"header={has_quote_header}, qty={has_qty}, amount={has_amount}",
            )
        )

    # Document type classification (rules-only MVP)
    doc_type = "OTHER"
    if any(k in text for k in ["catalog", "catalogue"]):
        doc_type = "CATALOG"
    elif "data sheet" in text or "datasheet" in text:
        doc_type = "DATASHEET"
    elif "drawing" in text:
        doc_type = "DRAWING"
    elif "nameplate" in text or "명판" in text:
        doc_type = "NAMEPLATE"

    logo_detected = bool(maker_norm) and (maker_norm in text or record.maker_name.lower() in text)

    reliability_score = 0
    if logo_detected:
        reliability_score += 40
    if doc_type in ("CATALOG", "DATASHEET"):
        reliability_score += 30
    if "©" in pdf_text_sample or "all rights reserved" in text:
        reliability_score += 20
    if any(ch.isdigit() for ch in text[:2000]):
        reliability_score += 10

    status = "PASS" if not flags else "FAIL"
    confidence = float(max(0.0, min(1.0, reliability_score / 100.0)))
    elapsed_ms = int((time.time() - start) * 1000)
    return StepResult(
        step_name="STEP2",
        status=status,
        confidence=confidence if status == "PASS" else min(confidence, 0.99),
        details={
            "doc_type": doc_type,
            "logo_detected": logo_detected,
            "reliability_score": reliability_score,
            "quote_detection": {
                "has_quote_header": has_quote_header,
                "has_qty": has_qty,
                "has_amount": has_amount,
                "is_quote": is_quote,
            },
        },
        flags_raised=flags,
        processing_time_ms=elapsed_ms,
    )

