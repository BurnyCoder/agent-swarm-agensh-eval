"""Offline tests for the ``ask`` subcommand with a fake retriever and a fake LLM."""

import argparse
import io
from dataclasses import dataclass

import pytest

from peptalk.ask_cli import add_ask_parser, cmd_ask, format_result, run_ask


@dataclass
class Hit:
    pep: int
    title: str
    section: str | None
    score: float
    snippet: str
    chunk_id: str
    start: int
    end: int
    text: str


HITS = [
    Hit(572, "Assignment Expressions", "Rationale", 0.91, "bind names...", "572-rationale-0", 100, 220,
        "Assignment expressions let you bind names inside an expression."),
    Hit(8, "Style Guide for Python Code", None, 0.42, "indent...", "8-intro-0", 0, 90,
        "Code should be indented with 4 spaces."),
]


class FakeRetriever:
    def __init__(self, hits=HITS):
        self.hits = hits
        self.calls = []

    def search(self, query, mode, k):
        self.calls.append((query, mode, k))
        return self.hits[:k]


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append((system, user))
        return self.answer


def _parse(argv):
    parser = argparse.ArgumentParser(prog="peptalk")
    add_ask_parser(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(argv)


def test_parser_defaults_to_hybrid_and_k5():
    args = _parse(["ask", "What is the walrus operator?"])
    assert args.question == "What is the walrus operator?"
    assert args.mode == "hybrid"
    assert args.k == 5
    assert args.func is cmd_ask


def test_parser_accepts_mode_and_k():
    args = _parse(["ask", "q", "--mode", "bm25", "-k", "3"])
    assert (args.mode, args.k) == ("bm25", 3)


def test_parser_rejects_unknown_mode():
    with pytest.raises(SystemExit):
        _parse(["ask", "q", "--mode", "fuzzy"])


def test_run_ask_prints_answer_and_cited_sources():
    retriever = FakeRetriever()
    llm = FakeLLM("The walrus operator binds names in expressions [PEP 572 §Rationale].")
    out, err = io.StringIO(), io.StringIO()

    code = run_ask("What is the walrus operator?", retriever=retriever, llm=llm, mode="bm25", k=2, out=out, err=err)

    assert code == 0
    assert retriever.calls == [("What is the walrus operator?", "bm25", 2)]
    text = out.getvalue()
    assert "[PEP 572 §Rationale]" in text
    assert "Sources:" in text
    assert "[1] PEP 572 §Rationale — Assignment Expressions (score 0.910)" in text
    assert "[2]" not in text  # only the cited chunk is listed
    assert err.getvalue() == ""


def test_run_ask_warns_about_invented_pep_numbers():
    llm = FakeLLM("Use it [PEP 572 §Rationale] and see [PEP 999].")
    out, err = io.StringIO(), io.StringIO()

    code = run_ask("q", retriever=FakeRetriever(), llm=llm, out=out, err=err)

    assert code == 0
    assert "[PEP 999]" not in out.getvalue()
    assert "PEP 999" in err.getvalue()


def test_run_ask_empty_question_is_an_error():
    out, err = io.StringIO(), io.StringIO()

    code = run_ask("   ", retriever=FakeRetriever(), llm=FakeLLM("x"), out=out, err=err)

    assert code == 2
    assert "question must not be empty" in err.getvalue()
    assert out.getvalue() == ""


def test_format_result_without_citations_says_so():
    from peptalk.generate import AskResult

    result = AskResult(
        question="q", mode="hybrid", answer="The retrieved PEP text does not answer this question.",
        grounded=False, cited=[], passages=list(HITS),
    )

    text = format_result(result)

    assert "does not answer" in text
    assert "No retrieved passage was cited." in text


def test_cmd_ask_without_api_key_returns_2(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AGENSH_MODEL_BASE", raising=False)
    args = _parse(["ask", "q"])

    assert cmd_ask(args) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
