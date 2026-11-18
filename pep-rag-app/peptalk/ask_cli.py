"""The ``ask`` subcommand: retrieve, then answer with cited sources.

``add_ask_parser(sub)`` registers the subcommand on an argparse subparsers
object; ``cmd_ask(args)`` runs it. The retriever is any object with
``search(query, mode, k) -> list[Hit]`` (the same contract as the web app).
Tests inject a fake retriever and LLM; the default loads the real service
lazily so importing this module never touches the index or the network.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, TextIO

from peptalk.generate import LLM, AskResult, LLMConfigError, ask, llm_from_env

DEFAULT_MODE = "hybrid"
DEFAULT_K = 5
MODE_CHOICES = ("bm25", "dense", "hybrid")


def add_ask_parser(sub: Any) -> argparse.ArgumentParser:
    """Register ``ask`` on an argparse subparsers action."""
    parser = sub.add_parser("ask", help="answer a question from the PEP corpus, with citations")
    parser.add_argument("question", help="the question to answer")
    parser.add_argument("--mode", choices=MODE_CHOICES, default=DEFAULT_MODE, help="retrieval mode (default: hybrid)")
    parser.add_argument("-k", type=int, default=DEFAULT_K, help="number of chunks to retrieve (default: 5)")
    parser.set_defaults(func=cmd_ask)
    return parser


def _default_retriever() -> Any:
    from peptalk.service import get_retriever  # lazy: needs the index and optional deps

    return get_retriever()


def format_result(result: AskResult) -> str:
    """Render the answer followed by the cited chunks, numbered as in the prompt."""
    lines = [result.answer.strip() or "(empty answer)", ""]
    if result.cited:
        lines.append("Sources:")
        for number, passage in enumerate(result.cited, start=1):
            section = f" §{passage.section}" if passage.section else ""
            lines.append(f"  [{number}] PEP {passage.pep}{section} — {passage.title} (score {passage.score:.3f})")
    else:
        lines.append("No retrieved passage was cited.")
    return "\n".join(lines)


def run_ask(
    question: str,
    *,
    retriever: Any,
    llm: LLM,
    mode: str = DEFAULT_MODE,
    k: int = DEFAULT_K,
    model: str = "",
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run one question through retrieval and generation. Returns a process exit code."""
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        result = ask(question, retriever.search, llm, mode=mode, k=k, model=model)
    except ValueError as exc:
        print(f"error: {exc}", file=err)
        return 2
    print(format_result(result), file=out)
    if result.dropped_citations:
        dropped = ", ".join(result.dropped_citations)
        print(f"warning: dropped citations to PEPs not in the retrieved sources: {dropped}", file=err)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    try:
        llm = llm_from_env()
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    retriever = _default_retriever()
    return run_ask(
        args.question,
        retriever=retriever,
        llm=llm,
        mode=args.mode,
        k=args.k,
        model=getattr(llm, "model", ""),
    )
