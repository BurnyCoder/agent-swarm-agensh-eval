"""Dense retrieval: cosine similarity over sentence embeddings (``fastembed``).

Chunk vectors are computed once per (embedding model, chunk set) and cached in
``data/embeddings-<model>.npz`` next to the chunk index, so later queries and
later processes do not re-embed the corpus.  The cache records the chunk ids
it was built from; if they differ from the current chunks it is rebuilt.

The embedder is injectable (``embedder=``) so tests run offline.  The default
is a fastembed ``TextEmbedding`` for ``settings.embed_model`` (ONNX, CPU); the
model is downloaded on first use only, never at import time.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

from ..config import Settings, get_settings
from ..corpus import Chunk
from . import Hit

CACHE_PREFIX = "embeddings-"


class Embedder(Protocol):
    """Maps text to unit-length float32 vectors, one row per input."""

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedEmbedder:
    """fastembed wrapper. Imports fastembed (and loads the model) on construction."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding  # imported here so BM25-only runs never load it

        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name)

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        vectors = np.asarray(list(self._model.embed(list(texts), batch_size=64)), dtype=np.float32)
        return _normalize(vectors.reshape(len(texts), -1))

    def embed_query(self, text: str) -> np.ndarray:
        # bge models expect the query instruction prefix; fastembed applies it in query_embed.
        vector = np.asarray(next(iter(self._model.query_embed([text]))), dtype=np.float32)
        return _normalize(vector.reshape(1, -1))[0]


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32)


def cache_path(data_dir: Path, model_name: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "--", model_name).strip("-")
    return data_dir / f"{CACHE_PREFIX}{slug}.npz"


class DenseRetriever:
    mode = "dense"

    def __init__(
        self,
        chunks: Sequence[Chunk],
        settings: Settings | None = None,
        embedder: Embedder | None = None,
        cache_file: Path | None = None,
    ):
        self._settings = settings or get_settings()
        self._chunks = list(chunks)
        self._embedder = embedder
        self._cache_file = cache_file
        self._vectors: np.ndarray | None = None  # shape (n_chunks, dim), built lazily

    def _model_name(self) -> str:
        return getattr(self._embedder, "model_name", None) or self._settings.embed_model

    def _get_embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = FastEmbedEmbedder(self._settings.embed_model)
        return self._embedder

    def _path(self) -> Path:
        if self._cache_file is not None:
            return self._cache_file
        return cache_path(self._settings.data_dir, self._model_name())

    def _load_vectors(self) -> np.ndarray:
        if self._vectors is not None:
            return self._vectors
        ids = [c.chunk_id for c in self._chunks]
        path = self._path()
        if path.is_file():
            try:
                with np.load(path, allow_pickle=False) as cached:
                    if list(cached["chunk_ids"]) == ids:
                        self._vectors = cached["vectors"].astype(np.float32)
                        return self._vectors
            except (OSError, KeyError, ValueError):
                pass  # unreadable or old cache: rebuild below
        vectors = self._embed_chunks()
        self._save(path, ids, vectors)
        self._vectors = vectors
        return vectors

    def _embed_chunks(self) -> np.ndarray:
        if not self._chunks:
            return np.zeros((0, 0), dtype=np.float32)
        texts = [c.search_text for c in self._chunks]
        return _normalize(np.asarray(self._get_embedder().embed_documents(texts), dtype=np.float32))

    @staticmethod
    def _save(path: Path, ids: list[str], vectors: np.ndarray) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.stem + ".tmp.npz")  # np.savez appends .npz only if missing
        np.savez_compressed(tmp, chunk_ids=np.array(ids, dtype=str), vectors=vectors)
        os.replace(tmp, path)

    def search(self, query: str, k: int = 5) -> list[Hit]:
        if k <= 0 or not self._chunks or not query.strip():
            return []
        vectors = self._load_vectors()
        q = _normalize(np.asarray(self._get_embedder().embed_query(query), dtype=np.float32).reshape(1, -1))[0]
        scores = vectors @ q
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))[:k]
        return [
            Hit(chunk=self._chunks[i], score=float(scores[i]), rank=r)
            for r, i in enumerate(order, start=1)
        ]
