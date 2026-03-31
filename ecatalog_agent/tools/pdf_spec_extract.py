"""
시스템 사양값과 PDF 대조 시, 모델명 텍스트 매칭과는 다른 전략이 필요하다.

- 텍스트만으로는 표·그래프·도면에 있는 수치가 누락되기 쉽다.
- 구조화 추출은 GPT 비전(페이지 이미지) 또는 표 전용 파서가 적합하다.

현재는 `vision_order_code`의 GPT 응답 필드 `spec_hints`를 judgment에 실어
UI·후속 파이프라인에서 활용한다. 별도 배치 추출 함수는 필요 시 여기에 추가한다.
"""

from __future__ import annotations

from typing import Any


def spec_hints_from_vision(parsed: dict[str, Any] | None) -> list[dict[str, str]]:
    if not parsed:
        return []
    raw = parsed.get("spec_hints")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        t = str(item.get("title") or item.get("name") or "").strip()
        v = str(item.get("value") or "").strip()
        if t and v:
            out.append({"title": t, "value": v})
    return out
