from peptalk.corpus import PREAMBLE_SECTION, Chunk, chunk_pep, find_sections, parse_header, parse_pep

SAMPLE = """\
PEP: 9999
Title: Sample Proposal
Author: A. Author <a@example.org>,
        B. Author <b@example.org>
Status: Draft
Type: Standards Track
Created: 01-Jan-2024

Abstract
========

This is the abstract.  It spans
one paragraph.

Rationale
---------

First rationale paragraph.

Second rationale paragraph.

Rationale
---------

A second section with the same heading.
"""


def test_parse_header_fields_and_continuations():
    fields, body_start = parse_header(SAMPLE)
    assert fields["PEP"] == "9999"
    assert fields["Title"] == "Sample Proposal"
    assert fields["Author"] == "A. Author <a@example.org>, B. Author <b@example.org>"
    assert fields["Created"] == "01-Jan-2024"
    assert SAMPLE[body_start:].startswith("Abstract")


def test_parse_pep_metadata():
    pep, _ = parse_pep(SAMPLE, source="peps/pep-9999.rst")
    assert pep.number == 9999
    assert pep.title == "Sample Proposal"
    assert pep.status == "Draft"
    assert pep.type == "Standards Track"
    assert pep.created == "01-Jan-2024"
    assert pep.source == "peps/pep-9999.rst"


def test_parse_pep_rejects_missing_number():
    try:
        parse_pep("Title: No number\n\nBody\n", source="x.rst")
    except ValueError as exc:
        assert "PEP" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_sections_include_repeated_headings_in_order():
    _, body_start = parse_header(SAMPLE)
    headings = [s.heading for s in find_sections(SAMPLE, body_start)]
    assert headings == ["Abstract", "Rationale", "Rationale"]


def test_overlined_heading_is_detected():
    text = "PEP: 1\nTitle: T\n\n====\nTop\n====\n\nBody of top.\n"
    _, body_start = parse_header(text)
    sections = find_sections(text, body_start)
    assert [s.heading for s in sections] == ["Top"]


def test_chunks_carry_metadata_and_offsets():
    pep, chunks = chunk_pep(SAMPLE, source="peps/pep-9999.rst")
    assert pep.number == 9999
    assert [c.section for c in chunks] == ["Abstract", "Rationale", "Rationale"]
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.pep == 9999 and c.title == "Sample Proposal"
        assert SAMPLE[c.start:c.end] == c.text
    assert chunks[0].text.startswith("This is the abstract.")
    assert chunks[1].text == "First rationale paragraph.\n\nSecond rationale paragraph."
    assert chunks[2].text == "A second section with the same heading."
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_citation_label_matches_required_form():
    _, chunks = chunk_pep(SAMPLE)
    assert chunks[1].citation == "PEP 9999 §Rationale"


def test_preamble_before_first_heading_is_kept():
    text = "PEP: 2\nTitle: Pre\n\nIntro text before any heading.\n\nSection\n-------\n\nBody.\n"
    _, chunks = chunk_pep(text)
    assert chunks[0].section == PREAMBLE_SECTION
    assert chunks[0].text == "Intro text before any heading."
    assert chunks[1].section == "Section"


def test_long_section_is_split_with_overlap_and_offsets():
    words = " ".join(f"word{i:04d}" for i in range(400))  # ~4400 chars in one section
    text = f"PEP: 3\nTitle: Long\n\nBig\n===\n\n{words}\n"
    _, chunks = chunk_pep(text, max_chars=500, overlap=100)
    assert len(chunks) > 5
    assert all(len(c.text) <= 500 for c in chunks)
    assert all(c.section == "Big" for c in chunks)
    for c in chunks:
        assert text[c.start:c.end] == c.text
    # consecutive chunks overlap, and together they cover every word
    for a, b in zip(chunks, chunks[1:]):
        assert b.start < a.end
    covered = set()
    for c in chunks:
        covered.update(c.text.split())
    assert covered == set(words.split())


def test_invalid_chunk_parameters_are_rejected():
    for kwargs in ({"max_chars": 0}, {"max_chars": 100, "overlap": 100}):
        try:
            chunk_pep("PEP: 4\nTitle: x\n\nS\n=\n\nbody\n", **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_real_pep_572_has_rationale_section():
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "corpus" / "peps" / "pep-0572.rst"
    pep, chunks = chunk_pep(path.read_text(encoding="utf-8"), source="peps/pep-0572.rst")
    assert pep.number == 572
    assert pep.title == "Assignment Expressions"
    assert any(c.section == "Rationale" for c in chunks)
    assert all(c.pep == 572 for c in chunks)
