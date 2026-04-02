from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def _ensure_parent(path: str | Path) -> None:
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)


def init_db(db_path: str | Path) -> None:
    """
    Create required tables if they don't exist.
    """
    _ensure_parent(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_processing_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                row_index INTEGER,
                step_name TEXT,
                status TEXT,
                confidence REAL,
                flags_raised TEXT,
                details TEXT,
                llm_prompt TEXT,
                llm_response TEXT,
                tool_calls TEXT,
                processing_ms INTEGER,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_final_decision (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                outcome TEXT,
                rejection_codes TEXT,
                rejection_summary TEXT,
                low_confidence_items TEXT,
                review_report_path TEXT,
                decided_at TEXT
            )
            """
        )

        # MVP: local duplicate memory (replaces POS-Appia Smart API)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_items (
                item_hash TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                model_name TEXT,
                maker_name TEXT,
                created_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _catalog_item_hash(model_name: str, maker_name: str) -> str:
    """step5_duplicate_check 와 동일한 해시 알고리즘."""
    from ecatalog_agent.utils.text_normalize import normalize_maker, normalize_model
    model = normalize_model(model_name or "")
    maker = normalize_maker(maker_name or "")
    raw = f"{model}||{maker}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_duplicate_baseline(
    db_path: str | Path,
    rows: list[dict[str, str]],
) -> tuple[int, int]:
    """
    중복 검사 기준 데이터를 processed_items 테이블에 적재한다.

    rows 형식: [{"request_id": ..., "model_name": ..., "maker_name": ...}, ...]

    기존 데이터를 모두 삭제하고 새로 적재한다 (전체 교체 방식).

    Returns:
        (inserted, skipped) — 삽입된 행 수, 빈 모델/메이커로 건너뛴 행 수
    """
    _ensure_parent(db_path)
    conn = sqlite3.connect(str(db_path))
    inserted = 0
    skipped = 0
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM processed_items")
        for row in rows:
            model = str(row.get("model_name") or "").strip()
            maker = str(row.get("maker_name") or "").strip()
            if not model or not maker:
                skipped += 1
                continue
            item_hash = _catalog_item_hash(model, maker)
            cur.execute(
                """
                INSERT OR IGNORE INTO processed_items
                  (item_hash, request_id, model_name, maker_name, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (item_hash, str(row.get("request_id") or ""), model, maker),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted, skipped


def get_duplicate_baseline_count(db_path: str | Path) -> int:
    """processed_items 테이블의 현재 행 수."""
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM processed_items")
        count: int = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def insert_step_log(
    db_path: str | Path,
    *,
    request_id: str,
    row_index: int,
    step_name: str,
    status: str,
    confidence: float | None,
    flags_raised: list[dict[str, Any]],
    details: dict[str, Any],
    llm_prompt: str | None,
    llm_response: str | None,
    tool_calls: dict[str, Any] | None,
    processing_ms: int,
    created_at: datetime | None = None,
) -> None:
    created_at = created_at or datetime.utcnow()
    _ensure_parent(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO agent_processing_log
              (request_id, row_index, step_name, status, confidence, flags_raised, details, llm_prompt, llm_response, tool_calls, processing_ms, created_at)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                row_index,
                step_name,
                status,
                confidence,
                json.dumps(flags_raised, ensure_ascii=False),
                json.dumps(details, ensure_ascii=False),
                llm_prompt,
                llm_response,
                json.dumps(tool_calls or {}, ensure_ascii=False),
                processing_ms,
                created_at.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def insert_final_decision(
    db_path: str | Path,
    *,
    request_id: str,
    outcome: str,
    rejection_codes: list[str],
    rejection_summary: str,
    low_confidence_items: list[str],
    review_report_path: str | None,
    decided_at: datetime | None = None,
) -> None:
    decided_at = decided_at or datetime.utcnow()
    _ensure_parent(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO agent_final_decision
              (request_id, outcome, rejection_codes, rejection_summary, low_confidence_items, review_report_path, decided_at)
            VALUES
              (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                outcome,
                json.dumps(rejection_codes, ensure_ascii=False),
                rejection_summary,
                json.dumps(low_confidence_items, ensure_ascii=False),
                review_report_path,
                decided_at.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

