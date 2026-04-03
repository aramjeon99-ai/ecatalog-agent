from __future__ import annotations

from datetime import datetime

from ecatalog_agent.models.state import ErrorFlag, FinalDecision, StepResult


def step6_final_decision(
    *,
    step_results: list[StepResult | None],
    error_flags: list[ErrorFlag],
    low_confidence_threshold: float = 0.75,
) -> FinalDecision:
    if error_flags:
        codes = sorted({f.code for f in error_flags})
        return FinalDecision(
            outcome="REJECTED",
            rejection_codes=codes,
            rejection_summary="; ".join(codes),
            low_confidence_items=[],
            decided_at=datetime.utcnow(),
        )

    low_items: list[str] = []
    for sr in step_results:
        if sr is None:
            continue
        # PASS 스텝은 confidence 무관하게 PENDING 유발하지 않음.
        # SKIP 스텝도 confidence가 낮아도 정상적인 "건너뜀"이므로 제외.
        # FAIL 스텝만 저신뢰도 PENDING 대상으로 간주.
        if sr.status == "FAIL" and sr.confidence < low_confidence_threshold:
            low_items.append(f"{sr.step_name}({sr.status})={sr.confidence:.2f}")

    if low_items:
        return FinalDecision(
            outcome="PENDING",
            rejection_codes=[],
            rejection_summary="",
            low_confidence_items=low_items,
            decided_at=datetime.utcnow(),
        )

    return FinalDecision(
        outcome="APPROVED",
        rejection_codes=[],
        rejection_summary="",
        low_confidence_items=[],
        decided_at=datetime.utcnow(),
    )

