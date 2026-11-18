"""BM25 lexical retrieval over chunks (``rank-bm25``, Okapi variant)."""
from __future__ import annotations

import re
from typing import Sequence

from rank_bm25 import BM25Okapi

from ..corpus import Chunk
from . import Hit, ranked_hits

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Function words that carry no retrieval signal in PEP prose.
STOPWORDS = frozenset(
    "a an and are as at be by for from has have if in is it its of on or that the "
    "this to was were will with".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, without stopwords."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


class BM25Retriever:
    mode = "bm25"

    def __init__(self, chunks: Sequence[Chunk]):
        self._chunks = list(chunks)
        corpus = [tokenize(c.search_text) for c in self._chunks]
        # BM25Okapi cannot be built on an empty corpus; keep search() working with no hits.
        self._bm25 = BM25Okapi(corpus) if corpus and any(corpus) else None

    def search(self, query: str, k: int = 5) -> list[Hit]:
        if self._bm25 is None or k <= 0:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        scored = [(i, float(scores[i])) for i in order if scores[i] > 0][:k]
        return ranked_hits(self._chunks, scored, k)
