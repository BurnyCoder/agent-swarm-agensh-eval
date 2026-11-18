# Evaluation results

Generated 2026-11-18 by `python3 -m peptalk.eval`. 86 questions in `eval/questions.jsonl`.

## Retrieval

Scored on the answerable questions (unanswerable questions have no gold PEPs). hit@k: a gold PEP is in the top k distinct PEPs. MRR: mean reciprocal rank of the first gold PEP (0 when it is not retrieved).

| mode | n | hit@1 | hit@5 | MRR |
| --- | ---: | ---: | ---: | ---: |
| bm25 | 83 | 0.867 | 0.988 | 0.918 |
| dense | 83 | 0.904 | 0.964 | 0.938 |
| hybrid | 83 | 0.940 | 1.000 | 0.963 |

### Misses (gold PEP not in the top 5)

**bm25**: 1 miss(es)
- q72: gold PEP 563; top PEPs [484, 681, 585, 695, 604]

**dense**: 3 miss(es)
- q72: gold PEP 563; top PEPs [484, 567, 343, 1, 654]
- q73: gold PEP 572; top PEPs [484, 1, 530, 567, 343]
- q80: gold PEP 3132; top PEPs [530, 343, 3135, 448, 484]

**hybrid**: 0 miss(es)
