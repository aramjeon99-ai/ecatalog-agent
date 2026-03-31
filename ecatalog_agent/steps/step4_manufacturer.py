from __future__ import annotations

import time

from ecatalog_agent.models.state import ErrorFlag, NPRRecord, StepResult
from ecatalog_agent.utils.text_normalize import normalize_maker


NOT_MANUFACTURER_KEYWORDS = [
    "agent",
    "distributor",
    "대리점",
    "판매",
    "대행",
    "총판",
]


def step4_manufacturer_verify(
    record: NPRRecord,
    *,
    pdf_text_sample: str,
    manufacturer_names: set[str],
) -> StepResult:
    start = time.time()
    flags: list[ErrorFlag] = []
    text = (pdf_text_sample or "").lower()

    maker_norm = normalize_maker(record.maker_name)
    known = maker_norm in manufacturer_names

    if known:
        status = "PASS"
        confidence = 0.9
        details = {"is_manufacturer": True, "matched_list": True}
    else:
        # Heuristic: if maker appears in PDF, we don't hard-reject in MVP.
        maker_in_pdf = bool(maker_norm) and (maker_norm in text or record.maker_name.lower() in text)
        if maker_in_pdf:
            status = "SKIP"
            confidence = 0.6
            details = {"is_manufacturer": True, "matched_list": False, "maker_in_pdf": True}
        else:
            # If we see distributor-like wording, treat as failure.
            is_not_manu = any(k in text for k in NOT_MANUFACTURER_KEYWORDS)
            if is_not_manu:
                flags.append(
                    ErrorFlag(
                        code="ERR_NOT_MANUFACTURER",
                        step="STEP4",
                        message="Maker doesn't look like a manufacturer (MVP heuristic).",
                        evidence="distributor-like keywords found",
                    )
                )
                status = "FAIL"
                confidence = 0.1
            else:
                status = "SKIP"
                confidence = 0.5
                details = {"is_manufacturer": False, "matched_list": False, "maker_in_pdf": False}

    elapsed_ms = int((time.time() - start) * 1000)
    return StepResult(
        step_name="STEP4",
        status=status,
        confidence=confidence if status != "FAIL" else confidence,
        details=details,
        flags_raised=flags,
        processing_time_ms=elapsed_ms,
    )

