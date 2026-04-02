from __future__ import annotations

import hashlib
import sqlite3
import time

from ecatalog_agent.models.state import ErrorFlag, NPRRecord, StepResult
from ecatalog_agent.utils.text_normalize import normalize_maker, normalize_model


def _item_hash(record: NPRRecord) -> str:
    model = normalize_model(record.model_name)
    maker = normalize_maker(record.maker_name)
    raw = f"{model}||{maker}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def step5_duplicate_check(
    record: NPRRecord,
    *,
    db_path: str,
) -> StepResult:
    """중복 검사.

    ``processed_items`` 테이블에 사전에 적재된 기준 데이터가 없으면
    비교 대상 자체가 없으므로 SKIP을 반환한다.
    기준 데이터가 있을 때만 중복 여부를 판단한다.
    """
    start = time.time()
    flags: list[ErrorFlag] = []

    item_hash = _item_hash(record)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # 비교 기준 데이터 존재 여부 확인
        cur.execute("SELECT COUNT(*) FROM processed_items")
        catalog_count: int = cur.fetchone()[0]

        if catalog_count == 0:
            # 비교할 기준 데이터가 없으므로 검사 건너뜀
            status = "SKIP"
            confidence = 1.0
            details = {"item_hash": item_hash, "skip_reason": "no_catalog_data"}
        else:
            cur.execute("SELECT request_id FROM processed_items WHERE item_hash = ?", (item_hash,))
            row = cur.fetchone()

            if row:
                flags.append(
                    ErrorFlag(
                        code="ERR_DUPLICATE_ITEM",
                        step="STEP5",
                        message="동일한 모델·메이커 조합이 기준 데이터에 이미 등록되어 있습니다.",
                        evidence=f"existing_request_id={row[0]}",
                    )
                )
                status = "FAIL"
                confidence = 0.99
            else:
                status = "PASS"
                confidence = 1.0
            details = {"item_hash": item_hash}
    finally:
        conn.close()

    elapsed_ms = int((time.time() - start) * 1000)
    return StepResult(
        step_name="STEP5",
        status=status,
        confidence=confidence,
        details=details,
        flags_raised=flags,
        processing_time_ms=elapsed_ms,
    )

