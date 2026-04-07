"""
산업 부품 사양 비교를 위한 단위 변환 유틸리티.

지원 단위 카테고리:
  - 압력: MPa, kPa, Pa, bar, kgf/cm², kgf/mm², psi, atm
  - 길이: mm, cm, m, inch, in
  - 온도: °C, °F, K (비교만)
  - 속도: mm/s, m/s, m/min
  - 힘: N, kN, kgf, lbf
"""

from __future__ import annotations

import re
from typing import Any


# ── 단위 → SI 기준 변환 계수 ─────────────────────────────────────────────────

# 압력 → Pa
_PRESSURE_TO_PA: dict[str, float] = {
    "pa":        1.0,
    "kpa":       1_000.0,
    "mpa":       1_000_000.0,
    "gpa":       1_000_000_000.0,
    "bar":       100_000.0,
    "mbar":      100.0,
    "kbar":      100_000_000.0,
    "atm":       101_325.0,
    "psi":       6_894.757,
    "ksi":       6_894_757.0,
    "kgf/cm2":   98_066.5,
    "kgf/cm²":   98_066.5,
    "kgf/mm2":   9_806_650.0,
    "kgf/mm²":   9_806_650.0,
    "kgfcm2":    98_066.5,
    "kgfmm2":    9_806_650.0,
    "n/mm2":     1_000_000.0,
    "n/mm²":     1_000_000.0,
    "n/cm2":     10_000.0,
}

# 길이 → mm
_LENGTH_TO_MM: dict[str, float] = {
    "mm":    1.0,
    "cm":    10.0,
    "dm":    100.0,
    "m":     1_000.0,
    "km":    1_000_000.0,
    "in":    25.4,
    "inch":  25.4,
    '"':     25.4,
    "ft":    304.8,
    "feet":  304.8,
    "'":     304.8,
}

# 속도 → mm/s
_SPEED_TO_MMS: dict[str, float] = {
    "mm/s":   1.0,
    "mm/sec": 1.0,
    "cm/s":   10.0,
    "m/s":    1_000.0,
    "m/min":  1_000.0 / 60,
    "mm/min": 1.0 / 60,
}

# 힘 → N
_FORCE_TO_N: dict[str, float] = {
    "n":    1.0,
    "kn":   1_000.0,
    "mn":   0.001,
    "kgf":  9.80665,
    "kp":   9.80665,
    "lbf":  4.44822,
    "tf":   9_806.65,
}

# 온도: 변환 함수 별도 처리
# 단위 카테고리 매핑
_UNIT_TABLES = [
    _PRESSURE_TO_PA,
    _LENGTH_TO_MM,
    _SPEED_TO_MMS,
    _FORCE_TO_N,
]


# ── 값 파싱 ───────────────────────────────────────────────────────────────────

_NUM_PATTERN = re.compile(
    r"([+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?)"  # 숫자
    r"\s*"
    r"([°℃℉a-zA-Z²³/\(\)·*'\"][\w²³/·*°℃℉\s]*)?",  # 단위 (선택)
    re.UNICODE,
)

_RANGE_PATTERN = re.compile(
    r"([+-]?\d+(?:[.,]\d+)?)\s*[~\-–—to]+\s*([+-]?\d+(?:[.,]\d+)?)"
    r"\s*([°℃℉a-zA-Z²³/\(\)·*'\"][\w²³/·*°℃℉\s]*)?",
    re.UNICODE,
)


def _clean_unit(unit_str: str) -> str:
    """단위 문자열을 정규화한다."""
    if not unit_str:
        return ""
    u = unit_str.strip().lower()
    u = re.sub(r"\s+", "", u)
    # ² → 2, ³ → 3
    u = u.replace("²", "2").replace("³", "3")
    u = u.replace("°c", "°c").replace("℃", "°c").replace("℉", "°f")
    # kgf/cm2 정규화
    u = re.sub(r"kgf[・·\*]?/?cm2?", "kgf/cm2", u)
    u = re.sub(r"kgf[・·\*]?/?mm2?", "kgf/mm2", u)
    u = re.sub(r"n/?mm2?", "n/mm2", u)
    return u


def parse_value_with_unit(s: str) -> list[dict[str, Any]]:
    """
    '1.0 MPa', '0 ~ 70 °C', '160 mm' 등을 파싱해
    [{"number": float, "unit": str, "is_range": bool, "range_min": float, "range_max": float}] 반환.
    """
    s = s.strip()
    results = []

    # 범위 먼저 시도
    rm = _RANGE_PATTERN.search(s)
    if rm:
        try:
            lo = float(rm.group(1).replace(",", "."))
            hi = float(rm.group(2).replace(",", "."))
            unit = _clean_unit(rm.group(3) or "")
            results.append({
                "number": (lo + hi) / 2,
                "unit": unit,
                "is_range": True,
                "range_min": lo,
                "range_max": hi,
            })
            return results
        except ValueError:
            pass

    # 단일 값
    for m in _NUM_PATTERN.finditer(s):
        try:
            num = float(m.group(1).replace(",", "."))
            unit = _clean_unit(m.group(2) or "")
            results.append({
                "number": num,
                "unit": unit,
                "is_range": False,
                "range_min": None,
                "range_max": None,
            })
        except ValueError:
            continue

    return results


# ── 단위 변환 ─────────────────────────────────────────────────────────────────

def _get_conversion_table(unit: str) -> dict[str, float] | None:
    """단위가 속한 변환 테이블을 반환한다."""
    u = unit.lower().replace(" ", "")
    for table in _UNIT_TABLES:
        if u in table:
            return table
    return None


def convert_to_base(value: float, unit: str) -> float | None:
    """값을 기준 단위(SI)로 변환한다. 알 수 없는 단위는 None."""
    table = _get_conversion_table(unit)
    if table is None:
        return None
    u = unit.lower().replace(" ", "")
    return value * table[u]


def _celsius_to_kelvin(c: float) -> float:
    return c + 273.15


def _fahrenheit_to_kelvin(f: float) -> float:
    return (f - 32) * 5 / 9 + 273.15


# ── 핵심 비교 함수 ────────────────────────────────────────────────────────────

_TOLERANCE = 0.03   # ±3% 허용 오차

# ── 타이틀 기반 기본 단위 (단위 없는 숫자에 적용) ──────────────────────────
# 시스템 데이터에 단위 없이 숫자만 등록된 필드의 암묵적 단위 정의
_TITLE_DEFAULT_UNITS: list[tuple[list[str], str]] = [
    # 압력: 시스템 데이터는 kgf/cm² 고정
    (["pressure", "압력", "정격압력", "최대압력", "사용압력",
      "pressure rating", "rated pressure", "max pressure"], "kgf/cm2"),
    # 전압: V 고정
    (["voltage", "전압", "정격전압", "공급전압", "rated voltage"], "v"),
    # 전류: A 고정
    (["current", "전류", "정격전류", "rated current"], "a"),
    # 주파수: Hz 고정
    (["frequency", "주파수", "hz", "freq"], "hz"),
    # 온도: °C 고정
    (["temperature", "온도", "작동온도", "사용온도", "ambient temperature"], "°c"),
    # 회전수: rpm 고정
    (["speed", "rpm", "회전수", "정격속도", "rated speed"], "rpm"),
]


def infer_default_unit(title: str) -> str | None:
    """사양 타이틀로부터 단위 없는 숫자에 적용할 기본 단위를 반환한다."""
    t = title.strip().lower()
    for keywords, unit in _TITLE_DEFAULT_UNITS:
        if any(k in t for k in keywords):
            return unit
    return None


def _apply_default_unit(val_str: str, parsed: list[dict], default_unit: str | None) -> tuple[str, list[dict]]:
    """파싱 결과에 단위가 없고 default_unit이 있으면 단위를 주입해 재파싱한다."""
    if not default_unit or not parsed:
        return val_str, parsed
    if parsed[0]["unit"]:  # 이미 단위 있음
        return val_str, parsed
    new_str = f"{parsed[0]['number']} {default_unit}"
    new_parsed = parse_value_with_unit(new_str)
    if new_parsed:
        return new_str, new_parsed
    return val_str, parsed


def _values_close(a: float, b: float, tol: float = _TOLERANCE) -> bool:
    """두 값이 허용 오차 내에 있는지 확인한다."""
    if a == 0 and b == 0:
        return True
    if a == 0 or b == 0:
        return abs(a - b) < 1e-9
    return abs(a - b) / max(abs(a), abs(b)) <= tol


def _normalize_ip_class(v: str) -> str | None:
    """'IP55' → '55', '55' → '55'. IP 등급 표기에서 숫자 부분만 반환.
    IP 등급처럼 보이지 않으면 None."""
    s = v.strip()
    m = re.match(r'^ip\s*(\d{2,3})$', s.lower())
    if m:
        return m.group(1)
    # 순수 2~3자리 숫자만 있으면 (맥락에 따라 IP 등급 숫자일 수 있음)
    if re.match(r'^\d{2,3}$', s):
        return s
    return None


def _parse_ratio(v: str) -> float | None:
    """'50:1' → 50.0, '1:50' → 0.02, '1/50' → 0.02. 비율 표기 파싱.
    비율로 해석 불가능한 경우 None 반환."""
    v = v.strip()
    # N:M 형식
    m = re.match(r'^(\d+(?:[.,]\d+)?)\s*[:/]\s*(\d+(?:[.,]\d+)?)$', v)
    if m:
        a = float(m.group(1).replace(',', '.'))
        b = float(m.group(2).replace(',', '.'))
        if b == 0:
            return None
        return a / b
    return None


def _normalize_rpm_slash(v: str) -> str | None:
    """'1735/35' 형태에서 '/'  앞 숫자(rpm)만 추출.
    'X/Y' 패턴에서 X가 rpm 값, Y가 Hz 등 다른 값인 경우.
    Returns: 앞 숫자 문자열 또는 None"""
    m = re.match(r'^(\d+(?:[.,]\d+)?)\s*/\s*\d+(?:[.,]\d+)?$', v.strip())
    if m:
        return m.group(1)
    return None


def compare_spec_values(
    sys_val_str: str,
    pdf_val_str: str,
    *,
    title: str | None = None,
) -> dict[str, Any]:
    """
    시스템 등록값과 PDF/도면 추출값을 단위 변환 후 비교한다.

    title: 사양 항목명 (예: "Pressure Rating"). 단위 없는 숫자에 기본 단위 적용에 사용.
           압력 항목은 kgf/cm²로 고정 등.

    Returns:
        {
            "match": "일치" | "불일치" | "단위변환일치" | "범위내" | "비교불가",
            "sys_parsed": ...,
            "pdf_parsed": ...,
            "sys_base": float | None,   # 기준 단위로 변환된 값
            "pdf_base": float | None,
            "note": str,
        }
    """
    # ── IP 등급 정규화 ('IP55' vs '55' 동일 판정) ──────────────────────
    sys_ip = _normalize_ip_class(sys_val_str)
    pdf_ip = _normalize_ip_class(pdf_val_str)
    if sys_ip is not None and pdf_ip is not None:
        match = "일치" if sys_ip == pdf_ip else "불일치"
        return {
            "match": match,
            "note": f"IP 등급 정규화: {sys_val_str} vs {pdf_val_str}",
            "sys_parsed": [], "pdf_parsed": [],
            "sys_base": None, "pdf_base": None,
        }

    # ── 감속비 비율 표기 정규화 ('50:1' vs '1:50' vs '1/50') ─────────
    sys_ratio = _parse_ratio(sys_val_str)
    pdf_ratio = _parse_ratio(pdf_val_str)
    if sys_ratio is not None and pdf_ratio is not None:
        # 두 비율을 각각 정방향/역방향으로 비교 (50:1 ↔ 1:50 둘 다 같은 비율)
        def _ratio_match(a: float, b: float) -> bool:
            if a == 0 or b == 0:
                return a == b
            return _values_close(a, b) or _values_close(a, 1 / b) or _values_close(1 / a, b)
        match = "일치" if _ratio_match(sys_ratio, pdf_ratio) else "불일치"
        return {
            "match": match,
            "note": f"감속비 정규화: {sys_val_str}({sys_ratio:.4g}) vs {pdf_val_str}({pdf_ratio:.4g})",
            "sys_parsed": [], "pdf_parsed": [],
            "sys_base": sys_ratio, "pdf_base": pdf_ratio,
        }

    # ── RPM/Hz 복합 표기 정규화 ('1735/35' vs '1735rpm' → 앞 숫자 비교) ─
    sys_rpm_str = _normalize_rpm_slash(sys_val_str)
    pdf_rpm_str = _normalize_rpm_slash(pdf_val_str)
    # 한쪽만 슬래시 표기인 경우도 처리 (1735/35 vs 1735)
    if sys_rpm_str is not None or pdf_rpm_str is not None:
        s_str = sys_rpm_str if sys_rpm_str is not None else sys_val_str
        p_str = pdf_rpm_str if pdf_rpm_str is not None else pdf_val_str
        # 앞 숫자끼리 재귀 비교 (단위 변환 포함)
        inner = compare_spec_values(s_str, p_str)
        if inner["match"] in ("일치", "단위변환일치", "범위내"):
            inner["note"] = f"RPM 복합표기 정규화: {sys_val_str} vs {pdf_val_str} → " + inner["note"]
        return inner

    sys_parsed = parse_value_with_unit(sys_val_str)
    pdf_parsed = parse_value_with_unit(pdf_val_str)

    # ── 타이틀 기반 기본 단위 적용 (단위 없는 숫자에만) ────────────────
    if title:
        default_unit = infer_default_unit(title)
        sys_val_str, sys_parsed = _apply_default_unit(sys_val_str, sys_parsed, default_unit)
        pdf_val_str, pdf_parsed = _apply_default_unit(pdf_val_str, pdf_parsed, default_unit)

    if not sys_parsed or not pdf_parsed:
        # 숫자가 없으면 문자열 정규화 비교
        sn = re.sub(r"[^a-z0-9가-힣]", "", sys_val_str.lower())
        pn = re.sub(r"[^a-z0-9가-힣]", "", pdf_val_str.lower())
        if sn and sn == pn:
            return {"match": "일치", "note": "문자열 일치", "sys_parsed": sys_parsed, "pdf_parsed": pdf_parsed}
        if sn and (sn in pn or pn in sn):
            return {"match": "일치", "note": "부분 문자열 일치", "sys_parsed": sys_parsed, "pdf_parsed": pdf_parsed}
        return {"match": "비교불가", "note": "숫자 없음", "sys_parsed": sys_parsed, "pdf_parsed": pdf_parsed}

    sp = sys_parsed[0]
    pp = pdf_parsed[0]

    sys_num = sp["number"]
    pdf_num = pp["number"]
    sys_unit = sp["unit"]
    pdf_unit = pp["unit"]

    # ── 온도 특수 처리 ──────────────────────────────────────────────
    temp_units = {"°c", "°f", "k", "c", "f"}
    if sys_unit in temp_units or pdf_unit in temp_units:
        def to_k(v: float, u: str) -> float:
            if u in ("°f", "f"):
                return _fahrenheit_to_kelvin(v)
            if u in ("k",):
                return v
            return _celsius_to_kelvin(v)  # °c or bare number

        try:
            sk = to_k(sys_num, sys_unit)
            pk = to_k(pdf_num, pdf_unit)
            if sp["is_range"] or pp["is_range"]:
                # 범위: PDF 범위 내에 시스템값이 있는지
                if pp["is_range"]:
                    pk_min = to_k(pp["range_min"], pdf_unit)
                    pk_max = to_k(pp["range_max"], pdf_unit)
                    sk_val = to_k(sp["number"], sys_unit) if not sp["is_range"] else to_k(sp["range_min"], sys_unit)
                    in_range = pk_min <= sk_val <= pk_max
                    return {
                        "match": "범위내" if in_range else "불일치",
                        "note": f"{sys_val_str} → {pdf_val_str} 범위 확인",
                        "sys_base": sk, "pdf_base": pk,
                        "sys_parsed": sys_parsed, "pdf_parsed": pdf_parsed,
                    }
            close = _values_close(sk, pk, tol=0.02)
            return {
                "match": "단위변환일치" if (sys_unit != pdf_unit and close) else ("일치" if close else "불일치"),
                "note": f"{sys_val_str} ≈ {pdf_val_str}" if close else f"{sys_val_str} ≠ {pdf_val_str}",
                "sys_base": sk, "pdf_base": pk,
                "sys_parsed": sys_parsed, "pdf_parsed": pdf_parsed,
            }
        except Exception:
            pass

    # ── 같은 단위 ────────────────────────────────────────────────────
    if sys_unit == pdf_unit or (not sys_unit and not pdf_unit):
        if sp["is_range"] and not pp["is_range"]:
            in_range = sp["range_min"] <= pdf_num <= sp["range_max"]
            return {
                "match": "범위내" if in_range else "불일치",
                "note": f"{pdf_num} in [{sp['range_min']}, {sp['range_max']}]",
                "sys_base": sys_num, "pdf_base": pdf_num,
                "sys_parsed": sys_parsed, "pdf_parsed": pdf_parsed,
            }
        if pp["is_range"] and not sp["is_range"]:
            in_range = pp["range_min"] <= sys_num <= pp["range_max"]
            return {
                "match": "범위내" if in_range else "불일치",
                "note": f"{sys_num} in [{pp['range_min']}, {pp['range_max']}]",
                "sys_base": sys_num, "pdf_base": pdf_num,
                "sys_parsed": sys_parsed, "pdf_parsed": pdf_parsed,
            }
        close = _values_close(sys_num, pdf_num)
        return {
            "match": "일치" if close else "불일치",
            "note": f"{sys_num} {'≈' if close else '≠'} {pdf_num} [{sys_unit}]",
            "sys_base": sys_num, "pdf_base": pdf_num,
            "sys_parsed": sys_parsed, "pdf_parsed": pdf_parsed,
        }

    # ── 단위 변환 ────────────────────────────────────────────────────
    sys_base = convert_to_base(sys_num, sys_unit)
    pdf_base = convert_to_base(pdf_num, pdf_unit)

    if sys_base is None or pdf_base is None:
        # 변환 불가 → 수치만 비교 (같은 단위 가정)
        close = _values_close(sys_num, pdf_num)
        return {
            "match": "일치" if close else "비교불가",
            "note": f"단위 변환 불가 ({sys_unit} ↔ {pdf_unit}), 수치만 비교",
            "sys_base": sys_base, "pdf_base": pdf_base,
            "sys_parsed": sys_parsed, "pdf_parsed": pdf_parsed,
        }

    close = _values_close(sys_base, pdf_base)
    return {
        "match": "단위변환일치" if close else "불일치",
        "note": f"{sys_val_str} → {sys_base:.4g} SI, {pdf_val_str} → {pdf_base:.4g} SI",
        "sys_base": sys_base, "pdf_base": pdf_base,
        "sys_parsed": sys_parsed, "pdf_parsed": pdf_parsed,
    }
