from __future__ import annotations

import re
import time
from typing import Any

from ecatalog_agent.models.state import ErrorFlag, NPRRecord, StepResult
from ecatalog_agent.tools.pdf_parser import pdf_parse
from ecatalog_agent.utils.fuzzy_match import partial_ratio, token_sort_ratio
from ecatalog_agent.utils.order_code_pdf_match import model_matches_order_code_table, normalize_model_compact
from ecatalog_agent.utils.text_normalize import normalize_maker, normalize_model


def step1_pdf_parse_and_match(
    record: NPRRecord,
    *,
    fuzzy_model_threshold: float = 0.90,
    fuzzy_maker_threshold: float = 0.85,
) -> tuple[StepResult, dict[str, Any]]:
    start = time.time()
    flags: list[ErrorFlag] = []

    parsed = pdf_parse(record.pdf_path)
    text = parsed.get("text", "") or ""

    model_norm = normalize_model(record.model_name)
    maker_norm = normalize_maker(record.maker_name)

    # 카탈로그 형번 표는 뒤쪽 페이지에 있을 수 있어 상한을 넉넉히 둠.
    text_sample = text[:200000].lower()
    text_compact = re.sub(r"[^a-z0-9]+", "", text_sample)

    model_compact = re.sub(r"[^a-z0-9]+", "", model_norm)
    maker_compact = re.sub(r"[^a-z0-9]+", "", maker_norm)

    model_found = bool(model_compact) and (model_compact in text_compact)
    maker_found = bool(maker_compact) and (maker_compact in text_compact)

    model_score = max(
        token_sort_ratio(model_norm, text_sample),
        partial_ratio(model_norm, text_sample),
    )
    maker_score = max(
        token_sort_ratio(maker_norm, text_sample),
        partial_ratio(maker_norm, text_sample),
    )

    pdf_full_compact = normalize_model_compact(text)
    order_code_ok = model_matches_order_code_table(record.model_name, pdf_full_compact)

    # If neither model nor maker appears directly in the extracted text,
    # treat it as "evidence unavailable" to avoid false REJECTED.
    # 형번 표 분해 일치면 모델 근거가 있는 것으로 본다.
    if text and not model_found and not maker_found and not order_code_ok:
        elapsed_ms = int((time.time() - start) * 1000)
        step_result = StepResult(
            step_name="STEP1",
            status="SKIP",
            confidence=0.2,
            details={
                "reason": "Model/Maker not found in extracted text; validation skipped (MVP).",
                "model_found": model_found,
                "maker_found": maker_found,
                "model_score": model_score,
                "maker_score": maker_score,
                "is_image_based": parsed.get("is_image_based"),
                "pages": parsed.get("pages"),
            },
            flags_raised=[],
            processing_time_ms=elapsed_ms,
            llm_prompt=None,
            llm_response=None,
        )
        parsed_evidence = {"pdf_text_sample": text_sample[:50000], "pdf_text_len": len(text)}
        return step_result, parsed_evidence

    if model_score < fuzzy_model_threshold and not order_code_ok:
        flags.append(
            ErrorFlag(
                code="ERR_MODEL_MISMATCH",
                step="STEP1",
                message="Model name did not match PDF evidence.",
                evidence=f"model_score={model_score:.3f}",
            )
        )

    if maker_score < fuzzy_maker_threshold:
        flags.append(
            ErrorFlag(
                code="ERR_MAKER_MISMATCH",
                step="STEP1",
                message="Maker name did not match PDF evidence.",
                evidence=f"maker_score={maker_score:.3f}",
            )
        )

    status = "PASS" if not flags else "FAIL"
    confidence = float((model_score + maker_score) / 2.0)

    elapsed_ms = int((time.time() - start) * 1000)
    step_result = StepResult(
        step_name="STEP1",
        status=status,
        confidence=confidence if status == "PASS" else min(confidence, 0.99),
        details={
            "model_score": model_score,
            "maker_score": maker_score,
            "order_code_table_match": order_code_ok,
            "is_image_based": parsed.get("is_image_based"),
            "pages": parsed.get("pages"),
        },
        flags_raised=flags,
        processing_time_ms=elapsed_ms,
        llm_prompt=None,
        llm_response=None,
    )

    parsed_evidence = {"pdf_text_sample": text_sample[:50000], "pdf_text_len": len(text)}
    return step_result, parsed_evidence

