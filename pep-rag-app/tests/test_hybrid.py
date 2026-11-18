from __future__ import annotations

import pytest

from peptalk.retrieval import MODES, build_retriever
from peptalk.retrieval.hybrid import RRF_K, HybridRetriever, rrf_fuse

from fakes import FIXTURE, FakeEmbedder, _settings


class _StubRanker:
    """Returns a fixed ranking as Hits so fusion can be checked exactly."""

    def __init__(self, mode: str, ordered_chunks):
        self.mode = mode
        self._ordered = ordered_chunks
        self.calls = []

    def search(self, query, k=5):
        from peptalk.retrieval import Hit

        self.calls.append(k)
        return [Hit(chunk=c, score=1.0 / (i + 1), rank=i + 1) for i, c in enumerate(self._ordered[:k])]


def test_rrf_fuse_known_values():
    # Item 1 is 2nd in ranking A and 1st in B; item 2 is 1st in A only; item 3 is 2nd in B only.
    fused = rrf_fuse([[2, 1], [1, 3]], k=60)
    expected_1 = 1 / 62 + 1 / 61
    expected_2 = 1 / 61
    expected_3 = 1 / 62
    assert [item for item, _ in fused] == [1, 2, 3]
    assert dict(fused) == pytest.approx({1: expected_1, 2: expected_2, 3: expected_3})


def test_rrf_fuse_default_k_is_60():
    assert RRF_K == 60
    assert rrf_fuse([[7]])[0] == (7, pytest.approx(1 / 61))


def test_rrf_fuse_ties_keep_first_seen_order():
    fused = rrf_fuse([[5, 6], [6, 5]])
    assert [item for item, _ in fused] == [5, 6]  # both score equally; 5 was seen first


def test_rrf_fuse_handles_empty_input():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []


def test_hybrid_fuses_bm25_and_dense_rankings(tmp_path):
    chunks = FIXTURE
    bm25 = _StubRanker("bm25", [chunks[0], chunks[3], chunks[1]])
    dense = _StubRanker("dense", [chunks[3], chunks[2], chunks[0]])
    retriever = HybridRetriever(chunks, settings=_settings(tmp_path), dense=dense, bm25=bm25)

    hits = retriever.search("anything", k=4)

    # chunk[3] is 2nd in bm25 and 1st in dense; chunk[0] is 1st in bm25 and 3rd in dense.
    expected = rrf_fuse([[0, 3, 1], [3, 2, 0]])
    assert [h.chunk.chunk_id for h in hits] == [chunks[i].chunk_id for i, _ in expected]
    assert [h.rank for h in hits] == [1, 2, 3, 4]
    assert [h.score for h in hits] == pytest.approx([s for _, s in expected])
    assert hits[0].chunk is chunks[3]


def test_hybrid_keeps_chunks_found_by_only_one_retriever(tmp_path):
    chunks = FIXTURE
    bm25 = _StubRanker("bm25", [chunks[0]])
    dense = _StubRanker("dense", [chunks[2]])
    hits = HybridRetriever(chunks, dense=dense, bm25=bm25).search("q", k=5)
    assert {h.chunk.pep for h in hits} == {10, 30}


def test_hybrid_uses_pool_depth_and_truncates_to_k(tmp_path):
    chunks = FIXTURE
    bm25 = _StubRanker("bm25", chunks)
    dense = _StubRanker("dense", chunks)
    retriever = HybridRetriever(chunks, dense=dense, bm25=bm25, pool=3)
    hits = retriever.search("q", k=2)
    assert len(hits) == 2
    assert bm25.calls == [3] and dense.calls == [3]  # depth = max(pool, k)

    retriever.search("q", k=10)
    assert bm25.calls[-1] == 10


def test_hybrid_k_zero_returns_nothing(tmp_path):
    chunks = FIXTURE
    retriever = HybridRetriever(chunks, dense=_StubRanker("dense", chunks), bm25=_StubRanker("bm25", chunks))
    assert retriever.search("q", k=0) == []


def test_hybrid_end_to_end_with_real_bm25_and_fake_dense(tmp_path):
    from peptalk.retrieval.dense import DenseRetriever

    settings = _settings(tmp_path)
    dense = DenseRetriever(FIXTURE, settings=settings, embedder=FakeEmbedder())
    retriever = HybridRetriever(FIXTURE, settings=settings, dense=dense)
    hits = retriever.search("walrus operator inside an expression", k=3)
    assert hits[0].chunk.pep == 40
    assert [h.rank for h in hits] == [1, 2, 3]


def test_build_retriever_hybrid_mode(tmp_path):
    assert "hybrid" in MODES
    retriever = build_retriever("hybrid", FIXTURE, settings=_settings(tmp_path), embedder=FakeEmbedder())
    assert retriever.mode == "hybrid"
    assert retriever.search("walrus operator", k=1)[0].chunk.pep == 40
