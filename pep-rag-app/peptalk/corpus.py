"""Parse PEP reStructuredText files and split them into citable chunks.

A PEP file is a header block of ``Key: value`` fields (continuation lines are
indented), a blank line, then a body whose sections are introduced by
reStructuredText underlined (or over- and underlined) headings.  Each section
becomes one or more chunks; sections longer than ``max_chars`` are split at
paragraph or word boundaries with ``overlap`` characters repeated between
neighbouring pieces.  Every chunk keeps its PEP number, title, section heading
and the character offsets ``[start, end)`` of its text in the source file.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

PREAMBLE_SECTION = "Preamble"

_UNDERLINE_RE = re.compile(r"^([!-/:-@\[-`{-~])\1{2,}\s*$")  # RST adornment: 3+ of one punctuation char
_PEP_FILE_RE = re.compile(r"^pep-(\d+)\.rst$")


@dataclass(frozen=True)
class PEP:
    number: int
    title: str
    status: str | None
    type: str | None
    created: str | None
    headers: dict[str, str]
    source: str  # path relative to the corpus directory, e.g. "peps/pep-0572.rst"


@dataclass(frozen=True)
class Chunk:
    chunk_id: str  # "pep-0572#003": stable, unique within the index
    pep: int
    title: str
    section: str  # section heading text, or "Preamble" before the first heading
    text: str  # chunk body exactly as in the source file (stripped)
    start: int  # character offset of ``text`` in the source file (inclusive)
    end: int  # character offset (exclusive)
    status: str | None = None
    type: str | None = None
    created: str | None = None

    @property
    def search_text(self) -> str:
        """Text used for indexing: title and section give context to the body."""
        return f"{self.title}\n{self.section}\n{self.text}"

    @property
    def citation(self) -> str:
        """Citation label in the required form, e.g. ``PEP 572 §Rationale``."""
        return f"PEP {self.pep} §{self.section}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Chunk":
        return cls(**data)


@dataclass(frozen=True)
class _Section:
    heading: str
    start: int  # offset of body text (after heading underline) in the file
    end: int  # offset where the next heading starts (or file end)


def _is_adornment(line: str) -> bool:
    return bool(_UNDERLINE_RE.match(line.rstrip("\n")))


def _split_lines(text: str) -> list[tuple[str, int]]:
    """(line-without-newline, start-offset) for every line."""
    out: list[tuple[str, int]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        out.append((line.rstrip("\r\n"), pos))
        pos += len(line)
    return out


def parse_header(text: str) -> tuple[dict[str, str], int]:
    """Parse the leading ``Key: value`` block.

    Returns the fields (keys as written, continuation lines joined with a
    space) and the offset where the body begins.
    """
    fields: dict[str, str] = {}
    last_key: str | None = None
    body_start = 0
    for line, offset in _split_lines(text):
        if not line.strip():
            if fields:
                body_start = offset + len(line) + _newline_len(text, offset + len(line))
                break
            continue  # tolerate leading blank lines
        if line[0].isspace() and last_key is not None:
            fields[last_key] = f"{fields[last_key]} {line.strip()}"
            continue
        key, sep, value = line.partition(":")
        if not sep or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key.strip()):
            break  # not a header line: stop
        last_key = key.strip()
        fields[last_key] = value.strip()
    else:
        body_start = len(text)
    return fields, body_start


def _newline_len(text: str, pos: int) -> int:
    if text.startswith("\r\n", pos):
        return 2
    return 1 if text.startswith("\n", pos) or text.startswith("\r", pos) else 0


def parse_pep(text: str, source: str = "") -> tuple[PEP, int]:
    """Parse a PEP file's header. Returns the PEP and the body start offset."""
    headers, body_start = parse_header(text)
    number_raw = headers.get("PEP", "")
    if not number_raw.isdigit():
        raise ValueError(f"{source or 'PEP file'}: missing or invalid 'PEP:' header ({number_raw!r})")
    pep = PEP(
        number=int(number_raw),
        title=headers.get("Title", "").strip(),
        status=headers.get("Status") or None,
        type=headers.get("Type") or None,
        created=headers.get("Created") or None,
        headers=headers,
        source=source,
    )
    return pep, body_start


def find_sections(text: str, body_start: int = 0) -> list[_Section]:
    """Split the body into sections at reStructuredText headings.

    Recognises a title line followed by an adornment line at least as long as
    the title, and the over/underlined form (adornment, title, adornment).
    The text before the first heading is returned as a ``Preamble`` section.
    """
    lines = [(l, o) for l, o in _split_lines(text) if o >= body_start]
    # (last line index of the heading block, heading start offset, title)
    headings: list[tuple[int, int, str]] = []
    i = 0
    while i < len(lines):
        line, offset = lines[i]
        if (
            i + 2 < len(lines)
            and _is_adornment(line)
            and lines[i + 1][0].strip()
            and not _is_adornment(lines[i + 1][0])
            and _is_adornment(lines[i + 2][0])
            and lines[i + 2][0][0] == line[0]
        ):
            headings.append((i + 2, offset, lines[i + 1][0].strip()))
            i += 3
            continue
        if (
            i + 1 < len(lines)
            and line.strip()
            and not _is_adornment(line)
            and _is_adornment(lines[i + 1][0])
            and len(lines[i + 1][0].rstrip()) >= len(line.strip())
        ):
            headings.append((i + 1, offset, line.strip()))
            i += 2
            continue
        i += 1

    sections: list[_Section] = []
    first_start = headings[0][1] if headings else len(text)
    if text[body_start:first_start].strip():
        sections.append(_Section(PREAMBLE_SECTION, body_start, first_start))
    for n, (last_line, _offset, title) in enumerate(headings):
        line_text, line_offset = lines[last_line]
        body_from = line_offset + len(line_text)
        body_from += _newline_len(text, body_from)
        end = headings[n + 1][1] if n + 1 < len(headings) else len(text)
        sections.append(_Section(title, body_from, end))
    return sections


def _strip_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Shrink [start, end) so that it excludes surrounding whitespace."""
    segment = text[start:end]
    stripped = segment.strip()
    if not stripped:
        return start, start
    lead = len(segment) - len(segment.lstrip())
    return start + lead, start + lead + len(stripped)


def _split_span(source: str, lo: int, hi: int, max_chars: int, overlap: int) -> list[tuple[int, int]]:
    """Split source[lo:hi] (offsets into source) into overlapping spans."""
    spans: list[tuple[int, int]] = []
    pos = lo
    while True:
        if hi - pos <= max_chars:
            spans.append((pos, hi))
            break
        window_end = pos + max_chars
        floor = pos + max_chars // 2
        cut = source.rfind("\n\n", floor, window_end)
        if cut == -1:
            cut = max(source.rfind("\n", floor, window_end), source.rfind(" ", floor, window_end))
        if cut == -1:
            cut = window_end  # no whitespace: hard split
        spans.append((pos, cut))
        next_pos = max(cut - overlap, pos + 1)
        # Start the next piece at a word boundary so no word is cut in half.
        space = source.find(" ", next_pos, cut)
        if space != -1 and space - next_pos < overlap:
            next_pos = space + 1
        while next_pos < hi and source[next_pos].isspace():
            next_pos += 1
        pos = next_pos
    return [(s, e) for s, e in ((_strip_span(source, a, b)) for a, b in spans) if e > s]


def chunk_pep(
    text: str,
    source: str = "",
    max_chars: int = 1200,
    overlap: int = 200,
) -> tuple[PEP, list[Chunk]]:
    """Parse one PEP file and return its metadata and chunks in reading order."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not 0 <= overlap < max_chars:
        raise ValueError("overlap must be >= 0 and smaller than max_chars")
    pep, body_start = parse_pep(text, source)
    chunks: list[Chunk] = []
    for section in find_sections(text, body_start):
        s, e = _strip_span(text, section.start, section.end)
        if e <= s:
            continue
        for part, (ps, pe) in enumerate(_split_span(text, s, e, max_chars, overlap)):
            chunks.append(
                Chunk(
                    chunk_id=f"pep-{pep.number:04d}#{len(chunks):03d}",
                    pep=pep.number,
                    title=pep.title,
                    section=section.heading,
                    text=text[ps:pe],
                    start=ps,
                    end=pe,
                    status=pep.status,
                    type=pep.type,
                    created=pep.created,
                )
            )
    return pep, chunks


def load_corpus(corpus_dir: Path, max_chars: int = 1200, overlap: int = 200) -> tuple[list[PEP], list[Chunk]]:
    """Read every ``pep-NNNN.rst`` under ``corpus_dir/peps`` (or ``corpus_dir``)."""
    root = corpus_dir / "peps" if (corpus_dir / "peps").is_dir() else corpus_dir
    peps: list[PEP] = []
    chunks: list[Chunk] = []
    for path in sorted(root.glob("pep-*.rst")):
        if not _PEP_FILE_RE.match(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        pep, file_chunks = chunk_pep(text, source=f"peps/{path.name}", max_chars=max_chars, overlap=overlap)
        peps.append(pep)
        chunks.extend(file_chunks)
    return peps, chunks
