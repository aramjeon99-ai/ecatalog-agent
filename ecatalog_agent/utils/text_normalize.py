import re


_LEGAL_SUFFIX_RE = re.compile(
    r"\b("
    r"co\.?|corp\.?|ltd\.?|inc\.?|gmbh|s\.?a\.?|주식회사|\(주\)"
    r")\b",
    flags=re.IGNORECASE,
)


def normalize_model(model: str) -> str:
    """
    Normalize model code for fuzzy matching/search.
    """
    m = (model or "").strip().lower()
    # Convert separators to spaces so token_sort_ratio works better.
    m = re.sub(r"[-_/\\.]+", " ", m)
    m = re.sub(r"\s+", " ", m).strip()
    return m


def normalize_maker(maker: str) -> str:
    m = (maker or "").strip().lower()
    m = _LEGAL_SUFFIX_RE.sub("", m)
    m = re.sub(r"\s+", " ", m).strip()
    return m

