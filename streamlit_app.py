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

from ecatalog_agent.streamlit_poc import (
    APP_CONFIG_PATH,
    STREAMLIT_DB_PATH,
    get_qcode_list,
    is_known_error_code,
    load_app_config,
    load_master_data,
    quick_status_check,
    resolve_pdf_base_dir,
    run_qcode_validation,
    save_app_config,
    _DATA_FILENAMES,
)
from ecatalog_agent.db.logger import load_duplicate_baseline, get_duplicate_baseline_count

st.set_page_config(page_title="POSCO MRO e-Catalog 검증", layout="wide", page_icon="🏭")

# ── POSCO MRO e-Catalog 스타일 CSS ───────────────────────────────────────
st.markdown("""
<style>
/* ── 전체 배경 ── */
.stApp { background-color: #f4f6f9; }

/* ── 최상단 헤더 바 ── */
[data-testid="stAppViewContainer"] > .main > div:first-child { padding-top: 0 !important; }

/* ── 사이드바 — 흰 배경 + 네이비 타이틀 헤더 ── */
[data-testid="stSidebar"] {
    background: #f8fafc;
    border-right: 2px solid #e2e8f0;
}

/* 사이드바 POSCO 타이틀 헤더 */
.sidebar-header {
    background: linear-gradient(135deg, #003087 0%, #0057b8 100%);
    margin: -1rem -1rem 1rem -1rem;
    padding: 18px 20px 14px 20px;
    border-bottom: 3px solid #ff6b00;
}
.sidebar-header .sb-brand {
    font-size: 11px; font-weight: 700; color: #7bb3ff;
    letter-spacing: 2px; text-transform: uppercase;
}
.sidebar-header .sb-title {
    font-size: 18px; font-weight: 800; color: #fff; margin: 2px 0 0 0;
}

/* 사이드바 내 모든 텍스트 — 어두운 색 */
[data-testid="stSidebar"] * { color: #1e293b !important; }

/* 섹션 라벨 (①②③④) */
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stMarkdown strong {
    color: #003087 !important;
    font-weight: 600 !important;
    font-size: 13px !important;
}

/* 텍스트 입력 */
[data-testid="stSidebar"] input[type="text"] {
    background: #fff !important;
    color: #1e293b !important;
    border: 1px solid #cbd5e1 !important;
    border-radius: 6px !important;
}
[data-testid="stSidebar"] input[type="text"]:focus {
    border-color: #003087 !important;
    box-shadow: 0 0 0 2px rgba(0,48,135,0.12) !important;
}

/* 파일 업로더 */
[data-testid="stSidebar"] [data-testid="stFileUploader"] {
    background: #fff;
    border-radius: 8px;
    border: 1px solid #e2e8f0;
    padding: 8px;
}
[data-testid="stSidebar"] [data-testid="stFileUploader"] * { color: #1e293b !important; }
[data-testid="stSidebar"] [data-testid="stFileUploader"] button {
    background: #003087 !important;
    color: #fff !important;
    border: none !important;
    border-radius: 6px !important;
    font-size: 13px !important;
    padding: 6px 16px !important;
}
[data-testid="stSidebar"] [data-testid="stFileUploader"] button:hover {
    background: #0057b8 !important;
}

/* 캡션/안내 텍스트 */
[data-testid="stSidebar"] small,
[data-testid="stSidebar"] .stCaption { color: #64748b !important; font-size: 12px !important; }

/* 구분선 */
[data-testid="stSidebar"] hr { border-color: #e2e8f0 !important; }

/* 버튼 — 데이터 저장 / 재검증 / 새로고침 */
[data-testid="stSidebar"] .stButton > button {
    background: #003087 !important;
    color: #fff !important;
    border: none !important;
    border-radius: 6px !important;
    font-weight: 600 !important;
    width: 100% !important;
    padding: 8px 0 !important;
}
[data-testid="stSidebar"] .stButton > button:hover { background: #0057b8 !important; }
/* 저장 버튼(primary)만 오렌지 */
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: #ff6b00 !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
    background: #e05c00 !important;
}

/* 성공/에러 알림 */
[data-testid="stSidebar"] .stSuccess { background: #f0fdf4; border-left: 3px solid #00a651; }
[data-testid="stSidebar"] .stError   { background: #fef2f2; border-left: 3px solid #cc0000; }

/* ── 헤더 배너 — 컨테이너 전체 너비로 확장 ── */
.posco-header {
    background: linear-gradient(135deg, #003087 55%, #0057b8 100%);
    border-radius: 10px;
    padding: 28px 48px 24px 48px;
    margin: -0.5rem -1rem 20px -1rem;   /* 좌우 마진 음수로 컨테이너 폭 초과 */
    display: flex;
    align-items: center;
    gap: 20px;
    box-shadow: 0 3px 12px rgba(0,48,135,0.22);
    min-height: 90px;
}
.posco-header .brand {
    font-size: 14px; font-weight: 700; color: #7bb3ff;
    letter-spacing: 3px; text-transform: uppercase; margin-bottom: 4px;
}
.posco-header .title { font-size: 32px; font-weight: 800; color: #fff; margin: 0; line-height: 1.1; }
.posco-header .subtitle { font-size: 14px; color: #a8c8ff; margin-top: 6px; }
.posco-header .badge {
    margin-left: auto;
    background: rgba(255,255,255,0.18);
    border: 1px solid rgba(255,255,255,0.3);
    border-radius: 24px;
    padding: 8px 20px;
    font-size: 13px; color: #fff; white-space: nowrap;
}

/* ── 카드 ── */
.posco-card {
    background: #fff;
    border-radius: 8px;
    padding: 16px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    margin-bottom: 12px;
    border-left: 4px solid #003087;
}
.posco-card.orange { border-left-color: #ff6b00; }
.posco-card.green  { border-left-color: #00a651; }
.posco-card.red    { border-left-color: #cc0000; }

/* ── 결과 배지 ── */
.badge-approved {
    display:inline-block; background:#00a651; color:#fff;
    padding:3px 12px; border-radius:12px; font-size:13.8px; font-weight:700;
}
.badge-rejected {
    display:inline-block; background:#cc0000; color:#fff;
    padding:3px 12px; border-radius:12px; font-size:13.8px; font-weight:700;
}
.badge-pending {
    display:inline-block; background:#ff6b00; color:#fff;
    padding:3px 12px; border-radius:12px; font-size:13.8px; font-weight:700;
}
.badge-skipped {
    display:inline-block; background:#666; color:#fff;
    padding:3px 12px; border-radius:12px; font-size:13.8px; font-weight:700;
}

/* ── 테이블 행 색상 ── */
.row-approved { background: #e8f5e9 !important; }
.row-rejected { background: #ffebee !important; }
.row-pending  { background: #fff3e0 !important; }

/* ── 기본 버튼 ── */
.stButton > button[kind="primary"] {
    background: #003087 !important;
    border: none !important;
    color: #fff !important;
    font-weight: 600 !important;
    border-radius: 4px !important;
}
.stButton > button[kind="primary"]:hover { background: #0057b8 !important; }

/* ── 탭 ── */
.stTabs [data-baseweb="tab-list"] {
    background: #fff;
    border-radius: 8px 8px 0 0;
    border-bottom: 2px solid #003087;
    padding: 0 8px;
}
.stTabs [data-baseweb="tab"] {
    font-weight: 600;
    color: #003087;
    padding: 8px 20px;
}
.stTabs [aria-selected="true"] {
    background: #003087 !important;
    color: #fff !important;
    border-radius: 6px 6px 0 0;
}

/* ── expander ── */
.streamlit-expanderHeader {
    background: #eef3fb !important;
    border-radius: 6px !important;
    font-weight: 600 !important;
    color: #003087 !important;
}

/* ── 상단 패딩 조정 ── */
.block-container { padding-top: 1.2rem !important; }

/* ── metric ── */
[data-testid="stMetric"] {
    background: #fff;
    border-radius: 8px;
    padding: 12px 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.07);
}
[data-testid="stMetricLabel"] { color: #003087 !important; font-weight: 600; }
[data-testid="stMetricValue"] { color: #003087 !important; font-size: 2rem !important; }
</style>
""", unsafe_allow_html=True)

# ── 헤더 배너 ─────────────────────────────────────────────────────────────
st.markdown("""
<div class="posco-header">
  <div>
    <div class="brand">POSCO</div>
    <div class="title">MRO e-Catalog</div>
    <div class="subtitle">공급사 사양검증 자동화 시스템</div>
  </div>
  <div class="badge">🏭 검토/승인</div>
</div>
""", unsafe_allow_html=True)

# ── PDF 등록 기준 안내 ────────────────────────────────────────────────────
with st.expander("📋 사양검증자료(PDF) 등록 기준 안내", expanded=False):
    st.markdown("""
<div class="posco-card">
<b>검증 가능한 문서 유형</b><br>
✅ 제조사 발행 카탈로그 / 데이터시트 / 도면 / 명판 관련 문서<br>
✅ PDF 내 제조사 근거(로고·상호·웹주소)와 모델(형번) + 사양값이 확인되어야 함
</div>
<div class="posco-card orange">
<b>반려 대상</b><br>
❌ 견적서·발주서 — 수량 + 금액이 함께 기재된 경우 자동 회송<br>
❌ 대리점·유통사 자료 — 제조사 발행 불명확 시 반려<br>
❌ 중복 — 동일 모델+메이커 이미 등록 이력 존재 시 회송
</div>
<div class="posco-card">
<b>이미지 기반 PDF (도면·OCR)</b><br>
🔍 텍스트 추출 불가 시 GPT Vision으로 DRAWING NO. 및 사양 표 자동 추출<br>
🔑 <code>OPENAI_API_KEY</code> 환경 변수 설정 시 동작
</div>
""", unsafe_allow_html=True)

# ── 사이드바: 기준 데이터 등록 ─────────────────────────────────────────
config = load_app_config()
data_loaded = config is not None

with st.sidebar:
    st.markdown("""
<div class="sidebar-header">
  <div class="sb-brand">POSCO</div>
  <div class="sb-title">MRO e-Catalog</div>
</div>
""", unsafe_allow_html=True)
    st.markdown("#### 📂 기준 데이터 등록")
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

    st.markdown("**④ 중복 검사용 기존 데이터** (선택, model_name·maker_name 컬럼 필요)")
    existing_data_file = st.file_uploader(
        "기존data(중복검색용)_system_data_200.xlsx", type=["xlsx", "xls"], key="existing_data"
    )
    dup_count = get_duplicate_baseline_count(STREAMLIT_DB_PATH)
    if dup_count > 0:
        st.caption(f"현재 중복 기준 데이터: {dup_count:,}건 적재됨")

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
            if existing_data_file:
                existing_df = pd.read_excel(existing_data_file, dtype=str)
                col_map = {c.lower().replace(" ", "_"): c for c in existing_df.columns}
                model_col = next((col_map[k] for k in col_map if "model" in k), None)
                maker_col = next((col_map[k] for k in col_map if "maker" in k), None)
                if model_col and maker_col:
                    rows = [
                        {
                            "request_id": str(i),
                            "model_name": str(r.get(model_col) or ""),
                            "maker_name": str(r.get(maker_col) or ""),
                        }
                        for i, r in existing_df.iterrows()
                    ]
                    inserted, skipped = load_duplicate_baseline(STREAMLIT_DB_PATH, rows)
                    st.info(f"중복 기준 데이터 적재 완료: {inserted}건 (건너뜀 {skipped}건)")
                else:
                    st.warning("④ 파일에서 model_name / maker_name 컬럼을 찾지 못했습니다.")
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

# ── 초반 사전 검증: 앞단 7개 미리 채우기 ─────────────────────────────
# Streamlit은 위에서부터 계속 재실행되므로, session_state 플래그로 1회만 수행합니다.
PRELOAD_COUNT = 7
if not st.session_state.get("_preloaded_first7_done", False):
    qcodes_to_preload = qcodes_with_pdf[:PRELOAD_COUNT]
    if qcodes_to_preload:
        st.session_state["_preloaded_first7_done"] = True
        st.sidebar.caption(f"초기 사전 검증 중… ({len(qcodes_to_preload)}개)")
        with st.spinner("앞단 7개 Q코드 사전 검증 중..."):
            for q_code in qcodes_to_preload:
                if q_code in st.session_state["validation_results"]:
                    continue
                try:
                    st.session_state["validation_results"][q_code] = run_qcode_validation(
                        q_code=q_code,
                        qcode_master_df=qcode_master_df,
                        spec_detail_df=spec_detail_df,
                        pdf_mapping_df=pdf_mapping_df,
                        maker_list_df=maker_list_df,
                        pdf_base_dir=pdf_base_dir,
                    )
                except Exception as e:
                    st.session_state["validation_results"][q_code] = {"error": str(e)}


# ── 검증 결과 다이얼로그 ───────────────────────────────────────────────
@st.dialog("검증 결과", width="large")
def show_validation_dialog(q_code: str) -> None:
    st.subheader(f"Q코드: {q_code}")

    if is_known_error_code(q_code):
        st.info("ℹ️ Q3 코드 — 모델·메이커 불일치 이력이 있는 코드입니다. 검토 결과를 확인하세요.")

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

    # Maker 목록에 없거나(=best_matched_maker가 None) 해서 "(없음)"으로 표시되더라도,
    # PDF 텍스트에서 시스템 메이커명이 실제로 감지된 경우에는
    # "동일 메이커"로 UI 판정을 보정한다.
    maker_in_pdf_text = bool(judgment.get("maker_in_pdf_text"))
    if maker_pdf_val == "(없음)" and maker_in_pdf_text:
        maker_result = "일치"
        maker_pdf_val = sys_maker

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
        drawing_r = judgment.get("drawing_validation_result")
        if judgment.get("is_drawing_document"):
            with st.expander("도면 검증 결과 (DRAWING NO. / SPEC BOX)", expanded=True):
                if drawing_r and drawing_r.get("ok"):
                    st.markdown(
                        f"**DRAWING NO.** `{drawing_r.get('drawing_no') or '-'}` &nbsp; "
                        f"**DRAWING NAME** `{drawing_r.get('drawing_name') or '-'}`"
                    )
                    match_icon = "✅" if drawing_r.get("drawing_no_matches_model") else "❌"
                    maker_icon = "✅" if drawing_r.get("is_same_maker") else "❌"
                    st.markdown(
                        f"모델 일치: {match_icon} &nbsp;|&nbsp; "
                        f"제조사 일치: {maker_icon} &nbsp;|&nbsp; "
                        f"표제란 제조사: `{drawing_r.get('maker_in_titleblock') or '-'}`"
                    )
                    if drawing_r.get("reason_ko"):
                        st.caption(drawing_r["reason_ko"])
                    specs = drawing_r.get("specs") or []
                    if specs:
                        st.markdown("**SPEC BOX 추출값:**")
                        spec_df = pd.DataFrame(specs)
                        st.dataframe(spec_df, use_container_width=True, hide_index=True)
                elif drawing_r:
                    st.warning(drawing_r.get("error", "도면 Vision 호출 실패"))
                else:
                    st.caption("도면 감지됨 — OPENAI_API_KEY 설정 시 Vision 검증 가능")

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
    online_hints = judgment.get("online_spec_hints") or []
    online_map: dict[str, str] = {}
    for h in online_hints:
        if not isinstance(h, dict):
            continue
        t = (h.get("title") or "").strip()
        v = (h.get("value") or "").strip()
        if t and v:
            online_map[t] = v
    for spec in expected_specs:
        title = spec.get("title", "")
        value = spec.get("value", "") or "(미입력)"
        pdf_val, match_st = _extract_pdf_value(title, value, pdf_text)
        # PDF에서 값이 안 잡히거나 불일치면 웹에서 긁어온 값으로 대체(가능할 때)
        if match_st != "일치" and title in online_map:
            web_val = online_map[title]
            value_norm = _normalize(value)
            web_val_norm = _normalize(web_val)
            pdf_val = web_val + " (웹)"
            match_st = "일치" if value_norm and value_norm in web_val_norm else "불일치"
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
    "REJECTED": "시스템 회송",
    "PENDING":  "사람승인",
}
_STATUS_BADGE = {
    "자동승인": '<span class="badge-approved">✓ 자동승인</span>',
    "시스템 회송": '<span class="badge-rejected">✗ 시스템 회송</span>',
    "사람승인": '<span class="badge-pending">⚑ 사람승인</span>',
}
_STATUS_BADGE_DIM = {
    "자동승인": '<span class="badge-skipped">○ 승인 추정</span>',
    "시스템 회송": '<span class="badge-skipped">○ 회송 추정</span>',
    "사람승인": '<span class="badge-skipped">○ 검토 추정</span>',
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

st.markdown(f"""
<div style="display:flex; align-items:center; gap:8px; margin-bottom:12px;">
  <div style="font-size:18px; font-weight:700; color:#003087;">📋 검토 대상 목록</div>
  <div style="font-size:13px; color:#666; margin-left:4px;">PDF 있는 항목: <b>{total}</b>건</div>
</div>
""", unsafe_allow_html=True)

mc1, mc2, mc3, mc4 = st.columns(4)
_pct = lambda n: f"{n/total*100:.1f}%" if total > 0 else "0%"
_unverified = total - sum(cnt_map.values())
mc1.metric("✅ 자동승인", cnt_map["APPROVED"], delta=_pct(cnt_map["APPROVED"]), delta_color="off")
mc2.metric("❌ 시스템 회송", cnt_map["REJECTED"], delta=_pct(cnt_map["REJECTED"]), delta_color="off")
mc3.metric("⏳ 사람승인", cnt_map["PENDING"],  delta=_pct(cnt_map["PENDING"]),  delta_color="off")
mc4.metric("⬜ 미검증",   _unverified,          delta=_pct(_unverified),          delta_color="off")

# 검색 필터
search = st.text_input("🔍 Q코드 / 품명 / 제조사 / 모델명 검색", value="", placeholder="입력 시 필터링")
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
labels = ["**Q-Code**", "**품명**", "**제조사**", "**모델명**", "**검증 결과**", "**상세**"]
for i, label in enumerate(labels):
    hcols[i].markdown(
        f'<span style="color:#003087;font-size:15px;font-weight:700;">{label.strip("*")}</span>',
        unsafe_allow_html=True,
    )
st.divider()

# 각 행 렌더링
for row in summary_rows:
    q = row["Q-Code"]
    c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 2, 3, 2, 1])
    q_label = f"⚠️ {q}" if is_known_error_code(q) else q
    c1.markdown(
        f'<span style="font-size:15px;font-weight:600;color:#003087;">{q_label}</span>',
        unsafe_allow_html=True,
    )
    c2.markdown(
        f'<span style="font-size:15px;">{row["품명"]}</span>',
        unsafe_allow_html=True,
    )
    c3.markdown(
        f'<span style="font-size:15px;">{row["제조사"]}</span>',
        unsafe_allow_html=True,
    )
    c4.markdown(
        f'<span style="font-size:13.8px;font-family:monospace;">{row["모델명"]}</span>',
        unsafe_allow_html=True,
    )

    outcome = row["_outcome"]
    if outcome:
        status = _OUTCOME_TO_STATUS.get(outcome, "사람승인")
        c5.markdown(_STATUS_BADGE[status], unsafe_allow_html=True)
    else:
        quick_status = statuses.get(q, ("사람승인", True))[0]
        c5.markdown(_STATUS_BADGE_DIM[quick_status], unsafe_allow_html=True)

    if c6.button("검증", key=f"btn_{q}", type="primary"):
        show_validation_dialog(q)
