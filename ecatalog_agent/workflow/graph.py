from __future__ import annotations

from pathlib import Path

from ecatalog_agent.db.logger import insert_final_decision, insert_step_log
from ecatalog_agent.models.state import AgentState, ErrorFlag, NPRRecord, ReviewReport
from ecatalog_agent.output.report_generator import generate_review_report
from ecatalog_agent.steps.step0_intake import step0_intake
from ecatalog_agent.steps.step1_pdf_match import step1_pdf_parse_and_match
from ecatalog_agent.steps.step2_reliability import step2_reliability_check
from ecatalog_agent.steps.step3_spec_compare import step3_spec_comparison
from ecatalog_agent.steps.step4_manufacturer import step4_manufacturer_verify
from ecatalog_agent.steps.step5_duplicate import step5_duplicate_check
from ecatalog_agent.steps.step6_decision import step6_final_decision


def _is_step0_critical(error_flags: list[ErrorFlag]) -> bool:
    critical_codes = {"ERR_MISSING_FIELD", "ERR_NO_PDF"}
    return any(f.code in critical_codes for f in error_flags)


def run_agent_for_record(
    record: NPRRecord,
    *,
    db_path: str | Path,
    output_dir: str | Path,
    manufacturer_names: set[str],
) -> AgentState:
    state = AgentState(record=record)

    # STEP 0
    state.step0_result = step0_intake(record)
    state.error_flags.extend(state.step0_result.flags_raised)

    # If critical step0 fails -> jump to step6
    pdf_text_sample = ""
    parsed_evidence = {}
    if state.step0_result.flags_raised and _is_step0_critical(state.step0_result.flags_raised):
        step_results_for_decision = [state.step0_result] + [None] * 5
    else:
        # STEP 1
        state.step1_result, parsed_evidence = step1_pdf_parse_and_match(record)
        state.error_flags.extend(state.step1_result.flags_raised)
        pdf_text_sample = parsed_evidence.get("pdf_text_sample", "") or ""

        # If we cannot validate model/maker from text, skip remaining steps.
        if state.step1_result.status == "SKIP":
            step_results_for_decision = [state.step0_result, state.step1_result, None, None, None, None]
        else:
            # STEP 2
            state.step2_result = step2_reliability_check(
                record,
                pdf_text_sample=pdf_text_sample,
            )
            state.error_flags.extend(state.step2_result.flags_raised)

            # STEP 3
            state.step3_result = step3_spec_comparison(
                record,
                pdf_text_sample=pdf_text_sample,
            )
            state.error_flags.extend(state.step3_result.flags_raised)

            # STEP 4
            state.step4_result = step4_manufacturer_verify(
                record,
                pdf_text_sample=pdf_text_sample,
                manufacturer_names=manufacturer_names,
            )
            state.error_flags.extend(state.step4_result.flags_raised)

            # STEP 5
            state.step5_result = step5_duplicate_check(
                record,
                db_path=str(db_path),
            )
            state.error_flags.extend(state.step5_result.flags_raised)

            step_results_for_decision = [
                state.step0_result,
                state.step1_result,
                state.step2_result,
                state.step3_result,
                state.step4_result,
                state.step5_result,
            ]

    # STEP 6
    state.final_decision = step6_final_decision(
        step_results=step_results_for_decision,
        error_flags=state.error_flags,
    )

    # Report
    report_path = generate_review_report(state, output_dir=output_dir)
    state.review_report = ReviewReport(request_id=state.record.request_id, excel_path=str(report_path))

    # Persist logs
    for sr in step_results_for_decision:
        if sr is None:
            continue
        insert_step_log(
            str(db_path),
            request_id=state.record.request_id,
            row_index=state.record.row_index,
            step_name=sr.step_name,
            status=sr.status,
            confidence=sr.confidence,
            flags_raised=[f.model_dump() for f in sr.flags_raised],
            details=sr.details,
            llm_prompt=sr.llm_prompt,
            llm_response=sr.llm_response,
            tool_calls={},
            processing_ms=sr.processing_time_ms,
        )

    insert_final_decision(
        str(db_path),
        request_id=state.record.request_id,
        outcome=state.final_decision.outcome,
        rejection_codes=state.final_decision.rejection_codes,
        rejection_summary=state.final_decision.rejection_summary,
        low_confidence_items=state.final_decision.low_confidence_items,
        review_report_path=str(report_path),
        decided_at=state.final_decision.decided_at,
    )

    return state

