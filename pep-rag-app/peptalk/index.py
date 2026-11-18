"""Persist and reload the chunk index under ``data/``.

Layout (``data/`` is gitignored)::

    data/chunks.json      all chunks, as JSON objects (see ``corpus.Chunk``)
    data/manifest.json    counts, chunking parameters and a corpus fingerprint

Dense embeddings are stored beside these files by the dense retriever.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .corpus import Chunk, load_corpus

CHUNKS_FILE = "chunks.json"
MANIFEST_FILE = "manifest.json"
FORMAT_VERSION = 1


@dataclass(frozen=True)
class Manifest:
    format_version: int
    corpus_sha256: str
    num_peps: int
    num_chunks: int
    max_chunk_chars: int
    chunk_overlap_chars: int
    built_at: float


class IndexMissing(RuntimeError):
    """Raised when ``data/`` has no index yet; the message says how to build it."""


def corpus_fingerprint(corpus_dir: Path) -> str:
    digest = hashlib.sha256()
    root = corpus_dir / "peps" if (corpus_dir / "peps").is_dir() else corpus_dir
    for path in sorted(root.glob("pep-*.rst")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_index(settings: Settings) -> Manifest:
    """Parse and chunk the corpus, then write the index into ``settings.data_dir``."""
    peps, chunks = load_corpus(settings.corpus_dir, max_chars=settings.max_chunk_chars,
                               overlap=settings.chunk_overlap_chars)
    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        format_version=FORMAT_VERSION,
        corpus_sha256=corpus_fingerprint(settings.corpus_dir),
        num_peps=len(peps),
        num_chunks=len(chunks),
        max_chunk_chars=settings.max_chunk_chars,
        chunk_overlap_chars=settings.chunk_overlap_chars,
        built_at=time.time(),
    )
    (data_dir / CHUNKS_FILE).write_text(
        json.dumps([c.to_dict() for c in chunks], ensure_ascii=False), encoding="utf-8"
    )
    (data_dir / MANIFEST_FILE).write_text(json.dumps(manifest.__dict__, indent=2), encoding="utf-8")
    return manifest


def load_manifest(settings: Settings) -> Manifest:
    path = settings.data_dir / MANIFEST_FILE
    if not path.exists():
        raise IndexMissing(f"no index in {settings.data_dir}; run `python3 -m peptalk index` first")
    return Manifest(**json.loads(path.read_text(encoding="utf-8")))


def load_chunks(settings: Settings) -> list[Chunk]:
    """Load the persisted chunks. Raises ``IndexMissing`` if the index was never built."""
    load_manifest(settings)
    raw = json.loads((settings.data_dir / CHUNKS_FILE).read_text(encoding="utf-8"))
    return [Chunk.from_dict(item) for item in raw]
