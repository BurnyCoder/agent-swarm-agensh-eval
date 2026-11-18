"""Offline fakes shared by the dense and hybrid retrieval tests."""

from __future__ import annotations

import zlib

import numpy as np

from peptalk.config import Settings
from peptalk.corpus import Chunk
from peptalk.retrieval.bm25 import tokenize

DIM = 128



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


class FakeEmbedder:
    """Hashed bag-of-words embedder: deterministic, offline, counts calls."""

    model_name = "fake/hash-embedder"

    def __init__(self):
        self.document_calls = 0
        self.query_calls = 0

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        vec = np.zeros(DIM, dtype=np.float32)
        for token in tokenize(text):
            vec[zlib.crc32(token.encode()) % DIM] += 1.0
        return vec

    def embed_documents(self, texts):
        self.document_calls += 1
        return np.stack([self._vector(t) for t in texts]).astype(np.float32)

    def embed_query(self, text):
        self.query_calls += 1
        return self._vector(text)


def _settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, embed_model=FakeEmbedder.model_name)
