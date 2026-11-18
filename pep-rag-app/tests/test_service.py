"""Offline tests for RetrievalService: flat hits, lazy retrievers, dense gating."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from peptalk import service
from peptalk.service import RetrievalService, make_snippet, to_service_hit


@dataclass
class FakeChunk:
    chunk_id: str
    pep: int
    title: str
    section: str
    text: str
    start: int
    end: int


@dataclass
class FakeHit:
    chunk: FakeChunk
    score: float
    rank: int


CHUNKS = [
    FakeChunk("572-rationale-0", 572, "Assignment Expressions", "Rationale",
              "Assignment expressions   let you\nbind names.", 100, 220),
    FakeChunk("8-0", 8, "Style Guide", "", "Use four spaces per indentation level.", 0, 90),
]


class FakeCore:
    """Stands in for a core retriever: search(query, k) -> list[Hit]."""

    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, query, k=5):
        self.calls.append((query, k))
        return self.hits[:k]


class Builder:
    def __init__(self):
        self.built = []

    def __call__(self, mode, chunks, **options):
        self.built.append((mode, list(chunks), options))
        return FakeCore([FakeHit(CHUNKS[0], 0.9, 1), FakeHit(CHUNKS[1], 0.4, 2)])


# ---------------------------------------------------------------- snippets


def test_make_snippet_collapses_whitespace():
    assert make_snippet("one\n\n two\tthree") == "one two three"


def test_make_snippet_keeps_short_text_whole():
    assert make_snippet("short text") == "short text"


def test_make_snippet_cuts_at_word_boundary_with_ellipsis():
    text = "word " * 100
    out = make_snippet(text, limit=20)
    assert out.endswith("…")
    assert len(out) <= 21
    assert not out[:-1].endswith(" ")
    assert out[:-1].rstrip() == out[:-1]


def test_make_snippet_handles_empty_and_none():
    assert make_snippet("") == ""
    assert make_snippet(None) == ""


def test_make_snippet_strips_trailing_punctuation_before_ellipsis():
    out = make_snippet("alpha beta, gamma delta", limit=11)
    assert out == "alpha beta…"


# ---------------------------------------------------------------- hit conversion


def test_to_service_hit_flattens_core_hit():
    out = to_service_hit(FakeHit(CHUNKS[0], 0.75, 3))
    assert out.rank == 3
    assert out.pep == 572
    assert out.title == "Assignment Expressions"
    assert out.section == "Rationale"
    assert out.score == 0.75
    assert out.snippet == "Assignment expressions let you bind names."
    assert out.chunk_id == "572-rationale-0"
    assert (out.start, out.end) == (100, 220)
    assert out.text == CHUNKS[0].text


def test_to_service_hit_fills_missing_section_with_empty_string():
    chunk = FakeChunk("1-0", 1, "T", None, "body", 0, 4)
    assert to_service_hit(FakeHit(chunk, 0.1, 1)).section == ""


# ---------------------------------------------------------------- RetrievalService


def test_search_returns_flat_hits_from_builder():
    builder = Builder()
    svc = RetrievalService(CHUNKS, builder=builder)
    hits = svc.search("walrus", mode="bm25", k=2)
    assert [h.pep for h in hits] == [572, 8]
    assert [h.rank for h in hits] == [1, 2]


def test_search_passes_query_and_k_to_core():
    builder = Builder()
    svc = RetrievalService(CHUNKS, builder=builder)
    svc.search("walrus", mode="bm25", k=1)
    core = svc.retriever("bm25")
    assert core.calls == [("walrus", 1)]


def test_retriever_is_built_lazily_and_cached():
    builder = Builder()
    svc = RetrievalService(CHUNKS, builder=builder)
    assert builder.built == []
    svc.search("a", mode="bm25")
    svc.search("b", mode="bm25")
    assert len(builder.built) == 1
    assert builder.built[0][0] == "bm25"


def test_bm25_builds_without_settings_options():
    builder = Builder()
    svc = RetrievalService(CHUNKS, settings="S", builder=builder)
    svc.retriever("bm25")
    assert builder.built[0][2] == {}


def test_dense_builds_with_settings_when_enabled():
    builder = Builder()
    svc = RetrievalService(CHUNKS, settings="S", dense=True, builder=builder)
    svc.retriever("dense")
    svc.retriever("hybrid")
    assert [m for m, _, _ in builder.built] == ["dense", "hybrid"]
    assert all(opts == {"settings": "S"} for _, _, opts in builder.built)


def test_dense_is_refused_when_disabled_and_never_built():
    builder = Builder()
    svc = RetrievalService(CHUNKS, builder=builder)
    assert svc.dense_available is False
    with pytest.raises(ValueError, match="dense embeddings"):
        svc.search("x", mode="dense")
    with pytest.raises(ValueError, match="dense embeddings"):
        svc.search("x", mode="hybrid")
    assert builder.built == []


def test_unknown_mode_is_rejected():
    svc = RetrievalService(CHUNKS, builder=Builder())
    with pytest.raises(ValueError, match="mode must be one of"):
        svc.search("x", mode="fuzzy")


def test_default_mode_is_bm25():
    builder = Builder()
    svc = RetrievalService(CHUNKS, builder=builder)
    svc.search("x")
    assert builder.built[0][0] == "bm25"


def test_modes_constant_lists_all_three():
    assert service.MODES == ("bm25", "dense", "hybrid")


def test_default_builder_imports_core_only_when_called(monkeypatch):
    import sys

    monkeypatch.delitem(sys.modules, "peptalk.retrieval", raising=False)
    svc = RetrievalService(CHUNKS)
    assert "peptalk.retrieval" not in sys.modules
    assert svc.dense_available is False


def test_get_retriever_loads_index_from_settings(monkeypatch):
    import sys
    import types

    seen = {}
    config = types.ModuleType("peptalk.config")
    config.get_settings = lambda: "SETTINGS"
    index = types.ModuleType("peptalk.index")

    def load_chunks(settings):
        seen["settings"] = settings
        return CHUNKS

    index.load_chunks = load_chunks
    monkeypatch.setitem(sys.modules, "peptalk.config", config)
    monkeypatch.setitem(sys.modules, "peptalk.index", index)

    svc = service.get_retriever()
    assert seen["settings"] == "SETTINGS"
    assert svc.chunks == CHUNKS
    assert svc.dense_available is True
    assert svc.settings == "SETTINGS"


def test_get_retriever_can_disable_dense(monkeypatch):
    import sys
    import types

    config = types.ModuleType("peptalk.config")
    config.get_settings = lambda: None
    index = types.ModuleType("peptalk.index")
    index.load_chunks = lambda settings: CHUNKS
    monkeypatch.setitem(sys.modules, "peptalk.config", config)
    monkeypatch.setitem(sys.modules, "peptalk.index", index)

    assert service.get_retriever(dense=False).dense_available is False
