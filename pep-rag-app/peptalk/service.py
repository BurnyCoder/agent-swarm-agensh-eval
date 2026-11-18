"""Retrieval service shared by the web app: one object, flat hits, lazy retrievers.

The core retrievers (``peptalk.retrieval``) return ``Hit(chunk, score, rank)`` and
take ``search(query, k)``. The web app and ``POST /api/ask`` want a flat record
per passage and ``search(query, mode, k)``. ``RetrievalService`` adapts one to
the other and builds each mode's retriever on first use, so a BM25-only server
never loads the embedding model.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Sequence

MODES = ("bm25", "dense", "hybrid")
SNIPPET_CHARS = 240

Builder = Callable[..., Any]


def build_retriever(mode: str, chunks: Sequence[Any], **options: Any) -> Any:
    """Default builder: import ``peptalk.retrieval`` only when a mode is first used.

    Keeps BM25-only servers (and the offline tests) from importing the embedding stack.
    """
    from peptalk.retrieval import build_retriever as core_build

    return core_build(mode, chunks, **options)


@dataclass(frozen=True)
class ServiceHit:
    """One retrieved chunk, flattened. ``text`` is the full chunk text (not serialised to clients)."""

    rank: int
    pep: int
    title: str
    section: str
    score: float
    snippet: str
    chunk_id: Any
    start: int
    end: int
    text: str


def make_snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    """Collapse whitespace and cut to about ``limit`` characters at a word boundary."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.") + "…"


def to_service_hit(hit: Any) -> ServiceHit:
    """Convert a core ``Hit`` (or anything with the same attributes) to a ``ServiceHit``."""
    chunk = hit.chunk
    return ServiceHit(
        rank=int(hit.rank),
        pep=int(chunk.pep),
        title=chunk.title or "",
        section=chunk.section or "",
        score=float(hit.score),
        snippet=make_snippet(chunk.text),
        chunk_id=chunk.chunk_id,
        start=int(chunk.start),
        end=int(chunk.end),
        text=chunk.text,
    )


class RetrievalService:
    """``search(query, mode, k)`` over a fixed list of chunks.

    ``dense`` says whether the embedding modes may be used. It is a flag, not a
    capability probe: with ``dense=False`` the dense and hybrid modes raise
    ``ValueError`` and no embedding code is imported.
    """

    def __init__(
        self,
        chunks: Sequence[Any],
        settings: Any | None = None,
        dense: bool = False,
        builder: Builder = build_retriever,
    ) -> None:
        self.chunks = list(chunks)
        self.settings = settings
        self.dense_available = bool(dense)
        self._builder = builder
        self._retrievers: dict[str, Any] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_index(cls, settings: Any, dense: bool = False) -> "RetrievalService":
        """Load the persisted index under ``settings.data_dir`` (see ``python3 -m peptalk index``)."""
        from peptalk.index import load_chunks

        return cls(load_chunks(settings), settings=settings, dense=dense)

    def retriever(self, mode: str) -> Any:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if mode != "bm25" and not self.dense_available:
            raise ValueError(f"mode '{mode}' needs dense embeddings, which are disabled")
        with self._lock:
            if mode not in self._retrievers:
                options: dict[str, Any] = {} if mode == "bm25" else {"settings": self.settings}
                self._retrievers[mode] = self._builder(mode, self.chunks, **options)
            return self._retrievers[mode]

    def search(self, query: str, mode: str = "bm25", k: int = 5) -> list[ServiceHit]:
        hits = self.retriever(mode).search(query, k=k)
        return [to_service_hit(h) for h in hits]


def get_retriever(dense: bool = True) -> RetrievalService:
    """Service over the persisted index, for CLI commands such as ``ask``.

    Config and index modules are imported here so importing ``service`` stays cheap.
    Dense is allowed by default because ``ask`` defaults to hybrid; the embedding
    model loads only when a dense or hybrid query first runs.
    """
    from peptalk.config import get_settings

    return RetrievalService.from_index(get_settings(), dense=dense)
