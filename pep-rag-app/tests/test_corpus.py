"""The corpus is present and every file is a PEP with a title (seed check; keep passing)."""
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "peps"


def test_corpus_has_peps():
    files = sorted(CORPUS.glob("pep-*.rst"))
    assert len(files) >= 50
    for path in files:
        head = path.read_text(encoding="utf-8")[:2000]
        assert "PEP:" in head and "Title:" in head, path.name
