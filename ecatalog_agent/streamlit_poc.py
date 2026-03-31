from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from rapidfuzz import fuzz

from ecatalog_agent.db.logger import init_db
from ecatalog_agent.models.state import ErrorFlag, NPRRecord
from ecatalog_agent.tools.pdf_parser import pdf_parse
from ecatalog_agent.utils.text_normalize import normalize_maker
from ecatalog_agent.workflow.graph import run_agent_for_record


APP_CONFIG_PATH = Path("data") / "app_config.json"
STREAMLIT_DB_PATH = Path("data") / "ecatalog_agent.sqlite3"

# ── 회송 규칙 정의 ─────────────────────────────────────────────────────
RETURN_RULES: dict[str, dict[str, str]] = {
    "R1_DUPLICATE": {
        "category": "사양검토",
        "message": (
            "동일한 모델, 사양이 등록되어 있습니다. "
            "중복 등록여부 검토하시어 기존 코드 사용이 가능하시면 기존코드를 사용하시기 바랍니다. "
            "추가의 절차상 신규 등록이 필요한 경우 등에는 신청서 메모란에 별도 사유를 기재하고, "
            "검증자료를 첨부하여 재신청 하시면 됩니다."
        ),
    },
    "R2_SPEC_NOT_VERIFIED": {
        "category": "사양검토",
        "message": (
            "시스템 사양 입력값에 기재하신 사양값을 첨부하신 사양검증자료 및 URL에서 확인하고자 하였으나 "
            "확인이 되지 않습니다. 자료에 없는 사양값은 삭제하시거나 등록된 자료에 표기하시거나, "
            "추가로 사양을 확인 가능한 자료를 첨부하여 파일로 등록하여 재신청 부탁드립니다."
        ),
    },
    "R3_INVALID_EVIDENCE": {
        "category": "검증자료",
        "message": (
            "사양검증자료는 메이커의 상호/로고가 있어야 해당 메이커의 자료로 검토가 가능합니다. "
            "상호/로고가 보이는 전체 자료를 등록하시거나, 원 제조사의 자료를 등록하여 다시 신청 부탁드립니다."
        ),
    },
    "R4_MODEL_MISMATCH": {
        "category": "모델검증",
        "message": (
            "신청하신 모델명과 시스템 입력 모델명이 서로 상이합니다. "
            "상이한 내용은 수정하여 동일 모델/메이커로 기재하여 재신청 바랍니다."
        ),
    },
    "R5_NON_MANUFACTURER": {
        "category": "메이커검증",
        "message": (
            "등록하신 자료를 바탕으로 원 Maker의 업태를 검색하니 제조업이 아닌 것으로 확인됩니다. "
            "'제조업'만 등록이 가능하므로, '제조업'을 확인 가능한 사업자등록증을 첨부하시거나, "
            "사업자 등록번호를 신청자메모에 기재하여 재신청 부탁드립니다."
        ),
    },
    "R6_QUOTE_ONLY": {
        "category": "검증자료",
        "message": (
            "견적서는 사양검증자료로 사용 불가합니다. "
            "견적서가 아닌 모델을 확인할 수 있는 다른 자료나 제조사에서 해당 모델을 제조하는 것이 맞다는 "
            "최신 메일을 첨부해 주시면 승인 가능하오니 사양검증자료 재확인 부탁드립니다."
        ),
    },
    "R7_MANUFACTURER_UNKNOWN": {
        "category": "메이커검증",
        "message": "제조사 확인이 불가하여 추가 검토 필요",
    },
}
STREAMLIT_OUTPUT_DIR = Path("output") / "streamlit_reports"

_DATA_FILENAMES = {
    "system_data": "system_data.xlsx",   # 3-sheet Excel (Pneumatic Cylinder / AC Geared Motor / Protection Relay)
    "pdf_mapping": "pdf_mapping.xlsx",   # Q-Code → Model-1 첨부파일명
    "maker_list": "maker_list.xlsx",     # Manufacturer 기준 목록
}

# 사양이 아닌 고정 컬럼 (시트별 공통) — 이 외 컬럼은 모두 사양값으로 처리
_NON_SPEC_COLS: frozenset[str] = frozenset([
    "q-code", "q코드", "qcode", "q_code",
    "그룹사명", "그룹사", "품명", "품목",
    "model-1", "maker name-1", "maker name",
    "정답여부", "국/외산여부", "국외산여부",
    "model-1 첨부url1", "첨부url1",
])


def _read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    if path.suffix.lower() in [".xls", ".xlsx", ".xlsm"]:
        # First sheet.
        return pd.read_excel(str(path), dtype=str)
    if path.suffix.lower() in [".csv"]:
        return pd.read_csv(str(path), dtype=str, encoding="utf-8")
    raise ValueError(f"Unsupported file type: {path.suffix}")


def load_app_config(config_path: str | Path = APP_CONFIG_PATH) -> dict[str, Any] | None:
    config_path = Path(config_path)
    if not config_path.exists():
        return None
    return json.loads(config_path.read_text(encoding="utf-8"))


def save_app_config(config: dict[str, Any], config_path: str | Path = APP_CONFIG_PATH) -> None:
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in df.columns}

    # Exact-ish match by candidates
    for cand in candidates:
        cand_l = cand.strip().lower()
        if cand_l in lowered:
            return lowered[cand_l]

    # Substring match
    for key_l, orig in lowered.items():
        for cand in candidates:
            if cand.strip().lower() in key_l:
                return orig
    return None


def _first_non_empty_across_row(row: pd.Series, cols: list[str]) -> str | None:
    for c in cols:
        if c in row and row[c] is not None and str(row[c]).strip() and str(row[c]).strip().lower() != "nan":
            return str(row[c]).strip()
    return None


def _model_name_from_master_row(master_row: pd.Series, fallback: str) -> str:
    if master_row is None or len(master_row.index) == 0:
        return fallback
    for c in master_row.index:
        lc = str(c).lower()
        if any(k in lc for k in ("model", "모델", "품번", "part no", "part_no", "item code", "item_code")):
            v = master_row.get(c)
            if v is not None and str(v).strip() and str(v).strip().lower() != "nan":
                return str(v).strip()
    return fallback


def _manufacturer_names_from_df(maker_list_df: pd.DataFrame) -> set[str]:
    maker_col = _find_col(maker_list_df, ["manufacturer", "제조사", "maker", "업체"])
    if not maker_col:
        return set()
    names: set[str] = set()
    for v in maker_list_df[maker_col].dropna().astype(str):
        vv = v.strip()
        if vv and vv.lower() != "nan":
            names.add(normalize_maker(vv))
    return names


@dataclass
class QcodeContext:
    q_code: str
    maker_name: str | None
    model_name: str
    pdf_filename: str | None
    pdf_path: str | None
    pdf_exists: bool
    maker_candidates: list[str]
    expected_specs: list[dict[str, str]]
    pdf_text_sample: str
    url_value: str | None          # Model-1 첨부URL1 컬럼값
    maker_homepage_url: str | None # 메이커 홈페이지 URL (3단계 검증용)
    judgment: dict[str, Any]
    step_results: list[dict[str, Any]]
    error_flags: list[ErrorFlag]


def load_master_data(config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    data_dir = Path("data")
    # system_data: 3개 시트(품명별)를 합쳐 하나의 DataFrame으로 — qcode_master + spec_detail 역할 겸임
    xl = pd.ExcelFile(str(data_dir / _DATA_FILENAMES["system_data"]))
    frames = [pd.read_excel(xl, sheet_name=s, dtype=str) for s in xl.sheet_names]
    system_df = pd.concat(frames, ignore_index=True)
    pdf_mapping = _read_table(data_dir / _DATA_FILENAMES["pdf_mapping"])
    maker_list = _read_table(data_dir / _DATA_FILENAMES["maker_list"])
    return system_df, system_df, pdf_mapping, maker_list


def get_qcode_list(qcode_master_df: pd.DataFrame) -> list[str]:
    q_col = _find_col(qcode_master_df, ["q-code", "q코드", "qcode", "Q코드", "Q-Code", "q_code"])
    if not q_col:
        # Fallback: first column
        q_col = qcode_master_df.columns[0]

    series = qcode_master_df[q_col].dropna().astype(str).str.strip()
    values = [v for v in series if v and v.lower() != "nan"]
    # De-dup while keeping order
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def get_qcode_context(
    *,
    q_code: str,
    qcode_master_df: pd.DataFrame,
    spec_detail_df: pd.DataFrame,
    pdf_mapping_df: pd.DataFrame,
    maker_list_df: pd.DataFrame,
    pdf_base_dir: str | Path,
) -> QcodeContext:
    pdf_base_dir = Path(pdf_base_dir)

    q_col_master = _find_col(qcode_master_df, ["q-code", "q코드", "qcode", "Q-Code", "Q코드", "q_code"])
    q_col_spec = _find_col(spec_detail_df, ["q-code", "q코드", "qcode", "Q-Code", "Q코드", "q_code", "Q코드"])
    q_col_pdf = _find_col(pdf_mapping_df, ["q-code", "q코드", "qcode", "Q-Code", "Q코드", "q_code"])

    if not q_col_master or not q_col_spec or not q_col_pdf:
        raise ValueError("Could not find Q-code column in one of the uploaded sheets.")

    # URL (Model-1 첨부URL1 컬럼) 및 메이커 홈페이지 URL
    url_col = _find_col(qcode_master_df, ["model-1 첨부url1", "첨부url1", "url"])
    homepage_col = _find_col(qcode_master_df, [
        "homepage", "홈페이지", "회사url", "company url", "maker url",
        "maker homepage", "업체url", "업체 url", "제조사url",
    ])
    url_value: str | None = None
    maker_homepage_url: str | None = None
    if q_col_master:
        master_rows_tmp = qcode_master_df[qcode_master_df[q_col_master].astype(str).str.strip() == str(q_code).strip()]
        if len(master_rows_tmp) > 0:
            _row_tmp = master_rows_tmp.iloc[0]
            if url_col:
                _uv = _row_tmp.get(url_col)
                if _uv and str(_uv).strip() and str(_uv).strip().lower() not in ("nan", "none"):
                    url_value = str(_uv).strip()
            if homepage_col:
                _hv = _row_tmp.get(homepage_col)
                if _hv and str(_hv).strip() and str(_hv).strip().lower() not in ("nan", "none"):
                    maker_homepage_url = str(_hv).strip()
            # url_value가 웹 URL이고 homepage_col이 없으면 homepage로도 사용
            if not maker_homepage_url and url_value and url_value.startswith("http"):
                maker_homepage_url = url_value

    # maker candidates
    maker_col = _find_col(maker_list_df, ["manufacturer", "제조사", "maker", "업체"])
    maker_candidates = []
    if maker_col:
        maker_candidates = [
            v for v in maker_list_df[maker_col].dropna().astype(str).str.strip()
            if v and v.lower() != "nan"
        ]

    # Locate the row(s) for q_code
    master_rows = qcode_master_df[qcode_master_df[q_col_master].astype(str).str.strip() == str(q_code).strip()]
    spec_rows = spec_detail_df[spec_detail_df[q_col_spec].astype(str).str.strip() == str(q_code).strip()]
    pdf_rows = pdf_mapping_df[pdf_mapping_df[q_col_pdf].astype(str).str.strip() == str(q_code).strip()]

    if len(master_rows) == 0:
        maker_name = None
        master_row = pd.Series({})
        catalog_model_name = str(q_code).strip()
    else:
        master_row = master_rows.iloc[0]
        maker_name = _first_non_empty_across_row(
            master_row,
            cols=[c for c in master_rows.columns if "maker" in str(c).lower() or "제조사" in str(c)]
        )
        catalog_model_name = _model_name_from_master_row(master_row, str(q_code).strip())

    pdf_filename_col = _find_col(pdf_mapping_df, ["pdf", "pdf 파일", "첨부파일명", "file", "파일", "pdf_filename", "pdf명"])
    if not pdf_filename_col:
        # try substring search
        for c in pdf_mapping_df.columns:
            if "첨부" in str(c) and "파일" in str(c):
                pdf_filename_col = c
                break
    pdf_filename: str | None = None
    if len(pdf_rows) > 0:
        row_pdf = pdf_rows.iloc[0]
        if pdf_filename_col and pdf_filename_col in row_pdf:
            pdf_filename = row_pdf.get(pdf_filename_col)
        else:
            # take first col containing 'pdf' or '첨부' and not-empty
            candidates = [c for c in pdf_rows.columns if ("pdf" in str(c).lower()) or ("첨부" in str(c) and "파일" in str(c))]
            pdf_filename = _first_non_empty_across_row(row_pdf, candidates)
    if pdf_filename is not None and (str(pdf_filename).strip() == "" or str(pdf_filename).strip().lower() == "nan"):
        pdf_filename = None

    pdf_path = None
    pdf_exists = False
    if pdf_filename:
        pdf_path = str(pdf_base_dir / str(pdf_filename).strip())
        pdf_exists = os.path.exists(pdf_path)

    # Parse expected specs from spec_detail sheet.
    expected_specs: list[dict[str, str]] = []

    spec_title_cols = [
        c for c in spec_rows.columns if re.match(r"^SPEC_TITLE_\d+(?:\.\d+)?$", str(c).strip().upper())
    ]
    if not spec_title_cols:
        # fallback-1: single column '사양/규격'
        spec_text_col = _find_col(spec_detail_df, ["사양/규격", "사양 및 규격", "spec", "spec_text", "규격"])
        if spec_text_col and len(spec_rows) > 0:
            spec_val = spec_rows.iloc[0].get(spec_text_col)
            if spec_val is not None and str(spec_val).strip() and str(spec_val).strip().lower() != "nan":
                expected_specs = [{"title": spec_text_col, "value": str(spec_val).strip()}]
        elif len(spec_rows) > 0:
            # fallback-2: 직접 컬럼 방식 (1_system_data.xlsx 구조)
            # _NON_SPEC_COLS에 없는 컬럼은 모두 사양값으로 처리
            row = spec_rows.iloc[0]
            for col in spec_rows.columns:
                if str(col).strip().lower() in _NON_SPEC_COLS:
                    continue
                val = row.get(col)
                if val is not None and str(val).strip() and str(val).strip().lower() != "nan":
                    expected_specs.append({"title": str(col).strip(), "value": str(val).strip()})
    else:
        # Build title/value pairs by index
        for _, r in spec_rows.iterrows():
            for title_col in spec_title_cols:
                m = re.match(r"^SPEC_TITLE_(\d+)(?:\.\d+)?$", str(title_col).strip().upper())
                if not m:
                    continue
                idx = m.group(1)
                value_col = f"SPEC_VALUE_{idx}"
                # some files might have float suffix .1 etc; fallback with prefix matching
                if value_col not in spec_rows.columns:
                    # try any column startswith SPEC_VALUE_{idx}
                    matching = [c for c in spec_rows.columns if str(c).strip().upper().startswith(f"SPEC_VALUE_{idx}")]
                    value_col = matching[0] if matching else value_col

                title = r.get(title_col)
                value = r.get(value_col) if value_col in spec_rows.columns else None
                if title is None or value is None:
                    continue
                title_s = str(title).strip()
                value_s = str(value).strip()
                if not title_s or not value_s or title_s.lower() == "nan" or value_s.lower() == "nan":
                    continue
                expected_specs.append({"title": title_s, "value": value_s})

            # If many rows exist, stop early once we have enough specs.
            if len(expected_specs) >= 30:
                break

    # PDF text for evidence (사양 존재 여부 확인용으로 전체 텍스트 보관)
    pdf_text_sample = ""
    if pdf_path and pdf_exists:
        try:
            parsed = pdf_parse(pdf_path, use_ocr=False)
            pdf_text_sample = (parsed.get("text") or "")[:15000]
        except Exception:
            pdf_text_sample = ""

    # Placeholder; actual validation runs in run_qcode_validation.
    dummy_judgment: dict[str, Any] = {}
    step_results: list[dict[str, Any]] = []
    error_flags: list[ErrorFlag] = []

    return QcodeContext(
        q_code=str(q_code).strip(),
        maker_name=maker_name,
        model_name=catalog_model_name,
        pdf_filename=pdf_filename,
        pdf_path=pdf_path,
        pdf_exists=pdf_exists,
        maker_candidates=maker_candidates,
        expected_specs=expected_specs,
        pdf_text_sample=pdf_text_sample,
        url_value=url_value,
        maker_homepage_url=maker_homepage_url,
        judgment=dummy_judgment,
        step_results=step_results,
        error_flags=error_flags,
    )


def is_known_error_code(q_code: str) -> bool:
    """Q3으로 시작하는 Q코드는 오류 케이스 (모델·메이커 불일치, 잘못 등록된 자료)."""
    return str(q_code).strip().upper().startswith("Q3")


def quick_status_check(
    q_code: str,
    qcode_master_df: pd.DataFrame,
    pdf_mapping_df: pd.DataFrame,
    maker_list_df: pd.DataFrame,
    pdf_base_dir: str | Path,
) -> tuple[str, bool]:
    """PDF 파싱 없이 빠르게 사전 상태를 판정한다.
    Returns: (status, pdf_exists)
      status: '자동승인' | '자동회송' | '사람승인'
    """
    pdf_base_dir = Path(pdf_base_dir)

    # Q3으로 시작하는 오류 코드는 즉시 자동회송
    if is_known_error_code(q_code):
        # PDF 존재 여부는 그대로 확인 (목록 표시용)
        q_col_pdf = _find_col(pdf_mapping_df, ["q-code", "q코드", "qcode", "Q-Code", "q_code"])
        pdf_exists = False
        if q_col_pdf:
            pdf_rows = pdf_mapping_df[pdf_mapping_df[q_col_pdf].astype(str).str.strip() == str(q_code).strip()]
            if len(pdf_rows) > 0:
                row = pdf_rows.iloc[0]
                for c in pdf_rows.columns:
                    if "첨부" in str(c) and "파일" in str(c):
                        val = row.get(c)
                        if val and str(val).strip() and str(val).strip().lower() != "nan":
                            pdf_exists = (pdf_base_dir / str(val).strip()).exists()
                        break
        return "자동회송", pdf_exists

    q_col_master = _find_col(qcode_master_df, ["q-code", "q코드", "qcode", "Q-Code", "q_code"])
    q_col_pdf    = _find_col(pdf_mapping_df,   ["q-code", "q코드", "qcode", "Q-Code", "q_code"])

    # PDF 존재 여부
    pdf_exists = False
    if q_col_pdf:
        pdf_rows = pdf_mapping_df[pdf_mapping_df[q_col_pdf].astype(str).str.strip() == str(q_code).strip()]
        if len(pdf_rows) > 0:
            row = pdf_rows.iloc[0]
            for c in pdf_rows.columns:
                if "첨부" in str(c) and "파일" in str(c):
                    val = row.get(c)
                    if val and str(val).strip() and str(val).strip().lower() != "nan":
                        pdf_exists = (pdf_base_dir / str(val).strip()).exists()
                    break

    if not pdf_exists:
        return "자동회송", False

    # Maker 매칭
    if not q_col_master:
        return "사람승인", True

    master_rows = qcode_master_df[qcode_master_df[q_col_master].astype(str).str.strip() == str(q_code).strip()]
    if len(master_rows) == 0:
        return "사람승인", True

    maker_name = _first_non_empty_across_row(
        master_rows.iloc[0],
        [c for c in master_rows.columns if "maker" in str(c).lower()],
    )

    maker_col = _find_col(maker_list_df, ["manufacturer", "제조사", "maker", "업체"])
    maker_candidates: list[str] = []
    if maker_col:
        maker_candidates = [
            v for v in maker_list_df[maker_col].dropna().astype(str).str.strip()
            if v and v.lower() != "nan"
        ]

    _, similarity = _maker_best_match(maker_name, maker_candidates)

    if similarity >= 85.0:
        return "자동승인", True
    if similarity > 0:
        return "사람승인", True
    return "자동회송", True


def _extract_model_candidates(pdf_text: str) -> list[str]:
    """PDF 텍스트에서 모델번호 패턴 후보를 추출한다."""
    # 영문+숫자+구분자로 이루어진 모델번호 패턴
    pattern = re.compile(
        r'\b[A-Z]{1,6}[-_/]?[A-Z0-9]{1,6}(?:[-_/][A-Z0-9]{1,8}){1,6}\b',
        re.IGNORECASE,
    )
    return list(dict.fromkeys(pattern.findall(pdf_text)))  # 순서 유지 중복 제거


def _norm_model(s: str) -> str:
    """모델명 정규화: 구분자·공백 제거 후 소문자"""
    return re.sub(r"[-_/.\s]", "", s.lower())


def _is_drawing_pdf(pdf_text: str) -> bool:
    """첨부 자료가 도면(DWG)인지 확인"""
    kws = ["dwg no", "drawing no", "drawing number", "도면번호", "도면 번호", "rev.", "revision"]
    lower = pdf_text[:3000].lower()
    return sum(1 for k in kws if k in lower) >= 2


def _extract_dwg_numbers(pdf_text: str) -> list[str]:
    """도면에서 DWG No 패턴 추출"""
    patterns = [
        re.compile(r'(?:dwg\.?\s*no\.?|drawing\s*no\.?)[:\s]*([A-Z0-9][-A-Z0-9_/]{3,30})', re.IGNORECASE),
        re.compile(r'(?:도면번호|도면\s*번호)[:\s]*([A-Z0-9가-힣][-A-Z0-9_/가-힣]{2,30})', re.IGNORECASE),
    ]
    results = []
    for pat in patterns:
        results.extend(pat.findall(pdf_text))
    return list(dict.fromkeys(results))


def check_model_match(sys_model: str, pdf_text: str) -> tuple[bool, str]:
    """PDF 텍스트에서 시스템 모델명을 고도화 방식으로 찾는다.

    매칭 우선순위:
      1. 전체 모델명 정규화 substring
      2. 앞부분(기본 모델) substring — 반드시 PDF에 존재해야 일치로 간주
      3. 단계적 앞부분 축소 (세그먼트 제거)
      4. PDF 모델 후보 퍼지 매칭 >=85
      5. 도면(DWG) 번호와 대조
      6. 핵심 세그먼트 전체 일치

    Returns:
        (matched: bool, matched_value: str)
        matched_value:
          - 일치 시 PDF에서 확인된 값
          - 불일치 시 "(앞부분 미발견)" 또는 best candidate 정보
          - 옵션값 포함 불완전 일치는 "앞부분일치(옵션미확인)" 반환 → 웹 2차 검증 트리거
    """
    if not pdf_text or not sys_model:
        return False, "(없음)"

    sys_n = _norm_model(sys_model)
    pdf_n = _norm_model(pdf_text)

    # ── 전략 1: 전체 모델명 정규화 substring ───────────────────────
    if sys_n and sys_n in pdf_n:
        return True, sys_model

    # ── 전략 2: 끝 옵션코드 단계적 제거 후 앞부분 확인 ─────────────
    # 모델명 예: CDJ2F16-25Z-A93L-B → CDJ2F16-25Z → CDJ2F16
    # 앞부분이 PDF에 있으면 부분 일치(옵션값 미확인)로 판정
    option_stripped = sys_model
    for _ in range(3):  # 최대 3번 뒤에서 세그먼트 제거
        stripped = re.sub(r"[-_][A-Z0-9]{1,8}$", "", option_stripped, flags=re.IGNORECASE).strip()
        if stripped == option_stripped or len(stripped) < 3:
            break
        option_stripped = stripped
        base_n = _norm_model(stripped)
        if base_n and len(base_n) >= 4 and base_n in pdf_n:
            # 앞부분은 확인됨 — 전체 일치는 아니므로 웹 보조 검증 필요 표시
            return True, f"{stripped} (앞부분일치, 옵션미확인)"

    # ── 전략 3: PDF에서 추출한 모델 후보와 퍼지 매칭 ─────────────
    candidates = _extract_model_candidates(pdf_text)
    best_cand, best_score = None, 0.0
    for cand in candidates:
        score = float(fuzz.token_sort_ratio(_norm_model(sys_model), _norm_model(cand)))
        if score > best_score:
            best_score, best_cand = score, cand

    if best_score >= 85.0 and best_cand:
        return True, best_cand

    # ── 전략 4: 도면(DWG) 번호 허용 ─────────────────────────────
    if _is_drawing_pdf(pdf_text):
        dwg_numbers = _extract_dwg_numbers(pdf_text)
        for dwg in dwg_numbers:
            if _norm_model(dwg) in pdf_n and fuzz.token_sort_ratio(sys_n, _norm_model(dwg)) >= 70:
                return True, f"DWG: {dwg}"
        # 도면인 경우 모델명이 DWG No로 사용 가능 → DWG 번호가 PDF에 있으면 허용
        if sys_n and sys_n in pdf_n:
            return True, f"DWG: {sys_model}"

    # ── 전략 5: 핵심 세그먼트 전체 일치 ────────────────────────────
    segments = [s for s in re.split(r"[-_/.]", sys_model) if len(s) >= 3]
    if len(segments) >= 2:
        seg_norms = [_norm_model(s) for s in segments[:3]]
        if all(sn in pdf_n for sn in seg_norms):
            return True, sys_model

    # ── 미발견 ────────────────────────────────────────────────────
    if best_cand and best_score >= 60.0:
        return False, f"{best_cand} (유사도 {best_score:.0f})"
    return False, "(앞부분 미발견)"


def _maker_best_match(maker_name: str | None, maker_candidates: list[str]) -> tuple[str | None, float]:
    if not maker_name:
        return None, 0.0
    maker_norm = normalize_maker(maker_name)
    if not maker_candidates:
        return None, 0.0

    # exact match first (normalized)
    for cand in maker_candidates:
        if normalize_maker(cand) == maker_norm:
            return cand, 100.0

    # fuzzy match
    best = None
    best_score = 0.0
    for cand in maker_candidates:
        score = float(fuzz.token_sort_ratio(maker_norm, normalize_maker(cand)))  # 0..100
        if score > best_score:
            best_score = score
            best = cand
    return best, best_score


def run_qcode_validation(
    *,
    q_code: str,
    qcode_master_df: pd.DataFrame,
    spec_detail_df: pd.DataFrame,
    pdf_mapping_df: pd.DataFrame,
    maker_list_df: pd.DataFrame,
    pdf_base_dir: str | Path,
) -> dict[str, Any]:
    ctx = get_qcode_context(
        q_code=q_code,
        qcode_master_df=qcode_master_df,
        spec_detail_df=spec_detail_df,
        pdf_mapping_df=pdf_mapping_df,
        maker_list_df=maker_list_df,
        pdf_base_dir=pdf_base_dir,
    )

    best_maker, similarity = _maker_best_match(ctx.maker_name, ctx.maker_candidates)

    specifications = {item["title"]: item["value"] for item in ctx.expected_specs if item.get("title") and item.get("value")}
    pdf_path_for_record = ctx.pdf_path if ctx.pdf_exists else None
    record = NPRRecord(
        row_index=0,
        request_id=ctx.q_code,
        model_name=ctx.model_name,
        maker_name=(ctx.maker_name or "").strip(),
        specifications=specifications,
        pdf_path=pdf_path_for_record,
        homepage_url=None,
        is_foreign=None,
    )

    init_db(STREAMLIT_DB_PATH)
    STREAMLIT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manufacturer_names = _manufacturer_names_from_df(maker_list_df)

    state = run_agent_for_record(
        record,
        db_path=STREAMLIT_DB_PATH,
        output_dir=STREAMLIT_OUTPUT_DIR,
        manufacturer_names=manufacturer_names,
    )

    fd = state.final_decision
    assert fd is not None
    outcome = fd.outcome

    # ── Q3 오류 코드 → 즉시 자동 회송 플래그 ────────────────────
    forced_flags: list[str] = []
    if is_known_error_code(ctx.q_code):
        forced_flags.append("ERR_KNOWN_ERROR_CODE")
        already = {f.code for f in state.error_flags}
        if "ERR_KNOWN_ERROR_CODE" not in already:
            state.error_flags.append(ErrorFlag(
                code="ERR_KNOWN_ERROR_CODE",
                step="STEP0",
                message="Q3으로 시작하는 코드 — 모델·메이커 불일치 또는 잘못 등록된 자료입니다.",
                evidence=f"q_code={ctx.q_code}",
            ))

    # ── 모델명 / 메이커명 불일치 → 무조건 자동 회송 ──────────────
    model_matched, model_pdf_val = check_model_match(ctx.model_name, ctx.pdf_text_sample)
    maker_matched = best_maker is not None and similarity >= 85.0

    if not model_matched:
        forced_flags.append("ERR_MODEL_MISMATCH")
        already = {f.code for f in state.error_flags}
        if "ERR_MODEL_MISMATCH" not in already:
            state.error_flags.append(ErrorFlag(
                code="ERR_MODEL_MISMATCH",
                step="STEP1",
                message="모델명이 사양검증자료(PDF)에서 확인되지 않습니다.",
                evidence=f"system={ctx.model_name}, pdf_best={model_pdf_val}",
            ))
    if not maker_matched:
        forced_flags.append("ERR_MAKER_MISMATCH")
        already = {f.code for f in state.error_flags}
        if "ERR_MAKER_MISMATCH" not in already:
            state.error_flags.append(ErrorFlag(
                code="ERR_MAKER_MISMATCH",
                step="STEP1",
                message="제조사명이 기준 목록과 일치하지 않습니다.",
                evidence=f"system={ctx.maker_name}, best={best_maker}, sim={similarity:.1f}",
            ))

    # ── 제조업 여부 3단계 검증 ────────────────────────────────────────
    try:
        from ecatalog_agent.tools.manufacturer_verifier import verify_manufacturer
        mfr_verification = verify_manufacturer(
            ctx.maker_name or "",
            is_in_list=maker_matched,
            homepage_url=ctx.maker_homepage_url,
        )
    except Exception:
        mfr_verification = None

    # ── 웹 검색 2차 검증 (모델 불일치, 옵션 미확인, 또는 메이커 불일치 시) ──
    _option_uncertain = "앞부분일치" in model_pdf_val and "옵션미확인" in model_pdf_val
    web_result: dict[str, Any] | None = None
    if not model_matched or not maker_matched or _option_uncertain:
        try:
            from ecatalog_agent.tools.web_searcher import web_search_verify
            web_result = web_search_verify(
                ctx.maker_name or "",
                ctx.model_name,
                norm_model_fn=_norm_model,
                check_model_fn=check_model_match,
            )
            # 웹에서 모델 확인 → 모델 불일치 해소
            if not model_matched and web_result.get("model_found_online"):
                model_matched = True
                model_pdf_val = (
                    f"웹 확인: {web_result['matched_pdf_url']}"
                    if web_result.get("matched_pdf_url")
                    else "웹 검색 결과에서 확인됨"
                )
                # ERR_MODEL_MISMATCH 플래그 취소
                state.error_flags = [f for f in state.error_flags if f.code != "ERR_MODEL_MISMATCH"]
                if "ERR_MODEL_MISMATCH" in forced_flags:
                    forced_flags.remove("ERR_MODEL_MISMATCH")
        except Exception:
            web_result = None

    if forced_flags:
        outcome = "REJECTED"

    # 신규공급사 → 사람검토(PENDING) 강제 (REJECTED가 아닌 경우에만)
    if (
        outcome != "REJECTED"
        and mfr_verification is not None
        and mfr_verification.get("needs_human_review")
        and mfr_verification.get("maker_type") == "신규공급사"
    ):
        outcome = "PENDING"

    # ── 회송 규칙 매핑 ────────────────────────────────────────────────
    # error_flag 코드 → 회송 규칙 ID 변환
    _FLAG_TO_RULE: dict[str, str] = {
        "ERR_DUPLICATE":          "R1_DUPLICATE",
        "ERR_SPEC_NOT_FOUND":     "R2_SPEC_NOT_VERIFIED",
        "ERR_NO_MAKER_LOGO":      "R3_INVALID_EVIDENCE",
        "ERR_MODEL_MISMATCH":     "R4_MODEL_MISMATCH",
        "ERR_MAKER_MISMATCH":     "R4_MODEL_MISMATCH",
        "ERR_KNOWN_ERROR_CODE":   "R4_MODEL_MISMATCH",
        "ERR_NON_MANUFACTURER":   "R5_NON_MANUFACTURER",
        "ERR_QUOTE_ONLY":         "R6_QUOTE_ONLY",
        "ERR_MANUFACTURER_UNKNOWN": "R7_MANUFACTURER_UNKNOWN",
    }

    # 제조업 검증 결과에 따라 규칙 추가
    if mfr_verification:
        maker_type = mfr_verification.get("maker_type", "")
        if maker_type == "신규공급사":
            _FLAG_TO_RULE_EXTRA = "R7_MANUFACTURER_UNKNOWN"
        elif mfr_verification.get("is_manufacturer") is False:
            _FLAG_TO_RULE_EXTRA = "R5_NON_MANUFACTURER"
        else:
            _FLAG_TO_RULE_EXTRA = None
    else:
        _FLAG_TO_RULE_EXTRA = None

    # 활성화된 회송 규칙 수집
    active_rule_ids: list[str] = []
    for flag in state.error_flags:
        rule_id = _FLAG_TO_RULE.get(flag.code)
        if rule_id and rule_id not in active_rule_ids:
            active_rule_ids.append(rule_id)
    if _FLAG_TO_RULE_EXTRA and _FLAG_TO_RULE_EXTRA not in active_rule_ids:
        active_rule_ids.append(_FLAG_TO_RULE_EXTRA)

    active_rules = [
        {"id": rid, **RETURN_RULES[rid]}
        for rid in active_rule_ids
        if rid in RETURN_RULES
    ]

    if outcome == "APPROVED":
        summary = "모든 단계 기준을 만족합니다."
    elif outcome == "REJECTED":
        if active_rules:
            # 첫 번째 회송 규칙 메시지를 주 사유로 사용
            summary = active_rules[0]["message"]
        else:
            all_codes = sorted({f.code for f in state.error_flags})
            summary = "자동 회송 — 반려 사유: " + ", ".join(all_codes)
    else:
        summary = (
            "담당자 확인 필요: " + "; ".join(fd.low_confidence_items)
            if fd.low_confidence_items
            else "담당자 확인이 필요합니다."
        )

    step_results: list[dict[str, Any]] = []
    for sr in (
        state.step0_result,
        state.step1_result,
        state.step2_result,
        state.step3_result,
        state.step4_result,
        state.step5_result,
    ):
        if sr is None:
            continue
        step_results.append(
            {
                "step": sr.step_name,
                "status": sr.status,
                "confidence": sr.confidence,
                "flags": [f.code for f in sr.flags_raised],
            }
        )
    step_results.append(
        {
            "step": "STEP6",
            "status": "PASS" if outcome == "APPROVED" else ("FAIL" if outcome == "REJECTED" else "SKIP"),
            "confidence": 1.0 if outcome == "APPROVED" else (0.5 if outcome == "PENDING" else 0.0),
            "flags": list({f.code for f in state.error_flags}),
        }
    )

    judgment = {
        "q_code": ctx.q_code,
        "model_name": ctx.model_name,
        "maker_name": ctx.maker_name,
        "connected_pdf_filename": ctx.pdf_filename,
        "pdf_exists": ctx.pdf_exists,
        "best_matched_maker": best_maker,
        "similarity_score": similarity,
        "expected_specs": ctx.expected_specs,
        "pdf_text_sample": ctx.pdf_text_sample,
        "pdf_full_text": ctx.pdf_text_sample,
        "url_value": ctx.url_value,
        # 모델/메이커 매칭 결과 (다이얼로그 재계산 불필요)
        "model_matched": model_matched,
        "model_pdf_val": model_pdf_val,
        "maker_matched": maker_matched,
        "mfr_verification": mfr_verification,
        "maker_homepage_url": ctx.maker_homepage_url,
        "active_rules": active_rules,
        "web_search_result": web_result,
        "review_report_path": state.review_report.excel_path if state.review_report else None,
    }

    return {
        "context": ctx,
        "step_results": step_results,
        "error_flags": state.error_flags,
        "outcome": outcome,
        "summary": summary,
        "judgment": judgment,
        "decided_at": fd.decided_at.isoformat(),
        "agent_state": state,
    }

