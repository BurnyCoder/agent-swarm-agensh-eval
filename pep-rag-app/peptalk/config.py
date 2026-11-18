"""Runtime configuration, read from environment variables.

Every setting has a default so that ``python3 -m peptalk index`` and the
BM25 retrieval path work with no environment and no network access.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    corpus_dir: Path = REPO_ROOT / "corpus" / "peps"
    data_dir: Path = REPO_ROOT / "data"
    # Generation (Claude).
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    # Base URL resolution: AGENSH_MODEL_BASE, then ANTHROPIC_BASE_URL, then SDK default (None).
    base_url: str | None = None
    workspace_id: str | None = None
    # Dense retrieval.
    embed_model: str = DEFAULT_EMBED_MODEL
    # Web server.
    host: str = "0.0.0.0"
    port: int = 8000
    # Chunking.
    max_chunk_chars: int = 1200
    chunk_overlap_chars: int = 200
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        port = _env("PORT", "8000")
        return cls(
            model=_env("RAG_MODEL", DEFAULT_MODEL),
            api_key=_env("ANTHROPIC_API_KEY"),
            base_url=_env("AGENSH_MODEL_BASE") or _env("ANTHROPIC_BASE_URL"),
            workspace_id=_env("ANTHROPIC_WORKSPACE_ID"),
            embed_model=_env("RAG_EMBED_MODEL", DEFAULT_EMBED_MODEL),
            port=int(port),
        )


def get_settings() -> Settings:
    """Settings snapshot from the current environment."""
    return Settings.from_env()
