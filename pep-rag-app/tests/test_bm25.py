from peptalk.corpus import Chunk
from peptalk.retrieval import MODES, Hit, build_retriever
from peptalk.retrieval.bm25 import BM25Retriever, tokenize


def _chunk(n: int, section: str, text: str, title: str = "T") -> Chunk:
    return Chunk(
        chunk_id=f"pep-{n:04d}#000",
        pep=n,
        title=title,
        section=section,
        text=text,
        start=0,
        end=len(text),
    )


FIXTURE = [
    _chunk(10, "Abstract", "We propose a lightweight syntax for binding values inside expressions."),
    _chunk(20, "Abstract", "Type hints annotate function parameters and return values for checkers."),
    _chunk(30, "Rationale", "Garbage collection reclaims memory via reference counting and cycles."),
    _chunk(40, "Specification", "The walrus operator := assigns a name inside an expression."),
]


def test_tokenize_lowercases_and_drops_stopwords():
    assert tokenize("The Walrus operator is := in an expression") == ["walrus", "operator", "expression"]


def test_bm25_ranks_the_matching_chunk_first():
    hits = BM25Retriever(FIXTURE).search("walrus operator", k=3)
    assert [h.chunk.pep for h in hits] == [40]
    assert hits[0].rank == 1
    assert hits[0].score > 0


def test_bm25_orders_by_score_and_drops_zero_scores():
    hits = BM25Retriever(FIXTURE).search("type hints annotate", k=5)
    assert hits[0].chunk.pep == 20
    assert all(h.score > 0 for h in hits)
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))
    assert len(hits) == 1  # no other fixture chunk shares a term


def test_bm25_respects_k_and_empty_queries():
    retriever = BM25Retriever(FIXTURE)
    assert len(retriever.search("expression", k=1)) == 1
    assert retriever.search("the of and", k=5) == []
    assert retriever.search("unknownterm", k=5) == []
    assert retriever.search("expression", k=0) == []


def test_bm25_uses_title_and_section_as_context():
    # "zebra" occurs only in the title of chunk 1, so it must still find that chunk.
    chunks = FIXTURE + [_chunk(1, "Motivation", "Something unrelated here.", title="Zebra Proposal")]
    hits = BM25Retriever(chunks).search("zebra", k=1)
    assert hits and hits[0].chunk.pep == 1


def test_bm25_handles_empty_index():
    assert BM25Retriever([]).search("anything", k=3) == []


def test_build_retriever_bm25_and_unknown_mode():
    assert set(MODES) == {"bm25", "dense", "hybrid"}
    retriever = build_retriever("bm25", FIXTURE)
    assert retriever.mode == "bm25"
    assert isinstance(retriever.search("garbage collection", k=1)[0], Hit)
    try:
        build_retriever("fuzzy", FIXTURE)
    except ValueError as exc:
        assert "fuzzy" in str(exc)
    else:
        raise AssertionError("expected ValueError")
