# Evaluation

`eval/questions.jsonl` is the gold set. `python3 -m peptalk.eval` scores retrieval
(bm25, dense, hybrid) against it and writes `eval/results.md`. Add `--generate` to
also ask Claude each question and score citations and answers.

`eval/results.md` holds the latest retrieval numbers for bm25, dense and hybrid,
generated from `main` with a built index. Regenerate it after changing the
retrieval code, the chunking or the gold set.

## Question schema

One JSON object per line:

| field              | type         | meaning                                                        |
|--------------------|--------------|----------------------------------------------------------------|
| `id`               | string       | unique, `q01`..`q86`                                           |
| `kind`             | string       | `lookup`, `section` or `unanswerable` (see below)              |
| `question`         | string       | the question as a user would ask it                            |
| `gold_peps`        | list of int  | PEP numbers that answer it; `[]` only for `unanswerable`       |
| `reference_answer` | string       | a short answer, used by the judge; not shown to retrieval      |

The loader (`peptalk.eval.load_questions`) rejects a row with a missing field, an
unknown kind, duplicate ids, duplicate gold PEPs, non-integer gold PEPs, an
answerable question with no gold, an unanswerable question with gold, or a gold
PEP that is not in `corpus/peps/`.

## Kinds

- **lookup** (65): the answer is a fact a reader finds by opening one PEP header or
  a short passage, such as "Which PEP introduced the walrus operator?"
- **section** (18): the answer sits in a specific section of a PEP, such as a
  rationale, a backwards-compatibility note or a specification detail. These test
  whether section-level chunking helps.
- **unanswerable** (3): the corpus does not answer the question. `gold_peps` is `[]`.

## Unanswerable convention

An unanswerable item has no gold, so it is skipped by the retrieval metrics. It is
scored only in generation mode, and only on citations: the answer is correct if it
cites no PEP. A system that cites any PEP for an unanswerable question is wrong,
because any citation there is invented. The judge still checks the answer text
against the reference, which says the corpus does not cover the question.

## How gold was chosen

Each gold PEP was checked against the text of `corpus/peps/pep-NNNN.rst`. The
reference answer is written from that text and is kept short. Where a question is
answered by more than one PEP, all of them are listed (for example q14 has gold
634, 635, 636, which together define the pattern-matching syntax). Four reference
answers were corrected after checking the source (q41, q70, q79, q81).

## Metrics

Retrieval metrics are computed over distinct PEP numbers in ranked chunk results,
first occurrence wins. Results are searched to depth 10.

- **hit@1**: the top-ranked PEP is a gold PEP.
- **hit@5**: some gold PEP is in the top five distinct PEPs.
- **MRR**: mean of 1/rank of the first gold PEP (0 when none is found in depth 10).

With `--generate`, the RAG pipeline runs in `--gen-mode` (default `hybrid`, k=5):

- **citation accuracy**: the cited PEPs are a non-empty subset of gold for an
  answerable question, or empty for an unanswerable one. Citations that the
  pipeline dropped (a PEP not in the retrieved context) still count, so an invented
  PEP number is wrong.
- **answer correctness**: Claude judges the answer against `reference_answer` using
  `JUDGE_RUBRIC` (CORRECT, PARTIAL or INCORRECT on the last line). Unparsed verdicts
  are counted separately.

## Run

```sh
python3 -m pip install -r requirements.txt
python3 -m peptalk index                         # build the index first
python3 -m peptalk.eval                          # retrieval, all three modes
python3 -m peptalk.eval --modes hybrid           # one mode
python3 -m peptalk.eval --generate --limit 10    # also generate and judge
```

Generation calls go through `peptalk.generate.llm_from_env()`, which reads the
same environment variables as `ask` (see the top-level README).
Generation uses the team's metered route, so run it on a subset while iterating.
