from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorFlag(BaseModel):
    code: str
    step: str
    message: str
    evidence: str | None = None


class StepResult(BaseModel):
    step_name: str
    status: Literal["PASS", "FAIL", "SKIP", "ERROR"]
    confidence: float = Field(ge=0.0, le=1.0)
    details: dict[str, Any] = Field(default_factory=dict)
    flags_raised: list[ErrorFlag] = Field(default_factory=list)
    processing_time_ms: int = 0
    llm_prompt: str | None = None
    llm_response: str | None = None


class NPRRecord(BaseModel):
    row_index: int
    request_id: str
    model_name: str
    maker_name: str
    specifications: dict[str, str] = Field(default_factory=dict)
    pdf_path: str | None = None
    homepage_url: str | None = None
    is_foreign: bool | None = None


class FinalDecision(BaseModel):
    outcome: Literal["APPROVED", "REJECTED", "PENDING"]
    rejection_codes: list[str] = Field(default_factory=list)
    rejection_summary: str = ""
    low_confidence_items: list[str] = Field(default_factory=list)
    decided_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewReport(BaseModel):
    request_id: str
    excel_path: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentState(BaseModel):
    record: NPRRecord

    step0_result: StepResult | None = None
    step1_result: StepResult | None = None
    step2_result: StepResult | None = None
    step3_result: StepResult | None = None
    step4_result: StepResult | None = None
    step5_result: StepResult | None = None

    error_flags: list[ErrorFlag] = Field(default_factory=list)
    final_decision: FinalDecision | None = None
    review_report: ReviewReport | None = None

