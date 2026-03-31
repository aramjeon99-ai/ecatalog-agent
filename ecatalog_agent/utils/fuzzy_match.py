from rapidfuzz import fuzz


def token_sort_ratio(a: str, b: str) -> float:
    return float(fuzz.token_sort_ratio(a or "", b or "")) / 100.0


def partial_ratio(a: str, b: str) -> float:
    return float(fuzz.partial_ratio(a or "", b or "")) / 100.0

