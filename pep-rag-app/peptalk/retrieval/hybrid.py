"""Hybrid retrieval: BM25 and dense rankings fused with reciprocal rank fusion.

Each retriever contributes its top ``pool`` chunks.  A chunk's fused score is

    score(chunk) = sum over rankings r containing it of 1 / (rrf_k + rank_r(chunk))

with 1-based ranks and ``rrf_k = 60`` by default.  Chunks found by only one
retriever still count; RRF needs no score calibration between BM25 and cosine.
"""
from __future__ import annotations

from typing import Sequence

from ..config import Settings
from ..corpus import Chunk
from . import Hit
from .bm25 import BM25Retriever
from .dense import DenseRetriever, Embedder

RRF_K = 60
POOL = 50


def rrf_fuse(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Fuse best-first rankings of item ids. Returns (item, score), best first.

    Ties keep first-seen order, so the result is deterministic for fixed input.
    """
    scores: dict[int, float] = {}
    first_seen: dict[int, int] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
            first_seen.setdefault(item, len(first_seen))
    return sorted(scores.items(), key=lambda pair: (-pair[1], first_seen[pair[0]]))


class HybridRetriever:
    mode = "hybrid"

    def __init__(
        self,
        chunks: Sequence[Chunk],
        settings: Settings | None = None,
        embedder: Embedder | None = None,
        dense: DenseRetriever | None = None,
        bm25: BM25Retriever | None = None,
        rrf_k: int = RRF_K,
        pool: int = POOL,
    ):
        self._chunks = list(chunks)
        self._index = {c.chunk_id: i for i, c in enumerate(self._chunks)}
        self._bm25 = bm25 if bm25 is not None else BM25Retriever(self._chunks)
        self._dense = (
            dense if dense is not None else DenseRetriever(self._chunks, settings=settings, embedder=embedder)
        )
        self._rrf_k = rrf_k
        self._pool = pool

    def search(self, query: str, k: int = 5) -> list[Hit]:
        if k <= 0:
            return []
        depth = max(self._pool, k)
        lexical = [self._index[h.chunk.chunk_id] for h in self._bm25.search(query, depth)]
        semantic = [self._index[h.chunk.chunk_id] for h in self._dense.search(query, depth)]
        fused = rrf_fuse([lexical, semantic], self._rrf_k)[:k]
        return [
            Hit(chunk=self._chunks[index], score=score, rank=rank)
            for rank, (index, score) in enumerate(fused, start=1)
        ]
