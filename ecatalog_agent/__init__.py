"""ecatalog_agent — import 시 프로젝트 루트의 `.env`를 로드한다 (OPENAI_API_KEY 등)."""

from __future__ import annotations

from pathlib import Path

__all__: list[str] = []


def _load_project_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")


_load_project_env()
