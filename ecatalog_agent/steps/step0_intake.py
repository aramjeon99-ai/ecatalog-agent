from __future__ import annotations

import time

from ecatalog_agent.models.state import AgentState, ErrorFlag, NPRRecord, StepResult


REQUIRED_FIELDS = ["request_id", "model_name", "maker_name", "specifications", "pdf_path"]


def step0_intake(record: NPRRecord) -> StepResult:
    start = time.time()
    flags: list[ErrorFlag] = []

    missing_fields = []
    for f in REQUIRED_FIELDS:
        v = getattr(record, f, None)
        if f == "specifications":
            if not v:
                missing_fields.append(f)
        else:
            if v is None or (isinstance(v, str) and not v.strip()):
                missing_fields.append(f)

    if missing_fields:
        flags.append(
            ErrorFlag(
                code="ERR_MISSING_FIELD",
                step="STEP0",
                message=f"Missing/invalid required field(s): {', '.join(missing_fields)}",
                evidence=",".join(missing_fields),
            )
        )

    # pdf_path existence
    if record.pdf_path:
        import os

        if not os.path.exists(record.pdf_path):
            flags.append(
                ErrorFlag(
                    code="ERR_NO_PDF",
                    step="STEP0",
                    message="PDF path does not exist on disk.",
                    evidence=record.pdf_path,
                )
            )
    else:
        # treat as missing pdf
        flags.append(
            ErrorFlag(
                code="ERR_NO_PDF",
                step="STEP0",
                message="PDF path is not provided.",
                evidence="None",
            )
        )

    status = "PASS" if not flags else "FAIL"
    elapsed_ms = int((time.time() - start) * 1000)
    return StepResult(
        step_name="STEP0",
        status=status,
        confidence=1.0 if status == "PASS" else 0.0,
        details={
            "missing_fields": missing_fields,
            "pdf_path": record.pdf_path,
        },
        flags_raised=flags,
        processing_time_ms=elapsed_ms,
    )

