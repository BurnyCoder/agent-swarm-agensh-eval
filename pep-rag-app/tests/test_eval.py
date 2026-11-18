"""Offline tests for the evaluation harness: metrics, loading, grading and reporting.

No index, embedding model or network is used. Retrieval and generation are
driven by fakes that implement the same contracts as the real services.
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from peptalk import eval as ev
from peptalk.generate import AskResult

REPO = Path(__file__).resolve().parent.parent


# --- Fakes -------------------------------------------------------------------


@dataclass
class Passage:
    pep: int
    title: str
    section: str | None
    text: str
    chunk_id: str = ""
    score: float = 0.0


@dataclass
class Hit:
    pep: int


class FakeRetriever:
    """Returns canned PEP numbers per query; ``search(query, mode, k)`` like the service."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def search(self, query, mode, k):
        self.calls.append((query, mode, k))
        return [Hit(n) for n in self.table.get(query, [])][:k]


class FakeLLM:
    """Answers the generation prompt with a fixed text and judges with a fixed verdict."""

    model = "fake-model"

    def __init__(self, answer="", verdict="CORRECT"):
        self.answer = answer
        self.verdict = verdict
        self.systems = []

    def complete(self, system, user):
        self.systems.append(system)
        if system == ev.JUDGE_RUBRIC:
            return f"Reasoning.\n{self.verdict}"
        return self.answer


def write_questions(path: Path, rows) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def row(**overrides):
    base = {
        "id": "q1",
        "kind": "lookup",
        "question": "Which PEP introduced the walrus operator?",
        "gold_peps": [572],
        "reference_answer": "PEP 572.",
    }
    base.update(overrides)
    return base


def question(id_="q1", gold=(572,), kind="lookup", text="Q?", ref="R."):
    return ev.Question(id=id_, question=text, gold_peps=tuple(gold), reference_answer=ref, kind=kind)


# --- Retrieval metrics -------------------------------------------------------


def test_distinct_peps_keeps_first_occurrence_order():
    assert ev.distinct_peps([8, 572, 8, 3107, 572]) == [8, 572, 3107]


def test_hit_at_k_respects_cutoff():
    ranked = [8, 572, 3107]
    assert ev.hit_at_k(ranked, [572], 1) is False
    assert ev.hit_at_k(ranked, [572], 2) is True
    assert ev.hit_at_k(ranked, [999], 5) is False


def test_reciprocal_rank_and_first_gold_rank():
    ranked = [8, 572, 3107]
    assert ev.reciprocal_rank(ranked, [572]) == pytest.approx(0.5)
    assert ev.reciprocal_rank(ranked, [3107, 572]) == pytest.approx(0.5)  # first gold wins
    assert ev.reciprocal_rank(ranked, [999]) == 0.0
    assert ev.first_gold_rank(ranked, [3107]) == 3
    assert ev.first_gold_rank(ranked, [999]) is None


def test_score_retrieval_hit_at_1_hit_at_5_and_mrr_on_fixture():
    questions = [
        question("a", gold=(572,), text="walrus"),  # rank 1
        question("b", gold=(484,), text="typing"),  # rank 3
        question("c", gold=(8,), text="style"),  # not retrieved
        question("u", gold=(), kind="unanswerable", text="weather"),  # skipped
    ]
    table = {
        "walrus": [572, 572, 8],
        "typing": [8, 8, 484, 3107],
        "style": [1, 2, 3, 4, 5, 6],
    }
    retriever = FakeRetriever(table)
    scores, rows = ev.score_retrieval(
        questions, lambda q: [h.pep for h in retriever.search(q, "bm25", 10)], "bm25"
    )
    assert scores.n == 3
    assert scores.hit_at_1 == pytest.approx(1 / 3)
    assert scores.hit_at_5 == pytest.approx(2 / 3)  # b at rank 2 in distinct PEPs
    assert scores.mrr == pytest.approx((1 + 0.5 + 0) / 3)
    assert [r.question_id for r in rows] == ["a", "b", "c"]
    assert rows[1].rank == 2
    assert rows[2].rank is None
    assert rows[2].top_peps == (1, 2, 3, 4, 5)


def test_score_retrieval_with_no_answerable_questions_is_zero():
    scores, rows = ev.score_retrieval([question("u", gold=(), kind="unanswerable")], lambda q: [], "dense")
    assert (scores.n, scores.hit_at_1, scores.hit_at_5, scores.mrr) == (0, 0.0, 0.0, 0.0)
    assert rows == []


# --- Loading and validation --------------------------------------------------


def test_load_questions_reads_valid_rows(tmp_path):
    path = write_questions(tmp_path / "q.jsonl", [row(), row(id="q2", gold_peps=[], kind="unanswerable")])
    loaded = ev.load_questions(path, known_peps=[572])
    assert [q.id for q in loaded] == ["q1", "q2"]
    assert loaded[0].gold_peps == (572,)
    assert loaded[1].kind == "unanswerable"


@pytest.mark.parametrize(
    "override, message",
    [
        ({"kind": "trivia"}, "kind must be one of"),
        ({"kind": "unanswerable", "gold_peps": [572]}, "unanswerable questions must have empty gold_peps"),
        ({"kind": "lookup", "gold_peps": []}, "need at least one gold PEP"),
        ({"gold_peps": [572, 572]}, "duplicates"),
        ({"gold_peps": ["572"]}, "list of PEP numbers"),
        ({"gold_peps": [True]}, "list of PEP numbers"),
        ({"question": "   "}, "empty question"),
    ],
)
def test_load_questions_rejects_bad_rows(tmp_path, override, message):
    path = write_questions(tmp_path / "q.jsonl", [row(**override)])
    with pytest.raises(ev.QuestionError, match=message):
        ev.load_questions(path, known_peps=[572])


def test_load_questions_rejects_missing_field(tmp_path):
    bad = row()
    del bad["reference_answer"]
    path = write_questions(tmp_path / "q.jsonl", [bad])
    with pytest.raises(ev.QuestionError, match="missing field 'reference_answer'"):
        ev.load_questions(path)


def test_load_questions_rejects_duplicate_ids(tmp_path):
    path = write_questions(tmp_path / "q.jsonl", [row(), row()])
    with pytest.raises(ev.QuestionError, match="duplicate id"):
        ev.load_questions(path)


def test_load_questions_rejects_unknown_gold_pep(tmp_path):
    path = write_questions(tmp_path / "q.jsonl", [row(gold_peps=[9999])])
    with pytest.raises(ev.QuestionError, match="not in corpus"):
        ev.load_questions(path, known_peps=[572])


def test_load_questions_reports_invalid_json_with_location(tmp_path):
    path = tmp_path / "q.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ev.QuestionError, match=r"q\.jsonl:1: invalid JSON"):
        ev.load_questions(path)


def test_load_questions_skips_blank_lines(tmp_path):
    path = tmp_path / "q.jsonl"
    path.write_text(json.dumps(row()) + "\n\n", encoding="utf-8")
    assert len(ev.load_questions(path)) == 1


def test_committed_question_set_is_valid_and_large_enough():
    questions = ev.load_questions(ev.DEFAULT_QUESTIONS, known_peps=ev.corpus_pep_numbers(ev.DEFAULT_CORPUS))
    assert len(questions) >= 40
    kinds = {q.kind for q in questions}
    assert {"lookup", "section", "unanswerable"} <= kinds
    assert len({q.id for q in questions}) == len(questions)
    assert any(q.kind == "section" for q in questions)


def test_corpus_pep_numbers_matches_files():
    numbers = ev.corpus_pep_numbers(ev.DEFAULT_CORPUS)
    assert 572 in numbers
    assert len(numbers) == len(list((REPO / "corpus" / "peps").glob("pep-*.rst")))
    assert numbers == sorted(numbers)


# --- Citations ---------------------------------------------------------------


def test_cited_peps_counts_passages_and_dropped_citations():
    result = AskResult(
        question="q",
        mode="hybrid",
        answer="Answer [PEP 572 §Rationale] and [PEP 9999 §Abstract].",
        grounded=True,
        cited=[Passage(572, "Assignment Expressions", "Rationale", "t")],
        passages=[],
        dropped_citations=["[PEP 9999 §Abstract]"],
    )
    assert ev.cited_peps(result) == [572, 9999]


def test_cited_peps_empty_when_nothing_cited():
    result = AskResult(question="q", mode="hybrid", answer="No.", grounded=False, cited=[], passages=[])
    assert ev.cited_peps(result) == []


@pytest.mark.parametrize(
    "cited, gold, expected",
    [
        ([572], [572], True),
        ([572], [572, 634], True),
        ([], [572], False),  # answerable but no citation
        ([8], [572], False),  # cites a wrong PEP
        ([572, 8], [572], False),  # subset violated
        ([], [], True),  # unanswerable and cites nothing
        ([572], [], False),  # unanswerable but cites something
    ],
)
def test_citation_correct(cited, gold, expected):
    assert ev.citation_correct(cited, gold) is expected


# --- Judge -------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply, verdict",
    [
        ("CORRECT", "CORRECT"),
        ("The answer matches.\nPARTIAL", "PARTIAL"),
        ("Verdict: incorrect", "INCORRECT"),
        ("reasoning\n\n  INCORRECT  \n\n", "INCORRECT"),
        ("I am not sure.", None),
        ("", None),
    ],
)
def test_parse_verdict_reads_last_line(reply, verdict):
    assert ev.parse_verdict(reply) == verdict


def test_build_judge_user_contains_all_three_parts():
    text = ev.build_judge_user("Why?", "Because.", "Maybe.")
    assert "QUESTION:\nWhy?" in text
    assert "REFERENCE:\nBecause." in text
    assert "ANSWER:\nMaybe." in text


def test_judge_rubric_names_the_three_verdicts():
    for word in ("CORRECT", "PARTIAL", "INCORRECT"):
        assert word in ev.JUDGE_RUBRIC


# --- Generation grading ------------------------------------------------------


def test_grade_generation_with_fake_ask_and_judge():
    questions = [
        question("a", gold=(572,), text="walrus", ref="PEP 572."),
        question("u", gold=(), kind="unanswerable", text="weather", ref="The corpus does not say."),
    ]
    answers = {
        "walrus": AskResult(
            question="walrus",
            mode="hybrid",
            answer="It is PEP 572 [PEP 572 §Abstract].",
            grounded=True,
            cited=[Passage(572, "Assignment Expressions", "Abstract", "t")],
            passages=[],
        ),
        "weather": AskResult(
            question="weather",
            mode="hybrid",
            answer="The corpus does not say.",
            grounded=False,
            cited=[],
            passages=[],
        ),
    }
    judge = FakeLLM(verdict="CORRECT")
    scores, rows = ev.grade_generation(questions, lambda q: answers[q], judge)

    assert scores.n == 2
    assert scores.citation_accuracy == 1.0
    assert scores.answer_correct == 1.0
    assert scores.answer_partial == 0.0
    assert scores.unparsed == 0
    assert [r.verdict for r in rows] == ["CORRECT", "CORRECT"]
    assert all(s == ev.JUDGE_RUBRIC for s in judge.systems)


def test_grade_generation_counts_wrong_citations_and_unparsed_verdicts():
    questions = [question("a", gold=(572,), text="walrus")]
    result = AskResult(
        question="walrus",
        mode="hybrid",
        answer="x [PEP 8 §Abstract]",
        grounded=False,
        cited=[],
        passages=[],
        dropped_citations=["[PEP 8 §Abstract]"],
    )
    judge = FakeLLM(verdict="maybe")  # not a verdict word
    scores, rows = ev.grade_generation(questions, lambda q: result, judge)
    assert scores.citation_accuracy == 0.0
    assert scores.unparsed == 1
    assert rows[0].cited == (8,)
    assert rows[0].verdict is None


def test_grade_generation_partial_counted_separately():
    questions = [question("a", gold=(572,), text="walrus")]
    result = AskResult(
        question="walrus",
        mode="hybrid",
        answer="x [PEP 572 §Abstract]",
        grounded=True,
        cited=[Passage(572, "t", "Abstract", "x")],
        passages=[],
    )
    scores, _ = ev.grade_generation(questions, lambda q: result, FakeLLM(verdict="PARTIAL"))
    assert scores.answer_partial == 1.0
    assert scores.answer_correct == 0.0


def test_grade_generation_empty_is_zero():
    scores, rows = ev.grade_generation([], lambda q: None, FakeLLM())
    assert (scores.n, scores.citation_accuracy, rows) == (0, 0.0, [])


# --- Reporting ---------------------------------------------------------------


def test_render_results_has_retrieval_table_and_misses():
    scores = [
        ev.RetrievalScores(mode="bm25", n=2, hit_at_1=0.5, hit_at_5=1.0, mrr=0.75),
        ev.RetrievalScores(mode="hybrid", n=2, hit_at_1=1.0, hit_at_5=1.0, mrr=1.0),
    ]
    rows = {
        "bm25": [
            ev.RetrievalRow("a", (572,), (572,), 1),
            ev.RetrievalRow("b", (484,), (8, 1, 2, 3, 4), None),
        ],
        "hybrid": [ev.RetrievalRow("a", (572,), (572,), 1), ev.RetrievalRow("b", (484,), (484,), 1)],
    }
    text = ev.render_results(scores, rows, questions_total=3, when=dt.date(2026, 1, 2))
    assert "Generated 2026-01-02" in text
    assert "| bm25 | 2 | 0.500 | 1.000 | 0.750 |" in text
    assert "| hybrid | 2 | 1.000 | 1.000 | 1.000 |" in text
    assert "## Generation" not in text
    assert "**bm25**: 1 miss(es)\n- b: gold PEP 484; top PEPs [8, 1, 2, 3, 4]\n" in text
    assert "**hybrid**: 0 miss(es)\n" in text
    assert "- a: gold PEP" not in text


def test_render_results_includes_generation_section():
    gen = ev.GenerationScores(n=1, citation_accuracy=1.0, answer_correct=1.0, answer_partial=0.0, unparsed=0)
    rows = [ev.GenerationRow("a", "lookup", (572,), True, "CORRECT")]
    text = ev.render_results([], {}, gen, rows, questions_total=1, generation_mode="dense", when=dt.date(2026, 1, 2))
    assert "## Generation" in text
    assert "`dense` mode" in text
    assert "| a | lookup | PEP 572 | yes | CORRECT |" in text


# --- CLI ---------------------------------------------------------------------


def _write_small_questions(tmp_path):
    return write_questions(
        tmp_path / "q.jsonl",
        [
            row(id="a", question="walrus", gold_peps=[572]),
            row(id="u", question="weather", gold_peps=[], kind="unanswerable", reference_answer="Not in the corpus."),
        ],
    )


def test_main_runs_retrieval_for_every_mode_and_writes_report(tmp_path, capsys):
    questions = _write_small_questions(tmp_path)
    out = tmp_path / "results.md"
    table = {"walrus": [572]}

    retriever = FakeRetriever(table)
    code = ev.main(["--questions", str(questions), "--out", str(out)], retriever=retriever)

    assert code == 0
    assert {mode for _, mode, _ in retriever.calls} == {"bm25", "dense", "hybrid"}
    assert all(k == ev.SEARCH_DEPTH for _, _, k in retriever.calls)
    text = out.read_text(encoding="utf-8")
    assert "| hybrid | 1 | 1.000 | 1.000 | 1.000 |" in text
    assert "## Generation" not in text
    assert "loaded 2 questions" in capsys.readouterr().out


def test_main_restricts_modes(tmp_path):
    questions = _write_small_questions(tmp_path)
    out = tmp_path / "results.md"
    retriever = FakeRetriever({"walrus": [572]})
    ev.main(["--questions", str(questions), "--out", str(out), "--modes", "bm25"], retriever=retriever)
    assert {mode for _, mode, _ in retriever.calls} == {"bm25"}
    assert "| bm25 |" in out.read_text(encoding="utf-8")
    assert "| dense |" not in out.read_text(encoding="utf-8")


def test_main_generate_uses_ask_pipeline_and_judge(tmp_path):
    questions = _write_small_questions(tmp_path)
    out = tmp_path / "results.md"
    retriever = FakeRetriever({"walrus": [572]})
    llm = FakeLLM(answer="Walrus is PEP 572 [PEP 572 §Abstract].", verdict="CORRECT")

    # The fake retriever has no passage text, so give ask real passages by wrapping search.
    class PassageRetriever:
        def search(self, query, mode, k):
            return [Passage(572, "Assignment Expressions", "Abstract", "Assignment expressions.", "c1", 0.9)] if query == "walrus" else []

    code = ev.main(
        ["--questions", str(questions), "--out", str(out), "--modes", "bm25", "--generate", "--limit", "1"],
        retriever=PassageRetriever(),
        llm=llm,
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "## Generation" in text
    assert "citation accuracy (cited PEPs within gold): 1.000" in text
    assert "answer correct: 1.000" in text
    # Limit 1: only the first question is answered and judged.
    assert llm.systems.count(ev.JUDGE_RUBRIC) == 1
    assert "| a | lookup | PEP 572 | yes | CORRECT |" in text


def test_main_generate_reports_missing_llm_config_cleanly(tmp_path, monkeypatch):
    questions = _write_small_questions(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AGENSH_MODEL_BASE", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        ev.main(
            ["--questions", str(questions), "--out", str(tmp_path / "r.md"), "--modes", "bm25", "--generate"],
            retriever=FakeRetriever({}),
        )


def test_parse_args_defaults():
    args = ev._parse_args([])
    assert args.modes == list(ev.EVAL_MODES)
    assert args.generate is False
    assert args.gen_mode == ev.GENERATE_MODE
    assert args.limit is None
