from __future__ import annotations

import os
import re
from pathlib import Path

# Streamlit 진입점: ecatalog_agent import 전에 .env 반영
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import pandas as pd
import streamlit as st

from ecatalog_agent.db.logger import (
    get_duplicate_baseline_count,
    init_db,
    load_duplicate_baseline,
)
from ecatalog_agent.streamlit_poc import (
    APP_CONFIG_PATH,
    STREAMLIT_DB_PATH,
    _DATA_FILENAMES,
    get_qcode_list,
    is_known_error_code,
    load_app_config,
    load_master_data,
    quick_status_check,
    resolve_pdf_base_dir,
    run_qcode_validation,
    save_app_config,
)

st.set_page_config(page_title="e-Catalog POC Validation", layout="wide")
st.title("e-Catalog POC 검증 화면")

# ── 사이드바: 기준 데이터 등록 ─────────────────────────────────────────
config = load_app_config()
data_loaded = config is not None

with st.sidebar:
    st.header("기준 데이터 등록")
    st.caption("최초 1회 업로드 후 자동 로드됩니다.")

    pdf_base_dir_input = st.text_input(
        "PDF 저장 폴더 경로",
        value=(config or {}).get("pdf_base_dir", "3_사양검증자료"),
        placeholder="예: 3_사양검증자료",
    )

    st.markdown("**① 시스템 데이터** (Q코드·사양, 3개 시트)")
    system_data_file = st.file_uploader(
        "1_system_data.xlsx", type=["xlsx", "xls"], key="system_data"
    )

    st.markdown("**② PDF 매핑 파일** (Q코드 ↔ 첨부파일명)")
    pdf_mapping_file = st.file_uploader(
        "pdf 파일명 Q코드랑 정리.xlsx", type=["xlsx", "xls", "csv"], key="pdf_map"
    )

    st.markdown("**③ 기준 제조사 목록** (선택)")
    maker_list_file = st.file_uploader(
        "industrial_manufacturers_list.xlsx", type=["xlsx", "xls", "csv"], key="maker"
    )

    saved_ok = False
    if st.button("데이터 저장", type="primary"):
        if not pdf_base_dir_input.strip():
            st.error("PDF 폴더 경로를 입력하세요.")
        elif not system_data_file or not pdf_mapping_file:
            st.error("① ② 파일은 필수입니다.")
        else:
            data_dir = Path("data")
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / _DATA_FILENAMES["system_data"]).write_bytes(system_data_file.getvalue())
            (data_dir / _DATA_FILENAMES["pdf_mapping"]).write_bytes(pdf_mapping_file.getvalue())
            if maker_list_file:
                (data_dir / _DATA_FILENAMES["maker_list"]).write_bytes(maker_list_file.getvalue())
            save_app_config(
                {
                    "pdf_base_dir": pdf_base_dir_input.strip(),
                    "saved_at": os.path.getmtime(data_dir / _DATA_FILENAMES["system_data"]),
                },
                APP_CONFIG_PATH,
            )
            # 데이터 변경 → 기존 검증 캐시 초기화
            st.session_state.pop("validation_results", None)
            saved_ok = True
            st.rerun()

    if data_loaded and not saved_ok:
        st.success("데이터 로드됨")
    elif saved_ok:
        st.success("저장 완료")

    st.divider()
    if st.button("전체 재검증"):
        st.session_state.pop("validation_results", None)
        st.rerun()
    if st.button("새로고침"):
        st.rerun()

    # ── 중복 검사 기준 데이터 ───────────────────────────────────────────
    st.divider()
    st.markdown("**④ 중복 검사 기준 데이터** (선택)")

    init_db(STREAMLIT_DB_PATH)
    dup_count = get_duplicate_baseline_count(STREAMLIT_DB_PATH)
    if dup_count > 0:
        st.caption(f"현재 적재: {dup_count:,}건")
    else:
        st.caption("기준 데이터 없음 — 중복 검사 건너뜀")

    dup_baseline_file = st.file_uploader(
        "기존 등록 데이터 (Q코드·모델명·메이커)",
        type=["xlsx", "xls"],
        key="dup_baseline",
        help="Q-Code, Model-1, Maker Name-1 컬럼이 있는 엑셀 파일을 올리세요. 여러 시트 모두 읽습니다.",
    )
    if st.button("기준 데이터 적재", type="secondary") and dup_baseline_file:
        try:
            xls = pd.ExcelFile(dup_baseline_file)
            rows: list[dict] = []
            for sheet in xls.sheet_names:
                df_sheet = xls.parse(sheet)
                # 컬럼명 유연하게 인식
                col_map: dict[str, str] = {}
                for c in df_sheet.columns:
                    cl = str(c).strip().lower()
                    if cl in ("q-code", "q_code", "qcode"):
                        col_map["request_id"] = c
                    elif cl in ("model-1", "model_1", "model name", "model"):
                        col_map["model_name"] = c
                    elif cl in ("maker name-1", "maker name_1", "maker name", "maker"):
                        col_map["maker_name"] = c
                if "model_name" not in col_map or "maker_name" not in col_map:
                    continue
                for _, r in df_sheet.iterrows():
                    rows.append({
                        "request_id": str(r.get(col_map.get("request_id", ""), "") or ""),
                        "model_name":  str(r.get(col_map["model_name"], "") or ""),
                        "maker_name":  str(r.get(col_map["maker_name"], "") or ""),
                    })
            inserted, skipped = load_duplicate_baseline(STREAMLIT_DB_PATH, rows)
            st.success(f"적재 완료: {inserted:,}건 (빈 항목 {skipped:,}건 제외)")
            st.rerun()
        except Exception as e:
            st.error(f"적재 실패: {e}")

    st.divider()
    st.caption("데이터 초기화")
    if st.button("🗑️ 저장 데이터 초기화", type="secondary"):
        data_dir = Path("data")
        for fname in _DATA_FILENAMES.values():
            fp = data_dir / fname
            if fp.exists():
                fp.unlink()
        if APP_CONFIG_PATH.exists():
            APP_CONFIG_PATH.unlink()
        st.session_state.clear()
        st.rerun()

# ── 데이터 로드 ────────────────────────────────────────────────────────
qcode_master_df = spec_detail_df = pdf_mapping_df = maker_list_df = None
pdf_base_dir = ""

if config:
    pdf_base_dir = config.get("pdf_base_dir", "3_사양검증자료")
    try:
        qcode_master_df, spec_detail_df, pdf_mapping_df, maker_list_df = load_master_data(config)
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}")

if qcode_master_df is None:
    st.info("사이드바에서 파일을 업로드하고 '데이터 저장'을 눌러주세요.")
    st.stop()

qcode_list = get_qcode_list(qcode_master_df)

st.sidebar.caption(
    "PDF 폴더(해석): `" + str(resolve_pdf_base_dir(pdf_base_dir)) + "`"
)
st.sidebar.caption(
    "메이커별 카탈로그 경향: `data/maker_catalog_hints.json` 편집 (저장 후 재검증)"
)

# 검증 결과 캐시: {q_code: result_dict}
if "validation_results" not in st.session_state:
    st.session_state["validation_results"] = {}


# ── 헬퍼 함수 ──────────────────────────────────────────────────────────
def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", s.lower())


def _extract_pdf_value(title: str, sys_value: str, pdf_text: str) -> tuple[str, str]:
    if not pdf_text:
        return "없음", "없음"
    text_lower = pdf_text.lower()
    title_lower = title.lower()
    sys_norm = _normalize(sys_value)
    idx = text_lower.find(title_lower)
    if idx == -1:
        for word in title_lower.split():
            if len(word) > 3:
                idx = text_lower.find(word)
                if idx != -1:
                    break
    if idx != -1:
        window = pdf_text[idx: idx + 200]
        win_norm = _normalize(window)
        if sys_norm and sys_norm in win_norm:
            return sys_value, "일치"
        snippet = re.sub(r"\s+", " ", window[:80]).strip()
        return snippet, "불일치"
    else:
        if sys_norm and sys_norm in _normalize(pdf_text):
            return sys_value, "일치"
        return "없음", "없음"


def _compare_maker(sys_maker: str, best_matched: str | None, score: float) -> tuple[str, str]:
    if not best_matched:
        return "비교 불가", "(없음)"
    if score >= 85.0:
        return "일치", best_matched
    return "불일치", best_matched or "(없음)"


def _color_match(val: str) -> str:
    if val == "일치":
        return "background-color: #d4edda; color: #155724"
    if val in ("불일치", "없음"):
        return "background-color: #f8d7da; color: #721c24"
    return "background-color: #fff3cd; color: #856404"


# ── 사전 상태 계산 (PDF 존재 여부 필터용) ──────────────────────────────
@st.cache_data(show_spinner="상태 사전 계산 중...")
def _compute_statuses(
    qcodes: tuple[str, ...],
    _qm: pd.DataFrame,
    _pm: pd.DataFrame,
    _ml: pd.DataFrame,
    base_dir: str,
) -> dict[str, tuple[str, bool]]:
    return {
        q: quick_status_check(q, _qm, _pm, _ml, base_dir)
        for q in qcodes
    }

statuses = _compute_statuses(
    tuple(qcode_list),
    qcode_master_df, pdf_mapping_df, maker_list_df,
    pdf_base_dir,
)

# PDF 있는 Q코드만 목록에 표시
qcodes_with_pdf = [q for q in qcode_list if statuses.get(q, (None, False))[1]]


# ── 검증 결과 다이얼로그 ───────────────────────────────────────────────
@st.dialog("검증 결과", width="large")
def show_validation_dialog(q_code: str) -> None:
    st.subheader(f"Q코드: {q_code}")

    if is_known_error_code(q_code):
        st.warning("⚠️ Q3 오류코드 — 모델·메이커 불일치 또는 잘못 등록된 자료입니다.")

    # 캐시된 결과 사용, 없으면 검증 실행
    if q_code not in st.session_state["validation_results"]:
        with st.spinner("검증 중..."):
            try:
                r = run_qcode_validation(
                    q_code=q_code,
                    qcode_master_df=qcode_master_df,
                    spec_detail_df=spec_detail_df,
                    pdf_mapping_df=pdf_mapping_df,
                    maker_list_df=maker_list_df,
                    pdf_base_dir=pdf_base_dir,
                )
                st.session_state["validation_results"][q_code] = r
            except Exception as e:
                st.error(f"검증 오류: {e}")
                return

    result = st.session_state["validation_results"][q_code]

    if result.get("error"):
        st.error(f"검증 오류: {result['error']}")
        return

    outcome   = result["outcome"]
    judgment  = result.get("judgment") or {}
    pdf_text  = judgment.get("pdf_full_text") or ""
    url_value = judgment.get("url_value") or "(미입력)"
    sys_model = judgment.get("model_name") or "(미입력)"
    sys_maker = judgment.get("maker_name") or "(미입력)"
    best_maker = judgment.get("best_matched_maker")
    similarity = judgment.get("similarity_score", 0.0)
    expected_specs = judgment.get("expected_specs") or []
    pdf_file  = judgment.get("connected_pdf_filename") or "없음"

    model_matched  = judgment.get("model_matched")
    maker_matched  = bool(judgment.get("maker_matched"))
    model_pdf_val  = judgment.get("model_pdf_val") or "(없음)"
    model_result   = "일치" if model_matched else "불일치"
    maker_result, maker_pdf_val = _compare_maker(sys_maker, best_maker, similarity)

    agent_state = result.get("agent_state")
    step4 = agent_state.step4_result if agent_state else None
    step4_details = (step4.details or {}) if step4 else {}
    step4_status = step4.status if step4 else "-"
    step4_conf   = step4.confidence if step4 else 0.0

    # ── 종합 판단 배너 ──────────────────────────────────────────────
    color = {"APPROVED": "green", "REJECTED": "red", "PENDING": "orange"}.get(outcome, "gray")
    st.markdown(f"### 종합 판단: :{color}[**{outcome}**]")
    st.caption(result.get("summary", ""))
    st.divider()

    # ── 1) 주요 사양 비교 결과 ──────────────────────────────────────
    st.markdown("#### 1) 주요 사양 비교 결과")

    both_match = (model_result == "일치" and maker_result == "일치")
    any_incomparable = (model_result == "비교 불가" or maker_result == "비교 불가")
    overall_label = "일치" if both_match else ("비교 불가" if any_incomparable else "불일치")
    label_color = {"일치": "green", "불일치": "red", "비교 불가": "orange"}[overall_label]
    st.markdown(f"**결과: :{label_color}[{overall_label}]**")

    with st.container(border=True):
        st.markdown(f"- **Model-1**: 시스템 `{sys_model}` vs e-catalog `{model_pdf_val}` → **{model_result}**")
        st.markdown(f"- **Maker-1**: 시스템 `{sys_maker}` vs e-catalog `{maker_pdf_val}` (유사도 {similarity:.1f}) → **{maker_result}**")
        src = pdf_file if pdf_text else "(PDF 없음)"
        st.caption(f"근거 자료: e-catalog — {src}")
        pdf_mv = judgment.get("pdf_maker_verified")
        if pdf_mv is not None:
            st.caption(
                f"첨부 PDF 제조사 일치(텍스트 또는 GPT 비전): "
                f"{'✅' if pdf_mv else '❌'}"
            )
        vision_r = judgment.get("vision_order_code_result")
        if vision_r:
            with st.expander("GPT 비전 — 형번·제조사 자료 판별", expanded=False):
                if vision_r.get("ok"):
                    vp = vision_r.get("parsed") or {}
                    st.json(
                        {
                            "선택_페이지": vision_r.get("selected_page_indices"),
                            "동일제조사자료": vp.get("is_same_manufacturer_document"),
                            "형번조합가능": vp.get("can_compose_model_from_order_tables"),
                            "신뢰도": vp.get("confidence"),
                            "근거": vp.get("reason_ko"),
                            "사양힌트": vp.get("spec_hints"),
                        }
                    )
                else:
                    st.warning(vision_r.get("error", "비전 호출 실패"))
        elif pdf_text and not os.environ.get("OPENAI_API_KEY", "").strip():
            st.caption("형번 표 OCR(GPT 비전)을 쓰려면 환경 변수 `OPENAI_API_KEY`를 설정하세요.")
        mh = judgment.get("maker_catalog_hint") or {}
        if mh.get("matched"):
            st.caption(f"메이커 힌트(JSON): {mh.get('notes_ko') or '등록됨'}")
            if mh.get("relax_pdf_source_reason"):
                st.success(mh["relax_pdf_source_reason"])

    st.divider()

    # 사양별 PDF 추출값 사전 계산
    spec_pdf_vals: list[dict] = []
    for spec in expected_specs:
        title = spec.get("title", "")
        value = spec.get("value", "") or "(미입력)"
        pdf_val, match_st = _extract_pdf_value(title, value, pdf_text)
        spec_pdf_vals.append({"title": title, "sys_value": value,
                               "pdf_value": pdf_val, "match": match_st})

    # ── 2) 사양 값 존재 여부 표 ────────────────────────────────────
    st.markdown("#### 2) 사양 값 존재 여부")

    exist_rows = [
        {"항목": "Model-1", "시스템 입력값": sys_model,
         "사양검증자료 값": model_pdf_val,
         "판정": model_result if model_result != "비교 불가" else "없음"},
        {"항목": "Maker-1", "시스템 입력값": sys_maker,
         "사양검증자료 값": maker_pdf_val,
         "판정": "일치" if maker_result == "일치" else "없음"},
    ]
    for s in spec_pdf_vals:
        exist_rows.append({"항목": s["title"], "시스템 입력값": s["sys_value"],
                            "사양검증자료 값": s["pdf_value"], "판정": s["match"]})

    st.dataframe(
        pd.DataFrame(exist_rows).style.applymap(_color_match, subset=["판정"]),
        use_container_width=True, hide_index=True,
    )
    st.divider()

    # ── 3) 제조업 여부 검증 ────────────────────────────────────────
    st.markdown("#### 3) 제조업 여부 검증")

    mfr_v       = judgment.get("mfr_verification") or {}
    maker_type  = mfr_v.get("maker_type", "신규공급사")
    v_step      = mfr_v.get("verification_step", 0)
    v_is_for    = mfr_v.get("is_foreign")
    v_evidence  = mfr_v.get("evidence", "")
    v_snippets  = mfr_v.get("web_snippets") or []

    _MAKER_TYPE_BADGE = {
        "기존 제조업": ":green[✅ 기존 제조업]",
        "해외제조사":  ":blue[🌐 해외제조사]",
        "신규공급사":  ":orange[⚠️ 신규공급사 — 사람검토]",
    }
    badge = _MAKER_TYPE_BADGE.get(maker_type, ":grey[미확인]")
    st.markdown(f"**판정: {badge}**")

    with st.container(border=True):
        # 1단계
        st.markdown(
            f"**1단계 — 기준 제조사 목록(industrial_manufacturers_list)**: "
            f"{'✅ 등록됨' if maker_matched else '❌ 미등록'} "
            f"(유사도 {similarity:.1f})"
        )

        # 2단계 (목록 미등록 시 실행됨)
        if v_step >= 2:
            st.markdown("**2단계 — 인터넷 검색 (1차: AI 분석, 2차: 일반 검색):**")
            foreign_txt = "🌐 해외 법인 형태 감지" if v_is_for else "🏢 해외 법인 미감지"
            st.markdown(f"  - {foreign_txt}")
            if maker_type == "해외제조사":
                st.markdown("  - ✅ 해외 제조사로 확인됨")
            elif maker_type == "신규공급사":
                st.markdown("  - ⚠️ 제조사 여부 미확인 → **신규공급사**로 분류, 담당자 검토 필요")
            st.caption(f"  근거: {v_evidence}")
            if v_snippets:
                with st.expander(f"인터넷 검색 결과 ({len(v_snippets)}건)", expanded=False):
                    for sn in v_snippets[:5]:
                        st.markdown(f"**{sn.get('title','')}**")
                        st.caption(sn.get("url",""))
                        st.write(sn.get("snippet","")[:200])
                        st.divider()
        elif not maker_matched:
            st.markdown("**2단계 — 인터넷 검색**: ⏭️ 검색 미실행")

        # agent step4 플래그 보조 표시
        step4_flags = step4.flags_raised if step4 else []
        if step4_flags:
            for fl in step4_flags:
                st.warning(f"{fl.code}: {fl.message}")

    st.divider()

    # ── 4) 종합 판단 ───────────────────────────────────────────────
    st.markdown("#### 4) 종합 판단")
    missing_specs = [r["항목"] for r in exist_rows[2:] if r["판정"] in ("없음", "불일치")]
    active_rules  = judgment.get("active_rules") or []

    with st.container(border=True):
        st.markdown(f"**결과: :{color}[{outcome}]**")
        st.markdown(f"- 주요 사양 일치: **{overall_label}**")
        st.markdown(
            f"- 제조업 여부: **{step4_status}** "
            f"({'목록 등록' if step4_details.get('matched_list') else 'PDF 근거'})"
        )
        if missing_specs:
            st.markdown(f"- 미확인/불일치 사양: {', '.join(missing_specs)}")
        else:
            st.markdown("- 사양 존재 여부: 모든 항목 확인됨")

        if active_rules:
            st.divider()
            st.markdown("**📋 회송 사유 및 재신청 안내:**")
            for rule in active_rules:
                st.warning(f"**[{rule['id']}] {rule['category']}**\n\n{rule['message']}")
        else:
            st.markdown(f"- 사유: {result.get('summary', '')}")

    st.divider()

    # ── 5) 확장 사양표 ─────────────────────────────────────────────
    st.markdown("#### 5) 확장 사양표")

    ext_rows = [
        {"항목": "Model-1", "시스템 입력값": sys_model,
         "사양검증자료(PDF) 값": model_pdf_val, "URL 사양검증자료": url_value,
         "판정": model_result},
        {"항목": "Maker-1", "시스템 입력값": sys_maker,
         "사양검증자료(PDF) 값": maker_pdf_val, "URL 사양검증자료": url_value,
         "판정": maker_result},
    ]
    for s in spec_pdf_vals:
        ext_rows.append({
            "항목": s["title"],
            "시스템 입력값": s["sys_value"],
            "사양검증자료(PDF) 값": s["pdf_value"],
            "URL 사양검증자료": "(미입력)",
            "판정": s["match"],
        })

    st.dataframe(
        pd.DataFrame(ext_rows).style.applymap(_color_match, subset=["판정"]),
        use_container_width=True, hide_index=True,
    )

    # ── 6) 웹 검색 2차 검증 ────────────────────────────────────────
    web_result = judgment.get("web_search_result")
    if web_result is not None:
        st.divider()
        st.markdown("#### 6) 웹 검색 2차 검증")
        if not web_result.get("searched"):
            st.info(f"웹 검색 미실행: {web_result.get('evidence_summary', '')}")
        else:
            mfr_confirmed = web_result.get("manufacturer_confirmed")
            model_online  = web_result.get("model_found_online", False)
            pdf_online    = web_result.get("matched_pdf_url")
            evidence_sum  = web_result.get("evidence_summary", "")

            mfr_label = (
                ":green[✅ 제조사 확인]" if mfr_confirmed is True
                else (":red[❌ 대리점/판매사 의심]" if mfr_confirmed is False
                      else ":orange[⚠️ 제조사 여부 미확인]")
            )
            model_label = ":green[✅ 온라인 확인됨]" if model_online else ":red[❌ 온라인 미확인]"

            with st.container(border=True):
                st.markdown(f"- **모델명 온라인 확인**: {model_label}")
                st.markdown(f"- **제조사 여부**: {mfr_label}")
                if pdf_online:
                    st.markdown(f"- **온라인 PDF 발견**: [{pdf_online}]({pdf_online})")
                st.caption(f"근거 요약: {evidence_sum}")

            snippets = web_result.get("search_snippets") or []
            if snippets:
                with st.expander(f"웹 검색 결과 ({len(snippets)}건)", expanded=False):
                    for sn in snippets[:5]:
                        st.markdown(f"**{sn.get('title', '')}**")
                        st.caption(sn.get("url", ""))
                        st.write(sn.get("snippet", "")[:200])
                        st.divider()

    # ── 근거 자료 (접기) ───────────────────────────────────────────
    with st.expander("PDF 원문 텍스트 (접기/펼치기)", expanded=False):
        st.caption(f"파일: {pdf_file}")
        st.text((pdf_text or "(PDF 텍스트 없음)")[:3000])


# ── Q코드 목록 ────────────────────────────────────────────────────────
_OUTCOME_TO_STATUS = {
    "APPROVED": "자동승인",
    "REJECTED": "자동회송",
    "PENDING":  "사람승인",
}
_STATUS_BADGE = {
    "자동승인": ":blue[● 자동승인]",
    "자동회송": ":red[● 자동회송]",
    "사람승인": ":orange[● 사람승인]",
}
_STATUS_BADGE_DIM = {
    "자동승인": ":grey[○ 자동승인 추정]",
    "자동회송": ":grey[○ 자동회송 추정]",
    "사람승인": ":grey[○ 사람승인 추정]",
}

# 요약 테이블 빌드 + PDF 없는 항목 제외
summary_rows = []
for q in qcodes_with_pdf:
    rows = qcode_master_df[qcode_master_df["Q-Code"].astype(str).str.strip() == q]
    r = rows.iloc[0] if len(rows) > 0 else None
    result = st.session_state["validation_results"].get(q)
    outcome = result["outcome"] if result else None
    summary_rows.append({
        "Q-Code": q,
        "품명":   (r.get("품명")        if r is not None else "") or "",
        "제조사": (r.get("Maker Name-1") if r is not None else "") or "",
        "모델명": (r.get("Model-1")      if r is not None else "") or "",
        "_outcome": outcome,
    })

total = len(summary_rows)
cnt_map = {"APPROVED": 0, "REJECTED": 0, "PENDING": 0}
for row in summary_rows:
    if row["_outcome"] in cnt_map:
        cnt_map[row["_outcome"]] += 1

st.subheader(f"Q코드 목록 (PDF 있는 항목: {total}건)")
mc1, mc2, mc3 = st.columns(3)
mc1.metric("🔵 자동승인", cnt_map["APPROVED"])
mc2.metric("🔴 자동회송", cnt_map["REJECTED"])
mc3.metric("🟠 사람승인", cnt_map["PENDING"])

# 검색 필터
search = st.text_input("🔍 Q코드 / 품명 / 제조사 검색", value="", placeholder="입력 시 필터링")
if search.strip():
    kw = search.strip().lower()
    summary_rows = [
        r for r in summary_rows
        if kw in r["Q-Code"].lower()
        or kw in r["품명"].lower()
        or kw in r["제조사"].lower()
        or kw in r["모델명"].lower()
    ]

if not summary_rows:
    st.warning("검색 결과가 없습니다.")
    st.stop()

# 헤더
hcols = st.columns([2, 2, 2, 3, 2, 1])
for i, label in enumerate(["**Q-Code**", "**품명**", "**제조사**", "**모델명**", "**검증 결과**", "**상세**"]):
    hcols[i].markdown(label)
st.divider()

# 각 행 렌더링
for row in summary_rows:
    q = row["Q-Code"]
    c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 2, 3, 2, 1])
    q_label = f"⚠️ {q}" if is_known_error_code(q) else q
    c1.write(q_label)
    c2.write(row["품명"])
    c3.write(row["제조사"])
    c4.write(row["모델명"])

    outcome = row["_outcome"]
    if outcome:
        status = _OUTCOME_TO_STATUS.get(outcome, "사람승인")
        c5.markdown(_STATUS_BADGE[status])
    else:
        quick_status = statuses.get(q, ("사람승인", True))[0]
        c5.markdown(_STATUS_BADGE_DIM[quick_status])

    if c6.button("검증", key=f"btn_{q}"):
        show_validation_dialog(q)
