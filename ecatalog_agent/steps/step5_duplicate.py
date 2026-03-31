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
    start = time.time()
    flags: list[ErrorFlag] = []

    item_hash = _item_hash(record)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT request_id FROM processed_items WHERE item_hash = ?", (item_hash,))
        row = cur.fetchone()

        if row:
            flags.append(
                ErrorFlag(
                    code="ERR_DUPLICATE_ITEM",
                    step="STEP5",
                    message="Duplicate item detected in local history (MVP).",
                    evidence=f"previous_request_id={row[0]}",
                )
            )
            status = "FAIL"
            confidence = 0.99
        else:
            cur.execute(
                """
                INSERT INTO processed_items (item_hash, request_id, model_name, maker_name, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (
                    item_hash,
                    record.request_id,
                    record.model_name,
                    record.maker_name,
                ),
            )
            conn.commit()
            status = "PASS"
            confidence = 1.0
    finally:
        conn.close()

    elapsed_ms = int((time.time() - start) * 1000)
    return StepResult(
        step_name="STEP5",
        status=status,
        confidence=confidence,
        details={"item_hash": item_hash},
        flags_raised=flags,
        processing_time_ms=elapsed_ms,
    )

