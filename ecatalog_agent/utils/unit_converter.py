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


def _values_close(a: float, b: float, tol: float = _TOLERANCE) -> bool:
    """두 값이 허용 오차 내에 있는지 확인한다."""
    if a == 0 and b == 0:
        return True
    if a == 0 or b == 0:
        return abs(a - b) < 1e-9
    return abs(a - b) / max(abs(a), abs(b)) <= tol


def compare_spec_values(
    sys_val_str: str,
    pdf_val_str: str,
) -> dict[str, Any]:
    """
    시스템 등록값과 PDF/도면 추출값을 단위 변환 후 비교한다.

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
    sys_parsed = parse_value_with_unit(sys_val_str)
    pdf_parsed = parse_value_with_unit(pdf_val_str)

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
