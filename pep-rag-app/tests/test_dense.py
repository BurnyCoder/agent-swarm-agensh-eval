from __future__ import annotations

import pytest

from fakes import FIXTURE, FakeEmbedder, _chunk, _settings
from peptalk.retrieval import MODES, build_retriever
from peptalk.retrieval.dense import DenseRetriever, cache_path


def test_search_ranks_the_matching_chunk_first(tmp_path):
    retriever = DenseRetriever(FIXTURE, settings=_settings(tmp_path), embedder=FakeEmbedder())
    hits = retriever.search("walrus operator assigns a name", k=3)
    assert hits[0].chunk.pep == 40
    assert [h.rank for h in hits] == [1, 2, 3]
    assert hits[0].score >= hits[1].score >= hits[2].score


def test_scores_are_cosine_similarities(tmp_path):
    retriever = DenseRetriever(FIXTURE, settings=_settings(tmp_path), embedder=FakeEmbedder())
    hits = retriever.search("walrus operator", k=len(FIXTURE))
    assert all(-1.0 - 1e-6 <= h.score <= 1.0 + 1e-6 for h in hits)
    assert hits[0].score == pytest.approx(max(h.score for h in hits))


def test_k_limits_results_and_nonpositive_k_returns_nothing(tmp_path):
    retriever = DenseRetriever(FIXTURE, settings=_settings(tmp_path), embedder=FakeEmbedder())
    assert len(retriever.search("expression", k=2)) == 2
    assert len(retriever.search("expression", k=100)) == len(FIXTURE)
    assert retriever.search("expression", k=0) == []
    assert retriever.search("expression", k=-3) == []


def test_blank_query_and_empty_chunks_return_nothing(tmp_path):
    embedder = FakeEmbedder()
    retriever = DenseRetriever(FIXTURE, settings=_settings(tmp_path), embedder=embedder)
    assert retriever.search("   ", k=5) == []
    assert embedder.query_calls == 0
    empty = DenseRetriever([], settings=_settings(tmp_path), embedder=FakeEmbedder())
    assert empty.search("anything", k=5) == []


def test_vectors_are_cached_and_reused_by_a_new_retriever(tmp_path):
    settings = _settings(tmp_path)
    first = FakeEmbedder()
    DenseRetriever(FIXTURE, settings=settings, embedder=first).search("walrus", k=1)
    assert first.document_calls == 1
    assert cache_path(tmp_path, FakeEmbedder.model_name).is_file()

    second = FakeEmbedder()
    hits = DenseRetriever(FIXTURE, settings=settings, embedder=second).search("walrus", k=1)
    assert second.document_calls == 0  # served from the cache file
    assert hits[0].chunk.pep == 40


def test_cache_is_rebuilt_when_chunk_ids_change(tmp_path):
    settings = _settings(tmp_path)
    DenseRetriever(FIXTURE, settings=settings, embedder=FakeEmbedder()).search("walrus", k=1)

    changed = FIXTURE + [_chunk(50, "Abstract", "Decorators wrap callables.")]
    embedder = FakeEmbedder()
    hits = DenseRetriever(changed, settings=settings, embedder=embedder).search("decorators", k=1)
    assert embedder.document_calls == 1
    assert hits[0].chunk.pep == 50


def test_unreadable_cache_is_rebuilt(tmp_path):
    settings = _settings(tmp_path)
    cache_path(tmp_path, FakeEmbedder.model_name).write_bytes(b"not an npz file")
    embedder = FakeEmbedder()
    hits = DenseRetriever(FIXTURE, settings=settings, embedder=embedder).search("walrus", k=1)
    assert embedder.document_calls == 1
    assert hits[0].chunk.pep == 40


def test_explicit_cache_file_is_used(tmp_path):
    cache = tmp_path / "custom.npz"
    DenseRetriever(FIXTURE, settings=_settings(tmp_path), embedder=FakeEmbedder(), cache_file=cache).search("x")
    assert cache.is_file()


def test_cache_path_is_slugged_under_data_dir(tmp_path):
    assert cache_path(tmp_path, "BAAI/bge-small-en-v1.5") == tmp_path / "embeddings-BAAI--bge-small-en-v1.5.npz"


def test_build_retriever_dense_mode(tmp_path):
    assert "dense" in MODES
    retriever = build_retriever("dense", FIXTURE, settings=_settings(tmp_path), embedder=FakeEmbedder())
    assert retriever.mode == "dense"
    assert retriever.search("walrus", k=1)[0].chunk.pep == 40
