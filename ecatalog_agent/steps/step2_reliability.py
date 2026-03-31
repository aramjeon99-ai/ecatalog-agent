from __future__ import annotations

import time
from typing import Any

from ecatalog_agent.models.state import ErrorFlag, NPRRecord, StepResult
from ecatalog_agent.utils.text_normalize import normalize_maker


QUOTE_KEYWORDS = [
    "견적",
    "quotation",
    "quote",
    "발주",
    "purchase order",
    "견적금액",
    "단가",
    "proforma",
]


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

    is_quote = any(k.lower() in text for k in QUOTE_KEYWORDS)
    if is_quote:
        flags.append(
            ErrorFlag(
                code="ERR_QUOTE_DOCUMENT",
                step="STEP2",
                message="PDF appears to be a quote/order document.",
                evidence="quote keywords matched",
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
    if not logo_detected and not is_quote:
        flags.append(
            ErrorFlag(
                code="ERR_NO_LOGO",
                step="STEP2",
                message="Maker/logo evidence not detected in PDF text.",
                evidence=f"maker={record.maker_name}",
            )
        )

    reliability_score = 0
    if logo_detected:
        reliability_score += 40
    if doc_type in ("CATALOG", "DATASHEET"):
        reliability_score += 30
    if "©" in pdf_text_sample or "all rights reserved" in text:
        reliability_score += 20
    if any(ch.isdigit() for ch in text[:2000]):
        reliability_score += 10

    if not flags and reliability_score < reliability_threshold:
        flags.append(
            ErrorFlag(
                code="ERR_LOW_RELIABILITY",
                step="STEP2",
                message=f"Reliability score below threshold ({reliability_threshold}).",
                evidence=f"score={reliability_score}",
            )
        )

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
        },
        flags_raised=flags,
        processing_time_ms=elapsed_ms,
    )

