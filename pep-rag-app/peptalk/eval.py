"""Evaluation harness: retrieval metrics and optional answer grading.

    python3 -m peptalk.eval                  # hit@1, hit@5, MRR for bm25, dense, hybrid
    python3 -m peptalk.eval --modes bm25     # a subset of modes
    python3 -m peptalk.eval --generate       # also answer, cite-check and grade with Claude

Retrieval is scored on PEP numbers, not chunks: a query hits when a gold PEP
appears among the PEPs of the top-ranked chunks (first occurrence wins).

Generation runs the same pipeline as ``ask`` and ``POST /api/ask``
(``peptalk.generate.ask``), so the eval grades what users get. Citation
accuracy counts every PEP the answer cites, including brackets the pipeline
dropped because the PEP was not retrieved.

The metric and grading functions are pure and take plain data, so they are
tested offline without an index, an embedding model or the network. Questions
with ``kind == "unanswerable"`` have no gold PEPs and are excluded from
retrieval metrics; for generation they must produce no citations.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from peptalk.generate import LLM, AskResult, LLMConfigError, ask, llm_from_env, parse_citations

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = REPO_ROOT / "eval" / "questions.jsonl"
DEFAULT_RESULTS = REPO_ROOT / "eval" / "results.md"
DEFAULT_CORPUS = REPO_ROOT / "corpus" / "peps"

KINDS = ("lookup", "section", "unanswerable")
EVAL_MODES = ("bm25", "dense", "hybrid")
# Chunks requested per query. Distinct PEPs in this top list feed hit@k and MRR.
SEARCH_DEPTH = 10
# Chunks given to the model when generating. Matches the product default for ask.
ASK_K = 5
GENERATE_MODE = "hybrid"

_REQUIRED_FIELDS = ("id", "question", "gold_peps", "reference_answer", "kind")


class QuestionError(ValueError):
    """Raised when eval/questions.jsonl is malformed."""


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    gold_peps: tuple[int, ...]
    reference_answer: str
    kind: str


# --- Loading -----------------------------------------------------------------


def load_questions(path: Path, known_peps: Iterable[int] | None = None) -> list[Question]:
    """Read and validate a JSONL question file.

    If ``known_peps`` is given, every gold PEP must be one of them.
    """
    known = set(known_peps) if known_peps is not None else None
    questions: list[Question] = []
    seen_ids: set[str] = set()
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            where = f"{path}:{lineno}"
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise QuestionError(f"{where}: invalid JSON: {exc}") from exc
            question = _parse_question(row, where)
            if question.id in seen_ids:
                raise QuestionError(f"{where}: duplicate id {question.id!r}")
            seen_ids.add(question.id)
            if known is not None:
                missing = [n for n in question.gold_peps if n not in known]
                if missing:
                    raise QuestionError(f"{where}: gold PEPs not in corpus: {missing}")
            questions.append(question)
    return questions


def _parse_question(row: object, where: str) -> Question:
    if not isinstance(row, dict):
        raise QuestionError(f"{where}: expected a JSON object")
    for field_name in _REQUIRED_FIELDS:
        if field_name not in row:
            raise QuestionError(f"{where}: missing field {field_name!r}")
    gold = row["gold_peps"]
    if not isinstance(gold, list) or not all(
        isinstance(n, int) and not isinstance(n, bool) for n in gold
    ):
        raise QuestionError(f"{where}: gold_peps must be a list of PEP numbers")
    if len(set(gold)) != len(gold):
        raise QuestionError(f"{where}: gold_peps contains duplicates")
    kind = row["kind"]
    if kind not in KINDS:
        raise QuestionError(f"{where}: kind must be one of {', '.join(KINDS)}")
    if kind == "unanswerable" and gold:
        raise QuestionError(f"{where}: unanswerable questions must have empty gold_peps")
    if kind != "unanswerable" and not gold:
        raise QuestionError(f"{where}: answerable questions need at least one gold PEP")
    question_text = str(row["question"]).strip()
    if not question_text:
        raise QuestionError(f"{where}: empty question")
    return Question(
        id=str(row["id"]),
        question=question_text,
        gold_peps=tuple(gold),
        reference_answer=str(row["reference_answer"]).strip(),
        kind=kind,
    )


def corpus_pep_numbers(corpus_dir: Path = DEFAULT_CORPUS) -> list[int]:
    """PEP numbers that have a ``pep-NNNN.rst`` file in the corpus."""
    numbers = []
    for path in Path(corpus_dir).glob("pep-*.rst"):
        match = re.fullmatch(r"pep-(\d{4})\.rst", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return sorted(numbers)


# --- Retrieval metrics (pure) ------------------------------------------------


def distinct_peps(pep_numbers: Iterable[int]) -> list[int]:
    """Collapse a ranked list of chunk-level PEP numbers to PEP-level ranks."""
    seen: set[int] = set()
    ranked: list[int] = []
    for number in pep_numbers:
        if number not in seen:
            seen.add(number)
            ranked.append(number)
    return ranked


def hit_at_k(ranked_peps: Sequence[int], gold: Iterable[int], k: int) -> bool:
    """True when any gold PEP is among the first ``k`` ranked PEPs."""
    gold_set = set(gold)
    return any(number in gold_set for number in ranked_peps[:k])


def reciprocal_rank(ranked_peps: Sequence[int], gold: Iterable[int]) -> float:
    """1 / rank of the first gold PEP, or 0.0 when none is ranked."""
    gold_set = set(gold)
    for position, number in enumerate(ranked_peps, start=1):
        if number in gold_set:
            return 1.0 / position
    return 0.0


def first_gold_rank(ranked_peps: Sequence[int], gold: Iterable[int]) -> int | None:
    """1-based rank of the first gold PEP, or None when none is ranked."""
    gold_set = set(gold)
    for position, number in enumerate(ranked_peps, start=1):
        if number in gold_set:
            return position
    return None


@dataclass(frozen=True)
class RetrievalScores:
    mode: str
    n: int
    hit_at_1: float
    hit_at_5: float
    mrr: float


@dataclass(frozen=True)
class RetrievalRow:
    question_id: str
    gold_peps: tuple[int, ...]
    top_peps: tuple[int, ...]  # first five distinct PEPs
    rank: int | None  # 1-based rank of the first gold PEP, None if not retrieved


def score_retrieval(
    questions: Sequence[Question],
    search: Callable[[str], Sequence[int]],
    mode: str,
) -> tuple[RetrievalScores, list[RetrievalRow]]:
    """Score one retrieval mode.

    ``search(question_text)`` returns the PEP number of each retrieved chunk,
    best first. Unanswerable questions are skipped.
    """
    rows: list[RetrievalRow] = []
    hit1 = hit5 = rr_total = 0.0
    for question in questions:
        if question.kind == "unanswerable":
            continue
        ranked = distinct_peps(search(question.question))
        gold = question.gold_peps
        hit1 += hit_at_k(ranked, gold, 1)
        hit5 += hit_at_k(ranked, gold, 5)
        rr_total += reciprocal_rank(ranked, gold)
        rows.append(
            RetrievalRow(
                question_id=question.id,
                gold_peps=gold,
                top_peps=tuple(ranked[:5]),
                rank=first_gold_rank(ranked, gold),
            )
        )
    n = len(rows)
    if n == 0:
        scores = RetrievalScores(mode=mode, n=0, hit_at_1=0.0, hit_at_5=0.0, mrr=0.0)
    else:
        scores = RetrievalScores(
            mode=mode,
            n=n,
            hit_at_1=hit1 / n,
            hit_at_5=hit5 / n,
            mrr=rr_total / n,
        )
    return scores, rows


# --- Generation grading (pure) -----------------------------------------------


def cited_peps(result: AskResult) -> list[int]:
    """Every PEP the answer cites, sorted.

    Counts the cited passages and also the brackets the pipeline dropped
    because their PEP was not retrieved, since those are still citations the
    model made.
    """
    peps = {passage.pep for passage in result.cited}
    for raw in result.dropped_citations:
        peps.update(citation.pep for citation in parse_citations(raw))
    return sorted(peps)


def citation_correct(cited: Iterable[int], gold: Iterable[int]) -> bool:
    """Cited PEPs must be a subset of gold.

    For answerable questions the answer must also cite something, so an
    answer without citations does not pass. For unanswerable questions
    (empty gold) the answer must cite nothing.
    """
    cited_set = set(cited)
    gold_set = set(gold)
    if not gold_set:
        return not cited_set
    return bool(cited_set) and cited_set <= gold_set


JUDGE_RUBRIC = """You grade an answer about Python Enhancement Proposals (PEPs).
Compare the ANSWER with the REFERENCE answer for the QUESTION.
- CORRECT: the answer states the facts in the reference and makes no contradicting claim.
- PARTIAL: the answer gets part of the reference right, or misses a key detail.
- INCORRECT: the answer contradicts the reference, is off-topic, or guesses.
If the reference says the corpus does not contain the answer, CORRECT means the
answer says the evidence is missing rather than guessing.
Reply with one word on the last line: CORRECT, PARTIAL or INCORRECT."""


def build_judge_user(question: str, reference: str, answer: str) -> str:
    return f"QUESTION:\n{question}\n\nREFERENCE:\n{reference}\n\nANSWER:\n{answer}"


_VERDICT_RE = re.compile(r"\b(CORRECT|PARTIAL|INCORRECT)\b")


def parse_verdict(text: str) -> str | None:
    """Verdict from the last non-empty line of the judge reply, or None."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return None
    found = _VERDICT_RE.findall(lines[-1].upper())
    return found[-1] if found else None


@dataclass(frozen=True)
class GenerationRow:
    question_id: str
    kind: str
    cited: tuple[int, ...]
    citation_ok: bool
    verdict: str | None  # CORRECT, PARTIAL, INCORRECT, or None if the judge reply was unparsable


@dataclass(frozen=True)
class GenerationScores:
    n: int
    citation_accuracy: float
    answer_correct: float
    answer_partial: float
    unparsed: int


def grade_generation(
    questions: Sequence[Question],
    ask_fn: Callable[[str], AskResult],
    judge: LLM,
) -> tuple[GenerationScores, list[GenerationRow]]:
    """Answer each question, check its citations, and grade it with the judge.

    ``ask_fn(question_text)`` runs the product pipeline and returns an
    ``AskResult``. ``judge`` is any object with ``complete(system, user)``.
    """
    rows: list[GenerationRow] = []
    for question in questions:
        result = ask_fn(question.question)
        cited = cited_peps(result)
        reply = judge.complete(
            JUDGE_RUBRIC,
            build_judge_user(question.question, question.reference_answer, result.answer),
        )
        rows.append(
            GenerationRow(
                question_id=question.id,
                kind=question.kind,
                cited=tuple(cited),
                citation_ok=citation_correct(cited, question.gold_peps),
                verdict=parse_verdict(reply),
            )
        )
    n = len(rows)
    if n == 0:
        return GenerationScores(0, 0.0, 0.0, 0.0, 0), rows
    scores = GenerationScores(
        n=n,
        citation_accuracy=sum(r.citation_ok for r in rows) / n,
        answer_correct=sum(r.verdict == "CORRECT" for r in rows) / n,
        answer_partial=sum(r.verdict == "PARTIAL" for r in rows) / n,
        unparsed=sum(r.verdict is None for r in rows),
    )
    return scores, rows


# --- Reporting ---------------------------------------------------------------


def render_results(
    retrieval: Sequence[RetrievalScores],
    retrieval_rows: Mapping[str, Sequence[RetrievalRow]],
    generation: GenerationScores | None = None,
    generation_rows: Sequence[GenerationRow] = (),
    *,
    questions_total: int,
    generation_mode: str = GENERATE_MODE,
    when: _dt.date | None = None,
) -> str:
    """Markdown report for ``eval/results.md``."""
    when = when or _dt.date.today()
    lines = [
        "# Evaluation results",
        "",
        f"Generated {when.isoformat()} by `python3 -m peptalk.eval`. "
        f"{questions_total} questions in `eval/questions.jsonl`.",
        "",
        "## Retrieval",
        "",
        "Scored on the answerable questions (unanswerable questions have no gold PEPs). "
        "hit@k: a gold PEP is in the top k distinct PEPs. MRR: mean reciprocal rank of the "
        "first gold PEP (0 when it is not retrieved).",
        "",
        "| mode | n | hit@1 | hit@5 | MRR |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for score in retrieval:
        lines.append(
            f"| {score.mode} | {score.n} | {score.hit_at_1:.3f} | {score.hit_at_5:.3f} | {score.mrr:.3f} |"
        )
    lines += ["", "### Misses (gold PEP not in the top 5)", ""]
    for score in retrieval:
        misses = [
            row
            for row in retrieval_rows.get(score.mode, ())
            if row.rank is None or row.rank > 5
        ]
        lines.append(f"**{score.mode}**: {len(misses)} miss(es)")
        for row in misses:
            gold = ", ".join(f"PEP {n}" for n in row.gold_peps)
            lines.append(f"- {row.question_id}: gold {gold}; top PEPs {list(row.top_peps)}")
        lines.append("")
    if generation is not None:
        lines += [
            "## Generation",
            "",
            f"Answers from `peptalk.generate.ask` in `{generation_mode}` mode with k={ASK_K}, "
            "graded by a Claude judge against each reference answer. "
            f"n = {generation.n}.",
            "",
            f"- citation accuracy (cited PEPs within gold): {generation.citation_accuracy:.3f}",
            f"- answer correct: {generation.answer_correct:.3f}",
            f"- answer partial: {generation.answer_partial:.3f}",
            f"- judge replies unparsed: {generation.unparsed}",
            "",
            "| id | kind | cited | citation ok | verdict |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in generation_rows:
            cited = ", ".join(f"PEP {n}" for n in row.cited) or "-"
            ok = "yes" if row.citation_ok else "no"
            lines.append(
                f"| {row.question_id} | {row.kind} | {cited} | {ok} | {row.verdict or 'unparsed'} |"
            )
        lines.append("")
    return "\n".join(lines)


# --- Command line ------------------------------------------------------------


def _default_retriever() -> Any:
    """The shared retrieval service (same one ``ask`` and the web app use)."""
    from peptalk.service import get_retriever  # lazy: needs the index and optional deps

    return get_retriever()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m peptalk.eval",
        description="Evaluate PEPtalk retrieval (and optionally generation).",
    )
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--out", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--modes", nargs="+", choices=EVAL_MODES, default=list(EVAL_MODES))
    parser.add_argument("--generate", action="store_true", help="also answer with Claude and grade the answers")
    parser.add_argument("--gen-mode", choices=EVAL_MODES, default=GENERATE_MODE, help="retrieval mode used for --generate")
    parser.add_argument("--limit", type=int, default=None, help="generate for the first N questions only (smoke runs)")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    retriever: Any | None = None,
    llm: LLM | None = None,
) -> int:
    """Run the evaluation.

    ``retriever`` is any object with ``search(query, mode, k)`` returning hits
    with a ``pep`` attribute (the contract ``ask`` uses). ``llm`` is any
    ``LLM``. Both can be injected for tests; by default they load lazily.
    """
    args = _parse_args(argv)
    questions = load_questions(args.questions, known_peps=corpus_pep_numbers())
    print(f"loaded {len(questions)} questions from {args.questions}")

    retrieval_scores: list[RetrievalScores] = []
    retrieval_rows: dict[str, list[RetrievalRow]] = {}
    for mode in args.modes:
        if retriever is None:
            retriever = _default_retriever()

        def search(query: str, _mode: str = mode) -> list[int]:
            return [hit.pep for hit in retriever.search(query, _mode, SEARCH_DEPTH)]

        scores, rows = score_retrieval(questions, search, mode)
        retrieval_scores.append(scores)
        retrieval_rows[mode] = rows
        print(
            f"{mode:7s} n={scores.n} hit@1={scores.hit_at_1:.3f} "
            f"hit@5={scores.hit_at_5:.3f} MRR={scores.mrr:.3f}"
        )

    generation_scores = None
    generation_rows: list[GenerationRow] = []
    if args.generate:
        if llm is None:
            try:
                llm = llm_from_env()
            except LLMConfigError as exc:
                raise SystemExit(f"error: {exc}. Any non-empty value works on the team route.") from exc
        if retriever is None:
            retriever = _default_retriever()
        model = getattr(llm, "model", "")

        def ask_fn(question: str) -> AskResult:
            return ask(question, retriever.search, llm, mode=args.gen_mode, k=ASK_K, model=model)

        subset = questions if args.limit is None else questions[: args.limit]
        generation_scores, generation_rows = grade_generation(subset, ask_fn, llm)
        print(
            f"generate ({args.gen_mode}) n={generation_scores.n} "
            f"citation_accuracy={generation_scores.citation_accuracy:.3f} "
            f"correct={generation_scores.answer_correct:.3f} "
            f"partial={generation_scores.answer_partial:.3f}"
        )

    report = render_results(
        retrieval_scores,
        retrieval_rows,
        generation_scores,
        generation_rows,
        questions_total=len(questions),
        generation_mode=args.gen_mode,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
