from __future__ import annotations

import time

from ecatalog_agent.models.state import ErrorFlag, NPRRecord, StepResult
from ecatalog_agent.utils.fuzzy_match import partial_ratio


def step3_spec_comparison(
    record: NPRRecord,
    *,
    pdf_text_sample: str,
    spec_match_threshold: float = 95.0,
) -> StepResult:
    start = time.time()
    flags: list[ErrorFlag] = []

    if not record.specifications:
        return StepResult(
            step_name="STEP3",
            status="SKIP",
            confidence=0.0,
            details={"reason": "No input specifications provided."},
            flags_raised=[],
            processing_time_ms=int((time.time() - start) * 1000),
        )

    # MVP: we treat spec_raw as a single string evidence.
    spec_raw = record.specifications.get("spec_raw")
    if not spec_raw:
        # Fall back to first value
        spec_raw = next(iter(record.specifications.values()), "") or ""

    text = pdf_text_sample or ""
    score = partial_ratio(spec_raw[:5000], text[:8000])  # 0..1
    match_rate = score * 100.0

    # MVP does not calculate structured completeness.
    completeness = 100 if spec_raw else 0

    if completeness < 100:
        flags.append(
            ErrorFlag(
                code="ERR_INCOMPLETE_SPEC",
                step="STEP3",
                message="Specification completeness is below 100% (MVP heuristic).",
                evidence=f"completeness={completeness}",
            )
        )
    elif match_rate < spec_match_threshold:
        flags.append(
            ErrorFlag(
                code="ERR_SPEC_MISMATCH",
                step="STEP3",
                message="Specification content did not match PDF evidence (MVP heuristic).",
                evidence=f"match_rate={match_rate:.1f}",
            )
        )

    status = "PASS" if not flags else ("FAIL" if flags else "SKIP")
    confidence = float(score)

    elapsed_ms = int((time.time() - start) * 1000)
    return StepResult(
        step_name="STEP3",
        status=status,
        confidence=confidence if status == "PASS" else min(confidence, 0.99),
        details={
            "match_rate": match_rate,
            "completeness": completeness,
        },
        flags_raised=flags,
        processing_time_ms=elapsed_ms,
    )

