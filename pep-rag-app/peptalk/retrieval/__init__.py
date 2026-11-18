"""Retrieval: turn a query into a ranked list of chunks.

Every retriever implements ``search(query, k) -> list[Hit]`` and has a ``mode``
name (``bm25``, ``dense`` or ``hybrid``).  ``build_retriever`` is the only place
that picks an implementation, so the CLI, the web API and the evaluation share
one code path.  Dense and hybrid modules are imported lazily: a BM25-only run
never loads the embedding model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ..corpus import Chunk

MODES = ("bm25", "dense", "hybrid")


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    rank: int  # 1-based position in the returned list


class Retriever(Protocol):
    mode: str

    def search(self, query: str, k: int = 5) -> list[Hit]:
        ...


def ranked_hits(chunks: Sequence[Chunk], scored: Sequence[tuple[int, float]], k: int) -> list[Hit]:
    """Turn (chunk index, score) pairs, best first, into ``Hit`` objects."""
    hits: list[Hit] = []
    for index, score in scored[:k]:
        hits.append(Hit(chunk=chunks[index], score=float(score), rank=len(hits) + 1))
    return hits


def build_retriever(mode: str, chunks: Sequence[Chunk], **options) -> Retriever:
    """Create the retriever for ``mode``. Extra options go to the dense/hybrid constructors."""
    if mode == "bm25":
        from .bm25 import BM25Retriever

        return BM25Retriever(chunks)
    if mode == "dense":
        from .dense import DenseRetriever

        return DenseRetriever(chunks, **options)
    if mode == "hybrid":
        from .hybrid import HybridRetriever

        return HybridRetriever(chunks, **options)
    raise ValueError(f"unknown retrieval mode {mode!r}; expected one of {', '.join(MODES)}")
